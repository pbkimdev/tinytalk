"""Build a `Provider` from a validated `BackendConfig` (#30).

Adapters are imported lazily so the hot path never pays for SDKs it doesn't use
(PRD §15 cold-start budget).
"""

from __future__ import annotations

from clite.config import BackendConfig, ConfigError
from clite.provider.base import Capabilities, Provider


def _capabilities(cfg: BackendConfig) -> Capabilities:
    return Capabilities(
        supports_tool_calling="tool_calling" in cfg.capabilities,
        supports_native_json="native_json" in cfg.capabilities,
        supports_grammar="grammar" in cfg.capabilities,
    )


def make_provider(cfg: BackendConfig) -> Provider:
    if cfg.kind == "openai-compat":
        from clite.provider.openai_compat import OpenAICompatProvider

        assert cfg.base_url is not None  # guaranteed by config validation
        return OpenAICompatProvider(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            capabilities=_capabilities(cfg),
        )
    if cfg.kind == "claude-agent-sdk":
        raise ConfigError(
            f"backend {cfg.name!r}: the claude-agent-sdk adapter is not built yet (#27)"
        )
    raise ConfigError(f"backend {cfg.name!r}: unknown kind {cfg.kind!r}")
