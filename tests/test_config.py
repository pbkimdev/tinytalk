"""Config loader (#30): parse, validate, select backend; clear errors otherwise."""

from __future__ import annotations

import pytest

from clite.config import ConfigError, Price, default_config_path, load_config
from clite.provider.factory import make_provider
from clite.provider.openai_compat import OpenAICompatProvider

GOOD = """\
[defaults]
backend = "local"
posture = "hybrid"
escalation_backend = "claude"

[backends.local]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "qwen3:8b"
api_key_env = "MY_KEY"
capabilities = ["tool_calling", "grammar"]

[backends.claude]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

[cache]
enabled = false
dir = "/tmp/clite-cache"

[prices."qwen3:8b"]
input_per_mtok = 0.1
output_per_mtok = 0.4
"""


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_happy_path(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.default_backend == "local"
    assert cfg.posture == "hybrid"
    assert cfg.escalation_backend == "claude"
    assert cfg.backend().kind == "openai-compat"
    assert cfg.backend("claude").model == "claude-sonnet-5"
    assert cfg.backend().capabilities == ("tool_calling", "grammar")
    assert cfg.cache_enabled is False
    assert str(cfg.cache_dir) == "/tmp/clite-cache"
    assert cfg.price("qwen3:8b") == Price(input_per_mtok=0.1, output_per_mtok=0.4)
    assert cfg.price("unpriced") == Price()


def test_missing_file_is_actionable(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigError) as exc:
        load_config(missing)
    assert str(missing) in str(exc.value)
    assert "[defaults]" in str(exc.value)  # shows a working example


def test_invalid_toml(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(write(tmp_path, "not = [valid"))


def test_unknown_default_backend(tmp_path):
    text = GOOD.replace('backend = "local"', 'backend = "missing"')
    with pytest.raises(ConfigError, match="'missing' is not defined"):
        load_config(write(tmp_path, text))


def test_unknown_kind(tmp_path):
    text = GOOD.replace('kind = "openai-compat"', 'kind = "gopher"')
    with pytest.raises(ConfigError, match="kind must be one of"):
        load_config(write(tmp_path, text))


def test_openai_compat_requires_base_url(tmp_path):
    text = GOOD.replace('base_url = "http://localhost:11434/v1"\n', "")
    with pytest.raises(ConfigError, match="requires base_url"):
        load_config(write(tmp_path, text))


def test_bad_posture(tmp_path):
    text = GOOD.replace('posture = "hybrid"', 'posture = "orbital"')
    with pytest.raises(ConfigError, match="posture must be one of"):
        load_config(write(tmp_path, text))


def test_unknown_capability(tmp_path):
    text = GOOD.replace('"grammar"', '"telepathy"')
    with pytest.raises(ConfigError, match="unknown capability"):
        load_config(write(tmp_path, text))


def test_unknown_backend_lookup(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    with pytest.raises(ConfigError, match="unknown backend"):
        cfg.backend("nope")


def test_api_key_from_env(tmp_path, monkeypatch):
    cfg = load_config(write(tmp_path, GOOD))
    monkeypatch.setenv("MY_KEY", "sekrit")
    assert cfg.backend("local").api_key == "sekrit"
    monkeypatch.delenv("MY_KEY")
    assert cfg.backend("local").api_key is None


def test_default_config_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLITE_CONFIG", str(tmp_path / "custom.toml"))
    assert default_config_path() == tmp_path / "custom.toml"
    monkeypatch.delenv("CLITE_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_path() == tmp_path / "xdg" / "clite" / "config.toml"


def test_factory_builds_openai_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sekrit")
    cfg = load_config(write(tmp_path, GOOD))
    provider = make_provider(cfg.backend("local"))
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.capabilities.supports_tool_calling
    assert provider.capabilities.supports_grammar
    assert not provider.capabilities.supports_native_json


def test_factory_claude_not_built_yet(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    with pytest.raises(ConfigError, match="#27"):
        make_provider(cfg.backend("claude"))
