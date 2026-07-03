"""Codex Agent SDK provider adapter (PRD-provider-setup.md §4).

Implements the `Provider` seam over `openai_codex.Codex` — a thin wrapper that drives a
local `codex` CLI binary via JSON-RPC, not an HTTP client to a "Codex API". Auth is
stateful, not per-call: `login_api_key()` persists a key into the SDK's own local
storage, or an existing local Codex CLI session is reused — tt manages no secret
here. The SDK (and its bundled CLI binary) is an optional extra (`tinytalk[codex]`) and is
imported lazily so unselected backends never pay for it.

`openai-codex` is pre-1.0 (0.1.0b3 at time of writing) — its API surface may shift; this
adapter only depends on `Codex()`, `.thread_start()`, `.turn()`, and `.models()`,
matching the package's own `13_model_select_and_turn_params` example. Re-verify against
the installed version if these calls start failing.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from tinytalk.provider.base import (
    Capabilities,
    Completion,
    CompletionRequest,
    Message,
    ProviderError,
    Role,
    Usage,
)

EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")

# codex_factory() -> a context-manager yielding a Codex-like object; injectable for tests.
CodexFactory = Callable[[], object]

_INSTALL_HINT = "openai-codex is not installed; `uv sync --extra codex` (or pip install 'tinytalk[codex]')"


class CodexAgentError(ProviderError):
    """The SDK failed or returned an error result."""


class CodexAgentProvider:
    """`Provider` over the Codex Agent SDK (single-shot, one turn per request)."""

    name: str
    capabilities: Capabilities

    def __init__(
        self,
        model: str,
        *,
        codex_factory: CodexFactory | None = None,
        default_effort: str | None = None,
    ):
        self.model = model
        self.name = f"codex-agent:{model}"
        self.capabilities = Capabilities(supports_native_json=True)
        self._codex_factory = codex_factory
        self._default_effort = default_effort if default_effort in EFFORT_LEVELS else None

    async def complete(self, request: CompletionRequest) -> Completion:
        prompt, system_prompt = _split_messages(request.messages)
        if system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"
        effort = (
            request.reasoning_effort
            if request.reasoning_effort in EFFORT_LEVELS
            else self._default_effort
        )

        try:
            with _open_codex(self._codex_factory) as codex:
                thread = codex.thread_start(
                    model=self.model,
                    config={"model_reasoning_effort": effort} if effort else {},
                )
                turn_kwargs: dict = {"model": self.model, "output_schema": _contract_schema()}
                if effort:
                    turn_kwargs["effort"] = effort
                result = thread.turn(prompt, **turn_kwargs).run()
        except CodexAgentError:
            raise
        except Exception as exc:  # SDK/CLI faults (binary missing, JSON-RPC error, process)
            raise CodexAgentError(f"openai-codex turn failed: {exc}") from exc

        final = getattr(result, "final_response", None)
        if final is None:
            raise CodexAgentError("openai-codex turn ended without a final_response")
        text = final if isinstance(final, str) else json.dumps(final)
        return Completion(
            text=text,
            usage=_map_usage(getattr(result, "usage", None)),
            model=self.model,
            raw=result,
        )


def _open_codex(codex_factory: CodexFactory | None):
    if codex_factory is not None:
        return codex_factory()
    try:
        from openai_codex import Codex
    except ImportError as exc:
        raise CodexAgentError(_INSTALL_HINT) from exc
    return Codex()


def list_models(*, codex_factory: CodexFactory | None = None, include_hidden: bool = True) -> list:
    """`codex.models(include_hidden=True)` — used by `tt auth` for live model discovery."""
    with _open_codex(codex_factory) as codex:
        return codex.models(include_hidden=include_hidden)


def login_api_key(api_key: str, *, codex_factory: CodexFactory | None = None) -> None:
    """`codex.login_api_key(key)` — persists the key into the SDK's own local storage.

    tt never stores this secret itself; the Codex CLI's own local session takes over
    after this call, same as reusing an existing ChatGPT/device-code login.
    """
    with _open_codex(codex_factory) as codex:
        codex.login_api_key(api_key)


def _contract_schema() -> dict:
    from tinytalk.contract import contract_json_schema

    return contract_json_schema()


def _split_messages(messages: list[Message]) -> tuple[str, str | None]:
    """Flatten seam messages into (prompt, system_prompt), matching claude_agent.py."""
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    rest = [m for m in messages if m.role is not Role.SYSTEM]
    if len(rest) == 1:
        prompt = rest[0].content
    else:
        prompt = "\n\n".join(f"[{m.role.value}] {m.content}" for m in rest)
    return prompt, ("\n\n".join(system_parts) or None)


def _map_usage(raw: object) -> Usage:
    if not isinstance(raw, dict):
        return Usage()

    def _field(key: str) -> int:
        try:
            return int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    # Codex reports cached_input_tokens as a subset of input_tokens; no normalization.
    prompt = _field("input_tokens")
    completion = _field("output_tokens")
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cached_prompt_tokens=_field("cached_input_tokens"),
    )
