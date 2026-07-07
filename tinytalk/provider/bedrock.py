"""AWS Bedrock provider adapter (PRD-provider-setup.md §4).

Implements the `Provider` seam over `bedrock-runtime`'s `converse()` API, which unifies
tool-calling across model vendors closely enough to mirror the Anthropic Messages API
shape (`toolConfig`/`toolUse` vs. `tools`/`tool_use`). boto3 is synchronous; calls run in
a thread via `asyncio.to_thread` so they don't block the event loop. `boto3` is an
optional extra (`tinytalk[bedrock]`) and is imported lazily so unselected backends never
pay for it.

Credentials resolve through boto3's own chain (env vars, `~/.aws/credentials`, SSO
cache, IAM role) via `region`/`profile` — no secret for tt to manage.
"""

from __future__ import annotations

import asyncio
import json

from tinytalk.contract import contract_json_schema
from tinytalk.provider.base import (
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

_CONTRACT_TOOL_NAME = "suggest_command"
# Claude-on-Bedrock only, via additionalModelRequestFields — no universal Bedrock effort concept.
EFFORT_BUDGET_TOKENS = {"low": 2048, "medium": 8192, "high": 24576}


def is_claude_model(model: str) -> bool:
    """Claude on Bedrock: 'anthropic.claude-*' or a cross-region profile 'us.anthropic.claude-*'."""
    return "anthropic." in model


_INSTALL_HINT = (
    "boto3 is not installed; `uv sync --extra bedrock` (or pip install 'tinytalk[bedrock]')"
)


class BedrockError(ProviderError):
    """A boto3/Bedrock call failed (auth, throttling, validation, transport)."""


class BedrockProvider:
    """`Provider` over `bedrock-runtime.converse()`."""

    name: str
    capabilities: Capabilities

    def __init__(
        self,
        model: str,
        *,
        region: str,
        profile: str | None = None,
        endpoint_url: str | None = None,
        capabilities: Capabilities | None = None,
        client: object | None = None,
        default_effort: str | None = None,
    ):
        self.model = model
        self.name = f"bedrock:{model}"
        # Conservative default, matching openai-compat: model support for tool-calling
        # varies by vendor even within one Bedrock region. Config opts into richer rungs.
        self.capabilities = capabilities or Capabilities()
        self._region = region
        self._profile = profile
        self._endpoint_url = endpoint_url
        self._client = client
        self._default_effort = default_effort

    async def complete(self, request: CompletionRequest) -> Completion:
        payload = self._build_payload(request)
        try:
            client = self._client or _build_client(
                "bedrock-runtime", self._region, self._profile, self._endpoint_url
            )
            response = await asyncio.to_thread(client.converse, **payload)
        except BedrockError:
            raise
        except Exception as exc:  # botocore errors (auth, throttling, validation, transport)
            if message := _credential_error_message(exc, self._profile):
                raise BedrockError(message) from exc
            raise BedrockError(f"bedrock converse failed: {exc}") from exc
        return self._parse_response(response)

    def _build_payload(self, request: CompletionRequest) -> dict:
        system, messages = _split_messages(request.messages)
        payload: dict = {"modelId": self.model, "messages": messages}
        if system:
            payload["system"] = [{"text": system}]

        if request.response_format is ResponseFormat.TOOL_CALL:
            tools = request.tools
            payload["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": {
                                "json": (
                                    t.parameters
                                    if t.parameters is not None
                                    else contract_json_schema()
                                )
                            },
                        }
                    }
                    for t in tools
                ],
                "toolChoice": {"tool": {"name": tools[0].name if tools else _CONTRACT_TOOL_NAME}},
            }

        inference_config = {}
        if request.temperature is not None:
            inference_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            inference_config["maxTokens"] = request.max_tokens
        if inference_config:
            payload["inferenceConfig"] = inference_config

        budget = EFFORT_BUDGET_TOKENS.get(request.reasoning_effort or self._default_effort or "")
        if budget is not None and is_claude_model(self.model):
            payload["additionalModelRequestFields"] = {
                "thinking": {"type": "enabled", "budget_tokens": budget}
            }
        return payload

    def _parse_response(self, response: object) -> Completion:
        output = response.get("output") if isinstance(response, dict) else None
        message = output.get("message") if isinstance(output, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            raise BedrockError("converse response has no message content")

        text = ""
        tool_calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        id=str(tu.get("toolUseId", "")),
                        name=str(tu.get("name", "")),
                        arguments=json.dumps(tu.get("input", {})),
                    )
                )
            elif "text" in block and not text:
                text = block["text"]

        usage = self._parse_usage(response.get("usage") if isinstance(response, dict) else None)
        return Completion(
            text=text, tool_calls=tool_calls, usage=usage, model=self.model, raw=response
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

        # Converse reports inputTokens EXCLUSIVE of cache reads/writes — normalize to
        # the seam's inclusive prompt_tokens convention (see `Usage`).
        cached = _field("cacheReadInputTokens")
        cache_write = _field("cacheWriteInputTokens")
        prompt = _field("inputTokens") + cached + cache_write
        completion = _field("outputTokens")
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            cached_prompt_tokens=cached,
            cache_write_tokens=cache_write,
        )


