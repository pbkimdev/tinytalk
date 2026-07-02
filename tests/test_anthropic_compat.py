"""Unit tests for the Anthropic Messages API adapter, over `httpx.MockTransport`."""

import asyncio
import json

import httpx
import pytest

from clite.contract import contract_json_schema
from clite.provider.anthropic_compat import (
    AnthropicCompatProvider,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTransportError,
    list_models,
)
from clite.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ResponseFormat,
    Role,
    Tool,
)

VALID = {
    "command": "ls -la",
    "explanation": "list files",
    "danger": "safe",
    "confidence": 0.95,
    "needs": [],
}
MSGS = [Message(role=Role.SYSTEM, content="you are clite"), Message(role=Role.USER, content="list files")]


def _run(coro):
    return asyncio.run(coro)


def _envelope(*, blocks, usage=None, model="claude-sonnet-5"):
    env: dict = {"type": "message", "model": model, "content": blocks}
    if usage is not None:
        env["usage"] = usage
    return env


def _provider(handler, *, capabilities=None, api_key=None, model="claude-sonnet-5"):
    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        idx = len(captured)
        captured.append(request)
        return handler(request, idx)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    prov = AnthropicCompatProvider(
        model, base_url="https://api.anthropic.com", api_key=api_key,
        capabilities=capabilities, client=client,
    )
    return prov, captured


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_seam_conformance():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    assert isinstance(prov, Provider)
    assert prov.name == "anthropic-compat:claude-sonnet-5"
    assert isinstance(prov.capabilities, Capabilities)
    assert prov.capabilities.supports_tool_calling  # native tool_use, on by default


def test_tool_use_response_parsed():
    usage = {"input_tokens": 3, "output_tokens": 4}
    prov, reqs = _provider(
        lambda req, i: httpx.Response(
            200,
            json=_envelope(
                blocks=[{"type": "tool_use", "id": "toolu_1", "name": "suggest_command", "input": VALID}],
                usage=usage,
            ),
        )
    )
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
    assert (call.id, call.name) == ("toolu_1", "suggest_command")
    assert json.loads(call.arguments) == VALID
    assert completion.usage.total_tokens == 7
    assert str(reqs[0].url) == "https://api.anthropic.com/v1/messages"


def test_system_message_split_from_messages():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    _run(prov.complete(CompletionRequest(MSGS)))
    body = _body(reqs[0])
    assert body["system"] == "you are clite"
    assert body["messages"] == [{"role": "user", "content": "list files"}]


def test_max_tokens_required_field_defaults():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    _run(prov.complete(CompletionRequest(MSGS)))
    assert _body(reqs[0])["max_tokens"] == 4096


def test_tool_call_wire_mapping():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    _run(
        prov.complete(
            CompletionRequest(
                MSGS,
                response_format=ResponseFormat.TOOL_CALL,
                tools=[Tool("suggest_command", "desc", contract_json_schema())],
            )
        )
    )
    body = _body(reqs[0])
    assert body["tools"][0]["input_schema"] == contract_json_schema()
    assert body["tool_choice"] == {"type": "tool", "name": "suggest_command"}


def test_effort_mapping():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="high")))
    assert _body(reqs[0])["output_config"] == {"effort": "high"}


def test_unknown_effort_omitted():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])))
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="ludicrous")))
    assert "output_config" not in _body(reqs[0])


def test_auth_header_uses_x_api_key():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(blocks=[])), api_key="sk-ant-1")
    _run(prov.complete(CompletionRequest(MSGS)))
    assert reqs[0].headers["x-api-key"] == "sk-ant-1"
    assert reqs[0].headers["anthropic-version"] == "2023-06-01"


def test_http_error_raises_typed_error():
    prov, _ = _provider(lambda req, i: httpx.Response(401, text="unauthorized"))
    with pytest.raises(ProviderHTTPError) as exc:
        _run(prov.complete(CompletionRequest(MSGS)))
    assert exc.value.status_code == 401


def test_non_json_body_raises_response_error():
    prov, _ = _provider(lambda req, i: httpx.Response(200, text="not json"))
    with pytest.raises(ProviderResponseError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_timeout_surfaces_as_transport_error():
    def _boom(req, i):
        raise httpx.ConnectTimeout("simulated timeout", request=req)

    prov, _ = _provider(_boom)
    with pytest.raises(ProviderTransportError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_list_models_returns_data():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-sonnet-5", "capabilities": {"effort": ["low", "high"]}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    models = _run(list_models("https://api.anthropic.com", "sk-ant-1", client=client))
    assert models == [{"id": "claude-sonnet-5", "capabilities": {"effort": ["low", "high"]}}]
    assert str(captured[0].url) == "https://api.anthropic.com/v1/models"
    assert captured[0].headers["x-api-key"] == "sk-ant-1"


def test_list_models_http_error():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(403, text="forbidden")))
    with pytest.raises(ProviderHTTPError):
        _run(list_models("https://api.anthropic.com", "bad-key", client=client))
