"""T0 exact cache (#36): hit/miss semantics and controller integration."""

from __future__ import annotations

import asyncio
import json

from tinytalk.cache import ExactCache, cache_key
from tinytalk.contract import Danger, Suggestion
from tinytalk.provider.base import Capabilities, Completion
from tinytalk.tiers import TierController, TierRequest
from tests.stubs import StubProvider

SUGGESTION = Suggestion(
    command="ls -lhS",
    explanation="list by size",
    danger=Danger.SAFE,
    confidence=0.9,
    needs=("ls",),
)

REQ = TierRequest(prompt="list files by size", cwd="/home/me")


def test_roundtrip(tmp_path):
    cache = ExactCache(tmp_path)
    assert cache.get(REQ, "stub") is None
    cache.put(REQ, "stub", SUGGESTION)
    assert cache.get(REQ, "stub") == SUGGESTION


def test_key_varies_by_prompt_cwd_backend(tmp_path):
    cache = ExactCache(tmp_path)
    cache.put(REQ, "stub", SUGGESTION)
    assert cache.get(TierRequest(prompt="delete everything", cwd=REQ.cwd), "stub") is None
    assert cache.get(TierRequest(prompt=REQ.prompt, cwd="/elsewhere"), "stub") is None
    assert cache.get(REQ, "other-backend") is None


def test_key_varies_by_language():
    ko = TierRequest(prompt=REQ.prompt, cwd=REQ.cwd, language="ko")
    en = TierRequest(prompt=REQ.prompt, cwd=REQ.cwd, language="en")
    assert cache_key(ko, "stub") != cache_key(REQ, "stub")
    assert cache_key(en, "stub") == cache_key(REQ, "stub")  # English keys unchanged (#107)


def test_prompt_normalization_hits():
    a = TierRequest(prompt="  List   Files  BY size ", cwd="/home/me")
    assert cache_key(a, "stub") == cache_key(REQ, "stub")


def test_corrupt_entry_is_a_miss_and_removed(tmp_path):
    cache = ExactCache(tmp_path)
    cache.put(REQ, "stub", SUGGESTION)
    path = cache._path(REQ, "stub")
    path.write_text(json.dumps({"command": ""}))  # violates the contract
    assert cache.get(REQ, "stub") is None
    assert not path.exists()


def test_repeated_request_skips_the_model(tmp_path):
    payload = {
        "command": "ls -lhS",
        "explanation": "list by size",
        "danger": "safe",
        "confidence": 0.9,
        "needs": ["ls"],
    }
    provider = StubProvider(Capabilities(), [Completion(text=json.dumps(payload))])
    controller = TierController(provider, cache=ExactCache(tmp_path))

    first = asyncio.run(controller.suggest(REQ))
    assert first.tier == 1
    second = asyncio.run(controller.suggest(REQ))
    assert second.tier == 0
    assert second.suggestion == first.suggestion
    assert len(provider.requests) == 1  # the second run never touched the model
