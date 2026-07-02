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

from dataclasses import dataclass, field
from typing import Callable, Protocol

from clite.contract import Suggestion
from clite.engine import Generation, generate
from clite.parsing import FormatError
from clite.provider.base import Message, Provider, ProviderError, Role, Usage


@dataclass(frozen=True)
class TierRequest:
    prompt: str
    cwd: str = "."
    session_context: str = ""  # redacted recent commands (#35)


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


class NoValidCommand(Exception):
    """Every tier failed; carries the last attempt (if any) for diagnostics."""

    def __init__(self, problems: tuple[str, ...], last: Suggestion | None = None):
        detail = "; ".join(problems) or "no backend produced a parseable suggestion"
        super().__init__(detail)
        self.problems = problems
        self.last = last


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


_BASE_SYSTEM = """\
You are CLITE. Turn the user's plain-English request into exactly one runnable
shell command (a pipeline counts as one command) for their system. Respond with
only a JSON object matching this shape, no prose around it:
{"command": "...", "explanation": "...", "danger": "safe|caution|destructive",
 "confidence": 0.0-1.0, "needs": ["binaries", "used"], "alternatives": []}"""


class StaticGrounding:
    """Placeholder grounding: the contract instructions only (real one lands in #33)."""

    def system_prompt(self, request: TierRequest) -> str:
        return _BASE_SYSTEM

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
    ):
        self._provider = provider
        self._escalation = escalation
        self._cache = cache or NullCache()
        self._grounding = grounding or StaticGrounding()
        self._validate = validator

    async def suggest(self, request: TierRequest) -> TierResult:
        # T0 — cache; re-validate hits (the environment may have changed).
        cached = self._cache.get(request, self._provider.name)
        if cached is not None:
            validation = self._validate(cached)
            if validation.ok:
                return TierResult(
                    suggestion=cached, validation=validation, tier=0, backend=self._provider.name
                )

        usage = Usage()
        attempts = 0
        problems: tuple[str, ...] = ()
        last: Suggestion | None = None

        # T1 — grounded ask against the default backend.
        messages = self._messages(request, extra="")
        try:
            gen = await generate(self._provider, messages)
            usage, attempts = _accumulate(usage, attempts, gen)
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
                )
            problems = validation.problems
            last = gen.suggestion
        except (FormatError, ProviderError) as exc:
            problems = (str(exc),)

        # T2 — enriched grounding + fallback backend when configured.
        needs = last.needs if last is not None else ()
        extra = self._grounding.enrich(needs, problems)
        provider = self._escalation() if self._escalation is not None else self._provider
        messages = self._messages(request, extra=extra, problems=problems)
        try:
            gen = await generate(provider, messages)
        except (FormatError, ProviderError) as exc:
            raise NoValidCommand(problems + (str(exc),), last) from exc
        usage, attempts = _accumulate(usage, attempts, gen)
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
            )
        raise NoValidCommand(problems + validation.problems, gen.suggestion)

    def _messages(
        self, request: TierRequest, *, extra: str, problems: tuple[str, ...] = ()
    ) -> list[Message]:
        system = self._grounding.system_prompt(request)
        if extra:
            system = f"{system}\n\n{extra}"
        user_parts = [request.prompt]
        if request.cwd and request.cwd != ".":
            user_parts.append(f"(current directory: {request.cwd})")
        if request.session_context:
            user_parts.append(f"Recent commands in this session:\n{request.session_context}")
        if problems:
            user_parts.append(
                "A previous attempt was rejected: " + "; ".join(problems) + ". Fix those issues."
            )
        return [
            Message(Role.SYSTEM, system),
            Message(Role.USER, "\n\n".join(user_parts)),
        ]


def _accumulate(usage: Usage, attempts: int, gen: Generation) -> tuple[Usage, int]:
    return (
        Usage(
            prompt_tokens=usage.prompt_tokens + gen.usage.prompt_tokens,
            completion_tokens=usage.completion_tokens + gen.usage.completion_tokens,
            total_tokens=usage.total_tokens + gen.usage.total_tokens,
        ),
        attempts + gen.attempts,
    )
