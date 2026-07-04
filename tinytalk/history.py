"""Command history store (Scope A1) — persist every prompt→command outcome.

A `HistoryRecord` is the lean, structured metadata for one request (capture-all
in memory, persist-lean: the engineered prompt text, the raw model response, and
the shell-context *content* are never stored — only its length). Records land in
dated JSONL segments under `XDG_STATE_HOME`, one JSON object per line, append-only
and `O_APPEND`-atomic. Writes are best-effort: an `OSError` is swallowed so a
capture failure never breaks a request (mirrors `cache.py:ExactCache.put`).

Ids are monotonic integers seeded from the highest id across all segments, so a
late append to an older-dated segment can't reissue a live id. They are single-
process display identifiers, not stable keys: the seed is a lockless read-then-
append, so two concurrent `tt` processes can compute the same next id — nothing
keys on `id` (`read_recent` orders by file position; the view dedups by command).
`read_recent(n)` returns newest-first. `dedup_key`/`dedup` collapse the *view* on
the exact-normalized command (reusing the T0 cache normalization) — the store
keeps everything; only the view dedupes.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from tinytalk.cache import _WS  # reuse the exact-cache prompt normalization (#36)

# Retention (spec-B1): keep a rolling window of day-segments plus a total-size cap.
# Both sweeps only ever unlink OLD files — never the active segment — so there is no
# lock and no lost-append race (mirrors the best-effort append path).
_RETENTION_DAYS = 7
_MAX_TOTAL_BYTES = 15 * 1024 * 1024  # 15 MB total safety trim


def default_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(xdg).expanduser() / "tinytalk"


@dataclass(frozen=True)
class HistoryRecord:
    """One prompt→command outcome. `id`/`ts` are stamped by the store on append;
    every other field is populated at the cli capture sites (spec-A3)."""

    id: int = 0
    ts: str = ""  # ISO-8601 local timestamp; the segment date is its YYYY-MM-DD prefix
    latency_ms: int = 0
    cwd: str = ""
    mode: str = ""  # widget | json | plain
    backend: str = ""
    model: str = ""
    provider_kind: str = ""
    posture: str = ""
    os_fingerprint: str = ""
    language: str = "en"
    prompt_surface_hash: str = ""
    context_chars: int = 0  # length of the shell-context content (never the content itself)
    prompt: str = ""  # raw natural-language request
    command: str = ""  # verbatim suggested command
    explanation: str = ""
    danger_model: str = ""  # the model's stated danger
    danger_final: str = ""  # the classifier's final danger
    confidence: float = 0.0
    needs: tuple[str, ...] = ()
    tier: int = 0
    attempts: int = 0
    escalated: bool = False
    cache_hit: bool = False
    outcome: str = ""  # ok | cache_hit | no_command | transport_error
    billable: bool = False
    usage: dict = field(default_factory=dict)  # {prompt,completion,total,cached_prompt,cache_write}
    cost_usd: float = 0.0
    cost_breakdown: dict = field(default_factory=dict)  # {fresh, cached, write, output}
    attempts_detail: list = field(default_factory=list)  # per-attempt ledger (spec-A2/A3)
    error_kind: str | None = None
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryRecord":
        names = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in names}
        for key in ("needs", "problems"):  # JSON arrays → tuples (round-trip fidelity)
            if kwargs.get(key) is not None:
                kwargs[key] = tuple(kwargs[key])
        return cls(**kwargs)


def dedup_key(command: str) -> str:
    """Exact-normalized key for view-dedup — the same whitespace/case fold the T0 cache uses."""
    return _WS.sub(" ", command.strip().lower())


def dedup(records: Iterable[HistoryRecord]) -> list[HistoryRecord]:
    """Collapse records with the same normalized command, keeping the first seen.

    Feed newest-first (e.g. `read_recent` output) and it keeps the newest per command.
    """
    seen: set[str] = set()
    out: list[HistoryRecord] = []
    for record in records:
        key = dedup_key(record.command)
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


class HistoryStore:
    """Dated-JSONL history sink under `<state>/history/`."""

    def __init__(self, directory: Path | None = None):
        self._dir = (directory or default_state_dir()) / "history"

    def append(self, record: HistoryRecord) -> HistoryRecord:
        """Stamp a monotonic id + timestamp, append one JSONL line, then sweep old
        segments (both best-effort; a capture failure never breaks a request)."""
        ts = record.ts or _now_iso()
        stored = dataclasses.replace(record, id=self._next_id(), ts=ts)
        segment = self._segment(ts[:10])
        self._write_line(segment, json.dumps(stored.to_dict(), ensure_ascii=False))
        self._prune(segment)
        return stored

    def read_recent(self, n: int) -> list[HistoryRecord]:
        """The `n` most recent records, newest-first across segment files."""
        if n <= 0:
            return []
        out: list[HistoryRecord] = []
        for path in reversed(self._segments()):  # newest date first
            for line in reversed(self._read_lines(path)):  # newest append first
                record = _parse_line(line)
                if record is None:
                    continue
                out.append(record)
                if len(out) >= n:
                    return out
        return out

    def _segment(self, date: str) -> Path:
        return self._dir / f"{date}.jsonl"

    def _segments(self) -> list[Path]:
        # YYYY-MM-DD.jsonl names sort lexically == chronologically.
        try:
            return sorted(self._dir.glob("*.jsonl"))
        except OSError:
            return []

    def _read_lines(self, path: Path) -> list[str]:
        # Split on literal "\n" only, not str.splitlines(): json.dumps escapes
        # real \n/\r inside values but emits U+2028/U+2029/U+0085 literally, so
        # splitlines() would break such a record's line in two (both halves then
        # fail to parse and the whole record vanishes). _parse_line skips the
        # trailing "" from the final newline.
        try:
            return path.read_text("utf-8").split("\n")
        except OSError:
            return []

    def _next_id(self) -> int:
        # Seed from the global max id, not just the newest-dated segment: the
        # highest id need not live in segments[-1]. A late append to an older
        # date, or an entirely-corrupt newest segment, would otherwise reissue a
        # live id. Each segment contributes its tail only, not a whole-file read.
        max_id = 0
        for path in self._segments():
            last = self._last_id(path)
            if last is not None and last > max_id:
                max_id = last
        return max_id + 1

    def _last_id(self, path: Path) -> int | None:
        """Id of the last parseable record in `path`, read from the tail only.

        Records land in id-increasing order, so the last parseable line holds the
        segment's max id. Reading the end in growing blocks keeps this hot append
        path off an O(file) whole-segment re-read (segments run up to the 15 MB cap).
        """
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                remaining = fh.tell()
                block = 8192
                tail = b""
                while remaining > 0:
                    step = min(block, remaining)
                    remaining -= step
                    fh.seek(remaining)
                    tail = fh.read(step) + tail
                    lines = tail.split(b"\n")
                    complete = lines if remaining == 0 else lines[1:]
                    for chunk in reversed(complete):
                        record = _parse_line(chunk.decode("utf-8", "replace"))
                        if record is not None:
                            return record.id
                    tail = b"" if remaining == 0 else lines[0]
                    block *= 2
        except OSError:
            return None
        return None

    def _write_line(self, path: Path, line: str) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            return  # history is best-effort; never break the request over it

    def _prune(self, active: Path) -> None:
        """Retention sweep, run after each append (best-effort, never raises).

        Unlink day-segments strictly older than 7 days, then trim the oldest
        survivors until the total on-disk size is under the 15 MB cap. Both sweeps
        skip `active` (today's segment we just wrote), so retention only ever
        touches OLD files — no lock, no lost-append race with a concurrent `tt`.
        """
        active_date = _segment_date(active)
        if active_date is None:
            return  # unrecognized active name — don't guess what counts as old
        cutoff = active_date - datetime.timedelta(days=_RETENTION_DAYS)
        survivors: list[Path] = []
        for path in self._segments():  # sorted oldest-first
            if path == active:
                continue
            seg_date = _segment_date(path)
            if seg_date is not None and seg_date < cutoff:
                _unlink(path)  # strictly older than the 7-day window
            else:
                survivors.append(path)
        self._trim_to_cap(active, active_date, survivors)

    def _trim_to_cap(
        self, active: Path, active_date: datetime.date, survivors: list[Path]
    ) -> None:
        """Drop oldest survivors until the total size is under the cap.

        The active segment counts toward the total but is never unlinked (we never
        rewrite it), so the total exceeds the cap only when today's file alone does.
        A survivor dated *after* the active date is another `tt` process's newer
        active segment — this process is a moment behind it across a midnight
        boundary — so it likewise counts toward the total but is never a size-cap
        victim; trimming it would delete that process's freshly-written records.
        Only strictly-older survivors are trimmable.
        """
        sizes = {path: _size(path) for path in (active, *survivors)}
        total = sum(sizes.values())
        for path in survivors:  # oldest-first
            if total <= _MAX_TOTAL_BYTES:
                return
            seg_date = _segment_date(path)
            if seg_date is not None and seg_date > active_date:
                continue  # a concurrent process's newer active segment — leave it
            _unlink(path)
            total -= sizes[path]


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def _segment_date(path: Path) -> datetime.date | None:
    """The segment's calendar day, from its `YYYY-MM-DD.jsonl` name.

    The filename — not mtime — is a day-segment's identity: a restore or copy
    rewrites mtime but not the name, so the name is the stable, cheap age signal.
    """
    try:
        return datetime.date.fromisoformat(path.stem)
    except ValueError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # already gone (concurrent sweep) is fine
    except OSError:
        pass  # retention is best-effort; a failed unlink never breaks the write


def _parse_line(line: str) -> HistoryRecord | None:
    line = line.strip()
    if not line:
        return None
    try:
        return HistoryRecord.from_dict(json.loads(line))
    except (json.JSONDecodeError, TypeError):
        return None  # a corrupt line is skipped, never a bad record