def _split_messages(messages: list[Message]) -> tuple[str, list[dict]]:
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    rest = [
        {"role": m.role.value, "content": [{"text": m.content}]}
        for m in messages
        if m.role is not Role.SYSTEM
    ]
    return "\n\n".join(system_parts), rest


def _build_client(
    service: str,
    region: str,
    profile: str | None,
    endpoint_url: str | None,
):
    from tinytalk.addons import AddonMissing, ensure_bedrock_importable

    try:
        ensure_bedrock_importable()  # frozen binary: pull boto3 in from the downloaded add-on
        import boto3
    except AddonMissing as exc:
        raise BedrockError(str(exc)) from exc
    except ImportError as exc:
        raise BedrockError(_INSTALL_HINT) from exc
    session_kwargs: dict = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile
    session = boto3.Session(**session_kwargs)
    client_kwargs: dict = {}
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    return session.client(service, **client_kwargs)


def list_foundation_models(
    *,
    region: str,
    profile: str | None = None,
    endpoint_url: str | None = None,
    client: object | None = None,
) -> list[dict]:
    """`bedrock.list_foundation_models()` — used by `tt auth` for live model discovery.

    Lists the region's model *catalog*, not confirmed per-account entitlement (PRD §3) —
    an unauthorized model choice surfaces as a runtime error on first real use, not here.
    """
    try:
        client = client or _build_client("bedrock", region, profile, endpoint_url)
        response = client.list_foundation_models()
    except Exception as exc:  # botocore errors (auth, throttling, transport)
        if message := _credential_error_message(exc, profile):
            raise BedrockError(message) from exc
        raise BedrockError(f"bedrock list_foundation_models failed: {exc}") from exc
    return response.get("modelSummaries", [])


def _credential_error_message(exc: Exception, profile: str | None) -> str | None:
    try:
        from botocore.exceptions import (
            NoCredentialsError,
            SSOTokenLoadError,
            TokenRetrievalError,
            UnauthorizedSSOTokenError,
        )
    except ImportError:
        return None

    if not isinstance(
        exc,
        (
            UnauthorizedSSOTokenError,
            SSOTokenLoadError,
            TokenRetrievalError,
            NoCredentialsError,
        ),
    ):
        return None
    if profile:
        return (
            f"bedrock credentials failed for AWS profile {profile!r}; "
            f"run `aws sso login --profile {profile}` and retry."
        )
    return (
        "bedrock credentials failed; configure the standard AWS credential chain "
        "(environment, ~/.aws/credentials, SSO, or IAM role) and retry."
    )
