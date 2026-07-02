"""Bedrock adapter tests, with boto3 faked via an injected client (no real AWS calls)."""

from __future__ import annotations

import asyncio
import json

import pytest

from clite.contract import contract_json_schema
from clite.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ResponseFormat,
    Role,
    Tool,
)
from clite.provider.bedrock import BedrockError, BedrockProvider, list_foundation_models

MSGS = [Message(Role.SYSTEM, "you are clite"), Message(Role.USER, "list files")]


class FakeRuntimeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class FakeControlClient:
    def __init__(self, summaries):
        self._summaries = summaries

    def list_foundation_models(self):
        return {"modelSummaries": self._summaries}


def _run(coro):
    return asyncio.run(coro)


def _envelope(*, blocks, usage=None):
    env: dict = {"output": {"message": {"role": "assistant", "content": blocks}}}
    if usage is not None:
        env["usage"] = usage
    return env


def test_seam_conformance():
    prov = BedrockProvider("anthropic.claude-opus-4-8-v1:0", region="us-east-1", client=FakeRuntimeClient(_envelope(blocks=[])))
    assert isinstance(prov, Provider)
    assert prov.name == "bedrock:anthropic.claude-opus-4-8-v1:0"
    assert isinstance(prov.capabilities, Capabilities)
    assert not prov.capabilities.supports_tool_calling  # conservative default


def test_text_response_parsed():
    client = FakeRuntimeClient(_envelope(blocks=[{"text": "ls -la"}], usage={"inputTokens": 3, "outputTokens": 4, "totalTokens": 7}))
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    assert completion.text == "ls -la"
    assert completion.usage.total_tokens == 7


def test_system_message_split():
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS)))
    payload = client.calls[0]
    assert payload["system"] == [{"text": "you are clite"}]
    assert payload["messages"] == [{"role": "user", "content": [{"text": "list files"}]}]


def test_tool_use_response_parsed():
    payload_data = {"command": "ls -la", "explanation": "x", "danger": "safe", "confidence": 0.9, "needs": []}
    client = FakeRuntimeClient(
        _envelope(blocks=[{"toolUse": {"toolUseId": "t1", "name": "suggest_command", "input": payload_data}}])
    )
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    completion = _run(
        prov.complete(
            CompletionRequest(
                MSGS,
                response_format=ResponseFormat.TOOL_CALL,
                tools=[Tool("suggest_command", "desc", contract_json_schema())],
            )
        )
    )
    call = completion.tool_calls[0]
    assert (call.id, call.name) == ("t1", "suggest_command")
    assert json.loads(call.arguments) == payload_data
    tool_config = client.calls[0]["toolConfig"]
    assert tool_config["tools"][0]["toolSpec"]["inputSchema"]["json"] == contract_json_schema()
    assert tool_config["toolChoice"] == {"tool": {"name": "suggest_command"}}


def test_effort_maps_to_thinking_budget():
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("anthropic.claude-opus-4-8-v1:0", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="high")))
    assert client.calls[0]["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 24576}
    }


def test_unsupported_effort_omitted():
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="xhigh")))
    assert "additionalModelRequestFields" not in client.calls[0]


def test_effort_skipped_for_non_claude_models():
    # PRD §4: thinking budget is Claude-on-Bedrock only — other families skip it silently.
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("meta.llama3-1-70b-instruct-v1:0", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="high")))
    assert "additionalModelRequestFields" not in client.calls[0]


def test_effort_applies_to_cross_region_claude_profile():
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("us.anthropic.claude-opus-4-8-v1:0", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="low")))
    thinking = client.calls[0]["additionalModelRequestFields"]["thinking"]
    assert thinking["budget_tokens"] == 2048


def test_converse_error_is_wrapped():
    client = FakeRuntimeClient(error=RuntimeError("AccessDeniedException"))
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    with pytest.raises(BedrockError, match="AccessDeniedException"):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_missing_content_raises():
    client = FakeRuntimeClient({"output": {"message": {}}})
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    with pytest.raises(BedrockError, match="no message content"):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_list_foundation_models():
    client = FakeControlClient([{"modelId": "anthropic.claude-opus-4-8-v1:0"}])
    models = list_foundation_models(region="us-east-1", client=client)
    assert models == [{"modelId": "anthropic.claude-opus-4-8-v1:0"}]


def test_missing_boto3_raises_actionable_error():
    # boto3 is an optional extra and isn't installed in the dev env — proves the real
    # (non-injected) path raises an install hint rather than a bare ImportError.
    prov = BedrockProvider("some-model", region="us-east-1")
    with pytest.raises(BedrockError, match="not installed"):
        _run(prov.complete(CompletionRequest(MSGS)))
