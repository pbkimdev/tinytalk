"""Bench analysis layer: delivery / layers / slices / stability (tinytalk/eval/analyze.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinytalk.eval.analyze import (
    CATEGORY,
    ESCAPE_HEAVY,
    analysis_to_dict,
    analyze,
    analyze_stability,
    classify_delivery_fault,
    first_failing_layer,
    main,
    render_analysis,
)
from tinytalk.eval.report import load_reports
from tinytalk.eval.runner import BackendReport, PromptResult, export
from tinytalk.eval.suite import SUITE

REAL_RUN = Path("docs/bench/2026-07-05/results.json")


def pr(prompt_id: str, **kw) -> PromptResult:
    """A delivered, strict-passing row by default; override to force a failure layer."""
    kw.setdefault("target", prompt_id.rsplit("-", 1)[0])
    kw.setdefault("lang", "ko" if prompt_id.endswith("-ko") else "en")
    kw.setdefault("command", "ls")
    kw.setdefault("format_ok", True)
    kw.setdefault("parses", True)
    kw.setdefault("binaries_exist", True)
    assertions = kw.setdefault("assertions", {})
    kw.setdefault("assertions_pass", all(assertions.values()) if assertions else True)
    return PromptResult(prompt_id=prompt_id, **kw)


def dropped(prompt_id: str, error: str) -> PromptResult:
    return pr(prompt_id, command=None, error=error, format_ok=False, parses=False,
              binaries_exist=False)


def rep(results: list[PromptResult], backend="b", model="m") -> BackendReport:
    return BackendReport(backend=backend, model=model, local=True, results=results)


# --- Metric #1: delivery-fault classification --------------------------------

def test_classify_backslash():
    r = dropped("k-en", "ladder exhausted: could not decode completion: Invalid \\escape: line 1")
    assert classify_delivery_fault(r) == "unescaped_backslash"


def test_classify_structure():
    r = dropped("c-ko", "could not decode completion: Expecting ',' delimiter: line 1 column 28")
    assert classify_delivery_fault(r) == "malformed_json"


def test_classify_transport():
    r = dropped("x-en", "claude-agent-sdk query failed: Reached maximum number of turns (1)")
    assert classify_delivery_fault(r) == "transport"


def test_classify_delivered_ignores_error():
    # The non-negotiable: a delivered command carrying a gate-rejection error is NOT a
    # delivery fault (it is an intent/runs miss on a delivered row).
    r = pr("g-en", error="git: unknown option --all", assertions_pass=False)
    assert classify_delivery_fault(r) is None


def test_classify_unknown():
    assert classify_delivery_fault(dropped("z-en", "")) == "unknown"


# --- Metric #2: layers -------------------------------------------------------

def test_first_failing_precedence():
    assert first_failing_layer(dropped("a-en", "could not decode completion: x")) == "delivered"
    assert first_failing_layer(pr("a-en", parses=False)) == "parses"
    assert first_failing_layer(pr("a-en", binaries_exist=False)) == "binaries"
    assert first_failing_layer(pr("a-en", assertions={"uses:x": False})) == "intent"
    assert first_failing_layer(pr("a-en")) == "pass"


def test_first_failing_partitions():
    results = [
        pr("a-en"), pr("b-en"),
        dropped("c-en", "could not decode completion: x"),
        pr("d-en", assertions={"uses:x": False}),
    ]
    a = analyze([rep(results)]).backends[0]
    assert sum(a.layers.first_failing.values()) == len(results)
    strict = sum(BackendReport._strict(r) for r in results)
    assert a.layers.first_failing.get("pass", 0) == strict == 2


def test_per_assertion_excludes_dropped():
    results = [
        pr("a-en", assertions={"uses:wc": True}),
        pr("b-en", assertions={"uses:wc": False}),
        dropped("c-en", "could not decode completion: x"),  # no scored assertions
    ]
    a = analyze([rep(results)]).backends[0]
    assert a.layers.per_assertion["uses:wc"] == [1, 2]  # dropped row not in the total


def test_intent_failures_keys():
    results = [pr("k-ko", assertions={"regex:(?i)restart": False, "contains:kubectl": True})]
    a = analyze([rep(results)]).backends[0]
    assert a.layers.intent_failures["k-ko"] == ["regex:(?i)restart"]


# --- Metric #3: slices -------------------------------------------------------

def test_category_map_complete():
    assert set(CATEGORY) == {p.target for p in SUITE}
    assert set(CATEGORY.values()) == {
        "text-processing", "structured-parsing", "pipelines",
        "networking", "kubernetes", "git-and-fs",
    }


def test_escape_heavy_subset():
    assert ESCAPE_HEAVY <= {p.target for p in SUITE}


def test_slices_ko_minus_en():
    results = [
        pr("count-lines-code-en"),  # en pass
        pr("count-lines-code-ko", assertions={"x": False}),  # ko fail
    ]
    s = analyze([rep(results)]).backends[0].slices
    entry = next(t for t in s.per_target if t[0] == "count-lines-code")
    assert entry == ["count-lines-code", True, False, -1]
    assert s.by_category["text-processing"]["ko_minus_en"] == -100.0


def test_slices_partial_export_does_not_fake_a_regression():
    # A subset sweep with only the EN prompt must NOT score the absent KO as a failure.
    s = analyze([rep([pr("count-lines-code-en")])]).backends[0].slices
    assert not any(t[0] == "count-lines-code" for t in s.per_target)  # incomplete pair skipped
    assert s.by_category["text-processing"]["ko_minus_en"] == 0.0  # no gap, not -100


# --- Metric #5: stability ----------------------------------------------------

def _run(verdicts: dict[str, bool], commands: dict[str, str | None] | None = None):
    commands = commands or {}
    results = [
        pr(pid, command=commands.get(pid, "ls"),
           assertions={"x": passed}, assertions_pass=passed)
        for pid, passed in verdicts.items()
    ]
    return rep(results)


def test_stability_flip_rate():
    runs = [
        _run({"a-en": True, "b-en": True}),
        _run({"a-en": True, "b-en": True}),
        _run({"a-en": False, "b-en": True}),  # a-en flips pass->fail
    ]
    st = analyze_stability(runs)
    assert st.flipped == ["a-en"]
    assert st.flip_rate == 50.0  # 1 of 2 prompts


def test_stability_command_flip_invariant():
    # Realistic data (verdict is a function of the command): a-en drifts command with a
    # stable verdict; b-en flips verdict, which necessarily changed its command too.
    runs = [
        _run({"a-en": True, "b-en": True}, {"a-en": "wc -l", "b-en": "ls"}),
        _run({"a-en": True, "b-en": True}, {"a-en": "wc -l", "b-en": "ls"}),
        _run({"a-en": True, "b-en": False}, {"a-en": "find . | wc", "b-en": "ls -Z"}),
    ]
    st = analyze_stability(runs)
    assert st.flipped == ["b-en"]
    assert set(st.command_flipped) == {"a-en", "b-en"}
    assert set(st.flipped) <= set(st.command_flipped)  # invariant flip_rate <= command_flip_rate
    assert st.flip_rate <= st.command_flip_rate


def test_stability_min_n():
    with pytest.raises(ValueError):
        analyze_stability([_run({"a-en": True}), _run({"a-en": True})])


def test_stability_prompt_mismatch():
    runs = [_run({"a-en": True}), _run({"a-en": True}), _run({"b-en": True})]
    with pytest.raises(ValueError):
        analyze_stability(runs)


# --- golden + serialization + CLI --------------------------------------------

@pytest.mark.skipif(not REAL_RUN.is_file(), reason="published 2026-07-05 run absent")
def test_analyze_real_export():
    reports = load_reports(REAL_RUN)
    a = analyze(reports, run_date="2026-07-05")
    by = {b.backend: b for b in a.backends}

    # Snapshot of the published 2026-07-05 run. The intent layer reads each row's
    # execution-oracle verdict, so these counts track the re-scored oracle grades
    # (18 covered targets) in that run's results.json.
    g = by["local-gemma4-12b-qat"]
    assert g.delivery.rate == 90.0
    assert g.delivery.faults == {"unescaped_backslash": 4, "malformed_json": 1}
    assert g.delivery.escape_fault_pct == 8.0
    assert g.slices.escape_heavy["delivery"] == pytest.approx(50.0)
    assert g.layers.first_failing.get("intent") == 13
    assert "loop-backup-copies-en" in g.layers.intent_failures

    s = by["sonnet5-low"]
    assert s.delivery.rate == 100.0
    assert s.layers.first_failing == {"pass": 46, "intent": 4}
    assert "yaml-image-policy-en" in s.layers.intent_failures


def test_analysis_json_serializable():
    results = [pr("a-en"), dropped("c-en", "could not decode completion: Invalid \\escape: x")]
    payload = analysis_to_dict(analyze([rep(results)], run_date="2026-01-01"))
    assert json.dumps(payload)  # no dataclass/tuple-key/enum leakage


def test_render_analysis_smoke():
    out = render_analysis(analyze([rep([pr("a-en")])], run_date="2026-01-01"))
    assert "delivery" in out and "layers" in out


def test_cli_dispatch(tmp_path):
    export([rep([pr("a-en"), pr("b-en", assertions={"uses:x": False})])], tmp_path / "results.json")
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / "analysis.json").is_file()


def test_cli_missing_dir(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 1
    assert "analyze:" in capsys.readouterr().err
