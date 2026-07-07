"""`tt auth` wizard tests: decision logic (kind->config mapping, credential-test
retry/abort, model/effort resolution, confirm gate, TOML merge-write) driven by a
scripted fake IO — no real questionary prompts, network calls, or SDKs involved
(PRD-provider-setup.md §8). Wizard-written configs are asserted valid per load_config,
as the DoD requires."""

from __future__ import annotations

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
    # Fresh config: no slot picker — the wizard goes straight to primary (#78).
    io = ScriptedIO(["claude-agent-sdk", "claude-opus-4-8", "high", "ko", True])
    result = auth.run_auth_wizard(config_path, io)
    assert result == "primary"

    doc = _read(config_path)
    assert doc["defaults"]["backend"] == "primary"
    assert doc["defaults"]["language"] == "ko"  # the language question writes through (#107)
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
    assert "language" not in doc["defaults"]  # only the primary flow asks (#107)
    cfg = load_config(config_path)  # valid per load_config (DoD)
    assert cfg.escalation_backend == "fallback"


def test_existing_config_replace_primary_confirms_upfront(tmp_path, monkeypatch):
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
            True,  # replace the existing primary?
            "claude-agent-sdk",
            "claude-sonnet-5",
            auth._NO_EFFORT,
            "en",  # explanation language
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
    io = ScriptedIO(["primary", "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, "en", True])
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
    io = ScriptedIO(["claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, "en", True])
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
    io = ScriptedIO(
        ["primary", True, "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, "en", True]
    )
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
    io = ScriptedIO(
        ["primary", True, "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, "en", True]
    )
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
    io = ScriptedIO(
        ["primary", True, "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, "en", True]
    )
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
    io = ScriptedIO(["claude-agent-sdk", "claude-opus-4-8", auth._NO_EFFORT, "en", False])
    assert auth.run_auth_wizard(config_path, io) is None
    assert not config_path.exists()


def test_declined_replace_confirm_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "x"\n'
    )
    before = config_path.read_text()
    io = ScriptedIO(["primary", False])  # decline "will replace the existing primary"
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


def test_setup_bedrock_ambient_credentials_used_first(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-west-2", "", "anthropic.claude-opus-4-8-v1:0", "low"])
    calls = []

    def prober(region, profile):
        calls.append((region, profile))
        return [{"modelId": "anthropic.claude-opus-4-8-v1:0"}], None

    draft = auth._setup_bedrock(io, prober=prober)
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

    draft = auth._setup_bedrock(io, prober=prober)
    assert calls == [("us-east-1", "myprofile")]
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

    draft = auth._setup_bedrock(io, prober=prober)
    assert calls == [("us-east-1", "sso-dev")]
    assert draft.fields["aws_profile"] == "sso-dev"
    assert draft.secret is None


def test_setup_bedrock_sso_credential_failure_retries(monkeypatch, capsys):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    probes = [
        ([], "bedrock credentials failed for AWS profile 'dev'; token expired"),
        ([{"modelId": "some-vendor.model-x"}], None),
    ]
    io = ScriptedIO(
        [
            "",
            "us-east-1",
            "dev",
            "retry",
            "some-vendor.model-x",
        ]
    )
    draft = auth._setup_bedrock(io, prober=lambda *a: probes.pop(0))
    assert draft.secret is None
    assert "aws sso login --profile dev" in capsys.readouterr().out


def test_setup_bedrock_failed_probe_declines_retry_manual_model(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", "locked-down.model-x"])
    draft = auth._setup_bedrock(io, prober=lambda region, profile: ([], "AccessDeniedException"))
    assert draft.fields["model"] == "locked-down.model-x"
    assert draft.secret is None


def test_setup_bedrock_failed_probe_declines_retry_blank_model_cancels(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", ""])
    draft = auth._setup_bedrock(io, prober=lambda region, profile: ([], "AccessDeniedException"))
    assert draft is None


def test_setup_bedrock_failed_probe_abort_skips_model_prompt(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "abort"])
    draft = auth._setup_bedrock(io, prober=lambda region, profile: ([], "AccessDeniedException"))
    assert draft is None


def test_setup_bedrock_token_validation_error_does_not_print_credential_hint(monkeypatch, capsys):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "manual", "some-vendor.model-x"])
    draft = auth._setup_bedrock(
        io, prober=lambda region, profile: ([], "ValidationException: input tokens too high")
    )
    out = capsys.readouterr().out
    assert draft.fields["model"] == "some-vendor.model-x"
    assert "aws sso login" not in out
    assert "standard AWS credential chain" not in out


def test_setup_bedrock_no_models_free_types_model(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "custom-model-id"])
    draft = auth._setup_bedrock(io, prober=lambda region, profile: ([], None))
    assert draft.fields["model"] == "custom-model-id"
    assert draft.secret is None
    assert "effort" not in draft.fields


def test_setup_bedrock_cross_region_claude_profile_gets_effort_and_tools(monkeypatch):
    monkeypatch.setattr(auth, "_available_aws_profiles", lambda: [])
    io = ScriptedIO(["", "us-east-1", "", "us.anthropic.claude-opus-4-8-v1:0", "medium"])
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
            "openai-compat",
            "manual",
            "https://api.openai.com/v1",
            "sk-real",
            "gpt-5.4",
            auth._NO_EFFORT,
            "en",  # explanation language
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
            "en",  # explanation language
            False,  # write? -> abort
        ]
    )
    assert auth.run_auth_wizard(config_path, io) is None
    assert stored == {}
    assert not config_path.exists()
