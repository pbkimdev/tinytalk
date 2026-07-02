"""`tt auth` wizard tests: decision logic (kind->config mapping, credential-test
retry/abort, model/effort resolution, confirm gate, TOML merge-write) driven by a
scripted fake IO — no real questionary prompts, network calls, or SDKs involved
(PRD-provider-setup.md §8). Wizard-written configs are asserted valid per load_config,
as the DoD requires."""

from __future__ import annotations

import json
import tomllib

import tinytalk.auth as auth
from tinytalk.config import load_config


class ScriptedIO:
    """Pops answers in call order, regardless of prompt kind — order must match the
    wizard's actual call sequence for the scenario under test."""

    def __init__(self, answers):
        self._answers = list(answers)

    def _next(self, message):
        if not self._answers:
            raise AssertionError(f"no scripted answer left for prompt: {message}")
        return self._answers.pop(0)

    def select(self, message, choices):
        return self._next(message)

    def text(self, message, default=""):
        return self._next(message)

    def password(self, message):
        return self._next(message)

    def confirm(self, message, default=True):
        return self._next(message)


def _read(path):
    return tomllib.loads(path.read_text())


def _probe_seq(*errors):
    """A completion-style probe (returns error-or-None) that pops scripted errors."""
    errs = list(errors)

    def probe(*args):
        return errs.pop(0)

    return probe


# --- top-level orchestration + TOML merge-write, via claude-agent-sdk ----------------
# (module probe monkeypatched: run_auth_wizard calls _KIND_SETUP[kind](io) directly)


def test_fresh_config_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-opus-4-8", "high", True])
    result = auth.run_auth_wizard(config_path, io)
    assert result == "primary"

    doc = _read(config_path)
    assert doc["defaults"]["backend"] == "primary"
    assert "escalation_backend" not in doc["defaults"]
    assert doc["backends"]["primary"] == {
        "kind": "claude-agent-sdk",
        "model": "claude-opus-4-8",
        "effort": "high",
    }
    assert load_config(config_path).default_backend == "primary"  # valid per load_config (DoD)


def test_existing_config_add_backend_preserves_other_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "first"

[backends.first]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

[cache]
enabled = false
"""
    )
    io = ScriptedIO(
        [
            "add",
            "second",
            "claude-agent-sdk",
            "claude-haiku-4-5",
            auth._NO_EFFORT,
            False,  # make default?
            True,  # set fallback?
            True,  # write?
        ]
    )
    result = auth.run_auth_wizard(config_path, io)
    assert result == "second"

    doc = _read(config_path)
    assert doc["backends"]["first"] == {"kind": "claude-agent-sdk", "model": "claude-sonnet-5"}
    assert doc["cache"]["enabled"] is False
    assert doc["backends"]["second"] == {"kind": "claude-agent-sdk", "model": "claude-haiku-4-5"}
    assert doc["defaults"]["backend"] == "first"  # set_default answered False — unchanged
    assert doc["defaults"]["escalation_backend"] == "second"  # set_fallback answered True
    cfg = load_config(config_path)  # valid per load_config (DoD)
    assert cfg.escalation_backend == "second"


def test_existing_config_replace_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "first"

[backends.first]
kind = "claude-agent-sdk"
model = "claude-haiku-4-5"

[cache]
enabled = false
"""
    )
    io = ScriptedIO(
        [
            "replace",
            "first",  # which backend to replace
            "claude-agent-sdk",
            "claude-sonnet-5",
            auth._NO_EFFORT,
            True,  # keep as default?
            False,  # set fallback?
            True,  # write?
        ]
    )
    result = auth.run_auth_wizard(config_path, io)
    assert result == "first"

    doc = _read(config_path)
    assert doc["backends"]["first"] == {"kind": "claude-agent-sdk", "model": "claude-sonnet-5"}
    assert doc["cache"]["enabled"] is False  # untouched
    assert doc["defaults"]["backend"] == "first"
    assert load_config(config_path).backend("first").model == "claude-sonnet-5"


