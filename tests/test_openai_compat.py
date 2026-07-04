"""Unit tests for the OpenAI-compatible adapter (#29), over `httpx.MockTransport`.

Deterministic and async-native: the mock transport lets us assert the outgoing
request (URL / headers / JSON body) and return canned OpenAI-shaped envelopes with no
real sockets. Tests run with `asyncio.run`, matching `tests/test_engine.py` (the repo
has no `pytest-asyncio`).
"""

import asyncio
import json

import httpx
import pytest

from tinytalk.contract import contract_json_schema
from tinytalk.engine import generate
from tinytalk.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ResponseFormat,
    Role,
    Tool,
)
from tinytalk.provider.openai_compat import (
    OpenAICompatProvider,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTransportError,
)

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


def _envelope(*, content=None, tool_arguments=None, usage=None, model="local-model"):
    message: dict = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    if tool_arguments is not None:
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "suggest_command", "arguments": tool_arguments},
            }
        ]
    env: dict = {"model": model, "choices": [{"index": 0, "message": message}]}
    if usage is not None:
        env["usage"] = usage
    return env


def _provider(
    handler, *, capabilities=None, api_key=None, model="local-model", default_effort=None
):
    """Provider wired to a MockTransport. `handler(request, call_index) -> httpx.Response`.

    Returns `(provider, captured_requests)`.
    """
    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        idx = len(captured)
        captured.append(request)
        return handler(request, idx)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    prov = OpenAICompatProvider(
        "http://local/v1",
        model,
        api_key=api_key,
        capabilities=capabilities,
        client=client,
        default_effort=default_effort,
    )
    return prov, captured


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_seam_conformance():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope(content="{}")))
    assert isinstance(prov, Provider)
    assert prov.name == "openai-compat:local-model"
    assert isinstance(prov.capabilities, Capabilities)


def test_complete_happy_json_mode():
    usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    prov, reqs = _provider(
        lambda req, i: httpx.Response(
            200, json=_envelope(content=json.dumps(VALID), usage=usage, model="served-model")
        ),
        capabilities=Capabilities(supports_native_json=True),
    )
    completion = _run(
        prov.complete(CompletionRequest(MSGS, response_format=ResponseFormat.JSON_OBJECT))
    )
    assert completion.text == json.dumps(VALID)
    assert completion.usage.total_tokens == 7
    assert completion.model == "served-model"
    assert str(reqs[0].url) == "http://local/v1/chat/completions"


def test_auth_keyless_omits_header():
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(content="{}")))
    _run(prov.complete(CompletionRequest(MSGS)))
    assert "authorization" not in reqs[0].headers


def test_auth_keyed_sets_bearer():
    prov, reqs = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(content="{}")), api_key="test-key"
    )
    _run(prov.complete(CompletionRequest(MSGS)))
    assert reqs[0].headers["authorization"] == "Bearer test-key"


def test_auth_empty_string_omits_header():
    prov, reqs = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(content="{}")), api_key=""
    )
    _run(prov.complete(CompletionRequest(MSGS)))
    assert "authorization" not in reqs[0].headers


def test_tool_call_object_arguments_normalized_to_string():
    # A non-spec local server returns function.arguments as an object, not a string.
    prov, _ = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(tool_arguments=VALID)),
        capabilities=Capabilities(supports_tool_calling=True),
    )
    completion = _run(
        prov.complete(CompletionRequest(MSGS, response_format=ResponseFormat.TOOL_CALL))
    )
    assert isinstance(completion.tool_calls[0].arguments, str)
    assert json.loads(completion.tool_calls[0].arguments) == VALID


@pytest.mark.parametrize(
    "fmt,request_kwargs,present,absent",
    [
        (
            ResponseFormat.TOOL_CALL,
            {"tools": [Tool("suggest_command", "desc", contract_json_schema())]},
            ("tools", "tool_choice"),
            ("response_format", "grammar"),
        ),
        (ResponseFormat.JSON_OBJECT, {}, ("response_format",), ("tools", "tool_choice", "grammar")),
        (
            ResponseFormat.GRAMMAR,
            {"grammar": "root ::= object"},
            ("grammar",),
            ("tools", "tool_choice", "response_format"),
        ),
        (ResponseFormat.TEXT, {}, (), ("tools", "tool_choice", "response_format", "grammar")),
    ],
)
def test_per_rung_wire_mapping(fmt, request_kwargs, present, absent):
    prov, reqs = _provider(lambda req, i: httpx.Response(200, json=_envelope(content="{}")))
    _run(prov.complete(CompletionRequest(MSGS, response_format=fmt, **request_kwargs)))
    body = _body(reqs[0])
    assert body["model"] == "local-model"
    assert body["messages"] == [{"role": "user", "content": "list files"}]
    for key in present:
        assert key in body, f"{fmt}: expected {key}"
    for key in absent:
        assert key not in body, f"{fmt}: unexpected {key}"
    if fmt is ResponseFormat.TOOL_CALL:
        assert body["tools"][0]["function"]["parameters"] == contract_json_schema()
        assert body["tool_choice"]["function"]["name"] == "suggest_command"
    if fmt is ResponseFormat.JSON_OBJECT:
        assert body["response_format"] == {"type": "json_object"}
    if fmt is ResponseFormat.GRAMMAR:
        assert body["grammar"] == "root ::= object"


