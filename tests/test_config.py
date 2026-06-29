from pathlib import Path

import pytest

from clite.config import (
    Config,
    ConfigError,
    ConfigNotFoundError,
    default_config_path,
    load_config,
)

VALID_LOCAL = """
[clite]
backend = "local"

[backends.local]
kind = "openai_compatible"
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434/v1"

[danger]
policy = "confirm"

[cache]
enabled = true
dir = "~/.cache/clite"

[prices."claude-sonnet-4-6"]
input = 3.0
output = 15.0
"""

VALID_CLOUD = """
[clite]
backend = "claude"
posture = "cloud"

[backends.claude]
kind = "claude_agent_sdk"
model = "claude-sonnet-4-6"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_valid_local_config(tmp_path):
    cfg = load_config(write(tmp_path, VALID_LOCAL))
    assert isinstance(cfg, Config)
    assert cfg.posture == "local"
    assert cfg.backend.name == "local"
    assert cfg.backend.kind == "openai_compatible"
    assert cfg.backend.model == "qwen2.5-coder:7b"
    assert cfg.backend.base_url == "http://localhost:11434/v1"
    # pass-through sections survive untouched
    assert cfg.danger == {"policy": "confirm"}
    assert cfg.cache == {"enabled": True, "dir": "~/.cache/clite"}
    assert cfg.prices == {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}}


def test_valid_cloud_backend(tmp_path):
    cfg = load_config(write(tmp_path, VALID_CLOUD))
    assert cfg.posture == "cloud"
    assert cfg.backend.kind == "claude_agent_sdk"
    assert cfg.backend.base_url is None


def test_posture_defaults_to_local(tmp_path):
    text = '[clite]\nbackend = "local"\n[backends.local]\nkind = "openai_compatible"\nmodel = "m"\n'
    assert load_config(write(tmp_path, text)).posture == "local"


def test_missing_file_raises_not_found(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigNotFoundError) as exc:
        load_config(missing)
    assert str(missing) in str(exc.value)
    assert "[clite]" in str(exc.value)  # message shows a minimal example


def test_malformed_toml(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, "[clite\nbackend ="))
    assert "config.toml" in str(exc.value)


def test_missing_backend_field(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, "[clite]\nposture = 'local'\n"))
    assert "backend" in str(exc.value)


def test_undefined_backend_table(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, '[clite]\nbackend = "ghost"\n'))
    assert "ghost" in str(exc.value)


def test_invalid_posture(tmp_path):
    text = '[clite]\nbackend = "local"\nposture = "moon"\n[backends.local]\nkind = "openai_compatible"\nmodel = "m"\n'
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, text))
    assert "local" in str(exc.value) and "cloud" in str(exc.value)


def test_unknown_backend_kind(tmp_path):
    text = '[clite]\nbackend = "local"\n[backends.local]\nkind = "telepathy"\nmodel = "m"\n'
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, text))
    assert "openai_compatible" in str(exc.value)


def test_default_path_env_precedence(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.toml"
    monkeypatch.setenv("CLITE_CONFIG", str(explicit))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_path() == explicit

    monkeypatch.delenv("CLITE_CONFIG")
    assert default_config_path() == tmp_path / "xdg" / "clite" / "config.toml"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_config_path() == tmp_path / "home" / ".config" / "clite" / "config.toml"


def test_explicit_path_bypasses_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLITE_CONFIG", str(tmp_path / "from_env.toml"))
    cfg = load_config(write(tmp_path, VALID_LOCAL))
    assert cfg.path == write(tmp_path, VALID_LOCAL)
