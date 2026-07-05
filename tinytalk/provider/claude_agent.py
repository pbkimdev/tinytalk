"""Claude Agent SDK provider adapter (#27).

Implements the `Provider` seam over `claude_agent_sdk.query()`. The SDK's
`output_format` (JSON-schema constrained) + `ResultMessage.structured_output` is
surfaced as the engine's JSON_OBJECT rung; the TEXT rung falls back to the plain
result text. Auth follows the SDK's own conventions (Claude Code login /
`ANTHROPIC_API_KEY`); the SDK is imported lazily so unselected backends never
pay for it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

from tinytalk.provider.base import (
    Capabilities,
    Completion,
    CompletionRequest,
    Message,
    ProviderError,
    ResponseFormat,
    Role,
    Usage,
)

_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# query_fn(prompt, options) -> async iterator of SDK messages; injectable for tests.
QueryFn = Callable[..., AsyncIterator[object]]


class ClaudeAgentError(ProviderError):
    """The SDK failed or returned an error result."""


class ClaudeAgentProvider:
    """`Provider` over the Claude Agent SDK (single-shot, no tool loop)."""

    name: str
    capabilities: Capabilities

    def __init__(
        self, model: str, *, query_fn: QueryFn | None = None, default_effort: str | None = None
    ):
        self.model = model
        self.name = f"claude-agent:{model}"
        self.capabilities = Capabilities(supports_native_json=True)
        self._query_fn = query_fn
        self._default_effort = default_effort if default_effort in _EFFORT_LEVELS else None

    async def complete(self, request: CompletionRequest) -> Completion:
        query_fn, options = self._build_call(request)
        prompt, system_prompt = _split_messages(request.messages)
        options.system_prompt = system_prompt

        result = None
        try:
            async for message in query_fn(prompt=prompt, options=options):
                if type(message).__name__ == "ResultMessage":
                    result = message
        except ClaudeAgentError:
            raise
        except Exception as exc:  # SDK errors (CLI missing, process, JSON decode)
            raise ClaudeAgentError(f"claude-agent-sdk query failed: {exc}") from exc

        if result is None:
            raise ClaudeAgentError("claude-agent-sdk stream ended without a result")
        return self._result_to_completion(result)

    def _result_to_completion(self, result: object) -> Completion:
        """Map a terminal `ResultMessage` to a `Completion`."""
        if getattr(result, "is_error", False):
            detail = getattr(result, "result", None) or getattr(result, "subtype", "unknown")
            raise ClaudeAgentError(f"claude-agent-sdk returned an error result: {detail}")
        structured = getattr(result, "structured_output", None)
        if structured is not None:
            text = json.dumps(structured)
        else:
            text = getattr(result, "result", None) or ""
        return Completion(
            text=text,
            usage=_map_usage(getattr(result, "usage", None)),
            model=self.model,
            raw=result,
        )

    def _build_call(self, request: CompletionRequest) -> tuple[QueryFn, object]:
        if self._query_fn is not None:
            from types import SimpleNamespace

            options: object = SimpleNamespace(
                model=self.model, max_turns=1, allowed_tools=[], system_prompt=None
            )
            query_fn = self._query_fn
        else:
            try:
                from claude_agent_sdk import ClaudeAgentOptions, query
            except ImportError as exc:
                raise ClaudeAgentError(
                    "claude-agent-sdk is not installed; `uv sync` (or pip install claude-agent-sdk)"
                ) from exc

            from tinytalk.addons import AddonMissing, claude_cli_path

            # Frozen binary: the `claude` CLI is a downloaded add-on, not bundled — point the
            # SDK at it. Source installs get None and the SDK resolves `claude` from $PATH.
            try:
                cli_path = claude_cli_path()
            except AddonMissing as exc:
                raise ClaudeAgentError(str(exc)) from exc

            options = ClaudeAgentOptions(
                model=self.model, max_turns=1, allowed_tools=[], cli_path=cli_path
            )
            query_fn = query

        if request.response_format is ResponseFormat.JSON_OBJECT:
            options.output_format = {"type": "json_schema", "schema": _contract_schema()}
        effort = (
            request.reasoning_effort
            if request.reasoning_effort in _EFFORT_LEVELS
            else self._default_effort
        )
        if effort is not None:
            options.effort = effort
        return query_fn, options


def _contract_schema() -> dict:
    from tinytalk.contract import contract_json_schema

    return contract_json_schema()


def _split_messages(messages: list[Message]) -> tuple[str, str | None]:
    """Flatten seam messages into (prompt, system_prompt) for the SDK."""
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

    # SDK usage mirrors the Messages API: input_tokens excludes cache reads/writes —
    # normalize to the seam's inclusive prompt_tokens convention (see `Usage`).
    cached = _field("cache_read_input_tokens")
    cache_write = _field("cache_creation_input_tokens")
    prompt = _field("input_tokens") + cached + cache_write
    completion = _field("output_tokens")
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cached_prompt_tokens=cached,
        cache_write_tokens=cache_write,
    )
