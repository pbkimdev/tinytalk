"""Tier controller — cheapest path first, escalate only on failure (#31, PRD §4).

T0 consults the cache; T1 asks the default backend with grounding context; T2
re-asks with enriched grounding (on-demand help) and the fallback backend when
one is configured. T1 falls through to T2 both on a bad-output validation
failure and on a provider-level fault (transport/auth/rate-limit — anything
raised as a `ProviderError`), so a dead or misconfigured primary backend
doesn't fail the whole request when a fallback is configured (PRD-provider-
setup.md §6). A validation gate runs between tiers: pass → return (and
cache), fail → escalate. The controller never executes commands.

The cache (#36), grounding (#33), and validation (#34) hooks have permissive
defaults so the controller works before those land and gets stricter as they do.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from tinytalk.contract import Suggestion
from tinytalk.engine import AttemptDetail, Generation, generate
from tinytalk.parsing import FormatError
from tinytalk.prompts import STATIC_SYSTEM, user_message
from tinytalk.provider.base import Message, Provider, ProviderError, Role, Usage


@dataclass(frozen=True)
class TierRequest:
    prompt: str
    cwd: str = "."
    session_context: str = ""  # redacted recent commands (#35)
    language: str = "en"  # explanation language (#107); "en" ⇒ no prompt clause, no key material


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    danger: str  # final classification — at least as severe as the model's claim
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class TierResult:
    suggestion: Suggestion
    validation: ValidationResult
    tier: int  # 0 = cache, 1 = grounded, 2 = escalated
    usage: Usage = field(default_factory=Usage)
    attempts: int = 0
    backend: str = ""
    # Hash of the assembled prompt surface; empty for a pure cache hit (assembles none).
    prompt_surface_hash: str = ""
    attempts_detail: tuple[AttemptDetail, ...] = ()


class NoValidCommand(Exception):
    """Every tier failed; carries the last attempt (if any) for diagnostics."""

    def __init__(
        self,
        problems: tuple[str, ...],
        last: Suggestion | None = None,
        *,
        kind: str = "no_command",
        backend: str = "",
        usage: Usage | None = None,
        attempts_detail: tuple[AttemptDetail, ...] = (),
    ):
        detail = "; ".join(problems) or "no backend produced a parseable suggestion"
        super().__init__(detail)
        self.problems = problems
        self.last = last
        self.kind = kind
        self.backend = backend
        self.usage = usage if usage is not None else Usage()
        self.attempts_detail = tuple(attempts_detail)


class Cache(Protocol):
    def get(self, request: TierRequest, backend: str) -> Suggestion | None: ...
    def put(self, request: TierRequest, backend: str, suggestion: Suggestion) -> None: ...


class Grounding(Protocol):
    def system_prompt(self, request: TierRequest) -> str: ...

    def enrich(self, needs: tuple[str, ...], problems: tuple[str, ...]) -> str:
        """Extra context for T2 (e.g. real --help output for the tools T1 named)."""
        ...


Validator = Callable[[Suggestion], ValidationResult]


class NullCache:
    def get(self, request: TierRequest, backend: str) -> Suggestion | None:
        return None

    def put(self, request: TierRequest, backend: str, suggestion: Suggestion) -> None:
        return None


class StaticGrounding:
    """Placeholder grounding: the contract instructions only (real one lands in #33)."""

    def system_prompt(self, request: TierRequest) -> str:
        return STATIC_SYSTEM

    def enrich(self, needs: tuple[str, ...], problems: tuple[str, ...]) -> str:
        return ""


def permissive_validator(suggestion: Suggestion) -> ValidationResult:
    """Trust the model until the real ladder lands (#34)."""
    return ValidationResult(ok=True, danger=suggestion.danger.value)


class TierController:
    def __init__(
        self,
        provider: Provider,
        *,
        escalation: Callable[[], Provider] | None = None,
        cache: Cache | None = None,
        grounding: Grounding | None = None,
        validator: Validator = permissive_validator,
        escalation_name: str = "",
        request_opts: dict[str, object] | None = None,
    ):
        self._provider = provider
        self._escalation = escalation
        self._escalation_name = escalation_name
        self._cache = cache or NullCache()
        self._grounding = grounding or StaticGrounding()
        self._validate = validator
        # Forwarded into every CompletionRequest (e.g. the eval runner pins temperature=0).
        self._request_opts = dict(request_opts or {})

    async def suggest(self, request: TierRequest) -> TierResult:
        # T0 — cache; re-validate hits (the environment may have changed).
        cached = self._cache.get(request, self._provider.name)
        if cached is not None:
            validation = self._validate(cached)
            if validation.ok:
                # A pure cache hit assembles no prompt surface → prompt_surface_hash "".
                return TierResult(
                    suggestion=cached, validation=validation, tier=0, backend=self._provider.name
                )

        usage = Usage()
        attempts = 0
        detail: list[AttemptDetail] = []
        problems: tuple[str, ...] = ()
        last: Suggestion | None = None

        # T1 — grounded ask against the default backend.
        messages, surface_hash = self._messages(request, extra="")
        try:
            gen = await generate(self._provider, messages, **self._request_opts)
            usage, attempts, detail = _merge_gen(
                usage, attempts, detail, gen, tier=1, backend=self._provider.name
            )
            validation = self._validate(gen.suggestion)
            if validation.ok:
                self._cache.put(request, self._provider.name, gen.suggestion)
                return TierResult(
                    suggestion=gen.suggestion,
                    validation=validation,
                    tier=1,
                    usage=usage,
                    attempts=attempts,
                    backend=self._provider.name,
                    prompt_surface_hash=surface_hash,
                    attempts_detail=tuple(detail),
                )
            problems = validation.problems
            last = gen.suggestion
        except FormatError as exc:
            problems = (str(exc),)
            usage, attempts, detail = _merge_error(
                usage, attempts, detail, exc, tier=1, backend=self._provider.name
            )
        except ProviderError as exc:
            problems = (str(exc),)
            usage, attempts, detail = _merge_error(
                usage, attempts, detail, exc, tier=1, backend=self._provider.name
            )

        # T2 — enriched grounding + fallback backend when configured.
        needs = last.needs if last is not None else ()
        extra = self._grounding.enrich(needs, problems)
        try:
            provider = self._escalation() if self._escalation is not None else self._provider
        except Exception as exc:
            backend = self._escalation_name or self._provider.name
            raise NoValidCommand(
                problems + (str(exc),), last, kind="transport", backend=backend,
                usage=usage, attempts_detail=tuple(detail),
            ) from exc
        messages, surface_hash = self._messages(request, extra=extra, problems=problems)
        try:
            gen = await generate(provider, messages, **self._request_opts)
        except ProviderError as exc:
            kind = "transport" if last is None else "no_command"
            usage, attempts, detail = _merge_error(
                usage, attempts, detail, exc, tier=2, backend=provider.name
            )
            raise NoValidCommand(
                problems + (str(exc),), last, kind=kind, backend=provider.name,
                usage=usage, attempts_detail=tuple(detail),
            ) from exc
        except FormatError as exc:
            usage, attempts, detail = _merge_error(
                usage, attempts, detail, exc, tier=2, backend=provider.name
            )
            raise NoValidCommand(
                problems + (str(exc),), last, kind="no_command", backend=provider.name,
                usage=usage, attempts_detail=tuple(detail),
            ) from exc
        usage, attempts, detail = _merge_gen(
            usage, attempts, detail, gen, tier=2, backend=provider.name
        )
        validation = self._validate(gen.suggestion)
        if validation.ok:
            self._cache.put(request, self._provider.name, gen.suggestion)
            return TierResult(
                suggestion=gen.suggestion,
                validation=validation,
                tier=2,
                usage=usage,
                attempts=attempts,
                backend=provider.name,
                prompt_surface_hash=surface_hash,
                attempts_detail=tuple(detail),
            )
        raise NoValidCommand(
            problems + validation.problems,
            gen.suggestion,
            kind="no_command",
            backend=provider.name,
            usage=usage,
            attempts_detail=tuple(detail),
        )

    def _messages(
        self, request: TierRequest, *, extra: str, problems: tuple[str, ...] = ()
    ) -> tuple[list[Message], str]:
        system = self._grounding.system_prompt(request)
        if extra:
            system = f"{system}\n\n{extra}"
        messages = [
            Message(Role.SYSTEM, system),
            Message(
                Role.USER,
                user_message(
                    request.prompt,
                    cwd=request.cwd,
                    session_context=request.session_context,
                    problems=problems,
                ),
            ),
        ]
        return messages, _surface_hash(messages)


def _surface_hash(messages: list[Message]) -> str:
    """Stable digest of the assembled prompt surface (the text itself is never stored)."""
    h = hashlib.sha256()
    for m in messages:
        h.update(m.role.value.encode())
        h.update(b"\0")
        h.update(m.content.encode())
        h.update(b"\0")
    return h.hexdigest()


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
        cached_prompt_tokens=a.cached_prompt_tokens + b.cached_prompt_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )


