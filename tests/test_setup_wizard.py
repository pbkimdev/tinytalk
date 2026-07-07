"""`tt setup` wizard tests (#130)."""

from __future__ import annotations

import tomllib

import pytest

import tinytalk.auth as auth
import tinytalk.setup_wizard as setup
from tinytalk import i18n
from tinytalk.rcfile import zsh_integration_block


class ScriptedIO:
    def __init__(self, answers):
        self._answers = list(answers)
        self.prompts = []

    def _next(self, kind, message):
        self.prompts.append((kind, message))
        if not self._answers:
            raise AssertionError(f"no scripted answer left for prompt: {message}")
        return self._answers.pop(0)

    def select(self, message, choices):
        return self._next("select", message)

    def text(self, message, default=""):
        return self._next("text", message)

    def password(self, message):
        return self._next("password", message)

    def confirm(self, message, default=True):
        return self._next("confirm", message)


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch):
    stored = []
    deleted = []
    monkeypatch.setattr(
        auth, "_store_secret", lambda account, value: stored.append((account, value))
    )
    monkeypatch.setattr(auth, "_delete_secret", lambda account: deleted.append(account))
    return stored, deleted


@pytest.fixture(autouse=True)
def reset_language_override():
    """Step 1 sets a process-wide UI-language override; don't leak it across tests."""
    yield
    i18n.set_language(None)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    zshrc = tmp_path / ".zshrc"
    config = tmp_path / "config.toml"
    monkeypatch.setattr(setup, "_stdin_isatty", lambda: True)
    monkeypatch.setattr(setup, "_zshrc_path", lambda: zshrc)
    return zshrc, config


def _read(path):
    return tomllib.loads(path.read_text())


def test_full_accept_path_writes_zsh_config_and_language_once(paths, monkeypatch, capsys):
    zshrc, config = paths
    monkeypatch.setattr(auth, "_probe_claude_agent", lambda model: None)
    # Language ("en") first, then zsh confirm, then the provider sub-wizard.
    io = ScriptedIO(["en", True, "claude-agent-sdk", "claude-sonnet-5", auth._NO_EFFORT, True])

    assert setup.run_setup_wizard(io=io, config_path=config) == 0

    marker, block = zsh_integration_block()
    assert zshrc.read_text().count(marker) == 1
    assert block in zshrc.read_text()
    doc = _read(config)
    assert doc["defaults"]["backend"] == "primary"
    assert doc["defaults"]["language"] == "en"
    assert doc["backends"]["primary"]["kind"] == "claude-agent-sdk"
    out = capsys.readouterr().out
    assert "Step 1 of 3 — language" in out
    assert "Step 2 of 3 — zsh integration" in out
    assert "Step 3 of 3 — provider" in out
    assert "✓" in out


def test_full_decline_leaves_rc_and_config_byte_identical(paths, capsys):
    zshrc, config = paths
    zshrc.write_text("export PATH=$HOME/bin:$PATH\n")
    config.write_text(
        '[defaults]\nbackend = "primary"\nlanguage = "en"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "claude-sonnet-5"\n'
    )
    before_rc = zshrc.read_bytes()
    before_config = config.read_bytes()
    io = ScriptedIO([None, False, False])

    assert setup.run_setup_wizard(io=io, config_path=config) == 0

    assert zshrc.read_bytes() == before_rc
    assert config.read_bytes() == before_config
    assert "Nothing was changed" in capsys.readouterr().out


def test_yes_prints_manual_lines_without_prompts_or_writes(paths, capsys):
    zshrc, config = paths
    io = ScriptedIO([])

    assert setup.run_setup_wizard(yes=True, io=io, config_path=config) == 0

    assert not zshrc.exists()
    assert not config.exists()
    assert io.prompts == []
    out = capsys.readouterr().out
    assert 'eval "$(tt init zsh)"' in out
    assert "tt auth" in out


def test_non_tty_prints_hint_and_exits_zero(paths, monkeypatch, capsys):
    zshrc, config = paths
    monkeypatch.setattr(setup, "_stdin_isatty", lambda: False)

    assert setup.run_setup_wizard(io=ScriptedIO([]), config_path=config) == 0

    assert not zshrc.exists()
    assert not config.exists()
    assert "Run 'tt setup' in a terminal" in capsys.readouterr().out


def test_rerun_skips_installed_widget_and_offers_reconfigure(paths, capsys):
    zshrc, config = paths
    marker, block = zsh_integration_block()
    zshrc.write_text(f"{marker}\n{block}")
    config.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "claude-sonnet-5"\n'
    )
    io = ScriptedIO(["en", False])

    assert setup.run_setup_wizard(io=io, config_path=config) == 0

    kinds = [kind for kind, _ in io.prompts]
    assert kinds == ["text", "confirm"]
    out = capsys.readouterr().out
    assert "already installed" in out
    assert "primary provider already configured" in out


def test_zshrc_path_honors_zdotdir(monkeypatch, tmp_path):
    """Same rc target as the install/uninstall scripts: ${ZDOTDIR:-$HOME}/.zshrc."""
    monkeypatch.delenv("ZDOTDIR", raising=False)
    assert setup._zshrc_path().name == ".zshrc"
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    assert setup._zshrc_path() == tmp_path / ".zshrc"


def test_fallback_slot_result_is_reported_not_skipped(paths, monkeypatch, capsys):
    """On a re-run the auth wizard may write the fallback slot — that's a
    success, not 'Provider setup skipped.'"""
    _zshrc, config = paths
    config.write_text(
        '[defaults]\nbackend = "primary"\n\n'
        '[backends.primary]\nkind = "claude-agent-sdk"\nmodel = "claude-sonnet-5"\n'
    )
    monkeypatch.setattr(setup, "run_auth_wizard", lambda config_path, io: "fallback")
    io = ScriptedIO([None, False, True])  # cancel language, skip widget, reconfigure=yes

    assert setup.run_setup_wizard(io=io, config_path=config) == 0

    out = capsys.readouterr().out
    assert "fallback provider configured" in out
    assert "Provider setup skipped" not in out


def test_choosing_ko_renders_the_rest_of_the_wizard_in_korean(paths, monkeypatch, capsys):
    """Language is step 1 so the remaining steps (and a failing provider step)
    render in the chosen language — and a provider failure still doesn't abort
    the summary."""
    _zshrc, config = paths
    monkeypatch.setattr(setup, "run_auth_wizard", lambda config_path, io: None)
    io = ScriptedIO(["ko", False])  # language=ko, decline widget

    assert setup.run_setup_wizard(io=io, config_path=config) == 0

    assert _read(config)["defaults"]["language"] == "ko"
    out = capsys.readouterr().out
    assert "Step 1 of 3 — language" in out  # asked before the switch, env language
    assert "2/3단계 — zsh 통합" in out
    assert "3/3단계 — 프로바이더" in out
    assert "프로바이더 설정을 건너뛰었습니다." in out
    assert "요약" in out
