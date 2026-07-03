"""Codex Agent SDK adapter tests, with the SDK faked via an injected codex_factory."""

from __future__ import annotations

import asyncio
import json

import pytest

from tinytalk.contract import contract_json_schema
from tinytalk.provider.base import CompletionRequest, Message, Role
from tinytalk.provider.codex_agent import (
    CodexAgentError,
    CodexAgentProvider,
    list_models,
    login_api_key,
)

PAYLOAD = {
    "command": "du -h -d1 . | sort -hr | head -20",
    "explanation": "Top-level disk usage, sorted",
    "danger": "safe",
    "confidence": 0.9,
    "needs": ["du", "sort", "head"],
}


class FakeResult:
    def __init__(self, *, final_response=None, usage=None):
        self.final_response = final_response
        self.usage = usage


class FakeTurn:
    def __init__(self, result):
        self._result = result

    def run(self):
        return self._result


class FakeCodex:
    def __init__(self, capture, result):
        self.capture = capture
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def thread_start(self, **kwargs):
        self.capture["thread_start_kwargs"] = kwargs
        return self

    def turn(self, prompt, **kwargs):
        self.capture["prompt"] = prompt
        self.capture["turn_kwargs"] = kwargs
        return FakeTurn(self.result)

    def models(self, include_hidden=True):
        self.capture["include_hidden"] = include_hidden
        return self.capture.get("models_result", [])

    def login_api_key(self, key):
        self.capture["login_key"] = key


def factory(capture, result=None):
    return lambda: FakeCodex(capture, result)


def request(fmt=None, **kwargs):
    return CompletionRequest(
        messages=[
            Message(Role.SYSTEM, "you are tt"),
            Message(Role.USER, "disk usage"),
        ],
        **kwargs,
    )


def test_structured_output_completion():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4", codex_factory=factory(capture, FakeResult(final_response=json.dumps(PAYLOAD)))
    )
    completion = asyncio.run(provider.complete(request()))
    assert json.loads(completion.text) == PAYLOAD
    assert completion.model == "gpt-5.4"
    assert capture["prompt"] == "you are tt\n\ndisk usage"
    assert capture["turn_kwargs"]["output_schema"] == contract_json_schema()


def test_effort_mapping():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4", codex_factory=factory(capture, FakeResult(final_response="{}"))
    )
    asyncio.run(provider.complete(request(reasoning_effort="high")))
    assert capture["thread_start_kwargs"]["config"] == {"model_reasoning_effort": "high"}
    assert capture["turn_kwargs"]["effort"] == "high"


def test_unsupported_effort_omitted():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4", codex_factory=factory(capture, FakeResult(final_response="{}"))
    )
    asyncio.run(provider.complete(request(reasoning_effort="ludicrous")))
    assert capture["thread_start_kwargs"]["config"] == {}
    assert "effort" not in capture["turn_kwargs"]


def test_usage_mapping():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4",
        codex_factory=factory(
            capture, FakeResult(final_response="{}", usage={"input_tokens": 10, "output_tokens": 5})
        ),
    )
    completion = asyncio.run(provider.complete(request()))
    assert (completion.usage.prompt_tokens, completion.usage.completion_tokens) == (10, 5)
    assert completion.usage.total_tokens == 15


def test_missing_final_response_raises():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4", codex_factory=factory(capture, FakeResult(final_response=None))
    )
    with pytest.raises(CodexAgentError, match="without a final_response"):
        asyncio.run(provider.complete(request()))


def test_sdk_exception_is_wrapped():
    def boom():
        raise RuntimeError("codex binary not found")

    provider = CodexAgentProvider("gpt-5.4", codex_factory=boom)
    with pytest.raises(CodexAgentError, match="codex binary not found"):
        asyncio.run(provider.complete(request()))


def test_missing_sdk_raises_actionable_error(monkeypatch):
    # openai-codex is an optional extra — simulate it being absent (regardless of the
    # dev env) and prove the real (non-injected) path raises an install hint rather
    # than a bare ImportError.
    import builtins

    real_import = builtins.__import__

    def no_codex(name, *args, **kwargs):
        if name == "openai_codex":
            raise ImportError("No module named 'openai_codex'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_codex)
    provider = CodexAgentProvider("gpt-5.4")
    with pytest.raises(CodexAgentError, match="not installed"):
        asyncio.run(provider.complete(request()))


def test_list_models():
    capture = {"models_result": ["gpt-5.4", "gpt-5.4-codex"]}
    models = list_models(codex_factory=factory(capture))
    assert models == ["gpt-5.4", "gpt-5.4-codex"]
    assert capture["include_hidden"] is True


def test_login_api_key_persists_via_sdk():
    capture = {}
    login_api_key("sk-test-key", codex_factory=factory(capture))
    assert capture["login_key"] == "sk-test-key"


def test_usage_cached_tokens_parsed():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4",
        codex_factory=factory(
            capture,
            FakeResult(
                final_response="{}",
                usage={"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 60},
            ),
        ),
    )
    completion = asyncio.run(provider.complete(request()))
    assert completion.usage.prompt_tokens == 100  # already inclusive of cached
    assert completion.usage.cached_prompt_tokens == 60


def test_default_effort_applied_and_request_wins():
    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.4",
        codex_factory=factory(capture, FakeResult(final_response="{}")),
        default_effort="low",
    )
    asyncio.run(provider.complete(request()))
    assert capture["turn_kwargs"]["effort"] == "low"
    asyncio.run(provider.complete(request(reasoning_effort="high")))
    assert capture["turn_kwargs"]["effort"] == "high"


def test_usage_object_shape_mapped():
    """SDK 0.1.0b3: TurnResult.usage is an object with a .total TokenUsageBreakdown."""

    class Breakdown:
        input_tokens = 26687
        output_tokens = 5
        cached_input_tokens = 2432

    class TurnUsage:
        total = Breakdown()

    capture = {}
    provider = CodexAgentProvider(
        "gpt-5.5",
        codex_factory=factory(capture, FakeResult(final_response="{}", usage=TurnUsage())),
    )
    completion = asyncio.run(provider.complete(request()))
    assert completion.usage.prompt_tokens == 26687
    assert completion.usage.completion_tokens == 5
    assert completion.usage.cached_prompt_tokens == 2432
