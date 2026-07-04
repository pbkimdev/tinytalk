"""Capability grounding — tell the model what this host really has.

`SystemGrounding` builds the T1 system prompt from host facts (OS, shell,
BSD-vs-GNU userland), a curated catalog of common tools filtered to what is
actually installed, and serves T2 enrichment by fetching real `--help`/`man`
text for the tools a failed attempt named. Help is fetched only for
name-validated binaries that exist on `$PATH`, with a timeout, and memoized.
With a cache dir, the PATH snapshot and fetched help (keyed per tool version)
persist across processes and are reused until stale (#88).

This module owns the host *logic* only — every model-facing word, including
the tool catalog and preference rules, lives in `tinytalk/prompts.py` (#102).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from tinytalk import __version__, groundcache
from tinytalk.prompts import (
    CURATED_TOOLS,
    ENRICH_MISSING_TOOLS,
    ENRICH_TOOL_DOC,
    HOST_FACTS_GNU,
    HOST_FACTS_MACOS,
    PREFERENCE_RULES,
    t1_system,
)
from tinytalk.tiers import TierRequest

_TOOL_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")
_HELP_TIMEOUT = 3.0
_HELP_MAX_CHARS = 4000
_FLAG_RE = re.compile(r"--[A-Za-z][A-Za-z0-9_-]*")


def extract_long_flags(help_text: str) -> frozenset[str]:
    """Every `--flag` named anywhere in a tool's help. Over-inclusive by design: the flag
    check only ever *clears* a flag (membership → accept), never invents a rejection, so a
    stray token here can suppress a false 'unknown option' but can never manufacture one.
    Parsed from the full, untruncated help so options past `_HELP_MAX_CHARS` still count (#34)."""
    return frozenset(_FLAG_RE.findall(help_text))


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
        name = f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
        return HOST_FACTS_MACOS.format(name=name, shell=shell)
    name = f"{system} {platform.release()} ({platform.machine()})"
    return HOST_FACTS_GNU.format(name=name, shell=shell)


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
        self._flags_cache: dict[str, frozenset[str] | None] = {}

    def system_prompt(self, request: TierRequest) -> str:
        tools = [
            (name, desc, self.versions.get(name))
            for name, desc in CURATED_TOOLS.items()
            if name in self.binaries
        ]
        preferences = [rule for gate, rule in PREFERENCE_RULES if gate in self.binaries]
        return t1_system(host_facts(), tools, preferences, language=request.language)

    def enrich(self, needs: tuple[str, ...], problems: tuple[str, ...]) -> str:
        """T2 context: real help for the tools a failed attempt named (PRD T2)."""
        sections: list[str] = []
        missing = [t for t in needs if _TOOL_NAME.match(t) and t not in self.binaries]
        if missing:
            sections.append(ENRICH_MISSING_TOOLS.format(tools=", ".join(missing)))
        for tool in needs:
            help_text = self.help_text(tool)
            if help_text:
                sections.append(ENRICH_TOOL_DOC.format(tool=tool, help=help_text))
        return "\n\n".join(sections)

    def help_text(self, tool: str) -> str | None:
        """Fetched-and-cached `--help`/`man` text; None if unavailable. Used by #34."""
        self._ensure_help(tool)
        return self._help_cache[tool]

    def known_flags(self, tool: str) -> frozenset[str] | None:
        """The `--flag` set this tool documents, or None when no help is available (the flag
        check then skips → never false-rejects). A present-but-empty set means the tool
        documents no long options, so any `--flag` is genuinely unknown. Used by #34."""
        self._ensure_help(tool)
        return self._flags_cache[tool]

    def _ensure_help(self, tool: str) -> None:
        """Populate both the help-text and flag caches for `tool` from one fetch (or the
        persisted record), so `help_text` and `known_flags` never fetch twice."""
        if tool in self._help_cache:
            return
        key = self._help_key(tool)
        if key is not None:
            found, text, flags = groundcache.load_help(self._cache_dir, tool, key)
            if found:
                self._help_cache[tool] = text
                self._flags_cache[tool] = flags
                return
        text, flags = self._fetch_help(tool)
        if key is not None:
            groundcache.save_help(self._cache_dir, tool, key, text, flags)
        self._help_cache[tool] = text
        self._flags_cache[tool] = flags

    def _help_key(self, tool: str) -> str | None:
        """Disk key for persisted help — probed version, else the binary's mtime — or None
        when persistence is off or the name is ineligible (same gate as `_fetch_help`)."""
        if self._cache_dir is None or not _TOOL_NAME.match(tool) or tool not in self.binaries:
            return None
        version = self.versions.get(tool)
        if version:
            return version
        executable = shutil.which(tool, path=self._path)
        if executable is None:
            return "unknown"
        try:
            return f"m{int(os.stat(executable).st_mtime)}"
        except OSError:
            return "unknown"

    def _fetch_help(self, tool: str) -> tuple[str | None, frozenset[str] | None]:
        """(help, flags) for `tool`: the help truncated for the prompt, the flag set parsed
        from the *full* output so options past the truncation still validate. (None, None)
        when no usable docs exist."""
        if not _TOOL_NAME.match(tool) or tool not in self.binaries:
            return None, None
        executable = shutil.which(tool, path=self._path)
        if executable is None:
            return None, None
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
                return text[:_HELP_MAX_CHARS], extract_long_flags(text)
        return None, None
