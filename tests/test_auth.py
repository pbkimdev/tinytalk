"""`tt auth` wizard tests: decision logic (kind->config mapping, credential-test
retry/abort, model/effort resolution, confirm gate, TOML merge-write) driven by a
scripted fake IO — no real questionary prompts, network calls, or SDKs involved
(PRD-provider-setup.md §8). Wizard-written configs are asserted valid per load_config,
as the DoD requires."""

from __future__ import annotations

import json
import tomllib

import pytest

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
    # Fresh config: no slot picker — the wizard goes straight to primary (#78).
    io = ScriptedIO(["claude-agent-sdk", "claude-opus-4-8", "high", True])
    result = auth.run_auth_wizard(config_path, io)
    assert result == "primary"

    doc = _read(config_path)
    assert doc["defaults"]["backend"] == "primary"
    assert "language" not in doc["defaults"]  # `tt setup` owns the language question (#130)
    assert "escalation_backend" not in doc["defaults"]
    assert doc["backends"]["primary"] == {
        "kind": "claude-agent-sdk",
        "model": "claude-opus-4-8",
        "effort": "high",
    }
    assert load_config(config_path).default_backend == "primary"  # valid per load_config (DoD)


def test_existing_config_setup_fallback_preserves_other_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "primary"

[backends.primary]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

[cache]
enabled = false
"""
    )
    io = ScriptedIO(
        [
            "fallback",  # slot
            "claude-agent-sdk",
            "claude-haiku-4-5",
            auth._NO_EFFORT,
            True,  # write?
        ]
    )
    result = auth.run_auth_wizard(config_path, io)
    assert result == "fallback"

    doc = _read(config_path)
    assert doc["backends"]["primary"] == {"kind": "claude-agent-sdk", "model": "claude-sonnet-5"}
    assert doc["cache"]["enabled"] is False
    assert doc["backends"]["fallback"] == {"kind": "claude-agent-sdk", "model": "claude-haiku-4-5"}
    assert doc["defaults"]["backend"] == "primary"  # untouched by a fallback write
    assert doc["defaults"]["escalation_backend"] == "fallback"  # implied by the slot
    assert "language" not in doc["defaults"]  # `tt setup` owns the language question (#130)
    cfg = load_config(config_path)  # valid per load_config (DoD)
    assert cfg.escalation_backend == "fallback"


def test_existing_config_replace_primary_skips_redundant_upfront_confirmation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "primary"

[backends.primary]
kind = "claude-agent-sdk"
model = "claude-haiku-4-5"

[cache]
enabled = false
"""
    )
    io = ScriptedIO(
        [
            "primary",  # slot
            "claude-agent-sdk",
            "claude-sonnet-5",
            auth._NO_EFFORT,
            True,  # write?
        ]
    )
    result = auth.run_auth_wizard(config_path, io)
    assert result == "primary"

    doc = _read(config_path)
    assert doc["backends"]["primary"] == {"kind": "claude-agent-sdk", "model": "claude-sonnet-5"}
    assert doc["cache"]["enabled"] is False  # untouched
    assert doc["defaults"]["backend"] == "primary"
    assert load_config(config_path).backend("primary").model == "claude-sonnet-5"


def test_legacy_default_offers_slots_and_primary_takes_over(tmp_path, monkeypatch):
    """A hand-written config with a non-slot default keeps its tables; primary takes the role."""
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "legacy"\n\n[backends.legacy]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "primary"

    doc = _read(config_path)
    assert doc["defaults"]["backend"] == "primary"
    assert doc["backends"]["legacy"] == {"kind": "claude-agent-sdk", "model": "x"}  # untouched


def test_no_valid_default_goes_straight_to_primary(tmp_path, monkeypatch):
    """defaults.backend missing/dangling → no slot picker; the wizard must yield a valid config."""
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text('[backends.orphan]\nkind = "claude-agent-sdk"\nmodel = "x"\n')
    # First scripted answer is the kind — a slot prompt would consume it and fail the sequence.
    io = ScriptedIO(["claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "primary"
    assert _read(config_path)["defaults"]["backend"] == "primary"
    assert load_config(config_path).default_backend == "primary"


def test_replace_deletes_stale_keyring_secret(tmp_path, monkeypatch):
    """Replacing a keyed slot with a keyless kind must not orphan the keychain entry."""
    deleted = []
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, account: deleted.append((service, account))
    )
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """\
[defaults]
backend = "primary"

[backends.primary]
kind = "openai-compat"
base_url = "https://api.openai.com/v1"
model = "gpt-5.4"
keyring_account = "primary"
"""
    )
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "primary"
    assert deleted == [("tinytalk", "primary")]
    assert "keyring_account" not in _read(config_path)["backends"]["primary"]


