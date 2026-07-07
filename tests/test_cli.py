import json

import pytest

import tinytalk.provider.factory as factory
from tinytalk.cli import build_parser, main
from tinytalk.provider.base import Capabilities, Completion, StreamChunk, Usage
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
    for command in ("auth", "eval", "ground", "init zsh", "prompt", "upgrade", "uninstall"):
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


def test_upgrade_subcommand_routes_to_installer(monkeypatch, capsys):
    import tinytalk.cli as cli

    seen = []
    monkeypatch.setattr(cli, "_perform_upgrade", lambda version: seen.append(version) or "9.9.9")

    assert main(["upgrade", "--version", "v9.9.9"]) == 0

    assert seen == ["v9.9.9"]
    assert "upgraded to 9.9.9" in capsys.readouterr().out


def _stage_upgrade(tmp_path, monkeypatch):
    """An existing install (marker file) plus a fake-download seam for `_perform_upgrade`."""
    from tests.test_addons import _fake_opener, _make_tar
    from tinytalk import addons

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(addons, "PLATFORM_TAG", "macos-arm64")
    tar = _make_tar({"tt/tt": b"#!/bin/sh\necho tt 9.9.9\n"}, mode=0o755)
    monkeypatch.setattr(addons, "_http_opener", _fake_opener(tar))
    lib_dir = tmp_path / "data" / "tinytalk"
    (lib_dir / "tt").mkdir(parents=True)
    (lib_dir / "tt" / "marker").write_text("old install\n")
    return lib_dir


def test_upgrade_failure_restores_old_install(tmp_path, monkeypatch):
    import subprocess

    import tinytalk.cli as cli

    lib_dir = _stage_upgrade(tmp_path, monkeypatch)

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("subprocess.check_output", boom)  # post-swap sanity check fails

    with pytest.raises(subprocess.CalledProcessError):
        cli._perform_upgrade("latest")

    assert (lib_dir / "tt" / "marker").read_text() == "old install\n"  # old install survives
    assert not (lib_dir / "tt.old").exists()
    assert not (lib_dir / "tt.partial").exists()


def test_upgrade_success_swaps_install_and_clears_aside(tmp_path, monkeypatch):
    import tinytalk.cli as cli

    lib_dir = _stage_upgrade(tmp_path, monkeypatch)
    monkeypatch.setattr("subprocess.check_output", lambda cmd, **kwargs: "tt 9.9.9")

    assert cli._perform_upgrade("latest") == "9.9.9"

    assert (lib_dir / "tt" / "tt").is_file()
    assert not (lib_dir / "tt" / "marker").exists()  # old tree fully replaced
    assert not (lib_dir / "tt.old").exists()
    assert not (lib_dir / "tt.partial").exists()


def test_uninstall_removes_installed_trees_and_keyring_accounts(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    config_home = tmp_path / "config"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = data / "tinytalk" / "tt" / "tt"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    link = bin_dir / "tt"
    link.symlink_to(launcher)
    (data / "tinytalk" / "addons" / "bedrock" / "old").mkdir(parents=True)
    (cache / "tinytalk" / "suggestions").mkdir(parents=True)
    config_path = config_home / "tinytalk" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """\
[defaults]
backend = "local"

[backends.local]
kind = "openai-compat"
base_url = "http://x/v1"
model = "m"
keyring_account = "local"
"""
    )
    deleted = []
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, account: deleted.append((service, account))
    )

    assert main(["uninstall", "--yes", "--config", str(config_path)]) == 0

    assert not link.exists()
    assert not (data / "tinytalk" / "tt").exists()
    assert not (data / "tinytalk" / "addons").exists()
    assert not (cache / "tinytalk").exists()
    assert not config_path.parent.exists()
    assert deleted == [("tinytalk", "local")]
    assert "tt zsh integration" in capsys.readouterr().out


def test_config_explanation_off_then_on(tmp_path, capsys):
    from tinytalk.config import load_config

    config_file = tmp_path / "config.toml"
    config_file.write_text(CONFIG)

    assert main(["config", "--config", str(config_file), "explanation", "off"]) == 0
    assert "hidden" in capsys.readouterr().out
    assert load_config(config_file).show_explanation is False
    assert "kind = \"openai-compat\"" in config_file.read_text()  # rest of the file untouched

    assert main(["config", "--config", str(config_file), "explanation", "on"]) == 0
    assert "shown" in capsys.readouterr().out
    assert load_config(config_file).show_explanation is True


