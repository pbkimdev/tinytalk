"""Config loader (#30): parse, validate, select backend; clear errors otherwise."""

from __future__ import annotations

import pytest

from tinytalk.config import ConfigError, Price, default_config_path, load_config
from tinytalk.provider.factory import make_provider
from tinytalk.provider.openai_compat import OpenAICompatProvider

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
dir = "/tmp/tt-cache"

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
    assert str(cfg.cache_dir) == "/tmp/tt-cache"
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


def test_api_key_env_present_skips_keyring(tmp_path, monkeypatch):
    text = GOOD.replace(
        'api_key_env = "MY_KEY"', 'api_key_env = "MY_KEY"\nkeyring_account = "local"'
    )
    cfg = load_config(write(tmp_path, text))
    monkeypatch.setenv("MY_KEY", "sekrit")

    def boom(service, account):
        raise AssertionError("keyring should not be consulted when the env var is set")

    monkeypatch.setattr("keyring.get_password", boom)
    assert cfg.backend("local").api_key == "sekrit"


def test_api_key_falls_back_to_keyring(tmp_path, monkeypatch):
    text = GOOD.replace(
        'api_key_env = "MY_KEY"', 'api_key_env = "MY_KEY"\nkeyring_account = "local"'
    )
    cfg = load_config(write(tmp_path, text))
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(
        "keyring.get_password",
        lambda service, account: (
            "from-keyring" if (service, account) == ("tinytalk", "local") else None
        ),
    )
    assert cfg.backend("local").api_key == "from-keyring"


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("anthropic-compat", ""),
        ("codex-agent-sdk", ""),
        ("bedrock", '\naws_region = "us-east-1"'),
        (
            "azure-openai",
            '\nbase_url = "https://my.openai.azure.com"\nazure_api_version = "2026-01-01-preview"',
        ),
    ],
)
def test_new_kinds_validate(tmp_path, kind, extra):
    text = f"""\
[defaults]
backend = "b"

[backends.b]
kind = "{kind}"
model = "some-model"{extra}
"""
    cfg = load_config(write(tmp_path, text))
    assert cfg.backend().kind == kind


def test_bedrock_requires_aws_region(tmp_path):
    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "bedrock"
model = "anthropic.claude-opus-4-8-v1:0"
"""
    with pytest.raises(ConfigError, match="requires aws_region"):
        load_config(write(tmp_path, text))


def test_azure_requires_base_url_and_api_version(tmp_path):
    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "azure-openai"
model = "gpt-5-4"
"""
    with pytest.raises(ConfigError, match="requires base_url"):
        load_config(write(tmp_path, text))

    text_missing_version = text.replace(
        'model = "gpt-5-4"', 'model = "gpt-5-4"\nbase_url = "https://my.openai.azure.com"'
    )
    with pytest.raises(ConfigError, match="requires azure_api_version"):
        load_config(write(tmp_path, text_missing_version))


def test_unknown_effort(tmp_path):
    text = GOOD.replace('kind = "openai-compat"', 'kind = "openai-compat"\neffort = "ludicrous"')
    with pytest.raises(ConfigError, match="unknown effort"):
        load_config(write(tmp_path, text))


