"""Capability grounding — tell the model what this host really has.

`SystemGrounding` builds the T1 system prompt from host facts (OS, shell,
BSD-vs-GNU userland), a curated catalog of common tools filtered to what is
actually installed, and serves T2 enrichment by fetching real `--help`/`man`
text for the tools a failed attempt named. Help is fetched only for
name-validated binaries that exist on `$PATH`, with a timeout, and memoized.
With a cache dir, the PATH snapshot persists across processes and is reused
until stale (#88).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from tinytalk import __version__, groundcache
from tinytalk.tiers import TierRequest

_TOOL_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")
_HELP_TIMEOUT = 3.0
_HELP_MAX_CHARS = 4000

# Seeded from tldr one-liners: purpose + the flags that matter for composing
# pipelines. Only entries whose binary is installed are injected.
CURATED_TOOLS: dict[str, str] = {
    "ls": "list directory contents; -l long, -h human sizes, -S sort by size, -t by mtime, -a all",
    "du": "disk usage; -h human, -d N depth (BSD & GNU), -s summary",
    "df": "filesystem free space; -h human",
    "find": "walk a file tree; -name, -type f/d, -size, -mtime, -maxdepth, -exec … \\;",
    "fd": "fast find alternative; pattern first, -e ext, -t f/d, -H hidden",
    "grep": "search text; -r recursive, -i ignore case, -n line numbers, -l files only, -E extended regex",
    "rg": "ripgrep, fast recursive search; -i, -l, -n, -t type, --hidden",
    "awk": "field/record processing; '{print $1}' prints first column, -F sets delimiter",
    "sed": "stream editor; s/old/new/g substitution, -n suppress, -i in-place (BSD needs -i '')",
    "sort": "sort lines; -n numeric, -h human sizes, -r reverse, -k field, -u unique",
    "uniq": "collapse repeated lines (input must be sorted); -c count",
    "head": "first lines; -n N",
    "tail": "last lines; -n N, -f follow",
    "wc": "count; -l lines, -w words, -c bytes",
    "cut": "extract columns; -d delimiter, -f fields",
    "tr": "translate/delete characters",
    "xargs": "build command lines from stdin; -I {} placeholder, -0 null-delimited",
    "cat": "concatenate files to stdout",
    "stat": "file metadata (BSD: stat -f fmt; GNU: stat -c fmt)",
    "cp": "copy files/directories; -R recursive, -p preserve mode/time, -n no clobber, -v verbose",
    "mv": "move or rename files; -n no clobber, -v verbose",
    "rm": "remove files/directories; -r recursive, -f force, -i prompt before remove",
    "touch": "create files or update mtimes; -t timestamp, -a access time, -m modify time",
    "pwd": "print current working directory; -P physical path, -L logical path",
    "dirname": "strip final path component",
    "basename": "strip directory and suffix from a path",
    "readlink": "resolve symlinks; -f canonical path on GNU, macOS lacks -f by default",
    "realpath": "print canonical absolute path; -m allow missing components on GNU",
    "env": "run or print environment; VAR=value command, -i empty env, -u unset variable",
    "which": "locate a command on PATH",
    "tee": "copy stdin to files and stdout; -a append",
    "tar": "archives; -c create, -x extract, -z gzip, -f file, -t list",
    "gzip": "compress; -d decompress, -k keep",
    "zip": "zip archives; -r recursive",
    "unzip": "extract zip; -l list",
    "curl": "HTTP client; -s silent, -L follow redirects, -o file, -X method, -H header",
    "wget": "download files; -O output",
    "git": "version control; log/diff/status/branch; --oneline, -p patches",
    "ps": "processes; aux for all (BSD-style on macOS)",
    "lsof": "open files/ports; -i :PORT who listens",
    "kill": "signal a process; -9 SIGKILL (last resort)",
    "chmod": "change permissions; -R recursive, numeric or u+x modes",
    "chown": "change owner; -R recursive",
    "ln": "links; -s symbolic",
    "mkdir": "make directories; -p parents",
    "rsync": "sync files; -a archive, -v verbose, -n dry-run, --delete",
    "jq": "JSON processor; '.field', '.[]', -r raw output",
    "brew": "Homebrew package manager; install/uninstall/list/info",
    "docker": "containers/images; ps, images, logs, exec -it, run --rm, build, compose up/down/logs",
    "kubectl": "Kubernetes CLI; get/describe/logs/apply/delete, -n namespace, -A all namespaces, -o yaml/json/wide",
    "helm": "Kubernetes package manager; list/install/upgrade/uninstall, -n namespace, -f values.yaml, --set key=value",
    "bash": "Bash shell; -c command, -lc login command, set -euo pipefail, printf/read/for/while/if builtins",
    "sh": "POSIX shell; -c command, portable scripts and pipelines",
    "printf": "format text portably; '%s\\n' for safe line output, avoid echo for escaped data",
    "uname": "system information; -a all, -s kernel name, -m machine",
    "hostname": "show or set hostname; -f fully qualified name where supported",
    "id": "user/group identity; -u user id, -g group id, -n names",
    "whoami": "print effective username",
    "openssl": "crypto toolkit; digests, certs",
    "diff": "compare files; -u unified, -r recursive",
    "date": "print/format date (BSD: date -v+1d; GNU: date -d tomorrow)",
}


def installed_binaries(path: str | None = None) -> frozenset[str]:
    """Names of executables on `$PATH` (PRD's PATH cache: existence only, no specs)."""
    names: set[str] = set()
    for entry in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not entry:
            continue
        try:
            with os.scandir(entry) as it:
                for de in it:
                    try:
                        if de.is_file() and os.access(de.path, os.X_OK):
                            names.add(de.name)
                    except OSError:
                        continue
        except OSError:
            continue
    return frozenset(names)


def host_facts() -> str:
    system = platform.system()
    shell = os.path.basename(os.environ.get("SHELL", "zsh"))
    if system == "Darwin":
        flavor = (
            "macOS (BSD userland): coreutils are BSD variants — GNU-only long options "
            "often do not exist (e.g. use `sed -i ''`, `du -d 1`, `stat -f`)."
        )
        name = f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    else:
        flavor = "GNU userland: GNU coreutils flags are available."
        name = f"{system} {platform.release()} ({platform.machine()})"
    return f"OS: {name}. Shell: {shell}. {flavor}"


class SystemGrounding:
    """The tier controller's `Grounding` hook, backed by the real host."""

    def __init__(self, *, path: str | None = None, cache_dir: Path | None = None):
        self._path = path if path is not None else os.environ.get("PATH", "")
        self._cache_dir = cache_dir
        if cache_dir is None:
            self.binaries = installed_binaries(self._path)
            self.versions: dict[str, str] = {}
        else:
            snap = groundcache.load_snapshot(cache_dir, path=self._path, tt_version=__version__)
            if snap is None:
                snap = groundcache.build_snapshot(
                    self._path,
                    installed_binaries(self._path),
                    version_candidates=frozenset(CURATED_TOOLS),
                )
                groundcache.save_snapshot(cache_dir, snap, tt_version=__version__)
            self.binaries = snap.binaries
            self.versions = snap.versions
        self._help_cache: dict[str, str | None] = {}

    def system_prompt(self, request: TierRequest) -> str:
        tools = []
        for name, desc in CURATED_TOOLS.items():
            if name not in self.binaries:
                continue
            version = self.versions.get(name)
            tools.append(f"- {name}: {desc} (v{version})" if version else f"- {name}: {desc}")
        return (
            "You are TinyTalk. Turn the user's plain-English request into exactly one "
            "runnable shell command (a pipeline counts as one command) for their system.\n"
            f"{host_facts()}\n\n"
            "Installed tools you should prefer (with their key flags):\n"
            + "\n".join(tools)
            + "\n\nOnly use tools from this list, shell builtins, or tools you are certain "
            "are installed; never invent flags. Commit to exactly one command — never a "
            "list of options or alternatives; if you are unsure, pick your best answer. "
            "Respond with only a JSON object matching this shape, no prose around it:\n"
            '{"command": "...", "explanation": "...", '
            '"danger": "safe|caution|destructive", "confidence": 0.0-1.0, '
            '"needs": ["binaries", "used"]}'
        )

    def enrich(self, needs: tuple[str, ...], problems: tuple[str, ...]) -> str:
        """T2 context: real help for the tools a failed attempt named (PRD T2)."""
        sections: list[str] = []
        missing = [t for t in needs if _TOOL_NAME.match(t) and t not in self.binaries]
        if missing:
            sections.append(
                "These tools are NOT installed on this system — do not use them: "
                + ", ".join(missing)
            )
        for tool in needs:
            help_text = self.help_text(tool)
            if help_text:
                sections.append(f"Real documentation for `{tool}` on this system:\n{help_text}")
        return "\n\n".join(sections)

    def help_text(self, tool: str) -> str | None:
        """Fetched-and-cached `--help`/`man` text; None if unavailable. Used by #34."""
        if tool in self._help_cache:
            return self._help_cache[tool]
        text = self._fetch_help(tool)
        self._help_cache[tool] = text
        return text

    def _fetch_help(self, tool: str) -> str | None:
        if not _TOOL_NAME.match(tool) or tool not in self.binaries:
            return None
        executable = shutil.which(tool, path=self._path)
        if executable is None:
            return None
        for argv, env in (
            ([executable, "--help"], None),
            (["man", tool], {**os.environ, "MANPAGER": "cat", "PAGER": "cat"}),
        ):
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=_HELP_TIMEOUT,
                    stdin=subprocess.DEVNULL,
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            text = (proc.stdout or proc.stderr).strip()
            # BSD tools print a usage line to stderr and exit non-zero on --help;
            # accept any output that looks like documentation.
            if text and ("usage" in text.lower() or len(text) > 80):
                return text[:_HELP_MAX_CHARS]
        return None
