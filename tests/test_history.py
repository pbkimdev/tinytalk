"""History store & record model (spec-A1): round-trip, ordering, ids, dedup."""

from __future__ import annotations

import dataclasses
import json
import stat
from pathlib import Path

from tinytalk.history import (
    HistoryRecord,
    HistoryStore,
    dedup,
    dedup_key,
    default_state_dir,
)

FULL = HistoryRecord(
    ts="2026-07-04T10:00:00-07:00",
    latency_ms=1234,
    cwd="/home/me",
    mode="widget",
    backend="local",
    model="qwen3:8b",
    provider_kind="openai-compat",
    posture="local",
    os_fingerprint="Linux-6.1-x86_64",
    language="ko",
    prompt_surface_hash="abc123",
    context_chars=42,
    prompt="list files by size",
    command="ls -lhS",
    explanation="list by size",
    danger_model="safe",
    danger_final="safe",
    confidence=0.9,
    needs=("ls",),
    tier=1,
    attempts=1,
    escalated=False,
    cache_hit=False,
    outcome="ok",
    billable=True,
    usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    cost_usd=0.0012,
    cost_breakdown={"fresh": 0.001, "cached": 0.0, "write": 0.0, "output": 0.0002},
    attempts_detail=[{"tier": 1, "backend": "local", "cost_usd": 0.0012}],
    error_kind=None,
    problems=(),
)


def test_default_state_dir_respects_xdg(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert default_state_dir() == Path("/xdg/state/tinytalk")


def test_default_state_dir_falls_back(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    got = default_state_dir()
    assert got.name == "tinytalk"
    assert got.parent.parts[-2:] == (".local", "state")


def test_record_roundtrips_through_json():
    restored = HistoryRecord.from_dict(json.loads(json.dumps(FULL.to_dict())))
    assert restored == FULL
    assert isinstance(restored.needs, tuple)  # JSON array folded back to a tuple


def test_from_dict_tolerates_missing_and_extra_keys():
    record = HistoryRecord.from_dict({"command": "pwd", "bogus": 1})
    assert record.command == "pwd"
    assert record.id == 0 and record.usage == {}  # defaults fill the gaps


def test_append_assigns_id_and_timestamp(tmp_path):
    store = HistoryStore(tmp_path)
    stored = store.append(HistoryRecord(command="pwd"))
    assert stored.id == 1
    assert stored.ts  # stamped when the caller left it blank
    assert store.read_recent(10)[0].command == "pwd"


def test_read_recent_newest_first(tmp_path):
    store = HistoryStore(tmp_path)
    for cmd in ("first", "second", "third"):
        store.append(HistoryRecord(command=cmd, ts="2026-07-04T10:00:00-07:00"))
    recent = store.read_recent(10)
    assert [r.command for r in recent] == ["third", "second", "first"]
    assert [r.id for r in recent] == [3, 2, 1]


def test_read_recent_respects_n(tmp_path):
    store = HistoryStore(tmp_path)
    for i in range(5):
        store.append(HistoryRecord(command=f"c{i}", ts="2026-07-04T10:00:00-07:00"))
    assert len(store.read_recent(2)) == 2
    assert store.read_recent(0) == []
    assert len(store.read_recent(99)) == 5


def test_ids_monotonic_across_segment_boundary(tmp_path):
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="a", ts="2026-07-03T23:00:00-07:00"))
    store.append(HistoryRecord(command="b", ts="2026-07-04T00:30:00-07:00"))
    store.append(HistoryRecord(command="c", ts="2026-07-04T01:00:00-07:00"))
    assert {p.name for p in tmp_path.joinpath("history").glob("*.jsonl")} == {
        "2026-07-03.jsonl",
        "2026-07-04.jsonl",
    }
    recent = store.read_recent(10)
    assert [r.id for r in recent] == [3, 2, 1]  # strictly increasing across the day boundary
    assert [r.command for r in recent] == ["c", "b", "a"]


def test_segment_file_is_0600(tmp_path):
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="pwd", ts="2026-07-04T10:00:00-07:00"))
    segment = tmp_path / "history" / "2026-07-04.jsonl"
    assert stat.S_IMODE(segment.stat().st_mode) == 0o600


def test_read_recent_skips_corrupt_lines(tmp_path):
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="good", ts="2026-07-04T10:00:00-07:00"))
    segment = tmp_path / "history" / "2026-07-04.jsonl"
    with segment.open("a") as fh:
        fh.write("not json\n")
    commands = [r.command for r in store.read_recent(10)]
    assert commands == ["good"]  # the garbage line is skipped, not fatal


