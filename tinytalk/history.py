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
        """Stamp a monotonic id + timestamp and append one JSONL line (best-effort; a
        capture failure never breaks a request)."""
        ts = record.ts or _now_iso()
        stored = dataclasses.replace(record, id=self._next_id(), ts=ts)
        segment = self._segment(ts[:10])
        self._write_line(segment, json.dumps(stored.to_dict(), ensure_ascii=False))
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


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def _parse_line(line: str) -> HistoryRecord | None:
    line = line.strip()
    if not line:
        return None
    try:
        return HistoryRecord.from_dict(json.loads(line))
    except (json.JSONDecodeError, TypeError):
        return None  # a corrupt line is skipped, never a bad record