def _merge_gen(
    usage: Usage,
    attempts: int,
    detail: list[AttemptDetail],
    gen: Generation,
    *,
    tier: int,
    backend: str,
) -> tuple[Usage, int, list[AttemptDetail]]:
    """Fold a winning generation's accumulated usage + per-attempt ledger into the totals."""
    tagged = [replace(e, tier=tier, backend=backend) for e in gen.attempts_detail]
    return _add_usage(usage, gen.usage), attempts + gen.attempts, detail + tagged


def _merge_error(
    usage: Usage,
    attempts: int,
    detail: list[AttemptDetail],
    exc: Exception,
    *,
    tier: int,
    backend: str,
) -> tuple[Usage, int, list[AttemptDetail]]:
    """Fold a terminal error's carried usage + ledger (getattr-readable) into the totals.

    Works for both a `FormatError` (ladder exhausted) and a `ProviderError` raised
    mid-ladder — engine attaches `usage`/`attempts_detail` to either before it escapes.
    """
    err_usage = getattr(exc, "usage", None) or Usage()
    err_detail = getattr(exc, "attempts_detail", ()) or ()
    tagged = [replace(e, tier=tier, backend=backend) for e in err_detail]
    return _add_usage(usage, err_usage), attempts + len(err_detail), detail + tagged
