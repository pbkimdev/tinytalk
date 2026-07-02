"""Tier controller (#31): T0 cache → T1 grounded → T2 escalated, gate between tiers."""

from __future__ import annotations

import asyncio
import json

import pytest

from clite.contract import Danger, Suggestion
from clite.provider.base import Capabilities, Completion, ProviderError, Usage
from clite.tiers import (
    NoValidCommand,
    TierController,
    TierRequest,
    ValidationResult,
)
from tests.stubs import StubProvider

SUGGESTION = Suggestion(
    command="du -h -d1 . | sort -hr",
    explanation="disk usage",
    danger=Danger.SAFE,
    confidence=0.9,
    needs=("du", "sort"),
)


def completion_for(command: str = "du -h -d1 . | sort -hr") -> Completion:
    payload = {
        "command": command,
        "explanation": "disk usage",
        "danger": "safe",
        "confidence": 0.9,
        "needs": ["du", "sort"],
    }
    return Completion(text=json.dumps(payload), usage=Usage(10, 5, 15))


class DictCache:
    def __init__(self, preloaded: Suggestion | None = None):
        self._value = preloaded
        self.puts: list[Suggestion] = []

    def get(self, request: TierRequest, backend: str) -> Suggestion | None:
        return self._value

    def put(self, request: TierRequest, backend: str, suggestion: Suggestion) -> None:
        self.puts.append(suggestion)


class RecordingGrounding:
    def __init__(self):
        self.enrich_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def system_prompt(self, request: TierRequest) -> str:
        return "SYSTEM-PROMPT"

    def enrich(self, needs, problems):
        self.enrich_calls.append((tuple(needs), tuple(problems)))
        return "ENRICHED-HELP"


def text_provider(*completions: Completion) -> StubProvider:
    return StubProvider(Capabilities(), list(completions))


def run(controller: TierController, prompt: str = "show disk usage") -> object:
    return asyncio.run(controller.suggest(TierRequest(prompt=prompt)))


def test_t0_cache_hit_skips_model():
    provider = text_provider()  # any call would raise IndexError
    cache = DictCache(preloaded=SUGGESTION)
    result = run(TierController(provider, cache=cache))
    assert result.tier == 0
    assert result.suggestion == SUGGESTION
    assert provider.requests == []


def test_t0_invalid_cache_entry_falls_through():
    def reject_cached(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=s is not SUGGESTION, danger="safe", problems=("stale",))

    provider = text_provider(completion_for())
    cache = DictCache(preloaded=SUGGESTION)
    result = run(TierController(provider, cache=cache, validator=reject_cached))
    assert result.tier == 1
    assert len(provider.requests) == 1


def test_t1_pass_returns_and_caches():
    provider = text_provider(completion_for())
    cache = DictCache()
    result = run(TierController(provider, cache=cache))
    assert result.tier == 1
    assert result.backend == "stub"
    assert result.usage == Usage(10, 5, 15)
    assert cache.puts == [result.suggestion]
    # grounding system prompt is actually sent
    assert "runnable" in provider.requests[0].messages[0].content


def test_t1_gate_fail_escalates_to_t2_with_enrichment():
    def validator(s: Suggestion) -> ValidationResult:
        if "gdu" in s.command:
            return ValidationResult(ok=False, danger="safe", problems=("gdu: not installed",))
        return ValidationResult(ok=True, danger="safe")

    primary = text_provider(completion_for("gdu -x"))
    escalated = text_provider(completion_for("du -h -d1 ."))
    grounding = RecordingGrounding()
    result = run(
        TierController(
            primary, escalation=lambda: escalated, grounding=grounding, validator=validator
        )
    )
    assert result.tier == 2
    assert result.backend == "stub"
    assert result.suggestion.command == "du -h -d1 ."
    # enrichment got the failed suggestion's needs + the gate's problems
    assert grounding.enrich_calls == [(("du", "sort"), ("gdu: not installed",))]
    # T2 messages carry the enrichment and the rejection feedback
    system, user = escalated.requests[0].messages
    assert "ENRICHED-HELP" in system.content
    assert "gdu: not installed" in user.content
    # usage accumulates across tiers
    assert result.usage.total_tokens == 30


def test_t1_format_error_escalates():
    primary = text_provider(Completion(text="no json here"), Completion(text="still no json"))
    escalated = text_provider(completion_for())
    result = run(TierController(primary, escalation=lambda: escalated))
    assert result.tier == 2


def test_both_tiers_failing_raises_with_history():
    def never_ok(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=False, danger="safe", problems=(f"bad: {s.command}",))

    primary = text_provider(completion_for("cmd-a"), completion_for("cmd-b"))
    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary, validator=never_ok))
    assert "cmd-a" in str(exc.value)
    assert "cmd-b" in str(exc.value)
    assert exc.value.last is not None


def test_t2_without_escalation_reuses_primary():
    def validator(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=s.command != "bad", danger="safe", problems=("nope",))

    primary = text_provider(completion_for("bad"), completion_for("good"))
    result = run(TierController(primary, validator=validator))
    assert result.tier == 2
    assert result.suggestion.command == "good"
    assert len(primary.requests) == 2


def test_t1_provider_error_falls_back():
    def boom(request, attempt):
        raise ProviderError("upstream unreachable")

    primary = StubProvider(Capabilities(), boom)
    fallback = text_provider(completion_for())
    result = run(TierController(primary, escalation=lambda: fallback))
    assert result.tier == 2
    assert result.suggestion.command == "du -h -d1 . | sort -hr"


def test_both_tiers_provider_error_raises_no_valid_command():
    def boom(request, attempt):
        raise ProviderError("still down")

    primary = StubProvider(Capabilities(), boom)
    with pytest.raises(NoValidCommand):
        run(TierController(primary))
