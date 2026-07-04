"""Bench publish workflow (#101): re-score, merge, and CLI dispatch."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from tinytalk.cli import main
from tinytalk.eval.publish import (
    bench_data_dir,
    load_backend_report,
    load_run_meta,
    merge_backend,
    parse_run_date,
    rescore_row,
    resolve_paths,
)
from tinytalk.eval.runner import BackendReport, PromptResult
from tinytalk.eval.suite import SUITE


def _row(prompt_id: str, *, command: str | None = "du -h", parses: bool = True) -> PromptResult:
    prompt = next(p for p in SUITE if p.id == prompt_id)
    ok = command is not None and parses
    assertions = {a: ok for a in prompt.assertions}
    return PromptResult(
        prompt_id=prompt_id,
        lang=prompt.lang,
        target=prompt.target,
        command=command,
        format_ok=command is not None,
        parses=ok,
        binaries_exist=ok,
        assertions=assertions,
        assertions_pass=ok and bool(assertions),
        danger="safe",
        danger_expected=prompt.expected_danger,
        danger_correct=ok,
    )


def test_bench_data_dir_uses_iso_date():
    assert bench_data_dir("2026-07-03") == Path("docs/bench/2026-07-03")


def test_parse_run_date_accepts_iso_date_only():
    assert parse_run_date("2026-07-03") == "2026-07-03"
    assert parse_run_date("2026-07") is None


def test_resolve_paths_defaults_to_bench_data_dir():
    data_dir, run_date = resolve_paths(None, "2026-07-03")
    assert run_date == "2026-07-03"
    assert data_dir == bench_data_dir("2026-07-03")


def test_resolve_paths_infers_run_date_from_data_dir():
    data_dir, run_date = resolve_paths(bench_data_dir("2026-07-03"), None)
    assert run_date == "2026-07-03"
    assert data_dir == bench_data_dir("2026-07-03")


def test_load_run_meta_from_json(tmp_path):
    tmp_path.joinpath("run_meta.json").write_text(
        json.dumps(
            {
                "run_date": "2026-07-03",
                "machine": "test machine",
                "pricing_notes": ["note one"],
            }
        ),
        encoding="utf-8",
    )
    meta = load_run_meta(tmp_path, "2026-07-04")
    assert meta.run_date == "2026-07-03"
    assert meta.machine == "test machine"
    assert meta.pricing_notes == ("note one",)


def test_rescore_row_without_command_is_unchanged():
    row = _row(SUITE[0].id, command=None, parses=False)
    assert rescore_row(row) is row


def test_merge_backend_keeps_rescored_rows_and_appends_new(monkeypatch):
    subset = SUITE[:2]
    kept_id, new_id = subset[0].id, subset[1].id
    monkeypatch.setattr("tinytalk.eval.publish.SUITE", subset)
    monkeypatch.setattr("tinytalk.eval.publish._SUITE_IDS", frozenset(p.id for p in subset))
    monkeypatch.setattr(
        "tinytalk.eval.publish._SUITE_ORDER", {p.id: i for i, p in enumerate(subset)}
    )
    monkeypatch.setattr("tinytalk.eval.publish._PROMPTS", {p.id: p for p in subset})

    old = BackendReport("alpha", "model-a", results=[_row(kept_id), _row(SUITE[2].id)])
    new = BackendReport("alpha", "model-a", results=[_row(new_id)])
    merged = merge_backend(old, new)
    assert [r.prompt_id for r in merged.results] == [kept_id, new_id]


def test_load_backend_report_rejects_empty_export(tmp_path):
    path = tmp_path / "alpha.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="no backend reports"):
        load_backend_report(path)


def test_publish_empty_export_returns_friendly_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "tinytalk.eval.publish.backends_from_config", lambda _path: ("alpha",)
    )
    (tmp_path / "alpha.json").write_text("[]", encoding="utf-8")
    (tmp_path / "v3new-alpha.json").write_text("[]", encoding="utf-8")

    assert main(["eval", "publish", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "publish:" in err
    assert "no backend reports" in err


def test_eval_publish_help():
    with pytest.raises(SystemExit) as exc:
        main(["eval", "publish", "--help"])
    assert exc.value.code == 0
