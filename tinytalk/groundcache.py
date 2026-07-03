"""Persistent grounding snapshot (#88) — capture the host picture once, reuse until stale.

A snapshot records the PATH-visible binaries (and, once probed, curated tool
versions), keyed by the exact `$PATH` string and stamped with the tt version,
OS fingerprint, per-directory mtimes, and a TTL. Reads fail to a miss and
writes are best-effort — a broken cache degrades to a live scan, never a
broken request.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

_SCHEMA_VERSION = 1
_SNAPSHOT_TTL_S = 7 * 24 * 3600
_VERSION_TIMEOUT = 1.0
_VERSION_MAX_CHARS = 24
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+[A-Za-z0-9.+-]*")  # emits filename-safe tokens only
_PROBE_WORKERS = 8
# Same gate as grounding's help fetch: never exec a name that isn't a plain tool name.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")


@dataclass(frozen=True)
class Snapshot:
    binaries: frozenset[str]
    versions: dict[str, str]
    path: str
    dir_mtimes: dict[str, float]
    created_at: float


def snapshot_path(cache_dir: Path, path: str) -> Path:
    """One snapshot file per distinct `$PATH`, so shells with differing PATHs never thrash."""
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"grounding-{digest}.json"


def _os_fingerprint() -> str:
    return f"{platform.system()}-{platform.release()}-{platform.machine()}"


def _dir_mtimes(path: str) -> dict[str, float]:
    mtimes: dict[str, float] = {}
    for entry in path.split(os.pathsep):
        if not entry or entry in mtimes:
            continue
        try:
            mtimes[entry] = os.stat(entry).st_mtime
        except OSError:
            continue
    return mtimes


def load_snapshot(cache_dir: Path, *, path: str, tt_version: str) -> Snapshot | None:
    """The stored snapshot for this `$PATH`, or None when missing, stale, or corrupt."""
    try:
        data = json.loads(snapshot_path(cache_dir, path).read_text("utf-8"))
        if (
            data["schema_version"] != _SCHEMA_VERSION
            or data["tt_version"] != tt_version
            or data["os"] != _os_fingerprint()
            or data["path"] != path
        ):
            return None
        created_at = float(data["created_at"])
        if time.time() - created_at > _SNAPSHOT_TTL_S:
            return None
        dir_mtimes = {str(d): float(m) for d, m in data["dir_mtimes"].items()}
        if dir_mtimes != _dir_mtimes(path):
            return None
        if not isinstance(data["binaries"], list):
            return None
        return Snapshot(
            binaries=frozenset(str(b) for b in data["binaries"]),
            versions={str(t): str(v) for t, v in data["versions"].items()},
            path=path,
            dir_mtimes=dir_mtimes,
            created_at=created_at,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def build_snapshot(
    path: str, binaries: frozenset[str], version_candidates: frozenset[str] = frozenset()
) -> Snapshot:
    return Snapshot(
        binaries=binaries,
        versions=probe_versions(version_candidates, binaries, path),
        path=path,
        dir_mtimes=_dir_mtimes(path),
        created_at=time.time(),
    )


def probe_versions(tools: Iterable[str], binaries: frozenset[str], path: str) -> dict[str, str]:
    """First `--version` line, regex-parsed, for name-validated installed tools."""
    candidates = sorted(t for t in set(tools) if _TOOL_NAME.match(t) and t in binaries)
    if not candidates:
        return {}
    versions: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(_PROBE_WORKERS, len(candidates))) as pool:
        for tool, version in pool.map(lambda t: (t, _probe_version(t, path)), candidates):
            if version:
                versions[tool] = version
    return versions


def _probe_version(tool: str, path: str) -> str | None:
    executable = shutil.which(tool, path=path)
    if executable is None:
        return None
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    match = _VERSION_RE.search(lines[0]) if lines else None
    return match.group(0)[:_VERSION_MAX_CHARS] if match else None


def _help_path(cache_dir: Path, tool: str, key: str) -> Path:
    return cache_dir / "help" / f"{tool}@{key}.json"


def load_help(cache_dir: Path, tool: str, key: str) -> tuple[bool, str | None]:
    """(found, text) for a persisted help entry; any unreadable or invalid entry is a miss."""
    if not _TOOL_NAME.match(tool) or not _TOOL_NAME.match(key):
        return False, None
    try:
        data = json.loads(_help_path(cache_dir, tool, key).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, None
    if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION and "help" in data:
        text = data["help"]
        if text is None or isinstance(text, str):
            return True, text
    return False, None  # invalid shape — the save after the re-fetch overwrites it


def save_help(cache_dir: Path, tool: str, key: str, text: str | None) -> None:
    """Atomic, best-effort persist of fetched help. None means 'no usable help' — cached too,
    because re-discovering it costs up to two subprocess timeouts."""
    if not _TOOL_NAME.match(tool) or not _TOOL_NAME.match(key):
        return
    target = _help_path(cache_dir, tool, key)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": _SCHEMA_VERSION, "help": text}), "utf-8")
        tmp.replace(target)
    except OSError:
        return


def save_snapshot(cache_dir: Path, snap: Snapshot, *, tt_version: str) -> None:
    """Atomic, best-effort write — caching must never break a request."""
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "tt_version": tt_version,
        "os": _os_fingerprint(),
        "created_at": snap.created_at,
        "path": snap.path,
        "dir_mtimes": snap.dir_mtimes,
        "binaries": sorted(snap.binaries),
        "versions": snap.versions,
    }
    target = snapshot_path(cache_dir, snap.path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), "utf-8")
        tmp.replace(target)
    except OSError:
        return