_SHARED_ACCOUNT_CONFIG = """\
[defaults]
backend = "primary"

[backends.primary]
kind = "openai-compat"
base_url = "https://api.cerebras.ai/v1"
model = "gemma-4-31b"
keyring_account = "shared-key"

[backends.legacy]
kind = "openai-compat"
base_url = "https://api.cerebras.ai/v1"
model = "other-model"
keyring_account = "shared-key"
"""


def test_replace_keeps_keyring_secret_shared_with_another_table(tmp_path, monkeypatch):
    """#86: an account another backend still references must survive a slot replace."""
    deleted = []
    monkeypatch.setattr("keyring.delete_password", lambda service, account: deleted.append(account))
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(_SHARED_ACCOUNT_CONFIG)
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "primary"
    assert deleted == []
    assert _read(config_path)["backends"]["legacy"]["keyring_account"] == "shared-key"


def test_stale_secret_survives_failed_save(tmp_path, monkeypatch):
    """#86: cleanup must not run before the config write has succeeded."""
    import pytest

    deleted = []
    monkeypatch.setattr("keyring.delete_password", lambda service, account: deleted.append(account))
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)

    def failing_save(path, doc):
        raise OSError("disk full")

    monkeypatch.setattr(auth, "_save", failing_save)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "openai-compat"\nbase_url = "https://x/v1"\n'
        'model = "m"\nkeyring_account = "primary"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])
    with pytest.raises(OSError):
        auth.run_auth_wizard(config_path, io)
    assert deleted == []
    assert config_path.read_text() == before


_FALLBACK_CONFIG = """\
[defaults]
backend = "primary"
escalation_backend = "fallback"

[backends.primary]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

[backends.fallback]
kind = "openai-compat"
base_url = "https://api.cerebras.ai/v1"
model = "gemma-4-31b"
keyring_account = "fallback"
"""


def test_remove_fallback_removes_key_table_and_secret(tmp_path, monkeypatch):
    deleted = []
    monkeypatch.setattr("keyring.delete_password", lambda service, account: deleted.append(account))
    config_path = tmp_path / "config.toml"
    config_path.write_text(_FALLBACK_CONFIG)
    io = ScriptedIO(["remove-fallback", True])
    assert auth.run_auth_wizard(config_path, io) == "fallback"

    doc = _read(config_path)
    assert "escalation_backend" not in doc["defaults"]
    assert "fallback" not in doc["backends"]
    assert doc["backends"]["primary"]["model"] == "claude-sonnet-5"  # untouched
    assert deleted == ["fallback"]
    assert load_config(config_path).escalation_backend is None  # valid per load_config


def test_remove_fallback_keeps_shared_secret(tmp_path, monkeypatch):
    deleted = []
    monkeypatch.setattr("keyring.delete_password", lambda service, account: deleted.append(account))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _FALLBACK_CONFIG.replace('keyring_account = "fallback"', 'keyring_account = "shared-key"')
        + '\n[backends.legacy]\nkind = "openai-compat"\nbase_url = "https://x/v1"\n'
        'model = "m"\nkeyring_account = "shared-key"\n'
    )
    io = ScriptedIO(["remove-fallback", True])
    assert auth.run_auth_wizard(config_path, io) == "fallback"
    assert deleted == []  # legacy still references shared-key


def test_remove_fallback_declined_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(_FALLBACK_CONFIG)
    before = config_path.read_text()
    io = ScriptedIO(["remove-fallback", False])
    assert auth.run_auth_wizard(config_path, io) is None
    assert config_path.read_text() == before


def test_remove_fallback_only_offered_when_fallback_exists(tmp_path):
    class RecordingIO(ScriptedIO):
        def __init__(self, answers):
            super().__init__(answers)
            self.slot_choices = None

        def select(self, message, choices):
            if self.slot_choices is None:
                self.slot_choices = [value for value, _ in choices]
            return super().select(message, choices)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    io = RecordingIO([None])
    auth.run_auth_wizard(config_path, io)
    assert io.slot_choices == ["primary", "fallback"]  # nothing to remove

    config_path.write_text(_FALLBACK_CONFIG)
    io = RecordingIO([None])
    auth.run_auth_wizard(config_path, io)
    assert io.slot_choices == ["primary", "fallback", "remove-fallback"]


