"""Bedrock adapter tests, with boto3 faked via an injected client (no real AWS calls)."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from tinytalk.contract import contract_json_schema
from tinytalk.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ResponseFormat,
    Role,
    Tool,
)
from tinytalk.provider.bedrock import BedrockError, BedrockProvider, list_foundation_models

MSGS = [Message(Role.SYSTEM, "you are tt"), Message(Role.USER, "list files")]


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
    def __init__(self, summaries=None, error=None):
        self._summaries = summaries
        self._error = error

    def list_foundation_models(self):
        if self._error is not None:
            raise self._error
        return {"modelSummaries": self._summaries}


def _run(coro):
    return asyncio.run(coro)


def _envelope(*, blocks, usage=None):
    env: dict = {"output": {"message": {"role": "assistant", "content": blocks}}}
    if usage is not None:
        env["usage"] = usage
    return env


def test_seam_conformance():
    prov = BedrockProvider(
        "anthropic.claude-opus-4-8-v1:0",
        region="us-east-1",
        client=FakeRuntimeClient(_envelope(blocks=[])),
    )
    assert isinstance(prov, Provider)
    assert prov.name == "bedrock:anthropic.claude-opus-4-8-v1:0"
    assert isinstance(prov.capabilities, Capabilities)
    assert not prov.capabilities.supports_tool_calling  # conservative default


def test_text_response_parsed():
    client = FakeRuntimeClient(
        _envelope(
            blocks=[{"text": "ls -la"}],
            usage={"inputTokens": 3, "outputTokens": 4, "totalTokens": 7},
        )
    )
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    assert completion.text == "ls -la"
    assert completion.usage.total_tokens == 7


def test_system_message_split():
    client = FakeRuntimeClient(_envelope(blocks=[]))
    prov = BedrockProvider("some-model", region="us-east-1", client=client)
    _run(prov.complete(CompletionRequest(MSGS)))
    payload = client.calls[0]
    assert payload["system"] == [{"text": "you are tt"}]
    assert payload["messages"] == [{"role": "user", "content": [{"text": "list files"}]}]


def test_tool_use_response_parsed():
    payload_data = {
        "command": "ls -la",
        "explanation": "x",
        "danger": "safe",
        "confidence": 0.9,
        "needs": [],
    }
    client = FakeRuntimeClient(
        _envelope(
            blocks=[
                {"toolUse": {"toolUseId": "t1", "name": "suggest_command", "input": payload_data}}
            ]
        )
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


def test_endpoint_url_passed_to_runtime_client(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def client(self, service, **kwargs):
            calls.append((self.kwargs, service, kwargs))
            return FakeRuntimeClient(_envelope(blocks=[]))

    monkeypatch.setattr("tinytalk.addons.ensure_bedrock_importable", lambda: None)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=FakeSession))
    prov = BedrockProvider(
        "some-model",
        region="us-east-1",
        profile="dev",
        endpoint_url="https://bedrock-runtime.example.test",
    )
    _run(prov.complete(CompletionRequest(MSGS)))
    assert calls == [
        (
            {"region_name": "us-east-1", "profile_name": "dev"},
            "bedrock-runtime",
            {"endpoint_url": "https://bedrock-runtime.example.test"},
        )
    ]


def test_endpoint_url_omitted_for_model_listing(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def client(self, service, **kwargs):
            calls.append((self.kwargs, service, kwargs))
            return FakeControlClient([])

    monkeypatch.setattr("tinytalk.addons.ensure_bedrock_importable", lambda: None)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=FakeSession))
    assert list_foundation_models(region="us-west-2") == []
    assert calls == [({"region_name": "us-west-2"}, "bedrock", {})]


def test_missing_boto3_raises_actionable_error(monkeypatch):
    # Force the optional-dependency branch so the test stays valid when the bedrock
    # extra happens to be installed in the developer's venv (#124).
    monkeypatch.setattr("tinytalk.addons.ensure_bedrock_importable", lambda: None)
    monkeypatch.setitem(sys.modules, "boto3", None)
    prov = BedrockProvider("some-model", region="us-east-1")
    with pytest.raises(BedrockError, match="not installed"):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_converse_sso_error_names_login_command(monkeypatch):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeRuntimeClient(error=errors.UnauthorizedSSOTokenError("expired"))
    prov = BedrockProvider("some-model", region="us-east-1", profile="dev", client=client)
    with pytest.raises(BedrockError, match="aws sso login --profile dev"):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_list_foundation_models_sso_error_names_login_command(monkeypatch):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeControlClient(error=errors.SSOTokenLoadError("missing"))
    with pytest.raises(BedrockError, match="aws sso login --profile dev"):
        list_foundation_models(region="us-east-1", profile="dev", client=client)


def test_converse_no_credentials_repairs_profile_without_sso_hint(monkeypatch):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeRuntimeClient(error=errors.NoCredentialsError("missing"))
    prov = BedrockProvider("some-model", region="us-east-1", profile="dev", client=client)
    with pytest.raises(BedrockError) as exc:
        _run(prov.complete(CompletionRequest(MSGS)))
    message = str(exc.value)
    assert "AWS profile 'dev'" in message
    assert "aws sso login" not in message


def test_list_foundation_models_no_credentials_points_at_standard_chain(monkeypatch):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeControlClient(error=errors.NoCredentialsError("missing"))
    with pytest.raises(BedrockError) as exc:
        list_foundation_models(region="us-east-1", client=client)
    message = str(exc.value)
    assert "standard AWS credential chain" in message
    assert "aws sso login" not in message


@pytest.mark.parametrize(
    "error_cls",
    [
        "PartialCredentialsError",
        "CredentialRetrievalError",
    ],
)
def test_non_sso_credential_errors_repair_profile_without_sso_hint(monkeypatch, error_cls):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeControlClient(error=getattr(errors, error_cls)("broken"))
    with pytest.raises(BedrockError) as exc:
        list_foundation_models(region="us-east-1", profile="dev", client=client)
    message = str(exc.value)
    assert "AWS profile 'dev'" in message
    assert "aws sso login" not in message


def test_profile_not_found_names_profile_without_sso_hint(monkeypatch):
    errors = _fake_botocore_errors(monkeypatch)
    client = FakeControlClient(error=errors.ProfileNotFound("dev"))
    with pytest.raises(BedrockError) as exc:
        list_foundation_models(region="us-east-1", profile="dev", client=client)
    message = str(exc.value)
    assert "AWS profile 'dev' was not found" in message
    assert "aws sso login" not in message


def test_usage_cache_tokens_normalized():
    client = FakeRuntimeClient(
        _envelope(
            blocks=[{"text": "ls -la"}],
            usage={
                "inputTokens": 10,
                "outputTokens": 4,
                "totalTokens": 104,
                "cacheReadInputTokens": 70,
                "cacheWriteInputTokens": 20,
            },
        )
    )
    provider = BedrockProvider("anthropic.claude-sonnet-5", region="us-east-1", client=client)
    completion = asyncio.run(provider.complete(CompletionRequest(MSGS)))
    # inputTokens is exclusive on Converse — normalized to the seam's inclusive convention
    assert completion.usage.prompt_tokens == 100
    assert completion.usage.cached_prompt_tokens == 70
    assert completion.usage.cache_write_tokens == 20


def _fake_botocore_errors(monkeypatch):
    class UnauthorizedSSOTokenError(Exception):
        pass

    class SSOTokenLoadError(Exception):
        pass

    class TokenRetrievalError(Exception):
        pass

    class NoCredentialsError(Exception):
        pass

    class PartialCredentialsError(Exception):
        pass

    class CredentialRetrievalError(Exception):
        pass

    class ProfileNotFound(Exception):
        pass

    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.UnauthorizedSSOTokenError = UnauthorizedSSOTokenError
    exceptions.SSOTokenLoadError = SSOTokenLoadError
    exceptions.TokenRetrievalError = TokenRetrievalError
    exceptions.NoCredentialsError = NoCredentialsError
    exceptions.PartialCredentialsError = PartialCredentialsError
    exceptions.CredentialRetrievalError = CredentialRetrievalError
    exceptions.ProfileNotFound = ProfileNotFound
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    return exceptions
