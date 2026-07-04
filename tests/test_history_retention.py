"""Retention sweep (spec-B1): 7-day age prune + 15 MB safety cap, never the active
segment. Every append triggers the sweep, which only ever unlinks OLD files."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import tinytalk.history as history
from tinytalk.history import HistoryRecord, HistoryStore

ACTIVE_TS = "2026-07-04T12:00:00-07:00"
ACTIVE_DATE = datetime.date(2026, 7, 4)


def _hist_dir(root: Path) -> Path:
    return root / "history"


def _make_segment(root: Path, date: datetime.date, *, records: int = 1, pad: int = 0) -> Path:
    """Create `<root>/history/<date>.jsonl` with `records` valid lines, each padded
    with an extra field to at least `pad` bytes so tests can drive the size cap."""
    directory = _hist_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{date.isoformat()}.jsonl"
    lines = []
    for i in range(records):
        obj = {"id": i + 1, "command": f"c-{date.isoformat()}-{i}", "ts": f"{date.isoformat()}T00:00:00-07:00"}
        if pad:
            obj["_pad"] = "x" * pad
        lines.append(json.dumps(obj))
    path.write_text("\n".join(lines) + "\n")
    return path


def _names(root: Path) -> set[str]:
    return {p.name for p in _hist_dir(root).glob("*.jsonl")}


def _seg_name(date: datetime.date) -> str:
    return f"{date.isoformat()}.jsonl"


def test_retention_constants_are_wired():
    # The real thresholds, not just the trim logic, are what ship.
    assert history._RETENTION_DAYS == 7
    assert history._MAX_TOTAL_BYTES == 15 * 1024 * 1024


def test_segments_older_than_7_days_are_unlinked_on_write(tmp_path):
    ancient = ACTIVE_DATE - datetime.timedelta(days=30)
    over_window = ACTIVE_DATE - datetime.timedelta(days=8)  # >7d → gone
    _make_segment(tmp_path, ancient)
    _make_segment(tmp_path, over_window)
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))

    names = _names(tmp_path)
    assert _seg_name(ancient) not in names
    assert _seg_name(over_window) not in names
    assert _seg_name(ACTIVE_DATE) in names  # active segment stays


def test_recent_and_boundary_segments_survive(tmp_path):
    recent = ACTIVE_DATE - datetime.timedelta(days=3)
    boundary = ACTIVE_DATE - datetime.timedelta(days=7)  # exactly 7d, not >7d → kept
    _make_segment(tmp_path, recent)
    _make_segment(tmp_path, boundary)
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))

    names = _names(tmp_path)
    assert _seg_name(recent) in names
    assert _seg_name(boundary) in names


def test_age_prune_keys_on_filename_date_not_mtime(tmp_path):
    # An old-dated segment whose mtime is fresh is still an old day-segment by name.
    old = ACTIVE_DATE - datetime.timedelta(days=30)
    path = _make_segment(tmp_path, old)
    os.utime(path, None)  # bump mtime to "now"
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))

    assert _seg_name(old) not in _names(tmp_path)  # pruned by name, mtime ignored


def test_active_segment_survives_its_own_write(tmp_path):
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="hello", ts=ACTIVE_TS))

    assert (_hist_dir(tmp_path) / _seg_name(ACTIVE_DATE)).exists()
    assert store.read_recent(1)[0].command == "hello"  # the just-written record is intact


def test_size_cap_trims_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_MAX_TOTAL_BYTES", 8000)
    # Three in-window segments (~5 KB each) blow past the cap; age can't remove them.
    d1 = ACTIVE_DATE - datetime.timedelta(days=3)  # oldest
    d2 = ACTIVE_DATE - datetime.timedelta(days=2)
    d3 = ACTIVE_DATE - datetime.timedelta(days=1)  # newest of the old ones
    _make_segment(tmp_path, d1, pad=5000)
    _make_segment(tmp_path, d2, pad=5000)
    _make_segment(tmp_path, d3, pad=5000)
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))

    names = _names(tmp_path)
    assert _seg_name(d1) not in names and _seg_name(d2) not in names  # oldest dropped first
    assert _seg_name(d3) in names and _seg_name(ACTIVE_DATE) in names  # newest + active kept
    total = sum((_hist_dir(tmp_path) / n).stat().st_size for n in names)
    assert total <= 8000


def test_active_segment_never_trimmed_even_when_it_alone_exceeds_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_MAX_TOTAL_BYTES", 500)
    store = HistoryStore(tmp_path)

    for i in range(20):  # today's segment alone grows past the tiny cap
        store.append(HistoryRecord(command=f"cmd-{i}", ts=ACTIVE_TS))

    active = _hist_dir(tmp_path) / _seg_name(ACTIVE_DATE)
    assert active.exists()
    assert active.stat().st_size > 500  # over cap, yet never rewritten or removed
    assert len(store.read_recent(50)) == 20  # every append survived the sweep


def test_size_cap_counts_active_and_trims_survivor_under_it(tmp_path, monkeypatch):
    # Active + one small in-window survivor exceed the cap, but the survivor alone is
    # under it. Correct accounting counts the (never-trimmed) active toward the total,
    # so the survivor is the trim victim; dropping active from the total would leave
    # the survivor and push the on-disk total past the cap. Also pins that the active
    # segment itself is excluded from trimming even while it dominates the total.
    monkeypatch.setattr(history, "_MAX_TOTAL_BYTES", 10000)
    survivor = ACTIVE_DATE - datetime.timedelta(days=1)  # in-window, ~2 KB, under cap
    _make_segment(tmp_path, survivor, pad=2000)
    _make_segment(tmp_path, ACTIVE_DATE, pad=13000)  # today's file alone ~13 KB, over cap
    store = HistoryStore(tmp_path)

    store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))  # grows the active segment

    names = _names(tmp_path)
    assert _seg_name(survivor) not in names  # trimmed: active counts toward the total
    assert _seg_name(ACTIVE_DATE) in names  # active never trimmed, even alone over cap
    assert any(r.command == "fresh" for r in store.read_recent(50))  # the append landed


def test_pruning_old_files_never_drops_active_appends(tmp_path):
    # An old (>7d) segment plus interleaved appends to today's segment from two
    # store handles (two `tt` processes): every today-append survives; old file gone.
    old = ACTIVE_DATE - datetime.timedelta(days=30)
    _make_segment(tmp_path, old)
    a = HistoryStore(tmp_path)
    b = HistoryStore(tmp_path)

    a.append(HistoryRecord(command="a1", ts=ACTIVE_TS))
    b.append(HistoryRecord(command="b1", ts=ACTIVE_TS))
    a.append(HistoryRecord(command="a2", ts=ACTIVE_TS))

    assert {r.command for r in a.read_recent(50)} == {"a1", "b1", "a2"}
    assert _seg_name(old) not in _names(tmp_path)


def test_prune_unlink_failure_is_swallowed(tmp_path, monkeypatch):
    old = ACTIVE_DATE - datetime.timedelta(days=30)
    _make_segment(tmp_path, old)
    store = HistoryStore(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("unlink denied")

    monkeypatch.setattr(history.Path, "unlink", boom)

    stored = store.append(HistoryRecord(command="fresh", ts=ACTIVE_TS))  # must not raise
    assert stored.command == "fresh"
    assert store.read_recent(1)[0].command == "fresh"  # the write still landed


def test_append_with_no_prior_segments_prunes_cleanly(tmp_path):
    # First-ever write: the sweep runs against an empty history dir without error.
    store = HistoryStore(tmp_path)
    stored = store.append(HistoryRecord(command="pwd", ts=ACTIVE_TS))
    assert stored.id == 1
    assert _names(tmp_path) == {_seg_name(ACTIVE_DATE)}
