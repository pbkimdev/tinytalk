import asyncio
import json

import pytest

from tinytalk.engine import build_ladder, generate, generate_sync
from tinytalk.parsing import FormatError
from tinytalk.provider.base import (
    Capabilities,
    Completion,
    Message,
    ProviderError,
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
    prov = StubProvider(
        Capabilities(supports_native_json=True), [Completion(text=json.dumps(VALID))]
    )
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
    prov = StubProvider(
        Capabilities(supports_native_json=True), [Completion(text=json.dumps(VALID))]
    )
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


def test_generate_accumulates_usage_across_failed_then_successful_attempt():
    prov = StubProvider(
        Capabilities(supports_native_json=True),
        [
            Completion(text="not json", usage=Usage(10, 2, 12)),
            Completion(text=json.dumps(VALID), usage=Usage(11, 3, 14)),
        ],
    )
    gen = _run(generate(prov, MSGS))
    assert gen.attempts == 2
    assert gen.suggestion.command == "ls -la"
    # usage sums the failed parse AND the winning parse — not just the winner.
    assert gen.usage == Usage(21, 5, 26)
    # one ledger entry per attempt, in order, untagged (the controller tags tier/backend).
    assert [d.result for d in gen.attempts_detail] == ["format_error", "ok"]
    assert [d.usage for d in gen.attempts_detail] == [Usage(10, 2, 12), Usage(11, 3, 14)]
    assert all(d.format_reached is ResponseFormat.JSON_OBJECT for d in gen.attempts_detail)
    assert all(isinstance(d.latency_ms, int) and d.latency_ms >= 0 for d in gen.attempts_detail)


def test_provider_error_mid_ladder_carries_accumulated_usage_and_ledger():
    # Attempt 1 parses-fails (billed), attempt 2 transport-dies: the ProviderError
    # must carry the earlier spend + ledger so the controller can bill it faithfully.
    def scripted(request, i):
        if i == 0:
            return Completion(text="not json", usage=Usage(9, 3, 12))
        raise ProviderError("transport died")

    prov = StubProvider(Capabilities(supports_native_json=True), scripted)
    with pytest.raises(ProviderError) as exc:
        _run(generate(prov, MSGS))
    assert exc.value.usage == Usage(9, 3, 12)
    assert [d.result for d in exc.value.attempts_detail] == ["format_error"]


def test_generate_coalesces_zero_total_to_prompt_plus_completion():
    prov = StubProvider(
        Capabilities(supports_native_json=True),
        [Completion(text=json.dumps(VALID), usage=Usage(prompt_tokens=8, completion_tokens=4))],
    )
    gen = _run(generate(prov, MSGS))
    assert gen.usage.total_tokens == 12  # openai-compat total==0 coalesced


def test_terminal_format_error_carries_usage_and_ledger():
    prov = StubProvider(
        Capabilities(),  # TEXT only → retries_per_tier attempts, all garbage
        [Completion(text="nope", usage=Usage(5, 1, 6)) for _ in range(2)],
    )
    with pytest.raises(FormatError) as exc:
        _run(generate(prov, MSGS))
    assert exc.value.usage == Usage(10, 2, 12)  # spend from every failed attempt
    assert len(exc.value.attempts_detail) == 2
    assert all(d.result == "format_error" for d in exc.value.attempts_detail)
