"""Execution oracle for behavioral eval cases."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Comparator = Callable[[str, Any], bool]
StateComparator = Callable[[Path], bool]


@dataclass(frozen=True)
class ExecCase:
    fixture_dir: Path
    comparator: Comparator | None = None
    allowed_returncodes: tuple[int, ...] = (0,)
    state: StateComparator | None = None


@dataclass(frozen=True)
class CandidateRun:
    stdout: str
    returncode: int
    state_pass: bool | None = None


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "v4"


def run_candidate(command: str, fixture_dir: Path) -> tuple[str, int]:
    result = _run_candidate(command, fixture_dir)
    return result.stdout, result.returncode


def _run_candidate(
    command: str,
    fixture_dir: Path,
    state: StateComparator | None = None,
) -> CandidateRun:
    with tempfile.TemporaryDirectory(prefix="tinytalk-oracle-") as tmp:
        cwd = Path(tmp)
        input_dir = fixture_dir / "input"
        if input_dir.exists():
            shutil.copytree(input_dir, cwd, dirs_exist_ok=True)
        _materialize_hardlinks(cwd, fixture_dir)

        path_parts = []
        fixture_bin = fixture_dir / "bin"
        if fixture_bin.exists():
            tmp_bin = cwd / ".bin"
            shutil.copytree(fixture_bin, tmp_bin, dirs_exist_ok=True)
            _chmod_tree_executable(tmp_bin)
            path_parts.append(str(tmp_bin))
        inherited_path = os.environ.get("PATH", "/usr/bin:/bin")
        path_parts.append(inherited_path)

        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=cwd,
                env={"PATH": ":".join(path_parts), "LC_ALL": "C"},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            return CandidateRun(exc.stdout or "", 124, False if state is not None else None)

        state_pass = state(cwd) if state is not None else None
        return CandidateRun(result.stdout, result.returncode, state_pass)


def oracle_pass(target: str, command: str) -> bool:
    case = CASES[target]
    result = _run_candidate(command, case.fixture_dir, case.state)
    if result.returncode not in case.allowed_returncodes:
        return False
    if case.state is not None:
        return result.state_pass is True
    if case.comparator is None:
        return False
    normalized = result.stdout.replace("\r\n", "\n").strip()
    if not normalized:
        return False
    expected = json.loads((case.fixture_dir / "expected.json").read_text())
    return case.comparator(normalized, expected)


def _chmod_tree_executable(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_file():
            child.chmod(child.stat().st_mode | stat.S_IXUSR)


def _materialize_hardlinks(cwd: Path, fixture_dir: Path) -> None:
    spec_path = fixture_dir / "hardlinks.json"
    if not spec_path.exists():
        return
    spec = json.loads(spec_path.read_text())
    for group in spec["groups"]:
        source = cwd / group[0]
        for rel in group[1:]:
            link = cwd / rel
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.exists():
                link.unlink()
            link.hardlink_to(source)


def _compare_env(stdout: str, expected: Any) -> bool:
    found = []
    for line in stdout.splitlines():
        number = re.search(r"\d+", line)
        key = re.search(r"\b[A-Z][A-Z0-9_]*\b", line)
        if not number or not key:
            return False
        found.append([int(number.group()), key.group()])
    return found == expected["missing"]


def _compare_log(stdout: str, expected: Any) -> bool:
    found: dict[str, int] = {}
    for line in stdout.splitlines():
        minute = re.search(r"\b\d{2}:\d{2}\b", line)
        counts = re.findall(r"(?<![:\d])\d+(?![:\d])", line)
        if not minute or len(counts) != 1:
            return False
        found[minute.group()] = int(counts[0])
    return found == expected


def _compare_yaml(stdout: str, expected: Any) -> bool:
    return _image_refs(stdout) == set(expected["images"])


def _image_refs(stdout: str) -> set[str]:
    refs = set()
    for line in stdout.splitlines():
        text = line.strip().strip("'\",")
        if not text:
            continue
        if "image:" in text:
            text = text.split("image:", 1)[1].strip()
        for token in re.split(r"\s+", text):
            token = token.strip("'\",")
            if token and not token.endswith(":") and "/" not in token.rstrip("/"):
                refs.add(token)
    return refs


def _compare_k8s(stdout: str, expected: Any) -> bool:
    found = set()
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            found.add((fields[0], fields[1]))
            continue
        if "/" in line:
            namespace, name = line.strip().split("/", 1)
            found.add((namespace, name))
    return found == {tuple(item) for item in expected["secrets"]}


def _compare_jsonl_p95(stdout: str, expected: Any) -> bool:
    rows = []
    for line in stdout.splitlines():
        route = re.search(r"/[A-Za-z0-9_./-]+", line)
        numbers = re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", line)
        if not route or not numbers:
            return False
        rows.append([route.group(), float(numbers[-1])])
    return rows == [[route, float(value)] for route, value in expected["routes"]]


def _compare_csv_join(stdout: str, expected: Any) -> bool:
    found: dict[str, float] = {}
    for line in stdout.splitlines():
        email = re.search(r"[\w.+-]+@[\w.-]+", line)
        numbers = re.findall(r"-?\d+(?:\.\d+)?", line)
        if not email or not numbers:
            return False
        found[email.group()] = round(float(numbers[-1]), 2)
    wanted = {email: round(float(total), 2) for email, total in expected["totals"].items()}
    return found == wanted


def _compare_hardlinks(stdout: str, expected: Any) -> bool:
    expected_groups = {_path_group(group) for group in expected["groups"]}
    expected_paths = {path for group in expected_groups for path in group}
    found_groups = set()
    by_inode: dict[str, set[str]] = {}
    current_inode: str | None = None

    for line in stdout.splitlines():
        paths = {_norm_path(token) for token in re.split(r"\s+", line) if _norm_path(token) in expected_paths}
        inode = re.search(r"(?<!\d)\d+(?!\d)", line)
        if inode and not paths:
            current_inode = inode.group()
            continue
        if len(paths) >= 2:
            found_groups.add(frozenset(paths))
            continue
        if len(paths) == 1:
            group_inode = inode.group() if inode else current_inode
            if group_inode:
                by_inode.setdefault(group_inode, set()).update(paths)

    found_groups.update(frozenset(paths) for paths in by_inode.values() if len(paths) >= 2)
    return found_groups == expected_groups


def _path_group(group: list[str]) -> frozenset[str]:
    return frozenset(_norm_path(path) for path in group)


def _norm_path(path: str) -> str:
    return path.strip("'\",").removeprefix("./")


def _compare_count_lines(stdout: str, expected: Any) -> bool:
    numbers = re.findall(r"(?<![\d.])\d+(?![\d.])", stdout)
    return len(numbers) == 1 and int(numbers[0]) == expected["total"]


def _compare_extract_ips(stdout: str, expected: Any) -> bool:
    ips = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", stdout))
    return ips == set(expected["ips"])


def _compare_ini_section(stdout: str, expected: Any) -> bool:
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    return lines == expected["lines"]


def _compare_awk_group_sum(stdout: str, expected: Any) -> bool:
    found: dict[str, float] = {}
    for line in stdout.splitlines():
        fields = re.split(r"[\s,]+", line.strip())
        if len(fields) < 2:
            return False
        try:
            found[fields[0].rstrip(":")] = float(fields[-1])
        except ValueError:
            return False
    wanted = {key: float(value) for key, value in expected["totals"].items()}
    return found == wanted


def _compare_diff_sorted(stdout: str, expected: Any) -> bool:
    lines = stdout.splitlines()
    only_allow = []
    only_deny = []
    if any(line.startswith(("< ", "> ")) for line in lines):
        # diff(1): "< x" is allow-only, "> x" is deny-only; ignore hunk headers/---
        for line in lines:
            if line.startswith("< "):
                only_allow.append(line[2:].strip())
            elif line.startswith("> "):
                only_deny.append(line[2:].strip())
    else:
        # comm(1) columns: file-1-only ("allow") is unindented, file-2-only ("deny")
        # is tab-indented. oracle_pass strips the whole output, which can only eat the
        # leading tab of the *first* line; this fixture's allow item sorts first and is
        # unindented, so nothing is lost. If a fixture ever lets a deny item sort first,
        # keep the allow item first (or stop stripping leading whitespace) — otherwise
        # that first deny line loses its tab and is miscounted as allow.
        for line in lines:
            if not line.strip():
                continue
            if line.startswith("\t"):
                only_deny.append(line.strip())
            else:
                only_allow.append(line.strip())
    return only_allow == expected["only_allow"] and only_deny == expected["only_deny"]


def _compare_csv_columns(stdout: str, expected: Any) -> bool:
    rows = []
    for line in stdout.splitlines():
        if "\t" in line:
            fields = line.split("\t")
        else:
            fields = re.split(r"\s*,\s*|\s+", line.strip())
        if len(fields) != 2:
            return False
        rows.append(fields)
    return rows == expected["rows"]


def _compare_json_extract(stdout: str, expected: Any) -> bool:
    names = [line.strip().strip("'\",") for line in stdout.splitlines() if line.strip()]
    return names == expected["names"]


_DELETE_NODE_MODULES_FILES = {
    "package.json": b'{"name":"state-oracle-fixture","private":true}\n',
    "src/app.js": b'console.log("keep me");\n',
}


def _state_delete_node_modules(cwd: Path) -> bool:
    return not (cwd / "node_modules").exists() and _files_match(cwd, _DELETE_NODE_MODULES_FILES)


_LOOP_BACKUP_FILES = {
    "app.conf": b"port=8080\nmode=prod\n",
    "db.conf": b"host=db.internal\npool=7\n",
    "worker.conf": b"queue=critical\nthreads=4\n",
    "README.txt": b"not a config file\n",
}


def _state_loop_backup_copies(cwd: Path) -> bool:
    conf_paths = [path for path in _LOOP_BACKUP_FILES if path.endswith(".conf")]
    expected_baks = {f"{path}.bak" for path in conf_paths}
    found_baks = {path.name for path in cwd.glob("*.bak")}
    if found_baks != expected_baks or not _files_match(cwd, _LOOP_BACKUP_FILES):
        return False
    return all(
        (cwd / f"{path}.bak").read_bytes() == _LOOP_BACKUP_FILES[path]
        for path in conf_paths
    )


_PARALLEL_COMPRESS_FILES = {
    "api.log": b"INFO api boot\nERROR api timeout\n",
    "worker.log": b"worker start\nworker done\n",
    "audit.log": b"user=ada action=login\nuser=lin action=logout\n",
    "README.txt": b"leave this uncompressed\n",
}


def _state_parallel_compress(cwd: Path) -> bool:
    if (cwd / "README.txt").read_bytes() != _PARALLEL_COMPRESS_FILES["README.txt"]:
        return False
    if (cwd / "README.txt.gz").exists():
        return False
    for name, content in _PARALLEL_COMPRESS_FILES.items():
        if not name.endswith(".log"):
            continue
        original = cwd / name
        if original.exists() and original.read_bytes() != content:
            return False
        compressed = cwd / f"{name}.gz"
        if not compressed.is_file():
            return False
        try:
            inflated = gzip.decompress(compressed.read_bytes())
        except (OSError, EOFError):
            return False
        if inflated != content:
            return False
    return True


_TAR_SAFE_FILES = {
    "logs/a.log": b"do not overwrite this sentinel\n",
    "logs/b.log": b"new log from backup\n",
}


def _state_tar_safe_extract_logs(cwd: Path) -> bool:
    if not _files_match(cwd, _TAR_SAFE_FILES):
        return False
    forbidden = [
        "logs/notes.txt",
        "logs/other.conf",
        "logs/var",
        "var/log/a.log",
        "var/log/b.log",
        "var/log/notes.txt",
        "etc/other.conf",
    ]
    return not any((cwd / path).exists() for path in forbidden)


def _files_match(cwd: Path, expected: dict[str, bytes]) -> bool:
    return all(
        (cwd / path).is_file() and (cwd / path).read_bytes() == content
        for path, content in expected.items()
    )


CASES = {
    "jsonl-p95-routes": ExecCase(FIXTURE_ROOT / "jsonl-p95-routes", _compare_jsonl_p95),
    "csv-quoted-join": ExecCase(FIXTURE_ROOT / "csv-quoted-join", _compare_csv_join),
    "env-missing-keys": ExecCase(FIXTURE_ROOT / "env-missing-keys", _compare_env),
    "find-hardlink-groups": ExecCase(FIXTURE_ROOT / "find-hardlink-groups", _compare_hardlinks),
    "count-lines-code": ExecCase(FIXTURE_ROOT / "count-lines-code", _compare_count_lines),
    "extract-ips": ExecCase(FIXTURE_ROOT / "extract-ips", _compare_extract_ips),
    "ini-section": ExecCase(FIXTURE_ROOT / "ini-section", _compare_ini_section),
    "awk-group-sum": ExecCase(FIXTURE_ROOT / "awk-group-sum", _compare_awk_group_sum),
    "diff-sorted": ExecCase(FIXTURE_ROOT / "diff-sorted", _compare_diff_sorted, (0, 1)),
    "csv-columns-transform": ExecCase(FIXTURE_ROOT / "csv-columns-transform", _compare_csv_columns),
    "json-extract": ExecCase(FIXTURE_ROOT / "json-extract", _compare_json_extract),
    "log-window-uniq-ip": ExecCase(FIXTURE_ROOT / "log-window-uniq-ip", _compare_log),
    "yaml-image-policy": ExecCase(FIXTURE_ROOT / "yaml-image-policy", _compare_yaml),
    "k8s-old-tls-secrets": ExecCase(FIXTURE_ROOT / "k8s-old-tls-secrets", _compare_k8s),
    "delete-node-modules": ExecCase(FIXTURE_ROOT / "delete-node-modules", state=_state_delete_node_modules),
    "loop-backup-copies": ExecCase(FIXTURE_ROOT / "loop-backup-copies", state=_state_loop_backup_copies),
    "parallel-compress": ExecCase(FIXTURE_ROOT / "parallel-compress", state=_state_parallel_compress),
    "tar-safe-extract-logs": ExecCase(
        FIXTURE_ROOT / "tar-safe-extract-logs",
        allowed_returncodes=(0, 1),
        state=_state_tar_safe_extract_logs,
    ),
}
