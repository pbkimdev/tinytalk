"""Carbon dashboard render (tinytalk/eval/dashboard.py) — data-driven, read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from tinytalk.eval.analyze import analyze
from tinytalk.eval.dashboard import main, render_dashboard
from tinytalk.eval.report import load_reports
from tinytalk.eval.runner import BackendReport, PromptResult, export

REAL_RUN = Path("docs/bench/2026-07-05/results.json")


def _pr(pid, **kw):
    kw.setdefault("target", pid.rsplit("-", 1)[0])
    kw.setdefault("lang", "ko" if pid.endswith("-ko") else "en")
    kw.setdefault("format_ok", True)
    kw.setdefault("parses", True)
    kw.setdefault("binaries_exist", True)
    kw.setdefault("assertions_pass", True)
    return PromptResult(prompt_id=pid, **kw)


def _analysis():
    reports = [BackendReport(backend="sonnet5-low", model="claude-sonnet-5", local=False,
                             results=[_pr("count-lines-code-en"), _pr("count-lines-code-ko")])]
    return analyze(reports, run_date="2026-01-01")


def test_render_is_self_contained_html():
    html = render_dashboard(_analysis())
    assert html.startswith("<!doctype html>")
    assert "IBM Plex" in html  # Carbon type
    assert "Carbon Design System" in html
    assert "Sonnet 5" in html


def test_watch_injects_auto_refresh():
    assert 'http-equiv="refresh"' in render_dashboard(_analysis(), watch=True)
    assert 'http-equiv="refresh"' not in render_dashboard(_analysis(), watch=False)


@pytest.mark.skipif(not REAL_RUN.is_file(), reason="published 2026-07-05 run absent")
def test_render_real_run_shows_headline_figures():
    runs: dict = {}
    for p in sorted(REAL_RUN.parent.glob("stability/*.json")):
        for rep in load_reports(p):
            runs.setdefault(rep.backend, []).append(rep)
    html = render_dashboard(analyze(load_reports(REAL_RUN), runs, run_date="2026-07-05"))
    for token in ("98", "88", "Gemma 12B", "structured-parsing"):  # scores + category
        assert token in html
    if runs:  # stability band + command-rewrite rate only render when repeats are present
        assert "96–100" in html and "52%" in html


def test_main_writes_dashboard(tmp_path):
    export([BackendReport(backend="sonnet5-low", model="claude-sonnet-5", local=False,
                          results=[_pr("count-lines-code-en"), _pr("count-lines-code-ko")])],
           tmp_path / "results.json")
    assert main([str(tmp_path)]) == 0
    out = tmp_path / "dashboard.html"
    assert out.is_file() and out.read_text().startswith("<!doctype html>")


def test_main_missing_dir(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 1
    assert "dashboard:" in capsys.readouterr().err
