from pathlib import Path

import pytest

from clite.config import (
    DEFAULT_CONFIG,
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
    p = write(tmp_path, VALID_LOCAL)
    assert load_config(p).path == p


def test_missing_model_field(tmp_path):
    text = '[clite]\nbackend = "local"\n[backends.local]\nkind = "openai_compatible"\n'
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, text))
    assert "model" in str(exc.value)


def test_scalar_backend_table(tmp_path):
    # A backend declared as a scalar must fail cleanly, not with a bare AttributeError.
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, '[clite]\nbackend = "local"\n[backends]\nlocal = "x"\n'))


def test_first_run_autocreates_at_default_path(tmp_path, monkeypatch, capsys):
    target = tmp_path / "clite" / "config.toml"
    monkeypatch.setenv("CLITE_CONFIG", str(target))
    assert not target.exists()
    cfg = load_config()  # default path, missing -> bootstrap then load
    assert target.exists()
    assert isinstance(cfg, Config)
    assert cfg.path == target
    assert target.read_text() == DEFAULT_CONFIG  # the file is the bundled default
    assert str(target) in capsys.readouterr().err  # non-silent


def test_first_run_honors_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("CLITE_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = load_config()
    assert cfg.path == tmp_path / "xdg" / "clite" / "config.toml"
    assert cfg.path.exists()


def test_existing_config_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "clite" / "config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(VALID_CLOUD)
    monkeypatch.setenv("CLITE_CONFIG", str(target))
    cfg = load_config()
    assert cfg.backend.kind == "claude_agent_sdk"  # untouched
    assert target.read_text() == VALID_CLOUD


def test_explicit_missing_path_still_raises(tmp_path):
    # An explicitly requested file that doesn't exist is an error, not a bootstrap.
    with pytest.raises(ConfigNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_default_config_matches_committed_template():
    # Single source of truth: the embedded default and the committed reference agree.
    example = Path(__file__).resolve().parent.parent / "config.toml.example"
    assert example.read_text() == DEFAULT_CONFIG