def test_added_backend_separated_from_next_section(tmp_path, monkeypatch):
    """#75: the inserted table must not glue the following section header onto itself."""
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
    io = ScriptedIO(["fallback", "claude-agent-sdk", "claude-haiku-4-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "fallback"

    text = config_path.read_text()
    assert "\n\n[cache]" in text  # blank line between [backends.fallback] and the next section
    assert "\n\n\n" not in text  # and no double-spacing introduced anywhere


def test_added_backend_separated_when_file_lacks_trailing_newline(tmp_path, monkeypatch):
    """#75: a hand-edited config without a final newline still gets a blank-line separator."""
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "first"\n\n'
        '[backends.first]\nkind = "claude-agent-sdk"\nmodel = "claude-sonnet-5"'
    )
    io = ScriptedIO(["fallback", "claude-agent-sdk", "claude-haiku-4-5", auth._NO_EFFORT, True])
    assert auth.run_auth_wizard(config_path, io) == "fallback"

    assert 'model = "claude-sonnet-5"\n\n[backends.fallback]' in config_path.read_text()


def test_slot_labels_show_provider_alias():
    cerebras = {
        "kind": "openai-compat",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "gemma-4-31b",
    }
    assert auth._slot_label("primary", {"primary": cerebras}) == "primary — cerebras/gemma-4-31b"
    assert auth._slot_label("fallback", {}) == "fallback — (not set)"


def test_alias_derivation_and_override():
    def describe(**table):
        return auth._describe(table)

    assert (
        describe(kind="openai-compat", base_url="http://localhost:11434/v1", model="q") == "local/q"
    )
    assert (
        describe(kind="openai-compat", base_url="http://127.0.0.1:8000/v1", model="m") == "local/m"
    )
    assert (
        describe(kind="openai-compat", base_url="http://192.168.1.7:3333/v1", model="m")
        == "local/m"
    )
    assert describe(kind="claude-agent-sdk", model="claude-sonnet-5") == "claude/claude-sonnet-5"
    assert describe(kind="codex-agent-sdk", model="gpt-5.4-codex") == "codex/gpt-5.4-codex"
    assert (
        describe(kind="azure-openai", base_url="https://my-res.openai.azure.com", model="d")
        == "azure/d"
    )
    assert (
        describe(kind="openai-compat", base_url="https://api.openai.com/v1", model="gpt-5.4")
        == "openai/gpt-5.4"
    )
    # explicit alias key wins over derivation
    assert (
        describe(
            kind="openai-compat", base_url="https://api.cerebras.ai/v1", model="m", alias="work"
        )
        == "work/m"
    )


def test_cancel_at_kind_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    io = ScriptedIO([None])  # fresh config: the kind select is the first prompt
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_cancel_at_slot_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "first"\n\n[backends.first]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO([None])
    assert auth.run_auth_wizard(config_path, io) is None
    assert config_path.read_text() == before


def test_declined_confirm_gate_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(["claude-agent-sdk", "claude-opus-4-8", auth._NO_EFFORT, False])
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_declining_final_write_when_replacing_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, False])
    assert auth.run_auth_wizard(config_path, io) is None
    assert config_path.read_text() == before


# --- openai-compat setup ------------------------------------------------------------


def test_setup_openai_compat_full_flow():
    io = ScriptedIO(["manual", "http://localhost:11434/v1", "sk-local", "qwen3:8b", "medium"])
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
    io = ScriptedIO(["manual", "http://localhost:11434/v1", "", "qwen3:8b", auth._NO_EFFORT])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: (["qwen3:8b"], None))
    assert draft.secret is None
    assert "effort" not in draft.fields


def test_setup_openai_compat_recognizes_real_openai_endpoint():
    io = ScriptedIO(["manual", "https://api.openai.com/v1", "sk-real", "gpt-5.4", auth._NO_EFFORT])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: (["gpt-5.4"], None))
    assert draft.fields["capabilities"] == ["tool_calling", "native_json"]


def test_setup_openai_compat_no_discovery_free_types():
    io = ScriptedIO(["manual", "http://weird-server/v1", "", "typed-model-id", auth._NO_EFFORT])
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
            "manual",
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
    io = ScriptedIO(["manual", "http://dead:1/v1", "k1", "abort"])
    draft = auth._setup_openai_compat(io, prober=lambda b, k: ([], "connection refused"))
    assert draft is None


def test_setup_openai_compat_managed_returns_provisioned_draft():
    canned = auth.BackendDraft(
        {
            "kind": "openai-compat",
            "base_url": "http://localhost:3333/v1",
            "model": "gemma-4-12B-it-8bit",
            "capabilities": [],
        }
    )
    io = ScriptedIO(["managed"])

    draft = auth._setup_openai_compat(io, provisioner=lambda io: canned)

    assert draft is canned


