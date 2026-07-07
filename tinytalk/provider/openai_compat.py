"""OpenAI-compatible provider adapter (#29).

Implements the `Provider` seam (`tinytalk.provider.base`) over any OpenAI-compatible
`POST {base_url}/chat/completions` endpoint, so the degradation chain
(`tinytalk.engine.generate`) can drive a local backend (Ollama / llama.cpp) end to end
and the strict parser (`tinytalk.parsing`) can turn the reply into a `Suggestion`.

A pure leaf: it maps the engine's per-rung `CompletionRequest` onto the wire and the
reply back to a `Completion`. It never judges *content* — a clean HTTP 200 whose body
is prose returns a normal `Completion`, and the strict parser then rejects it so the
engine degrades to the next rung. The adapter raises only on transport/envelope faults.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from tinytalk.contract import contract_json_schema
from tinytalk.provider.base import (
    Capabilities,
    Completion,
    CompletionRequest,
    ProviderError,
    ResponseFormat,
    StreamChunk,
    ToolCall,
    Usage,
)

_CONTRACT_TOOL_NAME = "suggest_command"


class OpenAICompatError(ProviderError):
    """Base error for the OpenAI-compatible adapter."""


class ProviderHTTPError(OpenAICompatError):
    """A non-2xx HTTP response from the endpoint."""

    def __init__(self, status_code: int, body: str = ""):
        super().__init__(f"endpoint returned HTTP {status_code}")
        self.status_code = status_code
        self.body = body


class ProviderResponseError(OpenAICompatError):
    """The HTTP body was missing, not JSON, or not a valid chat-completion envelope."""


class ProviderTransportError(OpenAICompatError):
    """A transport fault (timeout, connection error)."""


class OpenAICompatProvider:
    """`Provider` over an OpenAI-compatible `/chat/completions` endpoint."""

    name: str
    capabilities: Capabilities

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
        default_effort: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"openai-compat:{model}"
        # Conservative default: no native capabilities → the engine uses the universal
        # TEXT fenced-extraction rung, which works on any server. Callers opt into
        # richer rungs per endpoint.
        self.capabilities = capabilities or Capabilities()
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._default_effort = default_effort

    async def complete(self, request: CompletionRequest) -> Completion:
        payload = self._build_payload(request)
        headers = self._headers()
        url = self._url()
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTransportError(f"request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"transport error: {exc}") from exc

        if resp.status_code // 100 != 2:
            raise ProviderHTTPError(resp.status_code, resp.text)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderResponseError(f"response body is not JSON: {exc}") from exc
        return self._parse_response(data)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(request)
        # include_usage makes the server emit a terminal usage-only chunk, so the final
        # StreamChunk keeps the same usage fidelity as the blocking complete() path.
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        headers = self._headers()
        url = self._url()

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code // 100 != 2:
                        body = await resp.aread()
                        raise ProviderHTTPError(resp.status_code, body.decode(errors="replace"))
                    async for chunk in self._iter_chunks(resp):
                        yield chunk
            except httpx.TimeoutException as exc:
                raise ProviderTransportError(f"request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise ProviderTransportError(f"transport error: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

    async def _iter_chunks(self, resp: httpx.Response) -> AsyncIterator[StreamChunk]:
        # Accumulate the payload alongside the deltas so the terminal chunk carries a fully
        # assembled Completion (concatenated text or reassembled tool-call arguments) plus usage.
        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        model = self.model
        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(chunk, dict):
                continue
            served = chunk.get("model")
            if isinstance(served, str) and served:
                model = served
            if isinstance(chunk.get("usage"), dict):
                usage = self._parse_usage(chunk["usage"])
            delta = self._delta_of(chunk)
            content = delta.get("content")
            if isinstance(content, str) and content:
                text_parts.append(content)
                yield StreamChunk(delta=content)
            for fragment in self._accumulate_tool_calls(delta.get("tool_calls"), tool_calls):
                yield StreamChunk(delta=fragment)
        yield StreamChunk(
            completion=Completion(
                text="".join(text_parts),
                tool_calls=[
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                    for _, tc in sorted(tool_calls.items())
                ],
                usage=usage,
                model=model,
            )
        )

    @staticmethod
    def _delta_of(chunk: dict) -> dict:
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return {}
        delta = choices[0].get("delta")
        return delta if isinstance(delta, dict) else {}

    @staticmethod
    def _accumulate_tool_calls(raw: object, acc: dict[int, dict[str, str]]) -> list[str]:
        """Merge streamed tool-call fragments into `acc` (keyed by choice index) and return
        the new `arguments` fragments in arrival order — the deltas to surface. A tool call's
        id/name arrive in its first fragment; `arguments` accrues across the rest."""
        if not isinstance(raw, list):
            return []
        fragments: list[str] = []
        for tc in raw:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index")
            idx = idx if isinstance(idx, int) else 0
            slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = str(tc["id"])
            fn = tc.get("function")
            if isinstance(fn, dict):
                if fn.get("name"):
                    slot["name"] = str(fn["name"])
                args = fn.get("arguments")
                if isinstance(args, str) and args:
                    slot["arguments"] += args
                    fragments.append(args)
        return fragments

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Emit auth only for a truthy, non-blank key — never a bare "Bearer ".
        if self._api_key and self._api_key.strip():
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_payload(self, request: CompletionRequest) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        }
        fmt = request.response_format
        if fmt is ResponseFormat.TOOL_CALL:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": (
                            t.parameters if t.parameters is not None else contract_json_schema()
                        ),
                    },
                }
                for t in request.tools
            ]
            # Force the single contract tool by name to maximize format compliance;
            # servers that ignore tool_choice yield empty tool_calls and the chain degrades.
            name = request.tools[0].name if request.tools else _CONTRACT_TOOL_NAME
            payload["tool_choice"] = {"type": "function", "function": {"name": name}}
        elif fmt is ResponseFormat.JSON_OBJECT:
            payload["response_format"] = {"type": "json_object"}
        elif fmt is ResponseFormat.GRAMMAR and request.grammar is not None:
            # llama.cpp top-level grammar field; best-effort pass-through.
            payload["grammar"] = request.grammar
        # TEXT: no special fields.

        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        effort = request.reasoning_effort or self._default_effort
        if effort is not None:
            payload["reasoning_effort"] = effort
        return payload

    def _parse_response(self, data: object) -> Completion:
        if not isinstance(data, dict):
            raise ProviderResponseError("envelope is not an object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderResponseError("envelope has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderResponseError("choice has no message")

        text = message.get("content")
        if not isinstance(text, str):
            text = ""
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        usage = self._parse_usage(data.get("usage"))
        model = data.get("model")
        return Completion(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            model=model if isinstance(model, str) and model else self.model,
            raw=data,
        )

    @staticmethod
    def _parse_tool_calls(raw: object) -> list[ToolCall]:
        if not isinstance(raw, list):
            return []
        calls: list[ToolCall] = []
        for tc in raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            args = fn.get("arguments", "")
            # The strict parser does json.loads(arguments), so arguments MUST be a JSON
            # string. OpenAI returns a string; some local servers return an object.
            if not isinstance(args, str):
                args = json.dumps(args)
            calls.append(
                ToolCall(id=str(tc.get("id", "")), name=str(fn.get("name", "")), arguments=args)
            )
        return calls

    @staticmethod
    def _parse_usage(raw: object) -> Usage:
        if not isinstance(raw, dict):
            return Usage()

        def _field(source: dict, key: str) -> int:
            try:
                return int(source.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        # prompt_tokens already includes cached tokens on this API; no normalization.
        details = raw.get("prompt_tokens_details")
        cached = _field(details, "cached_tokens") if isinstance(details, dict) else 0
        return Usage(
            prompt_tokens=_field(raw, "prompt_tokens"),
            completion_tokens=_field(raw, "completion_tokens"),
            total_tokens=_field(raw, "total_tokens"),
            cached_prompt_tokens=cached,
        )


async def list_models(
    base_url: str, *, api_key: str | None = None, client: httpx.AsyncClient | None = None
) -> list[str]:
    """`GET {base_url}/models` — used by `tt auth` for live model discovery."""
    headers = {}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/models"
    if client is not None:
        resp = await client.get(url, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.get(url, headers=headers)
    if resp.status_code // 100 != 2:
        raise ProviderHTTPError(resp.status_code, resp.text)
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderResponseError(f"models response body is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ProviderResponseError("models envelope has no data list")
    return [m["id"] for m in data["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
