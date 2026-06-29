"""Degradation chain (PRD §10): native structured output → grammar → fenced text.

`generate()` walks the capability-derived ladder, retrying within each tier; malformed
output is rejected and never surfaced. The whole ladder exhausting raises `FormatError`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from clite.contract import Suggestion, contract_json_schema
from clite.parsing import FormatError, parse_completion
from clite.provider.base import (
    Capabilities,
    CompletionRequest,
    Message,
    Provider,
    ResponseFormat,
    Tool,
    Usage,
)

_CONTRACT_TOOL = Tool(
    name="suggest_command",
    description="Return the validated command suggestion.",
    parameters=contract_json_schema(),
)


@dataclass(frozen=True)
class Generation:
    suggestion: Suggestion
    response_format: ResponseFormat
    attempts: int
    usage: Usage

    @property
    def format_ok(self) -> bool:
        return True  # a Generation only exists for a validated suggestion


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
    """Run a request through the degradation ladder; never return malformed output."""
    attempts = 0
    last_error: FormatError | None = None
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
            completion = await provider.complete(request)
            try:
                suggestion = parse_completion(completion, fmt)
            except FormatError as exc:
                last_error = exc
                continue
            return Generation(
                suggestion=suggestion,
                response_format=fmt,
                attempts=attempts,
                usage=completion.usage,
            )
    raise FormatError(
        f"degradation ladder exhausted after {attempts} attempts: {last_error}"
    )


def generate_sync(provider: Provider, messages: list[Message], **kwargs: object) -> Generation:
    """Synchronous convenience wrapper for non-async callers."""
    return asyncio.run(generate(provider, messages, **kwargs))  # type: ignore[arg-type]