def test_setup_openai_compat_managed_decline_falls_back_to_manual():
    io = ScriptedIO(
        [
            "managed",
            "http://localhost:11434/v1",
            "",
            "qwen3:8b",
            auth._NO_EFFORT,
        ]
    )

    draft = auth._setup_openai_compat(
        io,
        provisioner=lambda io: None,
        prober=lambda b, k: (["qwen3:8b"], None),
    )

    assert draft.fields["base_url"] == "http://localhost:11434/v1"
    assert draft.secret is None


def test_setup_openai_compat_managed_failure_falls_back_to_manual():
    # A provisioner that raises must degrade to the manual flow, never crash the wizard.
    io = ScriptedIO(
        [
            "managed",
            "http://localhost:11434/v1",
            "",
            "qwen3:8b",
            auth._NO_EFFORT,
        ]
    )

    def boom(_io):
        raise RuntimeError("hf download failed")

    draft = auth._setup_openai_compat(
        io,
        provisioner=boom,
        prober=lambda b, k: (["qwen3:8b"], None),
    )

    assert draft.fields["base_url"] == "http://localhost:11434/v1"
    assert draft.secret is None


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


def _write_claude_settings(tmp_path, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    return path


def test_claude_bedrock_settings_maps_allowlisted_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "outer-process-profile")
    path = _write_claude_settings(
        tmp_path,
        {
            "model": "us.anthropic.claude-opus-4-8",
            "awsAuthRefresh": "do-not-run --profile imported",
            "awsCredentialExport": "do-not-export",
            "env": {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_PROFILE": "imported",
                "AWS_REGION": "us-east-1",
                "AWS_ACCESS_KEY_ID": "ignored",
                "AWS_SECRET_ACCESS_KEY": "ignored",
                "AWS_SESSION_TOKEN": "ignored",
                "AWS_BEARER_TOKEN_BEDROCK": "ignored",
            },
        },
    )

    settings = auth._load_claude_bedrock_settings(path)

    assert settings == auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="imported",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    assert "secret" not in repr(settings).lower()
    assert __import__("os").environ["AWS_PROFILE"] == "outer-process-profile"


def test_claude_bedrock_settings_prefers_first_concrete_model_and_records_1m(tmp_path):
    path = _write_claude_settings(
        tmp_path,
        {
            "model": "us.anthropic.claude-opus-4-8[1m]",
            "env": {
                "CLAUDE_CODE_USE_BEDROCK": "true",
                "AWS_REGION": "us-west-2",
                "ANTHROPIC_MODEL": "opus",
            },
        },
    )

    settings = auth._load_claude_bedrock_settings(path)

    assert settings == auth.ClaudeBedrockSettings(
        region="us-west-2",
        profile=None,
        model="us.anthropic.claude-opus-4-8",
        requested_1m=True,
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"model": "us.anthropic.claude-opus-4-8", "env": []},
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "0", "AWS_REGION": "us-east-1"},
        },
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {"CLAUDE_CODE_USE_BEDROCK": 1, "AWS_REGION": "us-east-1"},
        },
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "1"},
        },
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": " us-east-1"},
        },
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1\n"},
        },
        {
            "model": "opus",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1"},
        },
        {
            "model": "arn:aws:bedrock:us-east-1:123:application-inference-profile/example",
            "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1"},
        },
        {
            "model": "us.anthropic.claude-opus-4-8",
            "env": {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_REGION": "us-east-1",
                "ANTHROPIC_BEDROCK_BASE_URL": "https://gateway.example.test",
            },
        },
    ],
)
def test_unsupported_claude_bedrock_settings_are_not_reused(tmp_path, data):
    path = _write_claude_settings(tmp_path, data)
    assert auth._load_claude_bedrock_settings(path) is None


