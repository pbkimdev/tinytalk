import json

import pytest

import tinytalk.provider.factory as factory
from tinytalk.cli import build_parser, main
from tinytalk.provider.base import Capabilities, Completion, Usage
from tests.stubs import StubProvider

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
    "command": "ls -lhS",
    "explanation": "list files by size",
    "danger": "safe",
    "confidence": 0.9,
    "needs": ["ls"],
}


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG)
    return str(p)


@pytest.fixture
def stub_backend(monkeypatch):
    provider = StubProvider(
        Capabilities(), [Completion(text=json.dumps(PAYLOAD), usage=Usage(10, 5, 15))]
    )
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)
    return provider


def test_version_exits_zero():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0


def test_no_request_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    assert "tt" in capsys.readouterr().out.lower()


def test_help_lists_every_subcommand(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for command in ("auth", "eval", "init zsh"):
        assert command in out


def test_request_prints_command_to_stdout(config_path, stub_backend, capsys):
    assert main(["--config", config_path, "list", "files", "by", "size"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "ls -lhS"
    assert "[danger: safe]" in captured.err


def test_json_mode_emits_full_suggestion(config_path, stub_backend, capsys):
    assert main(["--config", config_path, "--json", "list", "files"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["command"] == "ls -lhS"
    assert data["danger"] == "safe"
    assert data["tier"] == 1
    assert data["backend"] == "stub"


def test_missing_config_is_actionable(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml"), "list", "files"]) == 1
    err = capsys.readouterr().err
    assert "no config found" in err
    assert "[defaults]" in err


def test_unknown_backend_flag_fails_cleanly(config_path, capsys):
    assert main(["--config", config_path, "--backend", "nope", "list"]) == 1
    assert "unknown backend" in capsys.readouterr().err


def test_auth_subcommand_success(tmp_path, monkeypatch, capsys):
    import tinytalk.auth as auth_mod

    monkeypatch.setattr(auth_mod, "run_auth_wizard", lambda path, io: "local")
    config_path = tmp_path / "config.toml"
    config_path.write_text(CONFIG)  # _auth re-reads the written file to report defaults
    assert main(["auth", "--config", str(config_path)]) == 0
    out = capsys.readouterr().out
    assert "'local' saved" in out
    assert str(config_path) in out
    assert "default backend: local" in out


def test_auth_subcommand_cancelled(tmp_path, monkeypatch, capsys):
    import tinytalk.auth as auth_mod

    monkeypatch.setattr(auth_mod, "run_auth_wizard", lambda path, io: None)
    assert main(["auth", "--config", str(tmp_path / "config.toml")]) == 1
    assert "cancelled" in capsys.readouterr().err


def test_eval_subcommand_renders_leaderboard(config_path, monkeypatch, capsys):
    import tinytalk.eval.runner as runner_mod

    provider = StubProvider(
        Capabilities(),
        lambda request, i: Completion(text=json.dumps(PAYLOAD), usage=Usage(10, 5, 15)),
    )
    monkeypatch.setattr(runner_mod, "make_provider", lambda cfg: provider)
    assert main(["eval", "--config", config_path, "--prompts", "list-by-size"]) == 0
    out = capsys.readouterr().out
    assert "backend" in out
    assert "list-by-size" in out
