"""AWS Bedrock provider adapter (PRD-provider-setup.md §4).

Implements the `Provider` seam over `bedrock-runtime`'s `converse()` API, which unifies
tool-calling across model vendors closely enough to mirror the Anthropic Messages API
shape (`toolConfig`/`toolUse` vs. `tools`/`tool_use`). boto3 is synchronous; calls run in
a thread via `asyncio.to_thread` so they don't block the event loop. boto3 is a normal
Python-package dependency but remains lazily imported; frozen releases load it from the
version-matched Bedrock add-on installed by `tt auth`.

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
EFFORT_BUDGET_TOKENS = {"low": 2048, "medium": 8192, "high": 20000}
_LEGACY_EFFORT_MAX_TOKENS = {"low": 4096, "medium": 12288, "high": 21333}
_NON_STREAMING_MAX_TOKENS = 21333
_ADAPTIVE_THINKING_MODELS = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-mythos-5",
    "claude-fable-5",
    "claude-mythos-preview",
)


def is_claude_model(model: str) -> bool:
    """Claude on Bedrock: 'anthropic.claude-*' or a cross-region profile 'us.anthropic.claude-*'."""
    return "anthropic.claude" in model


_INSTALL_HINT = "boto3 is not installed; reinstall TinyTalk (it is included in normal installs)"


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
        effort = request.reasoning_effort or self._default_effort or ""
        claude_model = is_claude_model(self.model)
        adaptive_model = (
            claude_model
            and effort in EFFORT_BUDGET_TOKENS
            and any(marker in self.model for marker in _ADAPTIVE_THINKING_MODELS)
        )
        adaptive_thinking = adaptive_model and (
            request.max_tokens is None or request.max_tokens <= _NON_STREAMING_MAX_TOKENS
        )
        budget = None if adaptive_model else EFFORT_BUDGET_TOKENS.get(effort)
        legacy_thinking = budget is not None and claude_model
        if legacy_thinking and request.max_tokens is not None:
            # Extended thinking requires budget_tokens < maxTokens and non-streaming
            # Converse caps maxTokens at 21,333. Preserve an explicit caller cap by
            # falling back to the normal request instead of silently changing it.
            legacy_thinking = budget < request.max_tokens <= _NON_STREAMING_MAX_TOKENS
        thinking_enabled = adaptive_thinking or legacy_thinking
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
                "toolChoice": (
                    {"auto": {}}
                    if thinking_enabled
                    else {"tool": {"name": tools[0].name if tools else _CONTRACT_TOOL_NAME}}
                ),
            }

        inference_config = {}
        if request.temperature is not None and not thinking_enabled:
            inference_config["temperature"] = request.temperature
        if legacy_thinking and request.max_tokens is None:
            inference_config["maxTokens"] = _LEGACY_EFFORT_MAX_TOKENS[effort]
        elif request.max_tokens is not None:
            inference_config["maxTokens"] = request.max_tokens
        if inference_config:
            payload["inferenceConfig"] = inference_config

        if adaptive_thinking:
            payload["additionalModelRequestFields"] = {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": effort},
            }
        elif legacy_thinking:
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
    client: object | None = None,
) -> list[dict]:
    """`bedrock.list_foundation_models()` — used by `tt auth` for live model discovery.

    Lists the region's model *catalog*, not confirmed per-account entitlement (PRD §3) —
    an unauthorized model choice surfaces as a runtime error on first real use, not here.
    """
    try:
        client = client or _build_client("bedrock", region, profile, None)
        response = client.list_foundation_models()
    except Exception as exc:  # botocore errors (auth, throttling, transport)
        if message := _credential_error_message(exc, profile):
            raise BedrockError(message) from exc
        raise BedrockError(f"bedrock list_foundation_models failed: {exc}") from exc
    return response.get("modelSummaries", [])


def list_available_models(
    *,
    region: str,
    profile: str | None = None,
    client: object | None = None,
) -> list[dict]:
    """List selectable active inference profiles and text foundation models.

    System-defined inference profiles come first because many current Bedrock models
    require their cross-region profile ID for on-demand Converse requests. Missing
    profile-list permission degrades to the foundation-model catalog, while credential
    failures still surface so `tt auth` can start AWS SSO recovery.
    """
    try:
        client = client or _build_client("bedrock", region, profile, None)
    except Exception as exc:
        if message := _credential_error_message(exc, profile):
            raise BedrockError(message) from exc
        raise BedrockError(f"bedrock model discovery failed: {exc}") from exc

    discovered: list[dict] = []
    seen: set[str] = set()

    try:
        request: dict = {"maxResults": 1000, "typeEquals": "SYSTEM_DEFINED"}
        while True:
            response = client.list_inference_profiles(**request)
            for item in response.get("inferenceProfileSummaries", []):
                model_id = item.get("inferenceProfileId")
                if (
                    item.get("status") == "ACTIVE"
                    and isinstance(model_id, str)
                    and model_id
                    and is_claude_model(model_id)
                    and model_id not in seen
                ):
                    discovered.append(
                        {
                            "modelId": model_id,
                            "modelName": item.get("inferenceProfileName") or model_id,
                            "source": "inference-profile",
                        }
                    )
                    seen.add(model_id)
            token = response.get("nextToken")
            if not token:
                break
            request["nextToken"] = token
    except Exception as exc:
        if message := _credential_error_message(exc, profile):
            raise BedrockError(message) from exc
        # Some otherwise usable roles cannot list inference profiles. Foundation
        # discovery below remains useful and preserves the existing setup path.
        pass

    try:
        response = client.list_foundation_models(byOutputModality="TEXT")
    except Exception as exc:
        if message := _credential_error_message(exc, profile):
            raise BedrockError(message) from exc
        if discovered:
            return discovered
        raise BedrockError(f"bedrock model discovery failed: {exc}") from exc

    for item in response.get("modelSummaries", []):
        model_id = item.get("modelId")
        lifecycle = item.get("modelLifecycle") or {}
        modalities = item.get("outputModalities") or ["TEXT"]
        if (
            isinstance(model_id, str)
            and model_id
            and is_claude_model(model_id)
            and model_id not in seen
            and lifecycle.get("status", "ACTIVE") == "ACTIVE"
            and "TEXT" in modalities
        ):
            discovered.append(
                {
                    "modelId": model_id,
                    "modelName": item.get("modelName") or model_id,
                    "source": "foundation-model",
                }
            )
            seen.add(model_id)
    return discovered


def _credential_error_message(exc: Exception, profile: str | None) -> str | None:
    try:
        from botocore.exceptions import (
            CredentialRetrievalError,
            NoCredentialsError,
            PartialCredentialsError,
            ProfileNotFound,
            SSOTokenLoadError,
            TokenRetrievalError,
            UnauthorizedSSOTokenError,
        )
    except ImportError:
        return None

    if not isinstance(
        exc,
        (
            CredentialRetrievalError,
            UnauthorizedSSOTokenError,
            SSOTokenLoadError,
            TokenRetrievalError,
            NoCredentialsError,
            PartialCredentialsError,
            ProfileNotFound,
        ),
    ):
        return None

    if isinstance(exc, (UnauthorizedSSOTokenError, SSOTokenLoadError, TokenRetrievalError)):
        if profile:
            return (
                f"bedrock SSO credentials failed for AWS profile {profile!r}; "
                f"run `aws sso login --profile {profile}` and retry."
            )
        return "bedrock SSO credentials failed; run `aws sso login` and retry."

    if isinstance(exc, ProfileNotFound):
        if profile:
            return (
                f"bedrock credentials failed: AWS profile {profile!r} was not found; "
                "fix ~/.aws/config, choose another profile, or re-run `tt auth`."
            )
        return (
            "bedrock credentials failed: the configured AWS profile was not found; "
            "fix ~/.aws/config or re-run `tt auth`."
        )

    if isinstance(exc, NoCredentialsError):
        if profile:
            return (
                f"bedrock credentials failed for AWS profile {profile!r}: no credentials "
                "were found; fix that profile's credential source or re-run `tt auth`."
            )
        return (
            "bedrock credentials failed: no credentials were found; configure the standard "
            "AWS credential chain (environment, ~/.aws/credentials, SSO, or IAM role) and retry."
        )

    if isinstance(exc, PartialCredentialsError):
        if profile:
            return (
                f"bedrock credentials failed for AWS profile {profile!r}: partial credentials "
                "were found; fix the incomplete credential source or re-run `tt auth`."
            )
        return (
            "bedrock credentials failed: partial credentials were found; fix the standard "
            "AWS credential chain and retry."
        )

    if isinstance(exc, CredentialRetrievalError):
        if profile:
            return (
                f"bedrock credentials failed for AWS profile {profile!r}: credential retrieval "
                "failed; fix that profile's credential_process/source_profile or re-run `tt auth`."
            )
        return (
            "bedrock credentials failed: credential retrieval failed; fix the standard AWS "
            "credential chain and retry."
        )

    if profile:
        return (
            f"bedrock credentials failed for AWS profile {profile!r}; "
            "fix that profile's AWS credential source or re-run `tt auth`."
        )
    return (
        "bedrock credentials failed; configure the standard AWS credential chain "
        "(environment, ~/.aws/credentials, SSO, or IAM role) and retry."
    )