def test_missing_or_malformed_claude_settings_are_not_reused(tmp_path):
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{ definitely not json")

    assert auth._load_claude_bedrock_settings(missing) is None
    assert auth._load_claude_bedrock_settings(malformed) is None


def test_setup_bedrock_reuses_claude_settings_with_runtime_probe(capsys):
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    io = ScriptedIO(["reuse", True, "medium"])
    calls = []
    output_at_probe = []

    def runtime_prober(model, region, profile):
        output_at_probe.append(capsys.readouterr().out)
        calls.append((model, region, profile))
        return None

    draft = auth._setup_bedrock(
        io,
        prober=lambda *args: (_ for _ in ()).throw(AssertionError("catalog probe called")),
        settings_loader=lambda: imported,
        runtime_prober=runtime_prober,
    )

    assert calls == [("us.anthropic.claude-opus-4-8", "us-east-1", "sso-dev")]
    assert "us-east-1" in output_at_probe[0]
    assert "sso-dev" in output_at_probe[0]
    assert "us.anthropic.claude-opus-4-8" in output_at_probe[0]
    assert draft.fields == {
        "kind": "bedrock",
        "model": "us.anthropic.claude-opus-4-8",
        "aws_region": "us-east-1",
        "aws_profile": "sso-dev",
        "capabilities": ["tool_calling"],
        "effort": "medium",
    }
    assert draft.secret is None


def test_setup_bedrock_detected_aws_settings_lets_user_choose_available_model():
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    catalog_calls = []
    runtime_calls = []
    io = ScriptedIO(["discover", "us.anthropic.claude-sonnet-4-6", "low"])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        prober=lambda region, profile: (
            catalog_calls.append((region, profile))
            or ([{"modelId": "us.anthropic.claude-sonnet-4-6"}], None)
        ),
        runtime_prober=lambda model, region, profile, endpoint=None: (
            runtime_calls.append((model, region, profile, endpoint)) or None
        ),
    )

    assert catalog_calls == [("us-east-1", "sso-dev")]
    assert runtime_calls == [("us.anthropic.claude-sonnet-4-6", "us-east-1", "sso-dev", None)]
    assert draft.fields == {
        "kind": "bedrock",
        "model": "us.anthropic.claude-sonnet-4-6",
        "aws_region": "us-east-1",
        "capabilities": ["tool_calling"],
        "aws_profile": "sso-dev",
        "effort": "low",
    }


def test_setup_bedrock_failed_selected_model_can_choose_another():
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    io = ScriptedIO(
        [
            "discover",
            "us.anthropic.claude-sonnet-4-6",
            "choose",
            "global.anthropic.claude-sonnet-5",
            auth._NO_EFFORT,
        ]
    )
    runtime_calls = []

    def runtime_prober(model, region, profile, endpoint=None):
        runtime_calls.append(model)
        return "AccessDeniedException" if len(runtime_calls) == 1 else None

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        prober=lambda *args: (
            [
                {"modelId": "us.anthropic.claude-sonnet-4-6"},
                {"modelId": "global.anthropic.claude-sonnet-5"},
            ],
            None,
        ),
        runtime_prober=runtime_prober,
    )

    assert runtime_calls == [
        "us.anthropic.claude-sonnet-4-6",
        "global.anthropic.claude-sonnet-5",
    ]
    assert draft.fields["model"] == "global.anthropic.claude-sonnet-5"
    assert draft.fields["capabilities"] == ["tool_calling"]


def test_fresh_config_detected_aws_settings_writes_user_selected_non_opus_model(
    tmp_path, monkeypatch
):
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    monkeypatch.setattr(auth, "_load_claude_bedrock_settings", lambda: imported)
    monkeypatch.setattr(
        auth,
        "_probe_bedrock",
        lambda *args: ([{"modelId": "us.anthropic.claude-sonnet-4-6"}], None),
    )
    monkeypatch.setattr(auth, "_probe_imported_bedrock", lambda *args: None)
    config_path = tmp_path / "fresh-config.toml"
    io = ScriptedIO(
        [
            "bedrock",
            "discover",
            "us.anthropic.claude-sonnet-4-6",
            auth._NO_EFFORT,
            True,
        ]
    )

    assert auth.run_auth_wizard(config_path, io) == "primary"
    assert io._answers == []
    assert _read(config_path)["backends"]["primary"]["model"] == ("us.anthropic.claude-sonnet-4-6")


def test_setup_bedrock_1m_reuse_requires_extra_confirmation():
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=True,
    )
    io = ScriptedIO(["reuse", True, True, auth._NO_EFFORT])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: None,
    )

    assert draft.fields["model"] == "us.anthropic.claude-opus-4-8"
    assert "effort" not in draft.fields


def test_setup_bedrock_declining_import_runs_complete_manual_flow(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    io = ScriptedIO(
        [
            "manual",
            "https://bedrock-runtime.example.test",
            "us-west-2",
            "manual-profile",
            "some-vendor.model-x",
        ]
    )

    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([{"modelId": "some-vendor.model-x"}], None),
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: None,
    )

    assert draft.fields["base_url"] == "https://bedrock-runtime.example.test"
    assert draft.fields["aws_region"] == "us-west-2"
    assert draft.fields["aws_profile"] == "manual-profile"
    assert draft.fields["model"] == "some-vendor.model-x"