def _clear_locale(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def test_language_from_defaults(tmp_path, monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")  # explicit config beats env
    text = GOOD.replace('backend = "local"', 'backend = "local"\nlanguage = "ko"')
    assert load_config(write(tmp_path, text)).language == "ko"


def test_language_detected_from_env(tmp_path, monkeypatch):
    path = write(tmp_path, GOOD)  # no language key
    _clear_locale(monkeypatch)
    assert load_config(path).language == "en"
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert load_config(path).language == "ko"
    monkeypatch.setenv("LC_ALL", "C")  # LC_ALL overrides LANG; C means English
    assert load_config(path).language == "en"


def test_language_must_be_string(tmp_path):
    text = GOOD.replace('backend = "local"', 'backend = "local"\nlanguage = 3')
    with pytest.raises(ConfigError, match="language must be a string"):
        load_config(write(tmp_path, text))


def test_show_explanation_defaults_true(tmp_path):
    assert load_config(write(tmp_path, GOOD)).show_explanation is True


def test_show_explanation_from_defaults(tmp_path):
    text = GOOD.replace('backend = "local"', 'backend = "local"\nexplanation = false')
    assert load_config(write(tmp_path, text)).show_explanation is False


def test_show_explanation_must_be_bool(tmp_path):
    text = GOOD.replace('backend = "local"', 'backend = "local"\nexplanation = "off"')
    with pytest.raises(ConfigError, match="explanation must be true or false"):
        load_config(write(tmp_path, text))


def test_default_config_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TT_CONFIG", str(tmp_path / "custom.toml"))
    assert default_config_path() == tmp_path / "custom.toml"
    monkeypatch.delenv("TT_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_path() == tmp_path / "xdg" / "tinytalk" / "config.toml"


def test_factory_builds_openai_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sekrit")
    cfg = load_config(write(tmp_path, GOOD))
    provider = make_provider(cfg.backend("local"))
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.capabilities.supports_tool_calling
    assert provider.capabilities.supports_grammar
    assert not provider.capabilities.supports_native_json


def test_factory_builds_claude_agent(tmp_path):
    from tinytalk.provider.claude_agent import ClaudeAgentProvider

    cfg = load_config(write(tmp_path, GOOD))
    provider = make_provider(cfg.backend("claude"))
    assert isinstance(provider, ClaudeAgentProvider)
    assert provider.capabilities.supports_native_json


def test_factory_builds_anthropic_compat_with_default_base_url(tmp_path):
    from tinytalk.provider.anthropic_compat import AnthropicCompatProvider

    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "anthropic-compat"
model = "claude-sonnet-5"
"""
    cfg = load_config(write(tmp_path, text))
    provider = make_provider(cfg.backend())
    assert isinstance(provider, AnthropicCompatProvider)
    assert provider.base_url == "https://api.anthropic.com"
    assert provider.capabilities.supports_tool_calling  # adapter's own default, not config-driven


def test_factory_builds_azure_openai(tmp_path):
    from tinytalk.provider.azure_openai import AzureOpenAIProvider

    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "azure-openai"
model = "gpt-5-4"
base_url = "https://my-resource.openai.azure.com"
azure_api_version = "2026-01-01-preview"
capabilities = ["tool_calling"]
"""
    cfg = load_config(write(tmp_path, text))
    provider = make_provider(cfg.backend())
    assert isinstance(provider, AzureOpenAIProvider)
    assert provider.name == "azure-openai:gpt-5-4"
    assert provider.capabilities.supports_tool_calling


def test_factory_builds_codex_agent(tmp_path):
    from tinytalk.provider.codex_agent import CodexAgentProvider

    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "codex-agent-sdk"
model = "gpt-5.4-codex"
"""
    cfg = load_config(write(tmp_path, text))
    provider = make_provider(cfg.backend())
    assert isinstance(provider, CodexAgentProvider)


def test_factory_builds_bedrock_with_ambient_credentials(tmp_path):
    from tinytalk.provider.bedrock import BedrockProvider

    text = """\
[defaults]
backend = "b"

[backends.b]
kind = "bedrock"
model = "anthropic.claude-opus-4-8-v1:0"
aws_region = "us-east-1"
aws_profile = "tt"
"""
    cfg = load_config(write(tmp_path, text))
    provider = make_provider(cfg.backend())
    assert isinstance(provider, BedrockProvider)
    assert provider.name == "bedrock:anthropic.claude-opus-4-8-v1:0"


@pytest.mark.parametrize("secret_field", ['api_key_env = "BEDROCK_CREDS"', 'keyring_account = "b"'])
def test_bedrock_rejects_legacy_stored_key_wiring(tmp_path, secret_field):
    text = f"""\
[defaults]
backend = "b"

[backends.b]
kind = "bedrock"
model = "anthropic.claude-opus-4-8-v1:0"
aws_region = "us-east-1"
{secret_field}
"""
    with pytest.raises(ConfigError, match="stored access keys are no longer read"):
        load_config(write(tmp_path, text))


def test_prices_cache_rates(tmp_path):
    text = GOOD.replace(
        "output_per_mtok = 0.4\n",
        "output_per_mtok = 0.4\ncached_input_per_mtok = 0.01\ncache_write_per_mtok = 0.125\n",
    )
    cfg = load_config(write(tmp_path, text))
    price = cfg.price("qwen3:8b")
    assert price.cached_input_per_mtok == 0.01
    assert price.cache_write_per_mtok == 0.125
    # unset cache rates default to 0 (billed at the input rate downstream)
    assert Price(input_per_mtok=1.0).cached_input_per_mtok == 0.0
