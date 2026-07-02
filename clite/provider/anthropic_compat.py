"""Raw Anthropic Messages API adapter (PRD-provider-setup.md §4).

Distinct from `claude_agent.py` (which drives the Claude Agent SDK): this adapter talks
directly to `POST {base_url}/v1/messages` over HTTP, for deployments/proxies that only
speak the plain Messages API. `tool_use` is native and reliable on this API, so the
engine's degradation ladder gets the TOOL_CALL rung by default; JSON_OBJECT/GRAMMAR have
no native equivalent here and fall back to the universal TEXT fenced-extraction rung.
"""

from __future__ import annotations

import json

import httpx

from clite.contract import contract_json_schema
from clite.provider.base import (
    Capabilities,
    Completion,
    CompletionRequest,
    Message,
    ProviderError,
    ResponseFormat,
    Role,
    ToolCall,
    Usage,
)

_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MAX_TOKENS = 4096
_CONTRACT_TOOL_NAME = "suggest_command"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class AnthropicCompatError(ProviderError):
    """Base error for the Anthropic Messages API adapter."""


class ProviderHTTPError(AnthropicCompatError):
    """A non-2xx HTTP response from the endpoint."""

    def __init__(self, status_code: int, body: str = ""):
        super().__init__(f"endpoint returned HTTP {status_code}")
        self.status_code = status_code
        self.body = body


class ProviderResponseError(AnthropicCompatError):
    """The HTTP body was missing, not JSON, or not a valid Messages API envelope."""


class ProviderTransportError(AnthropicCompatError):
    """A transport fault (timeout, connection error)."""


class AnthropicCompatProvider:
    """`Provider` over the raw Anthropic Messages API (`POST /v1/messages`)."""

    name: str
    capabilities: Capabilities

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"anthropic-compat:{model}"
        # tool_use is native and reliable on this API — the ladder gets TOOL_CALL by
        # default, unlike openai-compat's conservative empty default.
        self.capabilities = capabilities or Capabilities(supports_tool_calling=True)
        self._api_key = api_key
        self._timeout = timeout
        self._client = client

    async def complete(self, request: CompletionRequest) -> Completion:
        payload = self._build_payload(request)
        headers = self._headers()
        url = f"{self.base_url}/v1/messages"
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

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", "anthropic-version": _ANTHROPIC_VERSION}
        if self._api_key and self._api_key.strip():
            headers["x-api-key"] = self._api_key
        return headers

    def _build_payload(self, request: CompletionRequest) -> dict:
        system, messages = _split_messages(request.messages)
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens or _DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system

        if request.response_format is ResponseFormat.TOOL_CALL:
            tools = request.tools
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": (
                        t.parameters if t.parameters is not None else contract_json_schema()
                    ),
                }
                for t in tools
            ]
            name = tools[0].name if tools else _CONTRACT_TOOL_NAME
            payload["tool_choice"] = {"type": "tool", "name": name}
        # JSON_OBJECT/GRAMMAR/TEXT: no native equivalent — plain text, same as TEXT.

        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.reasoning_effort in EFFORT_LEVELS:
            payload["output_config"] = {"effort": request.reasoning_effort}
        return payload

    def _parse_response(self, data: object) -> Completion:
        if not isinstance(data, dict):
            raise ProviderResponseError("envelope is not an object")
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderResponseError("envelope has no content")

        text = ""
        tool_calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=json.dumps(block.get("input", {})),
                    )
                )
            elif block.get("type") == "text" and not text:
                text = block.get("text", "")

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
    def _parse_usage(raw: object) -> Usage:
        if not isinstance(raw, dict):
            return Usage()

        def _field(key: str) -> int:
            try:
                return int(raw.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        prompt = _field("input_tokens")
        completion = _field("output_tokens")
        return Usage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        )


def _split_messages(messages: list[Message]) -> tuple[str, list[dict]]:
    """Flatten seam messages into (system, [{role, content}]) for the Messages API."""
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    rest = [
        {"role": m.role.value, "content": m.content} for m in messages if m.role is not Role.SYSTEM
    ]
    return "\n\n".join(system_parts), rest


async def list_models(
    base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """`GET {base_url}/v1/models` — used by `clite auth` for live model discovery.

    Each entry's `capabilities.effort` (when present) lists the reasoning-effort levels
    that specific model supports — the wizard reads this directly rather than guessing.
    """
    headers = {"anthropic-version": _ANTHROPIC_VERSION, "x-api-key": api_key}
    url = f"{base_url.rstrip('/')}/v1/models"
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
    return data["data"]
