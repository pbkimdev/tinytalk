"""HTML report renderer (#99): fixture rendering, self-containment, JSON round-trip."""

from __future__ import annotations

from tinytalk.eval.report import RunMeta, load_reports, render_report
from tinytalk.eval.runner import BackendReport, PromptResult, export

META = RunMeta(run_date="2026-07-03", machine="Apple M5 Max")


def result(prompt_id, lang, target, *, passing=True, cached=0, error=None, cost=0.001, latency=1.0):
    ok = passing and error is None
    return PromptResult(
        prompt_id=prompt_id,
        lang=lang,
        target=target,
        command="du -h" if error is None else None,
        error=error,
        format_ok=error is None,
        parses=ok,
        binaries_exist=ok,
        assertions={"uses:du": ok},
        assertions_pass=ok,
        danger="safe",
        danger_expected="safe",
        danger_correct=ok,
        tier=1,
        prompt_tokens=100,
        completion_tokens=50,
        cached_prompt_tokens=cached,
        latency_s=latency,
        cost_usd=cost,
    )


def fixture_reports():
    cloud = BackendReport(
        "sonnet5-low",
        "claude-sonnet-5",
        local=False,
        results=[
            result("disk-usage-top-en", "en", "disk-usage-top", cached=40, cost=0.01, latency=2.0),
            result("disk-usage-top-ko", "ko", "disk-usage-top", cost=0.01, latency=2.2),
        ],
    )
    local = BackendReport(
        "gemma-26b",
        "gemma-4-26B-A4B",
        local=True,
        results=[
            result("disk-usage-top-en", "en", "disk-usage-top", cost=0.0, latency=6.0),
            result("disk-usage-top-ko", "ko", "disk-usage-top", passing=False, cost=0.0, latency=6.5),
        ],
    )
    flaky = BackendReport(
        "e4b",
        "gemma-4-E4B",
        local=True,
        results=[
            result("disk-usage-top-en", "en", "disk-usage-top", error="boom", cost=0.0, latency=0.5),
            result("disk-usage-top-ko", "ko", "disk-usage-top", passing=False, cost=0.0, latency=3.0),
        ],
    )
    return [cloud, local, flaky]


def test_render_contains_charts_table_and_no_external_urls():
    page = render_report(fixture_reports(), META)
    assert page.count("<svg") == 3
    for name in ("sonnet5-low", "gemma-26b", "e4b", "claude-sonnet-5"):
        assert name in page
    assert "http://" not in page and "https://" not in page  # fully self-contained
    assert "cached" in page  # table column present
    assert "†" in page  # local proxy marker + its fine-print explanation
    assert page.index("sonnet5-low") < page.index("gemma-26b")  # ranked best-first


def test_render_is_deterministic():
    assert render_report(fixture_reports(), META) == render_report(fixture_reports(), META)


def test_json_export_round_trip(tmp_path):
    reports = fixture_reports()
    path = tmp_path / "results.json"
    export(reports, path)
    loaded = load_reports(path)
    assert [r.backend for r in loaded] == [r.backend for r in reports]
    assert [r.local for r in loaded] == [False, True, True]
    assert render_report(loaded, META) == render_report(reports, META)
