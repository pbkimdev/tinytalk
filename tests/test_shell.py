"""Shell integration (#35): redaction, `clite init zsh`, `--widget` output."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from clite.cli import main
from clite.redact import redact

# --- redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "export OPENAI_API_KEY=sk-abc123def456ghi789jkl012",
        "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIx.eyJzdWIiOiIxMjM0NTY3ODkwIn0'",
        "mysql -u root --password hunter2",
        "git clone https://paul:hunter2@github.com/x/y.git",
        "echo ghp_abcdefghijklmnopqrstuvwxyz012345",
        "aws configure set aws_access_key_id AKIAIOSFODNN7EXAMPLE",
        "TOKEN=xoxb-1234567890-abcdefghij",
        "openssl passwd -1 d41d8cd98f00b204e9800998ecf8427e",
    ],
)
def test_secrets_are_redacted(line):
    cleaned = redact(line)
    for secret in (
        "hunter2",
        "sk-abc123",
        "eyJhbGci",
        "ghp_abcdef",
        "AKIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890",
        "d41d8cd98f00b204e9800998ecf8427e",
    ):
        assert secret not in cleaned, f"{secret!r} survived in: {cleaned!r}"


def test_ordinary_commands_pass_through():
    text = "du -h -d1 . | sort -hr | head -20\ngit log --oneline -5"
    assert redact(text) == text


def test_context_is_capped():
    assert len(redact("x" * 10_000)) == 2000


# --- clite init zsh -------------------------------------------------------------


def test_init_zsh_prints_widget(capsys):
    assert main(["init", "zsh"]) == 0
    script = capsys.readouterr().out
    assert "zle -N accept-line _clite_accept_line" in script
    assert "bindkey '?' _clite_question" in script  # `?` on empty line toggles AI mode
    assert "clite --widget" in script
    assert "DESTRUCTIVE" in script  # destructive commands inserted commented
    assert "CLITE_SESSION_CONTEXT" in script


def test_init_unknown_shell_fails(capsys):
    assert main(["init", "fish"]) == 2
    assert "usage" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_widget_script_is_valid_zsh(capsys):
    main(["init", "zsh"])
    script = capsys.readouterr().out
    proc = subprocess.run(["zsh", "-n"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --- --widget output ------------------------------------------------------------

CONFIG = """\
[defaults]
backend = "local"

[backends.local]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "test-model"

[cache]
enabled = false
"""

PAYLOAD = {
    "command": "find . -name '*.log' -size +10M",
    "explanation": "large log files",
    "danger": "safe",
    "confidence": 0.9,
    "needs": ["find"],
}


@pytest.fixture
def stubbed_cli(tmp_path, monkeypatch):
    import clite.provider.factory as factory
    from clite.provider.base import Capabilities, Completion
    from tests.stubs import StubProvider

    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    provider = StubProvider(Capabilities(), [Completion(text=json.dumps(PAYLOAD))])
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)
    return str(config), provider


def test_widget_output_is_shell_evalable(stubbed_cli, capsys):
    config, _ = stubbed_cli
    assert main(["--config", config, "--widget", "find", "big", "logs"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("clite_command=")
    assert "clite_danger=safe" in out
    if shutil.which("zsh"):
        proc = subprocess.run(
            ["zsh", "-c", 'eval "$1"; print -r -- "$clite_command"', "_", out],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == PAYLOAD["command"]


def test_widget_transport_fault_emits_error_contract(tmp_path, monkeypatch, capsys):
    import clite.provider.factory as factory
    from clite.provider.base import Capabilities, ProviderError
    from tests.stubs import StubProvider

    config = tmp_path / "config.toml"
    config.write_text(CONFIG)

    def fail(_request, _attempt):
        raise ProviderError("endpoint returned HTTP 500 for user's model")

    provider = StubProvider(Capabilities(), fail)
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)

    assert main(["--config", str(config), "--widget", "find", "big", "logs"]) == 1
    captured = capsys.readouterr()
    assert "clite_error=transport" in captured.out
    assert "local" in captured.out
    assert "HTTP 500" in captured.out
    assert "clite: no valid command:" in captured.err
    if shutil.which("zsh"):
        proc = subprocess.run(
            [
                "zsh",
                "-c",
                'eval "$1"; print -r -- "$clite_error|$clite_message"',
                "_",
                captured.out,
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == (
            "transport|clite: backend 'local' failed: endpoint returned HTTP 500 for user's "
            "model; check the server or defaults.backend"
        )


def test_widget_no_command_failure_emits_no_error_contract(tmp_path, monkeypatch, capsys):
    import clite.provider.factory as factory
    from clite.provider.base import Capabilities, Completion
    from tests.stubs import StubProvider

    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    payload = {**PAYLOAD, "command": "definitely_missing_tinytalk_binary_zzzz"}
    provider = StubProvider(
        Capabilities(), lambda _request, _attempt: Completion(text=json.dumps(payload))
    )
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)

    assert main(["--config", str(config), "--widget", "find", "big", "logs"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "clite: no valid command:" in captured.err


def test_session_context_reaches_model_redacted(stubbed_cli, capsys, monkeypatch):
    config, provider = stubbed_cli
    monkeypatch.setenv("CLITE_SESSION_CONTEXT", "export API_KEY=sk-verysecretkey12345678\nls -la")
    assert main(["--config", config, "find", "big", "logs"]) == 0
    user_message = provider.requests[0].messages[1].content
    assert "Recent commands in this session" in user_message
    assert "ls -la" in user_message
    assert "sk-verysecretkey12345678" not in user_message
