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


class TextBlock:
    """Shape-compatible stand-in for an AssistantMessage TextBlock."""

    def __init__(self, text):
        self.text = text


class AssistantMessage:
    """Shape-compatible stand-in for claude_agent_sdk.AssistantMessage."""

    def __init__(self, *blocks):
        self.content = list(blocks)


def fake_query(*messages, capture=None):
    async def query_fn(*, prompt, options):
        if capture is not None:
            capture["prompt"] = prompt
            capture["options"] = options
        for m in messages:
            yield m

    return query_fn


async def _collect(aiter):
    return [chunk async for chunk in aiter]


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


def test_usage_cache_tokens_normalized():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(
            ResultMessage(
                structured_output=PAYLOAD,
                usage={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 70,
                    "cache_creation_input_tokens": 20,
                },
            )
        ),
    )
    completion = asyncio.run(provider.complete(request()))
    assert completion.usage.prompt_tokens == 100  # inclusive, normalized
    assert completion.usage.cached_prompt_tokens == 70
    assert completion.usage.cache_write_tokens == 20


def test_default_effort_applied_and_request_wins():
    capture = {}
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(structured_output=PAYLOAD), capture=capture),
        default_effort="low",
    )
    asyncio.run(provider.complete(request()))
    assert capture["options"].effort == "low"
    asyncio.run(provider.complete(request(reasoning_effort="high")))
    assert capture["options"].effort == "high"


def test_stream_emits_text_deltas_then_terminal_completion():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(
            AssistantMessage(TextBlock('{"command": "du -h"')),
            OtherMessage(),  # non-assistant message contributes no delta
            AssistantMessage(TextBlock(', "danger": "safe"}')),
            ResultMessage(
                structured_output=PAYLOAD, usage={"input_tokens": 10, "output_tokens": 5}
            ),
        ),
    )
    chunks = asyncio.run(_collect(provider.stream(request())))
    deltas = [c.delta for c in chunks if c.completion is None]
    assert "".join(deltas) == '{"command": "du -h", "danger": "safe"}'
    terminal = chunks[-1].completion
    assert terminal is not None
    assert json.loads(terminal.text) == PAYLOAD  # reconciled to the validated payload
    assert terminal.usage.total_tokens == 15
    assert terminal.model == "claude-sonnet-5"


def test_stream_terminal_matches_complete():
    # The terminal StreamChunk carries the same validated Completion as the blocking path.
    def query():
        return fake_query(
            AssistantMessage(TextBlock("live preview")),
            ResultMessage(structured_output=PAYLOAD, usage={"input_tokens": 3, "output_tokens": 4}),
        )

    completion = asyncio.run(
        ClaudeAgentProvider("claude-sonnet-5", query_fn=query()).complete(request())
    )
    chunks = asyncio.run(
        _collect(ClaudeAgentProvider("claude-sonnet-5", query_fn=query()).stream(request()))
    )
    terminal = chunks[-1].completion
    assert terminal.text == completion.text
    assert terminal.usage.total_tokens == completion.usage.total_tokens
    assert terminal.model == completion.model


def test_stream_error_result_raises():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(ResultMessage(is_error=True, result="budget exceeded")),
    )
    with pytest.raises(ClaudeAgentError, match="budget exceeded"):
        asyncio.run(_collect(provider.stream(request())))


def test_stream_missing_result_raises():
    provider = ClaudeAgentProvider(
        "claude-sonnet-5",
        query_fn=fake_query(AssistantMessage(TextBlock("orphan preview"))),
    )
    with pytest.raises(ClaudeAgentError, match="without a result"):
        asyncio.run(_collect(provider.stream(request())))
