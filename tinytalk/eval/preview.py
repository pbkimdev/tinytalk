"""Eval-only fixture file previews for data-shape experiments."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from tinytalk.eval.oracle import CASES


_MAX_FILE_LINES = 10
_MAX_LINE_CHARS = 200
_MAX_PREVIEW_LINES = 40
_MAX_PREVIEW_CHARS = 2048
_BINARY_SUFFIXES = {".gz", ".tar", ".tgz", ".zip", ".bz2", ".xz"}


def build_file_preview(target: str) -> str:
    """Return compact read-only fixture context for an execution-oracle target."""
    case = CASES.get(target)
    if case is None:
        return ""
    input_dir = case.fixture_dir / "input"
    if not input_dir.is_dir():
        return ""

    lines = ["Working-directory file previews (read-only context):"]
    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if _is_binary(path):
            continue
        sample = _sample_lines(path)
        if not sample:
            continue
        rel = path.relative_to(input_dir).as_posix()
        lines.append(f"--- {rel} | {_shape_summary(sample)} ---")
        lines.extend(sample)
        if len(lines) >= _MAX_PREVIEW_LINES:
            break

    return _truncate_preview(lines) if len(lines) > 1 else ""


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_SUFFIXES or path.name.endswith(".tar.gz"):
        return True
    with path.open("rb") as handle:
        chunk = handle.read(4096)
    return b"\0" in chunk


def _sample_lines(path: Path) -> list[str]:
    out = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for _, line in zip(range(_MAX_FILE_LINES), handle):
            out.append(line.rstrip("\r\n")[:_MAX_LINE_CHARS])
    return out


def _shape_summary(lines: list[str]) -> str:
    delimiter, label = _sniff_delimiter(lines)
    rows = _parse_rows(lines, delimiter)
    cols = len(rows[0]) if rows else 0
    header = _looks_like_header(rows[0]) if rows else False
    quoted = _has_quoted_delimiter(rows, delimiter)

    parts = [label]
    if cols:
        parts.append(f"{cols} cols")
    if header:
        parts.append(f"header: {','.join(rows[0])}")
    else:
        parts.append("header: no")
    if quoted:
        parts.append("quoted delimiter: yes")
    return ", ".join(parts)


def _sniff_delimiter(lines: list[str]) -> tuple[str, str]:
    first = lines[0] if lines else ""
    if "," in first:
        return ",", "CSV, comma-delimited"
    if "\t" in first:
        return "\t", "TSV, tab-delimited"
    return " ", "whitespace-delimited"


def _parse_rows(lines: list[str], delimiter: str) -> list[list[str]]:
    if delimiter in {",", "\t"}:
        return [row for row in csv.reader(lines, delimiter=delimiter)]
    return [re.split(r"\s+", line.strip()) for line in lines if line.strip()]


def _looks_like_header(row: list[str]) -> bool:
    return bool(row) and all(_is_non_numeric(field) for field in row)


def _is_non_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return True
    return False


def _has_quoted_delimiter(rows: list[list[str]], delimiter: str) -> bool:
    if delimiter not in {",", "\t"}:
        return False
    return any(delimiter in field for row in rows for field in row)


def _truncate_preview(lines: list[str]) -> str:
    out: list[str] = []
    total = 0
    for line in lines[:_MAX_PREVIEW_LINES]:
        addition = len(line) + 1
        if total + addition > _MAX_PREVIEW_CHARS:
            break
        out.append(line)
        total += addition
    return "\n".join(out)
