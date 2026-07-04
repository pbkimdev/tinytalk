"""Degradation chain (PRD §10): native structured output → grammar → fenced text.

`generate()` walks the capability-derived ladder, retrying within each tier; malformed
output is rejected and never surfaced. The whole ladder exhausting raises `FormatError`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

from tinytalk.contract import Suggestion, contract_json_schema
from tinytalk.parsing import FormatError, parse_completion
from tinytalk.prompts import CONTRACT_TOOL_DESCRIPTION
from tinytalk.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ProviderError,
    ResponseFormat,
    Tool,
    Usage,
)

_CONTRACT_TOOL = Tool(
    name="suggest_command",  # wire identifier — mirrored in the providers, not prompt surface
    description=CONTRACT_TOOL_DESCRIPTION,
    parameters=contract_json_schema(),
)


@dataclass(frozen=True)
class AttemptDetail:
    """One format-attempt in the degradation ladder.

    The engine fills the per-attempt facts (`format_reached`, `usage`,
    `latency_ms`, `result`); `TierController` tags `tier`+`backend`; spec-A3
    enriches `model`+`cost_usd` (defaulted so a later `replace` can set them).
    """

    format_reached: ResponseFormat
    usage: Usage
    latency_ms: int
    result: str  # "ok" | "format_error"
    tier: int = 0
    backend: str = ""
    model: str = ""
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Generation:
    suggestion: Suggestion
    response_format: ResponseFormat
    attempts: int
    usage: Usage  # accumulated across every attempt in this call, not just the winning parse
    attempts_detail: tuple[AttemptDetail, ...] = ()

    @property
    def format_ok(self) -> bool:
        return True  # a Generation only exists for a validated suggestion


def _coalesce(usage: Usage) -> Usage:
    """Some openai-compat servers report `total=0` with prompt/completion set."""
    if usage.total_tokens == 0:
        return replace(usage, total_tokens=usage.prompt_tokens + usage.completion_tokens)
    return usage


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
        cached_prompt_tokens=a.cached_prompt_tokens + b.cached_prompt_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )


def build_ladder(caps: Capabilities) -> list[ResponseFormat]:
    """Ordered degradation ladder; the universal TEXT fallback is always last."""
    ladder: list[ResponseFormat] = []
    if caps.supports_tool_calling:
        ladder.append(ResponseFormat.TOOL_CALL)
    elif caps.supports_native_json:
        ladder.append(ResponseFormat.JSON_OBJECT)
    if caps.supports_grammar:
        ladder.append(ResponseFormat.GRAMMAR)
    ladder.append(ResponseFormat.TEXT)
    return ladder


async def generate(
    provider: Provider,
    messages: list[Message],
    *,
    grammar: str | None = None,
    retries_per_tier: int = 2,
    **req_opts: object,
) -> Generation:
    """Run a request through the degradation ladder; never return malformed output.

    Usage and per-attempt latency accumulate across **every** attempt (failed
    parses included) so retried successes and total failures both report their
    full spend; the accumulated usage and the per-attempt ledger ride the
    terminal `FormatError` as attributes for the caller to fold in.
    """
    attempts = 0
    last_error: FormatError | None = None
    usage = Usage()
    detail: list[AttemptDetail] = []
    for fmt in build_ladder(provider.capabilities):
        tools = [_CONTRACT_TOOL] if fmt is ResponseFormat.TOOL_CALL else []
        req_grammar = grammar if fmt is ResponseFormat.GRAMMAR else None
        for _ in range(retries_per_tier):
            attempts += 1
            request = CompletionRequest(
                messages=messages,
                tools=tools,
                response_format=fmt,
                grammar=req_grammar,
                **req_opts,  # type: ignore[arg-type]
            )
            start = time.perf_counter()
            try:
                completion = await provider.complete(request)
            except ProviderError as exc:
                # A transport fault mid-ladder still spent tokens on earlier
                # attempts; carry the accumulated usage + ledger so the caller
                # bills faithfully (mirrors the terminal FormatError path).
                exc.usage = usage
                exc.attempts_detail = tuple(detail)
                raise
            latency_ms = round((time.perf_counter() - start) * 1000)
            attempt_usage = _coalesce(completion.usage)
            usage = _add_usage(usage, attempt_usage)
            try:
                suggestion = parse_completion(completion, fmt)
            except FormatError as exc:
                last_error = exc
                detail.append(AttemptDetail(fmt, attempt_usage, latency_ms, "format_error"))
                continue
            detail.append(AttemptDetail(fmt, attempt_usage, latency_ms, "ok"))
            return Generation(
                suggestion=suggestion,
                response_format=fmt,
                attempts=attempts,
                usage=usage,
                attempts_detail=tuple(detail),
            )
    error = FormatError(f"degradation ladder exhausted after {attempts} attempts: {last_error}")
    error.usage = usage  # getattr-readable by the controller; no parsing.py change
    error.attempts_detail = tuple(detail)
    raise error


def generate_sync(provider: Provider, messages: list[Message], **kwargs: object) -> Generation:
    """Synchronous convenience wrapper for non-async callers."""
    return asyncio.run(generate(provider, messages, **kwargs))  # type: ignore[arg-type]