def test_append_is_best_effort_on_oserror(tmp_path):
    # A plain file where the history dir should be → mkdir/open raise OSError.
    (tmp_path / "history").write_text("")
    store = HistoryStore(tmp_path)
    stored = store.append(HistoryRecord(command="pwd"))  # must not raise
    assert stored.command == "pwd"
    assert store.read_recent(10) == []


def test_empty_store_reads_nothing(tmp_path):
    assert HistoryStore(tmp_path).read_recent(10) == []


def test_dedup_key_normalizes_whitespace_and_case():
    assert dedup_key("  LS   -la ") == dedup_key("ls -la")
    assert dedup_key("ls -la") != dedup_key("ls -lh")


def test_dedup_keeps_newest_per_command():
    records = [
        HistoryRecord(id=3, command="ls   -la"),  # newest
        HistoryRecord(id=2, command="pwd"),
        HistoryRecord(id=1, command="LS -la"),  # older duplicate of id=3 after normalization
    ]
    kept = dedup(records)
    assert [r.id for r in kept] == [3, 2]  # first-seen (newest) survives, dupe dropped


def test_full_record_roundtrips_through_store(tmp_path):
    # All fields survive append -> read_recent, not just command/id.
    store = HistoryStore(tmp_path)
    stored = store.append(FULL)
    got = store.read_recent(1)[0]
    assert got == dataclasses.replace(FULL, id=stored.id, ts=stored.ts)


def test_record_with_line_separators_survives_read(tmp_path):
    # U+2028/U+2029/U+0085 are emitted literally by json.dumps(ensure_ascii=False)
    # but str.splitlines() splits on them; reading one JSONL line must not lose
    # such a record (realistic vector: a paste from a web/PDF/word processor).
    store = HistoryStore(tmp_path)
    store.append(
        HistoryRecord(
            command="echo hi",
            prompt="para\u2028break\u2029end\u0085x",
            ts="2026-07-04T10:00:00-07:00",
        )
    )
    store.append(HistoryRecord(command="pwd", ts="2026-07-04T10:01:00-07:00"))
    recent = store.read_recent(10)
    assert [r.command for r in recent] == ["pwd", "echo hi"]  # neither record lost
    assert [r.id for r in recent] == [2, 1]  # id seeding unaffected by the separators
    assert recent[1].prompt == "para\u2028break\u2029end\u0085x"  # preserved verbatim


def test_read_recent_respects_n_across_segment_boundary(tmp_path):
    # n smaller than the total spanning two segments: read the newest segment,
    # cross into the older one, then truncate mid-segment.
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="a", ts="2026-07-03T09:00:00-07:00"))
    store.append(HistoryRecord(command="b", ts="2026-07-03T10:00:00-07:00"))
    store.append(HistoryRecord(command="c", ts="2026-07-04T09:00:00-07:00"))
    recent = store.read_recent(2)
    assert [r.command for r in recent] == ["c", "b"]  # "a" (id 1) truncated away
    assert [r.id for r in recent] == [3, 2]


def test_next_id_seeds_past_all_corrupt_newest_segment(tmp_path):
    # The highest id lives in an older segment; a realistic partial write leaves
    # the newest-dated segment entirely unparseable. Seeding must still clear it.
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="a", ts="2026-07-03T09:00:00-07:00"))
    store.append(HistoryRecord(command="b", ts="2026-07-03T10:00:00-07:00"))
    (tmp_path / "history" / "2026-07-04.jsonl").write_text("garbage\nnot json\n")
    stored = store.append(HistoryRecord(command="c", ts="2026-07-04T11:00:00-07:00"))
    assert stored.id == 3  # global max (2) + 1, not 1 from the empty newest segment
    ids = [r.id for r in store.read_recent(10)]
    assert len(ids) == len(set(ids))  # no id collision with the older segment


def test_next_id_survives_corrupt_trailing_line(tmp_path):
    # A corrupt trailing line in the ACTIVE segment must not reset id seeding.
    store = HistoryStore(tmp_path)
    store.append(HistoryRecord(command="a", ts="2026-07-04T09:00:00-07:00"))
    segment = tmp_path / "history" / "2026-07-04.jsonl"
    with segment.open("a") as fh:
        fh.write("not valid json\n")
    stored = store.append(HistoryRecord(command="b", ts="2026-07-04T10:00:00-07:00"))
    assert stored.id == 2  # tail scan skips the corrupt line back to id 1
    assert [r.id for r in store.read_recent(10)] == [2, 1]
