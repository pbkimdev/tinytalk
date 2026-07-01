"""Eval harness (#32): assertion DSL, scoring, leaderboard, export, e2e over stubs."""

from __future__ import annotations

import json

import pytest

import clite.eval.runner as runner_mod
from clite.config import load_config
from clite.eval.runner import export, render_leaderboard, render_matrix, run_eval
from clite.eval.suite import SUITE, check_assertion
from clite.provider.base import Capabilities, Completion, Usage
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


def test_suite_shape():
    assert len(SUITE) == 25
    assert len({p.id for p in SUITE}) == 25
    assert any(p.expected_danger == "destructive" for p in SUITE)
    for p in SUITE:
        assert p.assertions, p.id
        for a in p.assertions:
            check_assertion(a, "ls")  # every assertion parses


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
                text=payload("du -h -d1 . | sort -hr | head -20"), usage=Usage(100, 50, 150)
            ),
        )

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)


def test_end_to_end_eval_over_two_backends(config, stub_backends, tmp_path):
    reports = run_eval(
        config,
        ["alpha", "beta"],
        prompt_ids=["disk-usage-top", "grep-todo"],
        progress=False,
    )
    assert [r.backend for r in reports] == ["alpha", "beta"]
    alpha = reports[0]
    assert alpha.model == "model-a"
    assert len(alpha.results) == 2

    by_id = {r.prompt_id: r for r in alpha.results}
    disk = by_id["disk-usage-top"]
    assert disk.format_ok and disk.parses and disk.binaries_exist
    assert disk.assertions_pass
    assert disk.danger == "safe" and disk.danger_correct
    assert disk.tier == 1
    assert disk.prompt_tokens == 100
    # cost from the price table: 100×1.0/1e6 + 50×2.0/1e6
    assert disk.cost_usd == pytest.approx(0.0002)

    todo = by_id["grep-todo"]
    assert todo.format_ok  # the command is real, it just doesn't grep
    assert not todo.assertions_pass

    assert alpha.format_ok_pct == 100.0
    assert alpha.assertions_pct == 50.0
    assert alpha.total_tokens == 300
    assert alpha.total_cost_usd == pytest.approx(0.0004)


def test_leaderboard_and_matrix_render(config, stub_backends):
    reports = run_eval(config, ["alpha", "beta"], prompt_ids=["disk-usage-top"], progress=False)
    board = render_leaderboard(reports)
    assert "alpha" in board and "beta" in board
    assert "format" in board and "cost" in board
    matrix = render_matrix(reports)
    assert "disk-usage-top" in matrix
    assert "pass" in matrix


def test_export_json_and_csv(config, stub_backends, tmp_path):
    reports = run_eval(config, ["alpha"], prompt_ids=["disk-usage-top"], progress=False)
    json_path = tmp_path / "results.json"
    export(reports, json_path)
    data = json.loads(json_path.read_text())
    assert data[0]["backend"] == "alpha"
    assert data[0]["results"][0]["prompt_id"] == "disk-usage-top"

    csv_path = tmp_path / "results.csv"
    export(reports, csv_path)
    lines = csv_path.read_text().strip().splitlines()
    assert lines[0].startswith("backend,model,prompt_id")
    assert lines[1].startswith("alpha,model-a,disk-usage-top")

    with pytest.raises(ValueError, match="unsupported export"):
        export(reports, tmp_path / "results.xlsx")


def test_unknown_prompt_id_fails_fast(config, stub_backends):
    with pytest.raises(ValueError, match="unknown prompt ids"):
        run_eval(config, ["alpha"], prompt_ids=["nope"], progress=False)


def test_format_failure_is_scored_not_fatal(config, monkeypatch):
    def fake_make_provider(cfg):
        return StubProvider(Capabilities(), lambda request, i: Completion(text="no json at all"))

    monkeypatch.setattr(runner_mod, "make_provider", fake_make_provider)
    reports = run_eval(config, ["alpha"], prompt_ids=["disk-usage-top"], progress=False)
    result = reports[0].results[0]
    assert not result.format_ok
    assert result.error is not None
    assert reports[0].format_ok_pct == 0.0
