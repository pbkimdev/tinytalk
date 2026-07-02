"""Unit tests for the Azure OpenAI adapter — proves it overrides just URL/auth,
reusing OpenAICompatProvider's payload/response mapping unchanged."""

import asyncio
import json

import httpx

from tinytalk.provider.azure_openai import AzureOpenAIProvider
from tinytalk.provider.base import CompletionRequest, Message, Provider, Role

MSGS = [Message(role=Role.USER, content="list files")]


def _run(coro):
    return asyncio.run(coro)


def _envelope(*, content="{}", model="gpt-5-4"):
    return {
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _provider(handler, *, api_key=None):
    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        idx = len(captured)
        captured.append(request)
        return handler(request, idx)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    prov = AzureOpenAIProvider(
        "https://my-resource.openai.azure.com",
        "gpt-5-4",
        "2026-01-01-preview",
        api_key=api_key,
        client=client,
    )
    return prov, captured


def test_seam_conformance():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope()))
    assert isinstance(prov, Provider)
    assert prov.name == "azure-openai:gpt-5-4"


def test_url_shape():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope()))
    _run(prov.complete(CompletionRequest(MSGS)))
    assert str(reqs[0].url) == (
        "https://my-resource.openai.azure.com/openai/deployments/gpt-5-4/chat/completions"
        "?api-version=2026-01-01-preview"
    )


def test_auth_header_uses_api_key_not_bearer():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope()), api_key="azkey")
    _run(prov.complete(CompletionRequest(MSGS)))
    assert reqs[0].headers["api-key"] == "azkey"
    assert "authorization" not in reqs[0].headers


def test_keyless_omits_header():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope()))
    _run(prov.complete(CompletionRequest(MSGS)))
    assert "api-key" not in reqs[0].headers


def test_payload_shape_matches_openai_wire_format():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope()))
    _run(prov.complete(CompletionRequest(MSGS)))
    body = json.loads(reqs[0].content)
    assert body == {"model": "gpt-5-4", "messages": [{"role": "user", "content": "list files"}]}


def test_response_parsed_via_shared_logic():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope(content="hello", model="gpt-5-4")))
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    assert completion.text == "hello"
    assert completion.model == "gpt-5-4"
