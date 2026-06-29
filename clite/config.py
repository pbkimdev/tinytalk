"""Load and validate ``config.toml`` for CLITE.

Resolves the **backend** (which provider — kind/model/connection) and the runtime
**posture** (``local`` / ``cloud``). A missing or invalid file fails with a clear,
actionable error. Only ``backend`` + ``posture`` are strictly validated here; the
``danger`` / ``cache`` / ``prices`` sections are parsed and passed through loosely so
this module doesn't pre-empt the issues that own them (#34 / #36 / #32).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

BACKEND_KINDS = frozenset({"openai_compatible", "claude_agent_sdk", "openai_codex_sdk"})
POSTURES = frozenset({"local", "cloud"})

# The starter config written on first run. Embedded (not read from a packaged file) so
# the scaffold works from an installed CLI, where the repo-root template isn't shipped.
# Kept byte-identical to the committed config.toml.example (a test guards the drift).
DEFAULT_CONFIG = """\
# CLITE config — created automatically on first run; edit it to taste.
#
# Lives at $XDG_CONFIG_HOME/clite/config.toml (else ~/.config/clite/config.toml).
# Lookup order: $CLITE_CONFIG -> $XDG_CONFIG_HOME/clite/config.toml -> ~/.config/clite/config.toml
# config.toml.example (in the repo) is the committed reference copy of this file.

[clite]
backend = "local"   # required: name of a [backends.<name>] table below
posture = "local"   # optional: local | cloud  (default: local)

# A local, OpenAI-compatible endpoint (e.g. Ollama / LM Studio).
[backends.local]
kind = "openai_compatible"
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434/v1"
# api_key = "..."   # usually unneeded for local endpoints

# A cloud backend via the Claude Agent SDK.
[backends.claude]
kind = "claude_agent_sdk"
model = "claude-sonnet-4-6"

# --- Parsed but not enforced here (owned by later issues) ---

[danger]
policy = "confirm"   # how clite treats risky commands

[cache]
enabled = true
dir = "~/.cache/clite"

[prices."claude-sonnet-4-6"]
input = 3.0    # USD per 1M input tokens
output = 15.0  # USD per 1M output tokens
"""

_EXAMPLE = (
    '\n\n  [clite]\n  backend = "local"\n\n'
    '  [backends.local]\n  kind = "openai_compatible"\n'
    '  model = "qwen2.5-coder:7b"\n  base_url = "http://localhost:11434/v1"'
)


class ConfigError(Exception):
    """Config file is present but invalid."""


class ConfigNotFoundError(ConfigError):
    """No config file at the resolved path (lets callers offer to scaffold one)."""


@dataclass(frozen=True)
class Backend:
    name: str
    kind: str
    model: str
    base_url: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class Config:
    path: Path
    backend: Backend
    posture: str
    danger: dict
    cache: dict
    prices: dict


def default_config_path() -> Path:
    """$CLITE_CONFIG → $XDG_CONFIG_HOME/clite/config.toml → ~/.config/clite/config.toml."""
    if env := os.environ.get("CLITE_CONFIG"):
        return Path(env)
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg) / "clite" / "config.toml"
    return Path.home() / ".config" / "clite" / "config.toml"


def ensure_config(path: str | os.PathLike | None = None) -> Path:
    """Create the starter config from ``DEFAULT_CONFIG`` if it's missing; return its path.

    Non-silent — prints the created path to stderr, so ``clite`` never writes to home
    silently (the human explicitly opted into this lightweight bootstrap; see AGENTS.md).
    """
    path = Path(path) if path is not None else default_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
        print(f"clite: created starter config at {path} — edit it to pick your backend.", file=sys.stderr)
    return path


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load, validate, and resolve the config. See module docstring.

    With no ``path`` (the default-lookup case) a missing config is auto-created from the
    bundled defaults. An explicitly requested ``path`` that doesn't exist is an error.
    """
    path = Path(path) if path is not None else ensure_config()

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigNotFoundError(f"clite: no config at {path}. Create it, e.g.:{_EXAMPLE}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"clite: {path}: invalid TOML: {e}") from e

    clite = data.get("clite", {})
    backends = data.get("backends", {})

    name = clite.get("backend")
    if not name:
        raise ConfigError(
            f"clite: {path}: [clite].backend is required (name of a [backends.<name>] table)"
        )
    if name not in backends:
        raise ConfigError(
            f'clite: {path}: backend "{name}" selected but no [backends.{name}] table defined'
        )

    posture = clite.get("posture", "local")
    if posture not in POSTURES:
        allowed = ", ".join(sorted(POSTURES))
        raise ConfigError(f'clite: {path}: [clite].posture must be one of {allowed} (got "{posture}")')

    table = backends[name]
    if not isinstance(table, dict):
        raise ConfigError(f"clite: {path}: [backends.{name}] must be a table")
    kind = table.get("kind")
    if kind not in BACKEND_KINDS:
        allowed = ", ".join(sorted(BACKEND_KINDS))
        raise ConfigError(
            f'clite: {path}: [backends.{name}].kind must be one of {allowed} (got "{kind}")'
        )
    model = table.get("model")
    if not model:
        raise ConfigError(f"clite: {path}: [backends.{name}].model is required")

    backend = Backend(
        name=name,
        kind=kind,
        model=model,
        base_url=table.get("base_url"),
        api_key=table.get("api_key"),
    )
    return Config(
        path=path,
        backend=backend,
        posture=posture,
        danger=data.get("danger", {}),
        cache=data.get("cache", {}),
        prices=data.get("prices", {}),
    )
