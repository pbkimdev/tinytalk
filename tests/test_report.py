"""HTML report renderer (#99): fixture rendering, self-containment, JSON round-trip."""

from __future__ import annotations

from tinytalk.eval.report import RunMeta, _score_py, load_reports, render_report
from tinytalk.eval.runner import BackendReport, PromptResult, export

META = RunMeta(run_date="2026-07-03", machine="Apple M5 Max")


def result(
    prompt_id,
    lang,
    target,
    *,
    passing=True,
    cached=0,
    error=None,
    cost=0.001,
    latency=1.0,
    oracle_pass=None,
):
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
        oracle_pass=oracle_pass,
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


def test_sonnet_cost_shows_regular_plus_intro_discount():
    page = render_report([fixture_reports()[0]], META)
    assert 'class="cost-main"' in page
    assert '+ <small class="cost-note">(' in page
    assert "% intro)</small>" in page


def test_pass_rate_chart_has_en_ko_bars_and_average_text():
    page = render_report(fixture_reports(), META)
    # Two language bars per model, not a third overall row in the pass-rate chart.
    assert page.count(" EN: ") == 3
    assert page.count(" KO: ") == 3
    # Average is text, vertically centered in the EN+KO band — not a filled bar.
    assert 'height="34"' not in page.split("<svg")[1].split("</svg>")[0]
    assert 'dominant-baseline="central"' in page
    assert 'font-size="22"' in page


def test_score_axis_expands_sixty_to_one_hundred():
    mt, ph = 16, 358
    assert _score_py(100, mt, ph) == mt
    assert _score_py(60, mt, ph) == mt + ph * 0.8
    assert _score_py(0, mt, ph) == mt + ph
    assert _score_py(80, mt, ph) == mt + ph * 0.4


def test_score_cost_chart_uses_expanded_axis_ticks():
    page = render_report(fixture_reports(), META)
    assert "expanded from 60% upward" in page
    assert "Pareto frontier" in page
    assert ">60%</text>" in page or ">60%<" in page


def test_danger_label_column_has_tooltip():
    page = render_report(fixture_reports(), META)
    assert 'class="danger" title="' in page
    assert "Scored separately from strict pass" in page


def test_report_includes_foldable_test_suite_before_all_numbers():
    page = render_report(fixture_reports(), META)
    assert page.index("<h2>Test suite</h2>") < page.index("<h2>All numbers</h2>")
    assert '<details class="suite-item">' in page
    assert "suite-expected" in page
    assert "suite-outcomes" in page
    assert "suite-caret-closed" in page
    assert "▼" in page


def test_oracle_report_includes_section_key_and_table_column():
    report = BackendReport(
        "oracle-backend",
        "oracle-model",
        results=[
            result(
                "disk-usage-top-en",
                "en",
                "disk-usage-top",
                oracle_pass=True,
            ),
            result(
                "disk-usage-top-ko",
                "ko",
                "disk-usage-top",
                oracle_pass=False,
            ),
        ],
    )
    page = render_report([report], META)
    assert "<h2>Text grader vs. execution oracle</h2>" in page
    assert "<strong>Text grader</strong>" in page
    assert "<strong>Execution oracle</strong>" in page
    assert 'class="oracle" title="' in page
    assert ">Oracle</th>" in page


def test_scatter_charts_show_model_label_on_hover():
    page = render_report(fixture_reports(), META)
    assert page.count('class="chart-point"') == 6  # 3 models × 2 scatter charts
    assert "chart-point-label" in page
    assert "chart-point:hover" in page


def test_render_contains_charts_table_and_no_external_urls():
    page = render_report(fixture_reports(), META)
    assert page.count("<svg") == 3
    for name in ("Claude Sonnet 5", "gemma 4 26B A4B", "gemma 4 E4B"):
        assert name in page
    assert "http://" not in page and "https://" not in page  # fully self-contained
    assert "Cached" in page  # table column present
    assert "†" in page  # local proxy marker + its fine-print explanation
    assert page.index("Claude Sonnet 5") < page.index("gemma 4 26B A4B")  # ranked best-first


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
