"""Load and validate ``config.toml`` for CLITE.

Resolves the **backend** (which provider — kind/model/connection) and the runtime
**posture** (``local`` / ``cloud``). A missing or invalid file fails with a clear,
actionable error. Only ``backend`` + ``posture`` are strictly validated here; the
``danger`` / ``cache`` / ``prices`` sections are parsed and passed through loosely so
this module doesn't pre-empt the issues that own them (#34 / #36 / #32).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

BACKEND_KINDS = frozenset({"openai_compatible", "claude_agent_sdk", "openai_codex_sdk"})
POSTURES = frozenset({"local", "cloud"})

_EXAMPLE = (
    '\n\n  [clite]\n  backend = "local"\n\n'
    '  [backends.local]\n  kind = "openai_compatible"\n'
    '  model = "qwen2.5-coder:7b"\n  base_url = "http://localhost:11434/v1"\n\n'
    "copy the template: config.toml.example -> ~/.config/clite/config.toml"
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


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load, validate, and resolve the config. See module docstring."""
    path = Path(path) if path is not None else default_config_path()

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