def test_response_tool_calls_mapping():
    prov, _ = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(tool_arguments=json.dumps(VALID))),
        capabilities=Capabilities(supports_tool_calling=True),
    )
    completion = _run(
        prov.complete(CompletionRequest(MSGS, response_format=ResponseFormat.TOOL_CALL))
    )
    call = completion.tool_calls[0]
    assert (call.id, call.name) == ("call_1", "suggest_command")
    assert json.loads(call.arguments) == VALID


def test_missing_usage_defaults():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope(content="{}")))
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    assert completion.usage.total_tokens == 0


def test_non_numeric_usage_field_defaults_instead_of_raising():
    # A non-spec server returns a malformed usage block; it must not crash the completion.
    usage = {"prompt_tokens": "unknown", "completion_tokens": [1, 2, 3], "total_tokens": 7}
    prov, _ = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(content="{}", usage=usage))
    )
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    assert completion.usage.prompt_tokens == 0
    assert completion.usage.completion_tokens == 0
    assert completion.usage.total_tokens == 7


def test_http_500_raises_typed_error():
    prov, _ = _provider(lambda req, i: httpx.Response(500, text="upstream is down"))
    with pytest.raises(ProviderHTTPError) as exc:
        _run(prov.complete(CompletionRequest(MSGS)))
    assert exc.value.status_code == 500


def test_empty_envelope_raises_response_error():
    prov, _ = _provider(lambda req, i: httpx.Response(200, json={}))
    with pytest.raises(ProviderResponseError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_non_json_body_raises_response_error():
    prov, _ = _provider(
        lambda req, i: httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
    )
    with pytest.raises(ProviderResponseError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_timeout_surfaces_as_transport_error():
    def _boom(req, i):
        raise httpx.ConnectTimeout("simulated timeout", request=req)

    prov, _ = _provider(_boom)
    with pytest.raises(ProviderTransportError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_cancellation_propagates_untouched():
    def _cancel(req, i):
        raise asyncio.CancelledError()

    prov, _ = _provider(_cancel)
    with pytest.raises(asyncio.CancelledError):
        _run(prov.complete(CompletionRequest(MSGS)))


def test_e2e_generate_parses_contract():
    # Default capabilities → ladder is [TEXT]; the model fences the JSON in prose.
    reply = f"here you go:\n```json\n{json.dumps(VALID)}\n```"
    prov, _ = _provider(lambda req, i: httpx.Response(200, json=_envelope(content=reply)))
    gen = _run(generate(prov, MSGS))
    s = gen.suggestion
    assert (s.command, s.explanation, s.danger.value) == ("ls -la", "list files", "safe")
    assert (s.confidence, s.needs) == (0.95, ())


def test_e2e_degradation_json_rung_fails_then_text_rung():
    # [JSON_OBJECT, TEXT] ladder: both JSON-rung tries fail to parse, TEXT rung serves a
    # fenced block. Proves the adapter is correctly driven across rungs (DoD #7).
    fenced = f"```json\n{json.dumps(VALID)}\n```"

    def handler(req, i):
        content = "not json at all" if i < 2 else fenced
        return httpx.Response(200, json=_envelope(content=content))

    prov, reqs = _provider(handler, capabilities=Capabilities(supports_native_json=True))
    gen = _run(generate(prov, MSGS))
    assert gen.response_format is ResponseFormat.TEXT
    assert gen.attempts == 3
    assert gen.suggestion.command == "ls -la"
    # The first two requests took the JSON rung, the third dropped to TEXT.
    assert "response_format" in _body(reqs[0]) and "response_format" in _body(reqs[1])
    assert "response_format" not in _body(reqs[2])


def test_usage_cached_tokens_parsed():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 4,
        "total_tokens": 104,
        "prompt_tokens_details": {"cached_tokens": 60},
    }
    prov, _ = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(content="{}", usage=usage))
    )
    completion = _run(prov.complete(CompletionRequest(MSGS)))
    # prompt_tokens already includes cached on this API — no normalization
    assert completion.usage.prompt_tokens == 100
    assert completion.usage.cached_prompt_tokens == 60
    assert completion.usage.cache_write_tokens == 0


def test_default_effort_applied_and_request_wins():
    prov, reqs = _provider(
        lambda req, i: httpx.Response(200, json=_envelope(content="{}")), default_effort="low"
    )
    _run(prov.complete(CompletionRequest(MSGS)))
    assert _body(reqs[0])["reasoning_effort"] == "low"
    _run(prov.complete(CompletionRequest(MSGS, reasoning_effort="high")))
    assert _body(reqs[1])["reasoning_effort"] == "high"