def test_run_suppresses_explanation_when_disabled(config_path, stub_backend, capsys):
    from pathlib import Path

    Path(config_path).write_text(CONFIG.replace("[defaults]", "[defaults]\nexplanation = false"))
    assert main(["find big files", "--config", config_path]) == 0
    err = capsys.readouterr().err
    assert "list files by size" not in err
    assert "[danger:" in err


def test_ground_subcommand_reports_and_refreshes(tmp_path, monkeypatch, capsys):
    from tests.test_grounding import make_exe

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "rg", '#!/bin/sh\necho "ripgrep 14.1.0"\n')  # curated, versioned
    monkeypatch.setenv("PATH", str(bin_dir))
    config = tmp_path / "config.toml"
    config.write_text(
        CONFIG.replace("enabled = false", f'enabled = true\ndir = "{tmp_path / "cache"}"')
    )

    assert main(["ground", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "grounding-" in out
    assert "rebuilt in" in out
    assert "binaries: 1   curated installed: 1   versioned: 1" in out

    assert main(["ground", "--config", str(config)]) == 0
    assert "fresh (built" in capsys.readouterr().out

    assert main(["ground", "--refresh", "--config", str(config)]) == 0
    assert "rebuilt in" in capsys.readouterr().out


def test_ground_subcommand_notes_disabled_cache(config_path, capsys):
    assert main(["ground", "--config", config_path]) == 0
    assert "disabled" in capsys.readouterr().out


def test_prompt_subcommand_prints_assembled_prompt(tmp_path, monkeypatch, capsys):
    import os

    from tests.test_grounding import make_exe

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "ls")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("TT_SESSION_CONTEXT", raising=False)
    config = tmp_path / "config.toml"
    config.write_text(
        CONFIG.replace("enabled = false", f'enabled = true\ndir = "{tmp_path / "cache"}"')
    )

    assert main(["prompt", "--config", str(config), "list", "files"]) == 0
    out = capsys.readouterr().out
    system, user = out.split("=== user ===")
    assert "=== system ===" in system
    assert "- ls:" in system
    assert '"danger"' in system
    assert "list files" in user
    assert f"(current directory: {os.getcwd()})" in user  # same assembly a real request sends


def test_prompt_subcommand_shows_language_clause(tmp_path, monkeypatch, capsys):
    """End-to-end threading proof (#107): config → TierRequest → grounding → prompt."""
    from tests.test_grounding import make_exe

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "ls")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("TT_SESSION_CONTEXT", raising=False)
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.replace('backend = "local"', 'backend = "local"\nlanguage = "ko"'))

    assert main(["prompt", "--config", str(config), "list", "files"]) == 0
    assert 'Write the "explanation" value in Korean.' in capsys.readouterr().out


def test_eval_subcommand_renders_leaderboard(config_path, monkeypatch, capsys):
    import tinytalk.eval.runner as runner_mod

    provider = StubProvider(
        Capabilities(),
        lambda request, i: Completion(text=json.dumps(PAYLOAD), usage=Usage(10, 5, 15)),
    )
    monkeypatch.setattr(runner_mod, "make_provider", lambda cfg: provider)
    assert main(["eval", "--config", config_path, "--prompts", "count-lines-code"]) == 0
    out = capsys.readouterr().out
    assert "backend" in out
    assert "count-lines-code" in out


# --- widget streaming preview via TT_WIDGET_PARTIAL (#61) --------------------


class StreamingStub:
    """A streaming backend for the widget-preview wiring: emits the payload as growing
    deltas, then a terminal completion equal to what `complete()` returns."""

    name = "stub"

    def __init__(self, payload: dict, usage: Usage):
        self.capabilities = Capabilities()
        self._json = json.dumps(payload)
        self._usage = usage
        self.stream_calls = 0
        self.complete_calls = 0

    async def complete(self, request):
        self.complete_calls += 1
        return Completion(text=self._json, usage=self._usage)

    async def stream(self, request):
        self.stream_calls += 1
        text = self._json
        step = max(1, len(text) // 3)
        for start in range(0, len(text), step):
            yield StreamChunk(delta=text[start : start + step])
        yield StreamChunk(completion=Completion(text=text, usage=self._usage))


def test_widget_partial_preview_is_additive_and_stdout_unchanged(
    config_path, monkeypatch, tmp_path, capsys
):
    provider = StreamingStub(PAYLOAD, Usage(10, 5, 15))
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)

    # With TT_WIDGET_PARTIAL set, the widget run streams the growing command to the file.
    partial = tmp_path / "widget.partial"
    monkeypatch.setenv("TT_WIDGET_PARTIAL", str(partial))
    assert main(["--config", config_path, "--widget", "list", "files", "by", "size"]) == 0
    streamed_out = capsys.readouterr().out
    assert provider.stream_calls == 1 and provider.complete_calls == 0
    assert partial.read_text() == PAYLOAD["command"]  # preview file got the command

    # With the env unset, the same widget run blocks on complete() — and the final
    # tt_* stdout wire protocol is byte-identical (the preview is a pure add-on).
    monkeypatch.delenv("TT_WIDGET_PARTIAL")
    assert main(["--config", config_path, "--widget", "list", "files", "by", "size"]) == 0
    blocking_out = capsys.readouterr().out
    assert provider.complete_calls == 1  # second run took the blocking path

    assert streamed_out == blocking_out
    assert streamed_out.startswith("tt_command=")
    assert "tt_danger=safe" in streamed_out


