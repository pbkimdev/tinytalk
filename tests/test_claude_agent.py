"""Claude Agent SDK adapter (#27), with the SDK mocked via an injected query_fn."""

from __future__ import annotations

import asyncio
import json

import pytest

from tinytalk.contract import Danger
from tinytalk.engine import generate
from tinytalk.provider.base import CompletionRequest, Message, ResponseFormat, Role
from tinytalk.provider.claude_agent import ClaudeAgentError, ClaudeAgentProvider

PAYLOAD = {
    "command": "du -h -d1 . | sort -hr | head -20",
    "explanation": "Top-level disk usage, sorted",
    "danger": "safe",
    "confidence": 0.9,
    "needs": ["du", "sort", "head"],
}


class ResultMessage:
    """Shape-compatible stand-in for claude_agent_sdk.ResultMessage."""

    def __init__(self, *, structured_output=None, result=None, is_error=False, usage=None):
        self.structured_output = structured_output
        self.result = result
        self.is_error = is_error
        self.usage = usage
        self.subtype = "success"


class OtherMessage:
    pass


def fake_query(*messages, capture=None):
    async def query_fn(*, prompt, options):
        if capture is not None:
            capture["prompt"] = prompt
            capture["options"] = options
        for m in messages:
            yield m

    return query_fn


def request(fmt=ResponseFormat.JSON_OBJECT, **kwargs):
    return CompletionRequest(
        messages=[
            Message(Role.SYSTEM, "you are tt"),
            Message(Role.USER, "disk usage"),
        ],
        response_format=fmt,
        **kwargs,
    )


def test_structured_output_surfaces_as_json_text():
    capture = {}
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(
            OtherMessage(),
            ResultMessage(
                structured_output=PAYLOAD, usage={"input_tokens": 10, "output_tokens": 5}
            ),
            capture=capture,
        ),
    )
    completion = asyncio.run(provider.complete(request()))
    assert json.loads(completion.text) == PAYLOAD
    assert completion.usage.prompt_tokens == 10
    assert completion.usage.completion_tokens == 5
    assert completion.usage.total_tokens == 15
    assert completion.model == "claude-sonnet-5"
    # request mapping: system → system_prompt, user → prompt, schema requested
    assert capture["prompt"] == "disk usage"
    assert capture["options"].system_prompt == "you are tt"
    assert capture["options"].output_format == {
        "type": "json_schema",
        "schema": __import__(
            "tinytalk.contract", fromlist=["contract_json_schema"]
        ).contract_json_schema(),
    }


def test_text_fallback_uses_result_text():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(result=f"```json\n{json.dumps(PAYLOAD)}\n```")),
    )
    completion = asyncio.run(provider.complete(request(fmt=ResponseFormat.TEXT)))
    assert "du -h" in completion.text


def test_error_result_raises():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(is_error=True, result="budget exceeded")),
    )
    with pytest.raises(ClaudeAgentError, match="budget exceeded"):
        asyncio.run(provider.complete(request()))


def test_missing_result_raises():
    provider = ClaudeAgentProvider("claude-sonnet-5", query_fn=fake_query(OtherMessage()))
    with pytest.raises(ClaudeAgentError, match="without a result"):
        asyncio.run(provider.complete(request()))


def test_sdk_exception_is_wrapped():
    async def boom(*, prompt, options):
        raise RuntimeError("CLI not found")
        yield  # pragma: no cover

    provider = ClaudeAgentProvider("claude-sonnet-5", query_fn=boom)
    with pytest.raises(ClaudeAgentError, match="CLI not found"):
        asyncio.run(provider.complete(request()))


def test_effort_mapping():
    capture = {}
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(structured_output=PAYLOAD), capture=capture),
    )
    asyncio.run(provider.complete(request(reasoning_effort="low")))
    assert capture["options"].effort == "low"


def test_end_to_end_through_engine():
    """The engine's JSON_OBJECT rung parses the adapter's output into a Suggestion."""
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(structured_output=PAYLOAD)),
    )
    gen = asyncio.run(generate(provider, [Message(Role.USER, "disk usage")]))
    assert gen.response_format is ResponseFormat.JSON_OBJECT
    assert gen.suggestion.danger is Danger.SAFE
    assert gen.suggestion.command.startswith("du -h")
