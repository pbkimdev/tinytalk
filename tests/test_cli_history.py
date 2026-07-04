"""Capture hooks + sink + recall porcelain (spec-A3).

Every `tt` invocation on the request path appends exactly one faithful history
record across the outcome taxonomy (ok / cache_hit / no_command / transport_error);
capture is best-effort (never disturbs stdout/stderr/exit); `tt history --porcelain`
feeds the widget deduped recent commands NUL-delimited.
"""

from __future__ import annotations

import json

import pytest

import tinytalk.provider.factory as factory
from tinytalk.cli import main
from tinytalk.history import HistoryRecord, HistoryStore
from tinytalk.provider.base import Capabilities, Completion, ProviderError, Usage
from tests.stubs import StubProvider

PAYLOAD = {
    "command": "ls -lhS",
    "explanation": "list files by size",
    "danger": "safe",
    "confidence": 0.9,
    "needs": ["ls"],
}

# Backend named "stub" so it matches StubProvider.name — the record resolves its model
# via config.backend(result.backend).model, exactly the pinned rule.
CONFIG = """\
[defaults]
backend = "stub"

[backends.stub]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "test-model"

[cache]
enabled = false

[prices."test-model"]
input_per_mtok = 1.0
output_per_mtok = 2.0
"""

UNPRICED = """\
[defaults]
backend = "stub"

[backends.stub]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "test-model"

[cache]
enabled = false
"""

