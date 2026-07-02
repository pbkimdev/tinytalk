import asyncio
import json

import pytest

from tinytalk.engine import build_ladder, generate, generate_sync
from tinytalk.parsing import FormatError
from tinytalk.provider.base import (
    Capabilities,
    Completion,
    Message,
    ResponseFormat,
    Role,
    ToolCall,
    Usage,
)
from tests.stubs import StubProvider

VALID = {
    "command": "ls -la",
    "explanation": "list files",
    "danger": "safe",
    "confidence": 0.95,
    "needs": [],
}
MSGS = [Message(role=Role.USER, content="list files")]


def _run(coro):
    return asyncio.run(coro)


def test_native_tool_call_success():
    prov = StubProvider(
        Capabilities(supports_tool_calling=True),
        [Completion(tool_calls=[ToolCall("1", "suggest_command", json.dumps(VALID))])],
    )
    gen = _run(generate(prov, MSGS))
    assert gen.format_ok
    assert gen.attempts == 1
    assert gen.response_format is ResponseFormat.TOOL_CALL
    assert gen.suggestion.command == "ls -la"


def test_native_json_object_success():
    prov = StubProvider(Capabilities(supports_native_json=True), [Completion(text=json.dumps(VALID))])
    gen = _run(generate(prov, MSGS))
    assert gen.response_format is ResponseFormat.JSON_OBJECT
    assert gen.suggestion.command == "ls -la"


def test_garbage_then_valid_retries_within_tier():
    prov = StubProvider(
        Capabilities(supports_native_json=True),
        [Completion(text="not json"), Completion(text=json.dumps(VALID))],
    )
    gen = _run(generate(prov, MSGS))
    assert gen.attempts == 2
    assert gen.suggestion.command == "ls -la"  # malformed never surfaced


def test_text_only_falls_to_extraction():
    prov = StubProvider(
        Capabilities(),
        [Completion(text=f"here you go:\n```json\n{json.dumps(VALID)}\n```")],
    )
    gen = _run(generate(prov, MSGS))
    assert gen.response_format is ResponseFormat.TEXT
    assert gen.suggestion.command == "ls -la"


def test_all_garbage_raises():
    prov = StubProvider(Capabilities(), [Completion(text="nope") for _ in range(10)])
    with pytest.raises(FormatError):
        _run(generate(prov, MSGS))


def test_carries_usage():
    usage = Usage(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    prov = StubProvider(
        Capabilities(supports_native_json=True),
        [Completion(text=json.dumps(VALID), usage=usage)],
    )
    assert _run(generate(prov, MSGS)).usage == usage


def test_generate_sync_wrapper():
    prov = StubProvider(Capabilities(supports_native_json=True), [Completion(text=json.dumps(VALID))])
    assert generate_sync(prov, MSGS).suggestion.command == "ls -la"


@pytest.mark.parametrize(
    "caps,expected",
    [
        (Capabilities(supports_tool_calling=True), [ResponseFormat.TOOL_CALL, ResponseFormat.TEXT]),
        (
            Capabilities(supports_native_json=True),
            [ResponseFormat.JSON_OBJECT, ResponseFormat.TEXT],
        ),
        (Capabilities(), [ResponseFormat.TEXT]),
        (
            Capabilities(supports_tool_calling=True, supports_grammar=True),
            [ResponseFormat.TOOL_CALL, ResponseFormat.GRAMMAR, ResponseFormat.TEXT],
        ),
    ],
)
def test_build_ladder_ordering(caps, expected):
    assert build_ladder(caps) == expected