def test_set_primary_fallback_action(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "first"

[backends.first]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

[backends.second]
kind = "claude-agent-sdk"
model = "claude-haiku-4-5"
"""
    )
    io = ScriptedIO(["primary_fallback", "second", "first"])
    result = auth.run_auth_wizard(config_path, io)
    assert result == "second"

    doc = _read(config_path)
    assert doc["defaults"]["backend"] == "second"
    assert doc["defaults"]["escalation_backend"] == "first"


def test_cancel_at_name_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    io = ScriptedIO([None])
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_cancel_at_kind_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(["primary", None])
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_declined_confirm_gate_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-opus-4-8", auth._NO_EFFORT, False])
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_exit_action_on_existing_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "first"\n\n[backends.first]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO(["exit"])
    assert auth.run_auth_wizard(config_path, io) is None
    assert config_path.read_text() == before


def test_cancel_at_replace_pick_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "first"\n\n[backends.first]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO(["replace", None])
    assert auth.run_auth_wizard(config_path, io) is None
    assert config_path.read_text() == before


# --- openai-compat setup ------------------------------------------------------------


def test_setup_openai_compat_full_flow():
    io = ScriptedIO(["http://localhost:11434/v1", "sk-local", "qwen3:8b", "medium"])
    draft = auth._setup_openai_compat(
        io, prober=lambda base_url, api_key: (["qwen3:8b", "llama3"], None)
    )
    assert draft.fields == {
        "kind": "openai-compat",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "capabilities": [],
        "effort": "medium",
    }
    assert draft.secret == "sk-local"


def test_setup_openai_compat_keyless_local_server_no_secret():
    io = ScriptedIO(["http://localhost:11434/v1", "", "qwen3:8b", auth._NO_EFFORT])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: (["qwen3:8b"], None))
    assert draft.secret is None
    assert "effort" not in draft.fields


def test_setup_openai_compat_recognizes_real_openai_endpoint():
    io = ScriptedIO(["https://api.openai.com/v1", "sk-real", "gpt-5.4", auth._NO_EFFORT])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: (["gpt-5.4"], None))
    assert draft.fields["capabilities"] == ["tool_calling", "native_json"]


def test_setup_openai_compat_no_discovery_free_types():
    io = ScriptedIO(["http://weird-server/v1", "", "typed-model-id", auth._NO_EFFORT])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: ([], None))
    assert draft.fields["model"] == "typed-model-id"


def test_setup_openai_compat_probe_failure_retries():
    calls = []

    def prober(base_url, api_key):
        calls.append((base_url, api_key))
        if len(calls) == 1:
            return [], "connection refused"
        return ["qwen3:8b"], None

    io = ScriptedIO(
        [
            "http://dead:1/v1",
            "k1",
            "retry",  # credential test failed -> re-enter and try again
            "http://localhost:11434/v1",
            "k2",
            "qwen3:8b",
            auth._NO_EFFORT,
        ]
    )
    draft = auth._setup_openai_compat(io, prober=prober)
    assert calls == [("http://dead:1/v1", "k1"), ("http://localhost:11434/v1", "k2")]
    assert draft.fields["base_url"] == "http://localhost:11434/v1"
    assert draft.secret == "k2"


def test_setup_openai_compat_probe_failure_abort():
    io = ScriptedIO(["http://dead:1/v1", "k1", "abort"])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: ([], "connection refused"))
    assert draft is None


# --- anthropic-compat setup ----------------------------------------------------------


def test_setup_anthropic_compat_effort_scoped_to_model_capabilities():
    models = [{"id": "claude-sonnet-5", "capabilities": {"effort": ["low", "high"]}}]
    io = ScriptedIO([auth._DEFAULT_ANTHROPIC_BASE_URL, "sk-ant", "claude-sonnet-5", "high"])
    draft = auth._setup_anthropic_compat(io, prober=lambda base_url, api_key: (models, None))
    assert draft.fields == {
        "kind": "anthropic-compat",
        "model": "claude-sonnet-5",
        "effort": "high",
    }
    assert draft.secret == "sk-ant"


def test_setup_anthropic_compat_custom_base_url_included_when_overridden():
    io = ScriptedIO(["https://proxy.example.com", "sk-ant", "claude-sonnet-5", auth._NO_EFFORT])
    draft = auth._setup_anthropic_compat(io, prober=lambda b, k: ([], None))
    assert draft.fields["base_url"] == "https://proxy.example.com"
    assert "effort" not in draft.fields


def test_setup_anthropic_compat_default_base_url_omitted():
    io = ScriptedIO(
        [auth._DEFAULT_ANTHROPIC_BASE_URL, "sk-ant", "claude-sonnet-5", auth._NO_EFFORT]
    )
    draft = auth._setup_anthropic_compat(io, prober=lambda b, k: ([], None))
    assert "base_url" not in draft.fields


def test_setup_anthropic_compat_probe_failure_abort():
    io = ScriptedIO([auth._DEFAULT_ANTHROPIC_BASE_URL, "bad-key", "abort"])
    draft = auth._setup_anthropic_compat(io, prober=lambda b, k: ([], "HTTP 401"))
    assert draft is None


# --- claude-agent-sdk setup ----------------------------------------------------------


def test_setup_claude_agent_sdk_curated_list():
    io = ScriptedIO(["claude-opus-4-8", "xhigh"])
    draft = auth._setup_claude_agent_sdk(io, prober=lambda model: None)
    assert draft.fields == {
        "kind": "claude-agent-sdk",
        "model": "claude-opus-4-8",
        "effort": "xhigh",
    }
    assert draft.secret is None


def test_setup_claude_agent_sdk_custom_model_id():
    io = ScriptedIO([auth._CUSTOM_MODEL, "claude-sonnet-5-20260101", "low"])
    draft = auth._setup_claude_agent_sdk(io, prober=lambda model: None)
    assert draft.fields["model"] == "claude-sonnet-5-20260101"


def test_setup_claude_agent_sdk_test_call_uses_picked_model():
    probed = []

    def prober(model):
        probed.append(model)
        return None

    io = ScriptedIO(["claude-sonnet-5", auth._NO_EFFORT])
    auth._setup_claude_agent_sdk(io, prober=prober)
    assert probed == ["claude-sonnet-5"]


def test_setup_claude_agent_sdk_test_failure_retries():
    io = ScriptedIO(["claude-sonnet-5", "retry", auth._NO_EFFORT])
    draft = auth._setup_claude_agent_sdk(io, prober=_probe_seq("not logged in", None))
    assert draft.fields["model"] == "claude-sonnet-5"


def test_setup_claude_agent_sdk_test_failure_abort():
    io = ScriptedIO(["claude-sonnet-5", "abort"])
    draft = auth._setup_claude_agent_sdk(io, prober=_probe_seq("not logged in"))
    assert draft is None


# --- codex-agent-sdk setup ------------------------------------------------------------


def test_setup_codex_agent_sdk_reuses_existing_login():
    io = ScriptedIO([True, "gpt-5.4-codex", "high"])
    login_calls = []
    draft = auth._setup_codex_agent_sdk(
        io,
        prober=lambda: (["gpt-5.4-codex", "gpt-5.4"], None),
        login=lambda k: login_calls.append(k),
    )
    assert login_calls == []
    assert draft.fields == {"kind": "codex-agent-sdk", "model": "gpt-5.4-codex", "effort": "high"}


def test_setup_codex_agent_sdk_logs_in_with_api_key():
    io = ScriptedIO([False, "sk-openai", "gpt-5.4-codex", auth._NO_EFFORT])
    login_calls = []
    draft = auth._setup_codex_agent_sdk(
        io, prober=lambda: (["gpt-5.4-codex"], None), login=lambda k: login_calls.append(k)
    )
    assert login_calls == ["sk-openai"]
    assert "effort" not in draft.fields


def test_setup_codex_agent_sdk_login_failure_returns_none():
    io = ScriptedIO([False, "bad-key"])

    def boom(key):
        raise RuntimeError("invalid key")

    draft = auth._setup_codex_agent_sdk(io, login=boom)
    assert draft is None


def test_setup_codex_agent_sdk_probe_failure_retries():
    probes = [([], "codex binary not found"), (["gpt-5.4-codex"], None)]
    io = ScriptedIO([True, "retry", "gpt-5.4-codex", auth._NO_EFFORT])
    draft = auth._setup_codex_agent_sdk(io, prober=lambda: probes.pop(0))
    assert draft.fields["model"] == "gpt-5.4-codex"


# --- bedrock setup ---------------------------------------------------------------------


def test_setup_bedrock_ambient_credentials_used_first():
    io = ScriptedIO(["us-west-2", "", "anthropic.claude-opus-4-8-v1:0", "low"])
    calls = []

    def prober(region, profile, ak, sk):
        calls.append((region, profile, ak, sk))
        return [{"modelId": "anthropic.claude-opus-4-8-v1:0"}], None

    draft = auth._setup_bedrock(io, prober=prober)
    assert calls == [("us-west-2", None, None, None)]
    assert draft.fields == {
        "kind": "bedrock",
        "model": "anthropic.claude-opus-4-8-v1:0",
        "aws_region": "us-west-2",
        "capabilities": ["tool_calling"],
        "effort": "low",
    }
    assert draft.secret is None


def test_setup_bedrock_falls_back_to_explicit_credentials():
    io = ScriptedIO(["us-east-1", "myprofile", True, "AKIA123", "shh", "some-vendor.model-x"])
    calls = []

    def prober(region, profile, ak, sk):
        calls.append((region, profile, ak, sk))
        return ([], None) if ak is None else ([{"modelId": "some-vendor.model-x"}], None)

    draft = auth._setup_bedrock(io, prober=prober)
    assert calls == [
        ("us-east-1", "myprofile", None, None),
        ("us-east-1", "myprofile", "AKIA123", "shh"),
    ]
    assert draft.secret == json.dumps(
        {"aws_access_key_id": "AKIA123", "aws_secret_access_key": "shh"}
    )
    assert draft.fields["capabilities"] == []
    assert "effort" not in draft.fields


def test_setup_bedrock_explicit_credential_failure_retries():
    probes = [
        ([], "ambient chain has no credentials"),
        ([], "AccessDeniedException"),
        ([{"modelId": "anthropic.claude-opus-4-8-v1:0"}], None),
    ]
    io = ScriptedIO(
        [
            "us-east-1",
            "",
            True,  # enter access key pair?
            "AKIA-BAD",
            "wrong",
            "retry",  # credential test failed -> re-enter
            "AKIA-GOOD",
            "right",
            "anthropic.claude-opus-4-8-v1:0",
            auth._NO_EFFORT,
        ]
    )
    draft = auth._setup_bedrock(io, prober=lambda *a: probes.pop(0))
    assert json.loads(draft.secret)["aws_access_key_id"] == "AKIA-GOOD"


def test_setup_bedrock_declines_explicit_credentials_free_types_model():
    io = ScriptedIO(["us-east-1", "", False, "custom-model-id"])
    draft = auth._setup_bedrock(io, prober=lambda region, profile, ak, sk: ([], None))
    assert draft.fields["model"] == "custom-model-id"
    assert draft.secret is None
    assert "effort" not in draft.fields


def test_setup_bedrock_cross_region_claude_profile_gets_effort_and_tools():
    io = ScriptedIO(["us-east-1", "", "us.anthropic.claude-opus-4-8-v1:0", "medium"])
    draft = auth._setup_bedrock(
        io, prober=lambda *a: ([{"modelId": "us.anthropic.claude-opus-4-8-v1:0"}], None)
    )
    assert draft.fields["capabilities"] == ["tool_calling"]
    assert draft.fields["effort"] == "medium"


# --- azure-openai setup ---------------------------------------------------------------


def test_setup_azure_openai_fields():
    io = ScriptedIO(
        [
            "https://my-resource.openai.azure.com",
            "2026-01-01-preview",
            "gpt-5-4-deploy",
            "az-key",
            "high",
        ]
    )
    draft = auth._setup_azure_openai(io, prober=lambda *a: None)
    assert draft.fields == {
        "kind": "azure-openai",
        "base_url": "https://my-resource.openai.azure.com",
        "model": "gpt-5-4-deploy",
        "azure_api_version": "2026-01-01-preview",
        "capabilities": [],
        "effort": "high",
    }
    assert draft.secret == "az-key"


def test_setup_azure_openai_test_failure_retries():
    io = ScriptedIO(
        [
            "https://my.openai.azure.com",
            "2026-01-01-preview",
            "deploy",
            "bad-key",
            "retry",
            "https://my.openai.azure.com",
            "2026-01-01-preview",
            "deploy",
            "good-key",
            auth._NO_EFFORT,
        ]
    )
    draft = auth._setup_azure_openai(io, prober=_probe_seq("HTTP 401", None))
    assert draft.secret == "good-key"


def test_setup_azure_openai_test_failure_abort():
    io = ScriptedIO(
        ["https://my.openai.azure.com", "2026-01-01-preview", "deploy", "bad-key", "abort"]
    )
    draft = auth._setup_azure_openai(io, prober=_probe_seq("HTTP 401"))
    assert draft is None


# --- shared prompt helpers -------------------------------------------------------------


def test_pick_model_custom_escape_hatch():
    io = ScriptedIO([auth._CUSTOM_MODEL, "my-custom-model"])
    assert auth._pick_model(io, ["a", "b"]) == "my-custom-model"


def test_pick_model_no_discovery_free_types():
    io = ScriptedIO(["typed-model"])
    assert auth._pick_model(io, []) == "typed-model"


def test_pick_model_cancel_returns_none():
    io = ScriptedIO([None])
    assert auth._pick_model(io, ["a"]) is None


def test_pick_effort_none_choice_returns_none():
    io = ScriptedIO([auth._NO_EFFORT])
    assert auth._pick_effort(io, ("low", "high")) is None


def test_pick_effort_empty_levels_skips_prompt():
    io = ScriptedIO([])  # no answers needed — must not prompt at all
    assert auth._pick_effort(io, ()) is None


def test_pick_effort_cancel_returns_none():
    io = ScriptedIO([None])
    assert auth._pick_effort(io, ("low",)) is None


# --- secret storage ----------------------------------------------------------------------


def test_secret_stored_via_keyring_and_referenced_by_account(tmp_path, monkeypatch):
    stored = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, account, value: stored.update({(service, account): value}),
    )
    monkeypatch.setattr(auth, "_probe_openai_compat", lambda b, k: (["gpt-5.4"], None))
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(
        [
            "primary",
            "openai-compat",
            "https://api.openai.com/v1",
            "sk-real",
            "gpt-5.4",
            auth._NO_EFFORT,
            True,  # write?
        ]
    )
    result = auth.run_auth_wizard(config_path, io)
    assert result == "primary"
    assert stored == {("tinytalk", "primary"): "sk-real"}
    doc = _read(config_path)
    assert doc["backends"]["primary"]["keyring_account"] == "primary"
    assert "api_key" not in doc["backends"]["primary"]  # never written in plaintext
    assert load_config(config_path).backend("primary").kind == "openai-compat"


def test_secret_not_stored_when_confirm_declined(tmp_path, monkeypatch):
    stored = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, account, value: stored.update({(service, account): value}),
    )
    monkeypatch.setattr(auth, "_probe_openai_compat", lambda b, k: (["gpt-5.4"], None))
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(
        [
            "primary",
            "openai-compat",
            "https://api.openai.com/v1",
            "sk-real",
            "gpt-5.4",
            auth._NO_EFFORT,
            False,  # write? -> abort
        ]
    )
    assert auth.run_auth_wizard(config_path, io) is None
    assert stored == {}
    assert not config_path.exists()
