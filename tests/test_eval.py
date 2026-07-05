"""Eval harness (#32): assertion DSL, scoring, leaderboard, export, e2e over stubs."""

from __future__ import annotations

import json
import os

import pytest

import tinytalk.eval.runner as runner_mod
from tinytalk.config import Price, load_config
from tinytalk.cost import cost, cost_breakdown
from tinytalk.eval.runner import export, render_leaderboard, render_matrix, run_eval
from tinytalk.eval.suite import SUITE, check_assertion
from tinytalk.provider.base import Capabilities, Completion, Usage
from tests.stubs import StubProvider

# --- assertion DSL -----------------------------------------------------------


@pytest.mark.parametrize(
    ("assertion", "command", "expected"),
    [
        ("uses:du", "du -h -d1 . | sort -hr", True),
        ("uses:du", "echo du is great", False),  # not in command position
        ("uses_any:fd|find", "find . -name x", True),
        ("uses_any:fd|find", "ls -la", False),
        ("pipes_to:sort", "du -h | sort -hr", True),
        ("pipes_to:sort", "sort file.txt", False),  # first stage doesn't count
        ("contains:TODO", "grep -rn TODO .", True),
        ("not_contains:sudo", "ls -la", True),
        ("not_contains:sudo", "sudo ls", False),
        ("regex:-[a-zA-Z]*h", "df -kh", True),
    ],
)
def test_check_assertion(assertion, command, expected):
    assert check_assertion(assertion, command) is expected


def test_unknown_assertion_kind_raises():
    with pytest.raises(ValueError, match="unknown assertion kind"):
        check_assertion("telepathy:yes", "ls")


# --- cost model (spec-A2: lifted into tinytalk/cost.py) ----------------------


@pytest.mark.parametrize(
    "usage",
    [
        Usage(),
        Usage(100, 50, 150),
        Usage(100, 50, 150, cached_prompt_tokens=40),
        Usage(200, 80, 280, cached_prompt_tokens=50, cache_write_tokens=30),
    ],
)
def test_cost_breakdown_four_buckets_sum_to_cost(usage):
    price = Price(
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cached_input_per_mtok=0.3,
        cache_write_per_mtok=3.75,
    )
    breakdown = cost_breakdown(usage, price)
    assert set(breakdown) == {"fresh", "cached", "write", "output"}
    assert round(sum(breakdown.values()), 6) == cost(usage, price)


def test_cost_breakdown_concrete_bucket_values():
    # Each bucket priced at its own rate over independently-computed token counts —
    # fresh = prompt - cached - write, so the write bucket is really exercised.
    price = Price(
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cached_input_per_mtok=0.3,
        cache_write_per_mtok=3.75,
    )
    usage = Usage(200, 80, 280, cached_prompt_tokens=50, cache_write_tokens=30)
    breakdown = cost_breakdown(usage, price)
    assert breakdown["fresh"] == pytest.approx(120 * 3.0 / 1e6)  # 200 - 50 - 30 fresh
    assert breakdown["cached"] == pytest.approx(50 * 0.3 / 1e6)
    assert breakdown["write"] == pytest.approx(30 * 3.75 / 1e6)
    assert breakdown["output"] == pytest.approx(80 * 15.0 / 1e6)
    # total computed independently, not by re-summing the breakdown.
    assert cost(usage, price) == pytest.approx(
        round((120 * 3.0 + 50 * 0.3 + 30 * 3.75 + 80 * 15.0) / 1e6, 6)
    )


def test_cost_breakdown_cached_and_write_rates_fall_back_to_input_rate():
    # cached_input/cache_write rates unset → both bill at the plain input rate.
    price = Price(input_per_mtok=3.0, output_per_mtok=15.0)
    usage = Usage(200, 80, 280, cached_prompt_tokens=50, cache_write_tokens=30)
    breakdown = cost_breakdown(usage, price)
    assert breakdown["cached"] == pytest.approx(50 * 3.0 / 1e6)  # fell back to input rate
    assert breakdown["write"] == pytest.approx(30 * 3.0 / 1e6)  # fell back to input rate
    assert breakdown["fresh"] == pytest.approx(120 * 3.0 / 1e6)
    assert breakdown["output"] == pytest.approx(80 * 15.0 / 1e6)


def test_suite_shape():
    assert len(SUITE) == 50
    assert len({p.id for p in SUITE}) == 50
    assert any(p.expected_danger == "destructive" for p in SUITE)
    for p in SUITE:
        assert p.assertions, p.id
        assert p.id == f"{p.target}-{p.lang}"
        for a in p.assertions:
            check_assertion(a, "ls")  # every assertion parses