# Usage(10, 5, 15) at input=$1/Mtok, output=$2/Mtok → 10*1e-6 + 5*2e-6 = 2e-5.
EXPECTED_COST = 2e-5


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolate the history store (and the shell-context length) from the real machine."""
    directory = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(directory))
    monkeypatch.delenv("TT_SESSION_CONTEXT", raising=False)
    return directory


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    return str(path)


def _install(monkeypatch, provider):
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)
    return provider


def _completing(monkeypatch, usage=Usage(10, 5, 15), payload=PAYLOAD):
    return _install(
        monkeypatch,
        StubProvider(
            Capabilities(), lambda req, i: Completion(text=json.dumps(payload), usage=usage)
        ),
    )


def _records(n=50):
    return HistoryStore().read_recent(n)


def test_successful_run_appends_one_ok_record(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch)
    assert main(["--config", config_path, "list", "files", "by", "size"]) == 0
    assert capsys.readouterr().out.strip() == "ls -lhS"  # capture never touches stdout

    records = _records()
    assert len(records) == 1  # exactly one record per invocation
    rec = records[0]
    assert rec.outcome == "ok"
    assert rec.command == "ls -lhS"
    assert rec.prompt == "list files by size"
    assert rec.mode == "plain"
    assert rec.backend == "stub"
    assert rec.model == "test-model"  # resolved via config.backend(result.backend).model
    assert rec.provider_kind == "openai-compat"
    assert rec.posture == "local"
    assert rec.tier == 1
    assert rec.escalated is False
    assert rec.cache_hit is False
    assert rec.danger_model == "safe"
    assert rec.danger_final == "safe"
    assert rec.context_chars == 0
    assert rec.usage["total_tokens"] == 15
    assert rec.cost_usd == pytest.approx(EXPECTED_COST)
    assert rec.billable is True
    assert rec.prompt_surface_hash != ""  # a fresh assembled surface is hashed


def test_widget_mode_is_recorded_as_widget(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch)
    assert main(["--config", config_path, "--widget", "list", "files"]) == 0
    rec = _records()[0]
    assert rec.mode == "widget"  # the product's primary UI surface
    assert rec.outcome == "ok"


def test_shell_context_persists_length_not_content(state_dir, config_path, monkeypatch, capsys):
    # Lean persistence: the record stores the context LENGTH, never its content.
    context = "PRIOR_SHELL_CONTEXT: cd /var/tmp && echo marker-xyzzy"
    monkeypatch.setenv("TT_SESSION_CONTEXT", context)
    _completing(monkeypatch)
    assert main(["--config", config_path, "list", "files"]) == 0

    rec = _records()[0]
    assert rec.context_chars == len(context) > 0  # the redacted context is stored as a length

    segment = next((state_dir / "tinytalk" / "history").glob("*.jsonl"))
    assert context not in segment.read_text("utf-8")  # never the content itself


def test_cost_breakdown_buckets_sum_to_cost(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch)
    assert main(["--config", config_path, "list", "files"]) == 0
    rec = _records()[0]
    assert set(rec.cost_breakdown) == {"fresh", "cached", "write", "output"}
    assert sum(rec.cost_breakdown.values()) == pytest.approx(rec.cost_usd)  # pinned invariant


def test_attempts_detail_enriched_with_model_and_cost(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch)
    assert main(["--config", config_path, "list", "files"]) == 0
    detail = _records()[0].attempts_detail
    assert len(detail) == 1
    entry = detail[0]
    assert entry["tier"] == 1
    assert entry["backend"] == "stub"
    assert entry["model"] == "test-model"  # per-attempt model enrichment
    assert entry["result"] == "ok"
    assert entry["format_reached"] == "text"
    assert entry["cost_usd"] == pytest.approx(EXPECTED_COST)  # per-attempt cost enrichment


def test_headline_cost_is_per_attempt_summed_under_mixed_price():
    """DECISIONS §Usage fidelity: cost is computed per-attempt (exact under escalation), then
    summed. Under a mixed-price escalation — free local T1, priced cloud T2 — the headline
    cost must bill each attempt at its OWN backend's rate, never all the accumulated tokens at
    the winning (cloud) rate."""
    from tinytalk.cli import _enrich_attempts
    from tinytalk.config import BackendConfig, Config, Price
    from tinytalk.cost import cost
    from tinytalk.engine import AttemptDetail
    from tinytalk.provider.base import ResponseFormat

    local = BackendConfig(name="local", kind="openai-compat", model="m-local", base_url="http://x")
    cloud = BackendConfig(name="cloud", kind="anthropic-compat", model="m-cloud")
    config = Config(
        default_backend="local",
        backends={"local": local, "cloud": cloud},
        prices={"m-cloud": Price(input_per_mtok=3.0, output_per_mtok=15.0)},  # m-local unpriced
    )
    detail = (
        AttemptDetail(
            ResponseFormat.TEXT, Usage(100, 10, 110), 5, "format_error", tier=1, backend="local"
        ),
        AttemptDetail(
            ResponseFormat.TOOL_CALL, Usage(200, 20, 220), 8, "ok", tier=2, backend="cloud"
        ),
    )
    entries, breakdown = _enrich_attempts(config, cloud, detail)
    cost_usd = round(sum(breakdown.values()), 6)

    assert entries[0]["cost_usd"] == 0.0  # free local attempt costs nothing
    assert entries[1]["cost_usd"] == pytest.approx(0.0009)  # 200*3e-6 + 20*15e-6
    assert cost_usd == pytest.approx(0.0009)  # headline == per-attempt sum
    assert sum(e["cost_usd"] for e in entries) == pytest.approx(cost_usd)  # buckets-sum invariant
    # ...and NOT the naive "all accumulated tokens at the winning cloud rate".
    naive = cost(Usage(300, 30, 330), config.price("m-cloud"))
    assert naive == pytest.approx(0.00135)
    assert cost_usd != pytest.approx(naive)


def test_json_mode_is_recorded_as_json(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch)
    assert main(["--config", config_path, "--json", "list", "files"]) == 0
    rec = _records()[0]
    assert rec.mode == "json"
    assert rec.outcome == "ok"


def test_cache_hit_is_recorded_and_not_billable(state_dir, tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "cache"
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.replace("enabled = false", f'enabled = true\ndir = "{cache_dir}"'))
    _completing(monkeypatch)

    assert main(["--config", str(config), "list", "files"]) == 0  # T1 populates the cache
    assert main(["--config", str(config), "list", "files"]) == 0  # T0 cache hit

    records = _records()
    assert len(records) == 2  # one record per invocation, hit included
    hit = records[0]
    assert hit.outcome == "cache_hit"
    assert hit.cache_hit is True
    assert hit.tier == 0
    assert hit.cost_usd == 0.0
    assert hit.billable is False  # a cache hit is never billable
    assert hit.prompt_surface_hash == ""  # a pure cache hit assembles no surface


def test_unpriced_model_is_not_billable(state_dir, tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text(UNPRICED)
    _completing(monkeypatch)  # tokens spent, but the model has no price table
    assert main(["--config", str(config), "list", "files"]) == 0
    rec = _records()[0]
    assert rec.usage["total_tokens"] == 15
    assert rec.billable is False


def test_zero_token_success_is_not_billable(state_dir, config_path, monkeypatch, capsys):
    _completing(monkeypatch, usage=Usage())  # priced model, but no tokens reported
    assert main(["--config", config_path, "list", "files"]) == 0
    rec = _records()[0]
    assert rec.outcome == "ok"
    assert rec.usage["total_tokens"] == 0
    assert rec.billable is False


def test_no_command_outcome_is_recorded(state_dir, config_path, monkeypatch, capsys):
    _install(
        monkeypatch,
        StubProvider(
            Capabilities(), lambda req, i: Completion(text="not a command", usage=Usage(10, 5, 15))
        ),
    )
    assert main(["--config", config_path, "list", "files"]) == 1
    records = _records()
    assert len(records) == 1  # exactly one record, even on the error write site
    rec = records[0]
    assert rec.outcome == "no_command"
    assert rec.error_kind == "no_command"
    assert rec.command == ""  # nothing reusable was produced
    assert rec.problems  # the failing attempts are recorded
    assert rec.escalated is True  # fell through to T2
    assert all(e["result"] == "format_error" for e in rec.attempts_detail)


def test_transport_fault_via_generic_exception_is_recorded(
    state_dir, config_path, monkeypatch, capsys
):
    def boom(request, i):
        raise RuntimeError("kaboom")

    _install(monkeypatch, StubProvider(Capabilities(), boom))
    assert main(["--config", config_path, "list", "files"]) == 1
    records = _records()
    assert len(records) == 1  # the generic-fault write site appends exactly one record
    rec = records[0]
    assert rec.outcome == "transport_error"
    assert rec.error_kind == "transport"
    assert rec.billable is False
    assert any("kaboom" in p for p in rec.problems)


def test_provider_error_is_recorded_as_transport(state_dir, config_path, monkeypatch, capsys):
    def down(request, i):
        raise ProviderError("backend unreachable")

    _install(monkeypatch, StubProvider(Capabilities(), down))
    assert main(["--config", config_path, "list", "files"]) == 1
    records = _records()
    assert len(records) == 1  # a provider error appends exactly one transport record
    rec = records[0]
    assert rec.outcome == "transport_error"
    assert rec.error_kind == "transport"
    assert rec.usage["total_tokens"] == 0
    assert rec.billable is False


def test_config_error_writes_no_record(state_dir, tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml"), "list", "files"]) == 1
    assert "no config found" in capsys.readouterr().err
    assert _records() == []  # a ConfigError never appends a record


def test_config_load_oserror_returns_1_and_writes_no_record(state_dir, tmp_path, capsys):
    # `--config <dir>` → IsADirectoryError inside load_config, which is NOT a ConfigError, so
    # it reaches the generic handler before config/backend_cfg are bound. It must degrade to a
    # clean exit 1 (no UnboundLocalError traceback) and, like any config-load failure, write
    # no record — the backend was never known.
    config_dir = tmp_path / "config_is_a_dir"
    config_dir.mkdir()
    assert main(["--config", str(config_dir), "list", "files"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "UnboundLocalError" not in err
    assert _records() == []


def test_capture_failure_is_best_effort(state_dir, config_path, monkeypatch, capsys):
    # A plain file where the history dir should be → the sink's mkdir/open raise OSError.
    (state_dir / "tinytalk").mkdir(parents=True)
    (state_dir / "tinytalk" / "history").write_text("")
    _completing(monkeypatch)
    assert main(["--config", config_path, "list", "files"]) == 0  # exit unchanged
    captured = capsys.readouterr()
    assert captured.out.strip() == "ls -lhS"  # stdout unchanged
    # stderr is the normal success line only — no capture traceback leaks onto it.
    assert captured.err.strip() == "# list files by size  [danger: safe]"
    assert _records() == []  # the record could not be written, and that is fine


def test_porcelain_empty_store_prints_nothing(state_dir, capsys):
    assert main(["history", "--porcelain"]) == 0
    assert capsys.readouterr().out == ""


def test_porcelain_emits_deduped_commands_nul_delimited(state_dir, capsys):
    store = HistoryStore()
    store.append(HistoryRecord(command="git status", ts="2026-07-04T10:00:00-07:00"))
    store.append(HistoryRecord(command="", ts="2026-07-04T10:01:00-07:00"))  # failed run
    store.append(HistoryRecord(command="ls -la", ts="2026-07-04T10:02:00-07:00"))
    store.append(
        HistoryRecord(command="GIT   status", ts="2026-07-04T10:03:00-07:00")
    )  # newest dup

    assert main(["history", "--porcelain"]) == 0
    out = capsys.readouterr().out
    assert "\0" in out
    commands = [c for c in out.split("\0") if c]
    # newest-first, exact-normalized dedup keeps the newest "GIT   status", empty skipped.
    assert commands == ["GIT   status", "ls -la"]