def test_partial_env_is_widget_gated(config_path, monkeypatch, tmp_path):
    # TT_WIDGET_PARTIAL only matters in --widget mode; json/plain/eval never stream a preview.
    provider = StreamingStub(PAYLOAD, Usage(10, 5, 15))
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)
    partial = tmp_path / "widget.partial"
    monkeypatch.setenv("TT_WIDGET_PARTIAL", str(partial))
    assert main(["--config", config_path, "--json", "list", "files"]) == 0
    assert not partial.exists()  # no preview outside widget mode
    assert provider.complete_calls == 1 and provider.stream_calls == 0


def test_streaming_smoke_file_grows_and_stdout_matches_blocking(
    config_path, monkeypatch, tmp_path, capsys
):
    """Integration smoke (#61): a streaming backend fills TT_WIDGET_PARTIAL with a
    monotonically growing command, and the final tt_command=/tt_danger=/tt_explanation=
    stdout is byte-identical to the non-streaming widget run for the same suggestion."""
    payload = {
        "command": "find . -type f -name '*.log' -size +10M",
        "explanation": "find large log files",
        "danger": "safe",
        "confidence": 0.9,
        "needs": ["find"],
    }
    usage = Usage(10, 5, 15)

    class CharStreamingStub:
        """Emits the payload one character at a time so the extracted command grows
        step by step, then a terminal completion equal to what complete() returns."""

        name = "stub"

        def __init__(self):
            self.capabilities = Capabilities()
            self._json = json.dumps(payload)
            self.stream_calls = 0
            self.complete_calls = 0

        async def complete(self, request):
            self.complete_calls += 1
            return Completion(text=self._json, usage=usage)

        async def stream(self, request):
            self.stream_calls += 1
            for ch in self._json:
                yield StreamChunk(delta=ch)
            yield StreamChunk(completion=Completion(text=self._json, usage=usage))

    provider = CharStreamingStub()
    monkeypatch.setattr(factory, "make_provider", lambda cfg: provider)

    partial = tmp_path / "widget.partial"

    # Snapshot every atomic commit to the preview file to prove it grows; leave every
    # other os.replace (history writes, etc.) untouched.
    import os as _os

    real_replace = _os.replace
    snapshots: list[str] = []

    def spy_replace(src, dst):
        if str(dst) == str(partial):
            with open(src, encoding="utf-8") as f:
                snapshots.append(f.read())
        return real_replace(src, dst)

    monkeypatch.setattr(_os, "replace", spy_replace)
    monkeypatch.setenv("TT_WIDGET_PARTIAL", str(partial))
    assert main(["--config", config_path, "--widget", "find", "big", "logs"]) == 0
    streamed_out = capsys.readouterr().out

    # The streamed path ran, and the preview file grew monotonically to the full command.
    assert provider.stream_calls == 1 and provider.complete_calls == 0
    assert len(snapshots) >= 3  # several growth steps, not one shot
    for earlier, later in zip(snapshots, snapshots[1:]):
        assert later.startswith(earlier)  # never rewrites backwards
    assert snapshots[-1] == payload["command"]
    assert partial.read_text() == payload["command"]

    # Same suggestion, non-streaming widget run: the tt_* stdout is byte-identical.
    monkeypatch.setattr(_os, "replace", real_replace)
    monkeypatch.delenv("TT_WIDGET_PARTIAL")
    assert main(["--config", config_path, "--widget", "find", "big", "logs"]) == 0
    blocking_out = capsys.readouterr().out
    assert provider.complete_calls == 1  # blocking path this time

    assert streamed_out == blocking_out
    assert streamed_out.startswith("tt_command=")
    assert "tt_danger=" in streamed_out
    assert "tt_explanation=" in streamed_out
