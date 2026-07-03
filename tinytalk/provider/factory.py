"""Build a `Provider` from a validated `BackendConfig` (#30).

Adapters are imported lazily so the hot path never pays for SDKs it doesn't use
(PRD §15 cold-start budget).
"""

from __future__ import annotations

from tinytalk.config import BackendConfig, ConfigError
from tinytalk.provider.base import Capabilities, Provider


def _capabilities(cfg: BackendConfig) -> Capabilities:
    return Capabilities(
        supports_tool_calling="tool_calling" in cfg.capabilities,
        supports_native_json="native_json" in cfg.capabilities,
        supports_grammar="grammar" in cfg.capabilities,
    )


def make_provider(cfg: BackendConfig) -> Provider:
    if cfg.kind == "openai-compat":
        from tinytalk.provider.openai_compat import OpenAICompatProvider

        assert cfg.base_url is not None  # guaranteed by config validation
        return OpenAICompatProvider(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            capabilities=_capabilities(cfg),
            default_effort=cfg.effort,
        )
    if cfg.kind == "anthropic-compat":
        from tinytalk.provider.anthropic_compat import DEFAULT_BASE_URL, AnthropicCompatProvider

        return AnthropicCompatProvider(
            model=cfg.model,
            base_url=cfg.base_url or DEFAULT_BASE_URL,
            api_key=cfg.api_key,
            default_effort=cfg.effort,
        )
    if cfg.kind == "azure-openai":
        from tinytalk.provider.azure_openai import AzureOpenAIProvider

        assert cfg.base_url is not None  # guaranteed by config validation
        assert cfg.azure_api_version is not None  # guaranteed by config validation
        return AzureOpenAIProvider(
            cfg.base_url,
            cfg.model,
            cfg.azure_api_version,
            api_key=cfg.api_key,
            capabilities=_capabilities(cfg),
            default_effort=cfg.effort,
        )
    if cfg.kind == "claude-agent-sdk":
        from tinytalk.provider.claude_agent import ClaudeAgentProvider

        return ClaudeAgentProvider(model=cfg.model, default_effort=cfg.effort)
    if cfg.kind == "codex-agent-sdk":
        from tinytalk.provider.codex_agent import CodexAgentProvider

        return CodexAgentProvider(model=cfg.model, default_effort=cfg.effort)
    if cfg.kind == "bedrock":
        from tinytalk.provider.bedrock import BedrockProvider

        assert cfg.aws_region is not None  # guaranteed by config validation
        access_key_id, secret_access_key = _bedrock_credential_pair(cfg)
        return BedrockProvider(
            model=cfg.model,
            region=cfg.aws_region,
            profile=cfg.aws_profile,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            capabilities=_capabilities(cfg),
            default_effort=cfg.effort,
        )
    raise ConfigError(f"backend {cfg.name!r}: unknown kind {cfg.kind!r}")


def _bedrock_credential_pair(cfg: BackendConfig) -> tuple[str | None, str | None]:
    """The keyring/env-resolved secret holds a `{"aws_access_key_id", "aws_secret_access_key"}`
    JSON blob for bedrock — the explicit-credential fallback when boto3's own default
    credential chain doesn't apply. A missing/malformed blob just means "use the default
    chain", not an error."""
    import json

    raw = cfg.api_key
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    return parsed.get("aws_access_key_id"), parsed.get("aws_secret_access_key")
