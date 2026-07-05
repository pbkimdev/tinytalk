"""Atuin-vs-TinyTalk comparison: fixture set, oracle grading, and report shape."""

from __future__ import annotations

import json

from tinytalk.eval.atuin import (
    ATUIN_LABEL,
    fixture_prompts,
    grade_commands,
    reference_rows,
    render,
)
from tinytalk.eval.oracle import CASES

# Commands verified to pass their fixture's oracle (see fixtures/v4/*).
CORRECT_COUNT_LINES = r"find . -name '*.py' | xargs cat | wc -l"
CORRECT_AWK_GROUP_SUM = r"awk -F, 'NR>1{s[$1]+=$3} END{for(k in s) print k, s[k]}' sales.csv"


def test_fixture_prompts_are_the_oracle_cases():
    prompts = fixture_prompts()
    assert len(prompts) == len(CASES)
    assert {target for target, _ in prompts} == set(CASES)
    assert all(text for _, text in prompts)


def test_grade_commands_scores_by_execution_not_phrasing():
    row = grade_commands(
        "cand",
        {
            "count-lines-code": CORRECT_COUNT_LINES,
            "awk-group-sum": "echo wrong",  # runs, wrong answer -> fail
            "json-extract": "",  # empty -> fail, not crash
            "not-a-fixture": "true",  # unknown target -> ignored
        },
    )
    assert row.passed == frozenset({"count-lines-code"})
    assert "not-a-fixture" not in row.commands  # unknown targets dropped
    assert set(row.commands) == {"count-lines-code", "awk-group-sum", "json-extract"}


def test_reference_rows_regrade_stored_commands(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            [
                {
                    "backend": "modelX",
                    "model": "x",
                    "local": False,
                    "results": [
                        {"target": "awk-group-sum", "lang": "en", "command": CORRECT_AWK_GROUP_SUM},
                        {"target": "awk-group-sum", "lang": "ko", "command": CORRECT_AWK_GROUP_SUM},
                        {"target": "count-lines-code", "lang": "en", "command": "echo nope"},
                        {"target": "json-extract", "lang": "en", "command": None},
                    ],
                }
            ]
        )
    )
    (row,) = reference_rows(results)
    assert row.label == "modelX"
    assert row.passed == frozenset({"awk-group-sum"})  # ko ignored, wrong fails
    assert "json-extract" not in row.commands  # None command dropped


def test_render_pins_atuin_first_and_lists_every_fixture():
    rows = [
        grade_commands("modelX", {"count-lines-code": CORRECT_COUNT_LINES, "awk-group-sum": CORRECT_AWK_GROUP_SUM}),
        grade_commands(ATUIN_LABEL, {"count-lines-code": CORRECT_COUNT_LINES}),
    ]
    out = render(rows)
    assert f"1={ATUIN_LABEL}" in out  # Atuin is column 1 even with a lower score
    for target in CASES:
        assert target in out