def test_suite_is_parallel_en_ko_pairs():
    by_target: dict[str, list] = {}
    for p in SUITE:
        by_target.setdefault(p.target, []).append(p)
    assert len(by_target) == 25
    for target, pair in by_target.items():
        langs = {p.lang for p in pair}
        assert langs == {"en", "ko"}, target
        en = next(p for p in pair if p.lang == "en")
        ko = next(p for p in pair if p.lang == "ko")
        assert en.assertions == ko.assertions, target
        assert en.expected_danger == ko.expected_danger, target
        assert en.text != ko.text, target


# --- runner over stub backends ----------------------------------------------

CONFIG = """\
[defaults]
backend = "alpha"

[backends.alpha]
kind = "openai-compat"
base_url = "http://localhost:1/v1"
model = "model-a"

[backends.beta]
kind = "openai-compat"
base_url = "http://localhost:2/v1"
model = "model-b"

[prices."model-a"]
input_per_mtok = 1.0
output_per_mtok = 2.0
cached_input_per_mtok = 0.1
"""


def payload(command: str) -> str:
    return json.dumps(
        {
            "command": command,
            "explanation": "x",
            "danger": "safe",
            "confidence": 0.9,
            "needs": [],
        }
    )


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG)
    return load_config(p)


@pytest.fixture
def stub_backends(monkeypatch):
    def fake_make_provider(cfg):
        return StubProvider(
            Capabilities(),
            lambda request, i: Completion(
                text=payload("awk '$9 == 500 {print $7}' access.log | sort | uniq -c | sort -rn"),
                usage=Usage(100, 50, 150),
            ),
        )

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)


def test_end_to_end_eval_over_two_backends(config, stub_backends, tmp_path):
    reports = run_eval(
        config,
        ["alpha", "beta"],
        prompt_ids=["log-top-errors-en", "count-lines-code-en"],
        progress=False,
    )
    assert [r.backend for r in reports] == ["alpha", "beta"]
    alpha = reports[0]
    assert alpha.model == "model-a"
    assert len(alpha.results) == 2

    by_id = {r.prompt_id: r for r in alpha.results}
    disk = by_id["log-top-errors-en"]
    assert disk.lang == "en" and disk.target == "log-top-errors"
    assert disk.format_ok and disk.parses and disk.binaries_exist
    assert disk.assertions_pass
    assert disk.danger == "safe" and disk.danger_correct
    assert disk.tier == 1
    assert disk.prompt_tokens == 100
    # cost from the price table: 100×1.0/1e6 + 50×2.0/1e6
    assert disk.cost_usd == pytest.approx(0.0002)

    todo = by_id["count-lines-code-en"]
    assert todo.format_ok  # the command is real, it just doesn't find
    assert not todo.assertions_pass

    assert alpha.format_ok_pct == 100.0
    assert alpha.assertions_pct == 50.0
    assert alpha.strict_pass_pct == 50.0
    assert alpha.strict_pass_pct_en == 50.0
    assert alpha.strict_pass_pct_ko == 0.0  # no ko rows selected
    assert alpha.total_tokens == 300
    assert alpha.total_cost_usd == pytest.approx(0.0004)


def test_bare_target_selects_both_languages(config, stub_backends):
    reports = run_eval(config, ["alpha"], prompt_ids=["log-top-errors"], progress=False)
    results = reports[0].results
    assert [r.prompt_id for r in results] == ["log-top-errors-en", "log-top-errors-ko"]
    assert {r.lang for r in results} == {"en", "ko"}
    # the stub answers both languages identically, so per-language rates agree
    assert reports[0].strict_pass_pct_en == reports[0].strict_pass_pct_ko == 100.0


def test_leaderboard_and_matrix_render(config, stub_backends):
    reports = run_eval(config, ["alpha", "beta"], prompt_ids=["log-top-errors"], progress=False)
    board = render_leaderboard(reports)
    assert "alpha" in board and "beta" in board
    assert "format" in board and "cost" in board
    assert "pass" in board and "EN" in board and "KO" in board
    matrix = render_matrix(reports)
    assert "log-top-errors-en" in matrix and "log-top-errors-ko" in matrix
    assert "pass" in matrix