def test_setup_bedrock_import_probe_failure_can_restart_manual_flow(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    io = ScriptedIO(
        [
            "reuse",
            True,
            "manual",
            "",
            "us-west-2",
            "",
            "some-vendor.model-x",
        ]
    )

    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([{"modelId": "some-vendor.model-x"}], None),
        settings_loader=lambda: imported,
        runtime_prober=_probe_seq("ValidationException: Converse is unsupported", None),
    )

    assert draft.fields["aws_region"] == "us-west-2"
    assert draft.fields["model"] == "some-vendor.model-x"


def test_setup_bedrock_import_probe_sso_failure_logs_in_and_retries_automatically(capsys):
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    errors = ["bedrock SSO credentials failed for AWS profile 'sso-dev'", None]
    logins = []
    io = ScriptedIO(["reuse", True, auth._NO_EFFORT])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: errors.pop(0),
        sso_login=lambda profile: logins.append(profile),
    )

    assert draft.fields["aws_profile"] == "sso-dev"
    assert logins == ["sso-dev"]
    output = capsys.readouterr().out
    assert "Opening your browser for AWS SSO" in output
    assert "automatically retrying Bedrock validation" in output


def test_setup_bedrock_sso_login_runs_browser_capable_aws_cli_without_shell(monkeypatch):
    import subprocess

    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    errors = ["bedrock SSO credentials failed for AWS profile 'sso-dev'", None]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    io = ScriptedIO(["reuse", True, auth._NO_EFFORT])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: errors.pop(0),
    )

    assert draft.fields["aws_profile"] == "sso-dev"
    assert calls == [
        (
            ["aws", "sso", "login", "--profile", "sso-dev", "--no-cli-pager"],
            {"check": False},
        )
    ]


def test_setup_bedrock_missing_aws_cli_remains_recoverable(monkeypatch, capsys):
    import subprocess

    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )

    def missing_aws(*args, **kwargs):
        raise FileNotFoundError("aws")

    monkeypatch.setattr(subprocess, "run", missing_aws)
    io = ScriptedIO(["reuse", True, "abort"])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: "bedrock SSO credentials failed for AWS profile 'sso-dev'",
    )

    assert draft is None
    output = capsys.readouterr().out
    assert "AWS CLI is not installed" in output
    assert "aws sso login --profile sso-dev" in output


def test_setup_bedrock_failed_sso_login_remains_recoverable(monkeypatch, capsys):
    import subprocess

    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 23),
    )
    io = ScriptedIO(["reuse", True, "abort"])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: "bedrock SSO credentials failed for AWS profile 'sso-dev'",
    )

    assert draft is None
    assert "AWS CLI exited with status 23" in capsys.readouterr().out


def test_setup_bedrock_sso_login_success_is_attempted_only_once_per_flow():
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    errors = [
        "bedrock SSO credentials failed for AWS profile 'sso-dev'",
        "bedrock SSO credentials failed for AWS profile 'sso-dev'",
    ]
    logins = []
    io = ScriptedIO(["reuse", True, "abort"])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: errors.pop(0),
        sso_login=lambda profile: logins.append(profile),
    )

    assert draft is None
    assert logins == ["sso-dev"]


def test_setup_bedrock_import_probe_failure_can_abort():
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=False,
    )
    io = ScriptedIO(["reuse", True, "abort"])

    draft = auth._setup_bedrock(
        io,
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: "AccessDeniedException",
        sso_login=lambda profile: (_ for _ in ()).throw(AssertionError("SSO login called")),
    )

    assert draft is None


def test_setup_bedrock_declining_1m_compatibility_runs_manual_flow(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    imported = auth.ClaudeBedrockSettings(
        region="us-east-1",
        profile="sso-dev",
        model="us.anthropic.claude-opus-4-8",
        requested_1m=True,
    )
    io = ScriptedIO(
        [
            "reuse",
            True,
            False,
            "",
            "us-west-2",
            "",
            "some-vendor.model-x",
        ]
    )

    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([{"modelId": "some-vendor.model-x"}], None),
        settings_loader=lambda: imported,
        runtime_prober=lambda *args: None,
    )

    assert draft.fields["aws_region"] == "us-west-2"
    assert draft.fields["model"] == "some-vendor.model-x"


