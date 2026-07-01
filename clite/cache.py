"""T0 exact cache (#36, PRD §9) — don't pay twice for the same question.

Key = sha256(normalized prompt + cwd + OS fingerprint + backend). Values are
`Suggestion` dicts as JSON files; reads go back through the strict parser, so a
corrupt or stale file is a miss (and removed), never a bad suggestion. The tier
controller re-validates hits, so cache never bypasses the safety ladder.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from pathlib import Path

from clite.contract import Suggestion
from clite.parsing import FormatError, parse_payload
from clite.tiers import TierRequest

_WS = re.compile(r"\s+")


def default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(xdg).expanduser() / "clite"


def _os_fingerprint() -> str:
    return f"{platform.system()}-{platform.release()}-{platform.machine()}"


def cache_key(request: TierRequest, backend: str) -> str:
    normalized = _WS.sub(" ", request.prompt.strip().lower())
    material = "\x1f".join((normalized, request.cwd, _os_fingerprint(), backend))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ExactCache:
    """The tier controller's T0 `Cache` hook, backed by JSON files."""

    def __init__(self, directory: Path | None = None):
        self._dir = (directory or default_cache_dir()) / "suggestions"

    def _path(self, request: TierRequest, backend: str) -> Path:
        return self._dir / f"{cache_key(request, backend)}.json"

    def get(self, request: TierRequest, backend: str) -> Suggestion | None:
        path = self._path(request, backend)
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return parse_payload(data)
        except FormatError:
            path.unlink(missing_ok=True)  # corrupt/stale entry — drop it
            return None

    def put(self, request: TierRequest, backend: str, suggestion: Suggestion) -> None:
        path = self._path(request, backend)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(suggestion.to_dict()), "utf-8")
            tmp.replace(path)
        except OSError:
            return  # caching is best-effort; never break the request over it