def test_export_json_and_csv(config, stub_backends, tmp_path):
    reports = run_eval(config, ["alpha"], prompt_ids=["log-top-errors-en"], progress=False)
    json_path = tmp_path / "results.json"
    export(reports, json_path)
    data = json.loads(json_path.read_text())
    assert data[0]["backend"] == "alpha"
    assert data[0]["results"][0]["prompt_id"] == "log-top-errors-en"
    assert data[0]["results"][0]["lang"] == "en"
    assert data[0]["results"][0]["target"] == "log-top-errors"

    csv_path = tmp_path / "results.csv"
    export(reports, csv_path)
    lines = csv_path.read_text().strip().splitlines()
    assert lines[0].startswith("backend,model,prompt_id,lang,target")
    assert lines[1].startswith("alpha,model-a,log-top-errors-en,en,log-top-errors")

    with pytest.raises(ValueError, match="unsupported export"):
        export(reports, tmp_path / "results.xlsx")


def test_unknown_prompt_id_fails_fast(config, stub_backends):
    with pytest.raises(ValueError, match="unknown prompt ids"):
        run_eval(config, ["alpha"], prompt_ids=["nope"], progress=False)


def test_format_failure_is_scored_not_fatal(config, monkeypatch):
    def fake_make_provider(cfg):
        return StubProvider(Capabilities(), lambda request, i: Completion(text="no json at all"))

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)
    reports = run_eval(config, ["alpha"], prompt_ids=["log-top-errors"], progress=False)
    result = reports[0].results[0]
    assert not result.format_ok
    assert result.error is not None
    assert reports[0].format_ok_pct == 0.0


def test_cached_tokens_flow_and_cache_aware_cost(config, monkeypatch):
    def fake_make_provider(cfg):
        return StubProvider(
            Capabilities(),
            lambda request, i: Completion(
                text=payload("awk '{print $1}' access.log | sort | uniq -c | sort -rn"),
                usage=Usage(100, 50, 150, cached_prompt_tokens=40),
            ),
        )

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)
    reports = run_eval(
        config, ["alpha"], prompt_ids=["log-top-errors-en"], progress=False, warmup=False
    )
    result = reports[0].results[0]
    assert result.cached_prompt_tokens == 40
    assert result.cache_write_tokens == 0
    # 60 fresh × $1 + 40 cached × $0.1 + 50 out × $2, per MTok
    assert result.cost_usd == pytest.approx((60 * 1.0 + 40 * 0.1 + 50 * 2.0) / 1e6)


def test_warmup_and_temperature_pinning(config, monkeypatch):
    providers = []

    def fake_make_provider(cfg):
        provider = StubProvider(
            Capabilities(),
            lambda request, i: Completion(
                text=payload("awk '{print $1}' access.log | sort | uniq -c | sort -rn"), usage=Usage(10, 5, 15)
            ),
        )
        providers.append(provider)
        return provider

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)
    reports = run_eval(config, ["alpha"], prompt_ids=["log-top-errors-en"], progress=False)
    assert len(providers[0].requests) == 2  # warmup + one scored prompt
    assert all(req.temperature == 0.0 for req in providers[0].requests)
    assert all(req.max_tokens == 1024 for req in providers[0].requests)
    assert len(reports[0].results) == 1
    assert reports[0].total_tokens == 15  # warmup usage never scored

    providers.clear()
    run_eval(config, ["alpha"], prompt_ids=["log-top-errors-en"], progress=False, warmup=False)
    assert len(providers[0].requests) == 1


def test_eval_uses_isolated_cache_and_state_roots(config, monkeypatch):
    seen: list[tuple[str | None, str | None]] = []
    monkeypatch.setenv("XDG_CACHE_HOME", "/real/cache")
    monkeypatch.setenv("XDG_STATE_HOME", "/real/state")

    def fake_make_provider(cfg):
        return StubProvider(
            Capabilities(),
            lambda request, i: (
                seen.append((os.environ.get("XDG_CACHE_HOME"), os.environ.get("XDG_STATE_HOME")))
                or Completion(
                    text=payload("awk '{print $1}' access.log | sort | uniq -c | sort -rn")
                )
            ),
        )

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)
    run_eval(config, ["alpha"], prompt_ids=["log-top-errors-en"], progress=False, warmup=False)

    assert seen
    assert seen[0][0] != "/real/cache"
    assert seen[0][1] != "/real/state"
    assert seen[0][0] and os.path.basename(seen[0][0]).startswith("tt-eval-cache-")
    assert seen[0][1] and os.path.basename(seen[0][1]).startswith("tt-eval-state-")
    assert os.environ["XDG_CACHE_HOME"] == "/real/cache"
    assert os.environ["XDG_STATE_HOME"] == "/real/state"