def test_auth_wizard_imports_bedrock_without_keyring_or_command_execution(tmp_path, monkeypatch):
    import subprocess

    settings_path = _write_claude_settings(
        tmp_path,
        {
            "model": "us.anthropic.claude-opus-4-8",
            "awsAuthRefresh": "do-not-run --profile imported",
            "awsCredentialExport": "do-not-export",
            "env": {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_REGION": "us-east-1",
                "AWS_PROFILE": "sso-dev",
                "AWS_ACCESS_KEY_ID": "ignored",
                "AWS_SECRET_ACCESS_KEY": "ignored",
                "AWS_SESSION_TOKEN": "ignored",
            },
        },
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("import must not execute commands or access keyring secrets")

    real_loader = auth._load_claude_bedrock_settings
    monkeypatch.setattr(auth, "_load_claude_bedrock_settings", lambda: real_loader(settings_path))
    monkeypatch.setattr(auth, "_probe_imported_bedrock", lambda *args: None)
    monkeypatch.setattr(auth, "_store_secret", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr("keyring.get_password", forbidden)
    monkeypatch.setattr("tinytalk.addons.install_addon", lambda name: None)
    config_path = tmp_path / "config.toml"
    io = ScriptedIO(["bedrock", "reuse", True, auth._NO_EFFORT, True])

    assert auth.run_auth_wizard(config_path, io) == "primary"
    assert _read(config_path)["backends"]["primary"] == {
        "kind": "bedrock",
        "model": "us.anthropic.claude-opus-4-8",
        "aws_region": "us-east-1",
        "capabilities": ["tool_calling"],
        "aws_profile": "sso-dev",
    }
    backend = load_config(config_path).backend()
    assert backend.api_key is None

    from tinytalk.provider.factory import make_provider

    provider = make_provider(backend)
    assert provider.model == "us.anthropic.claude-opus-4-8"
    assert provider._region == "us-east-1"
    assert provider._profile == "sso-dev"


def test_imported_bedrock_values_are_escaped_for_display_and_shell(capsys):
    imported = auth.ClaudeBedrockSettings(
        region='us-east-1"quoted',
        profile="dev; echo not-executed",
        model="us.anthropic.claude-opus-4-8`literal`",
        requested_1m=False,
    )

    auth._print_claude_bedrock_settings(imported)
    auth._print_bedrock_credential_hint("SSO token expired", imported.profile)

    output = capsys.readouterr().out
    assert 'AWS region: "us-east-1\\"quoted"' in output
    assert 'AWS profile: "dev; echo not-executed"' in output
    assert 'model: "us.anthropic.claude-opus-4-8`literal`"' in output
    assert "aws sso login --profile 'dev; echo not-executed'" in output


def test_probe_imported_bedrock_uses_runtime_provider(monkeypatch):
    calls = []

    class FakeProvider:
        def __init__(self, model, *, region, profile, endpoint_url=None):
            calls.append(("init", model, region, profile, endpoint_url))

        async def complete(self, request):
            calls.append(("complete", request.messages[0].content, request.max_tokens))

    monkeypatch.setattr("tinytalk.provider.bedrock.BedrockProvider", FakeProvider)

    error = auth._probe_imported_bedrock("us.anthropic.claude-opus-4-8", "us-east-1", "sso-dev")

    assert error is None
    assert calls == [
        ("init", "us.anthropic.claude-opus-4-8", "us-east-1", "sso-dev", None),
        ("complete", "Reply with the word ok.", 8),
    ]


def test_probe_imported_bedrock_returns_runtime_error(monkeypatch):
    class FailingProvider:
        def __init__(self, model, *, region, profile, endpoint_url=None):
            pass

        async def complete(self, request):
            raise RuntimeError("Converse rejected this model")

    monkeypatch.setattr("tinytalk.provider.bedrock.BedrockProvider", FailingProvider)

    assert (
        auth._probe_imported_bedrock("us.anthropic.claude-opus-4-8", "us-east-1", "sso-dev")
        == "Converse rejected this model"
    )


def test_setup_bedrock_ambient_credentials_used_first(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-west-2", "", "anthropic.claude-opus-4-8-v1:0", "low"])
    calls = []

    def prober(region, profile):
        calls.append((region, profile))
        return [{"modelId": "anthropic.claude-opus-4-8-v1:0"}], None

    draft = auth._setup_bedrock(
        io,
        prober=prober,
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
    )
    assert calls == [("us-west-2", None)]
    assert draft.fields == {
        "kind": "bedrock",
        "model": "anthropic.claude-opus-4-8-v1:0",
        "aws_region": "us-west-2",
        "capabilities": ["tool_calling"],
        "effort": "low",
    }
    assert draft.secret is None


def test_setup_bedrock_custom_endpoint_and_profile_store_no_secret(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(
        ["https://bedrock.example.test", "us-east-1", "myprofile", "some-vendor.model-x"]
    )
    calls = []

    def prober(region, profile):
        calls.append((region, profile))
        return [{"modelId": "some-vendor.model-x"}], None

    runtime_calls = []
    draft = auth._setup_bedrock(
        io,
        prober=prober,
        settings_loader=lambda: None,
        runtime_prober=lambda *args: runtime_calls.append(args) or None,
    )
    assert calls == [("us-east-1", "myprofile")]
    assert runtime_calls == [
        ("some-vendor.model-x", "us-east-1", "myprofile", "https://bedrock.example.test")
    ]
    assert draft.fields["base_url"] == "https://bedrock.example.test"
    assert draft.fields["aws_profile"] == "myprofile"
    assert draft.fields["capabilities"] == []
    assert "effort" not in draft.fields
    assert draft.secret is None


def test_setup_bedrock_profile_select_allows_free_text(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: ["dev", "prod"])
    io = ScriptedIO(
        [
            "",
            "us-east-1",
            auth._CUSTOM_AWS_PROFILE,
            "sso-dev",
            "some-vendor.model-x",
        ]
    )
    calls = []

    def prober(region, profile):
        calls.append((region, profile))
        return [{"modelId": "some-vendor.model-x"}], None

    draft = auth._setup_bedrock(
        io,
        prober=prober,
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
    )
    assert calls == [("us-east-1", "sso-dev")]
    assert draft.fields["aws_profile"] == "sso-dev"
    assert draft.secret is None


def test_setup_bedrock_manual_sso_failure_logs_in_and_retries_automatically(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    probes = [
        ([], "bedrock SSO credentials failed for AWS profile 'dev'"),
        ([{"modelId": "some-vendor.model-x"}], None),
    ]
    logins = []
    io = ScriptedIO(
        [
            "",
            "us-east-1",
            "dev",
            "some-vendor.model-x",
        ]
    )
    draft = auth._setup_bedrock(
        io,
        prober=lambda *a: probes.pop(0),
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
        sso_login=lambda profile: logins.append(profile),
    )
    assert draft.secret is None
    assert logins == ["dev"]


def test_setup_bedrock_failed_probe_declines_retry_manual_model(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", "locked-down.model-x"])
    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([], "AccessDeniedException"),
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
    )
    assert draft.fields["model"] == "locked-down.model-x"
    assert draft.secret is None


def test_setup_bedrock_failed_probe_declines_retry_blank_model_cancels(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", ""])
    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([], "AccessDeniedException"),
        settings_loader=lambda: None,
    )
    assert draft is None


def test_setup_bedrock_failed_probe_abort_skips_model_prompt(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "abort"])
    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([], "AccessDeniedException"),
        settings_loader=lambda: None,
    )
    assert draft is None


def test_setup_bedrock_token_validation_error_does_not_print_credential_hint(monkeypatch, capsys):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", "some-vendor.model-x"])
    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([], "ValidationException: input tokens too high"),
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
    )
    out = capsys.readouterr().out
    assert draft.fields["model"] == "some-vendor.model-x"
    assert "aws sso login" not in out
    assert "standard AWS credential chain" not in out


def test_setup_bedrock_no_models_free_types_model(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "custom-model-id"])
    draft = auth._setup_bedrock(
        io,
        prober=lambda region, profile: ([], None),
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
    )
    assert draft.fields["model"] == "custom-model-id"
    assert draft.secret is None
    assert "effort" not in draft.fields


def test_setup_bedrock_cross_region_claude_profile_gets_effort_and_tools(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "us.anthropic.claude-opus-4-8-v1:0", "medium"])
    draft = auth._setup_bedrock(
        io,
        prober=lambda *a: ([{"modelId": "us.anthropic.claude-opus-4-8-v1:0"}], None),
        settings_loader=lambda: None,
        runtime_prober=lambda *args: None,
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


def test_pick_bedrock_model_filters_choices_to_claude_ids():
    class RecordingIO(ScriptedIO):
        def select(self, message, choices):
            self.choices = choices
            return super().select(message, choices)

    io = RecordingIO(["global.anthropic.claude-sonnet-5"])
    picked = auth._pick_bedrock_model(
        io,
        [
            {"modelId": "global.anthropic.claude-sonnet-5", "modelName": "Claude Sonnet 5"},
            {"modelId": "amazon.nova-pro-v1:0", "modelName": "Nova Pro"},
            {"modelId": "us.meta.llama3-2-3b-instruct-v1:0", "modelName": "Llama"},
        ],
    )

    assert picked == "global.anthropic.claude-sonnet-5"
    assert [value for value, _label in io.choices] == [
        "global.anthropic.claude-sonnet-5",
        auth._CUSTOM_MODEL,
    ]


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
            "openai-compat",
            "manual",
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
            "openai-compat",
            "manual",
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
