"""Tier controller (#31): T0 cache → T1 grounded → T2 escalated, gate between tiers."""

from __future__ import annotations

import asyncio
import json

import pytest

from tinytalk.config import Price
from tinytalk.contract import Danger, Suggestion
from tinytalk.cost import cost
from tinytalk.provider.base import Capabilities, Completion, ProviderError, Usage
from tinytalk.tiers import (
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
    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary))
    assert exc.value.kind == "transport"
    assert exc.value.backend == "stub"


def test_final_provider_error_after_format_error_is_transport():
    def boom(request, attempt):
        raise ProviderError("fallback died")

    primary = text_provider(Completion(text="not json"), Completion(text="<html>bad gateway</html>"))
    fallback = StubProvider(Capabilities(), boom)
    fallback.name = "cloud"

    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary, escalation=lambda: fallback, escalation_name="cloud"))

    assert exc.value.kind == "transport"
    assert exc.value.backend == "cloud"
    assert exc.value.last is None


def test_final_provider_error_after_invalid_command_remains_no_command():
    def never_ok(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=False, danger="safe", problems=("missing binary",))

    def boom(request, attempt):
        raise ProviderError("fallback died")

    primary = text_provider(completion_for("missing-tool"))
    fallback = StubProvider(Capabilities(), boom)
    fallback.name = "cloud"

    with pytest.raises(NoValidCommand) as exc:
        run(
            TierController(
                primary,
                escalation=lambda: fallback,
                escalation_name="cloud",
                validator=never_ok,
            )
        )

    assert exc.value.kind == "no_command"
    assert exc.value.backend == "cloud"
    assert exc.value.last is not None


# --- faithful usage & cost plumbing (spec-A2) -------------------------------


def test_gate_failure_reports_nonzero_tokens_and_cost():
    # Both tiers produce parseable commands the gate always rejects → NoValidCommand,
    # yet the spend still rides the exception (usage plumbed through failure).
    def never_ok(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=False, danger="safe", problems=(f"bad: {s.command}",))

    primary = text_provider(completion_for("cmd-a"), completion_for("cmd-b"))
    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary, validator=never_ok))
    assert exc.value.usage.total_tokens == 30  # both attempts counted
    assert cost(exc.value.usage, Price(input_per_mtok=1.0, output_per_mtok=2.0)) > 0


def test_provider_error_mid_retry_still_reports_earlier_attempt_spend():
    # T1 attempt-1 parses-fails (billed 12 tokens), attempt-2 transport-dies mid-ladder;
    # the earlier spend + ledger must still ride the terminal NoValidCommand, not vanish.
    def scripted(request, attempt):
        if attempt == 0:
            return Completion(text="not json", usage=Usage(9, 3, 12))
        raise ProviderError("transport died")

    primary = StubProvider(Capabilities(), scripted)  # TEXT-only → both attempts in one tier
    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary))
    assert exc.value.usage.total_tokens == 12
    assert [(d.tier, d.result) for d in exc.value.attempts_detail] == [(1, "format_error")]


def test_format_failure_across_both_tiers_reports_full_spend():
    primary = text_provider(
        Completion(text="nope", usage=Usage(4, 1, 5)),
        Completion(text="still nope", usage=Usage(4, 1, 5)),
    )
    escalated = text_provider(
        Completion(text="no json", usage=Usage(6, 2, 8)),
        Completion(text="also no", usage=Usage(6, 2, 8)),
    )
    escalated.name = "cloud"
    with pytest.raises(NoValidCommand) as exc:
        run(TierController(primary, escalation=lambda: escalated))
    # T1: two failed attempts (5+5) + T2: two failed attempts (8+8) = 26.
    assert exc.value.usage.total_tokens == 26
    assert len(exc.value.attempts_detail) == 4
    # each ledger entry tagged with the (tier, backend) that produced it —
    # primary "stub" for the two T1 attempts, fallback "cloud" for the two T2 attempts.
    assert [(d.tier, d.backend) for d in exc.value.attempts_detail] == [
        (1, "stub"),
        (1, "stub"),
        (2, "cloud"),
        (2, "cloud"),
    ]


def test_attempts_detail_tagged_with_tier_and_backend():
    # T1 rejected by the gate (one attempt), T2 succeeds (one attempt) on a named fallback.
    def validator(s: Suggestion) -> ValidationResult:
        return ValidationResult(ok=s.command == "good", danger="safe", problems=("nope",))

    primary = text_provider(completion_for("bad"))
    escalated = text_provider(completion_for("good"))
    escalated.name = "cloud"
    result = run(TierController(primary, escalation=lambda: escalated, validator=validator))
    assert result.tier == 2
    # one entry per attempt, each tagged with the tier + backend that produced it.
    assert [(d.tier, d.backend, d.result) for d in result.attempts_detail] == [
        (1, "stub", "ok"),
        (2, "cloud", "ok"),
    ]
    assert result.attempts == len(result.attempts_detail) == 2


def test_prompt_surface_hash_set_on_ask_empty_on_cache_hit():
    cache = DictCache(preloaded=SUGGESTION)
    hit = run(TierController(text_provider(), cache=cache))
    assert hit.tier == 0
    assert hit.prompt_surface_hash == ""  # a pure cache hit assembles no surface

    asked = run(TierController(text_provider(completion_for())))
    assert asked.tier == 1
    assert len(asked.prompt_surface_hash) == 64  # sha256 hex over the assembled surface
