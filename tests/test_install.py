"""Installer script (#58): sandboxed install, config scaffold, idempotent rc wiring."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
MARKER = "# tt zsh integration (added by install.sh)"
PATH_MARKER = "# tt PATH (added by install.sh)"


def make_exe(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def sandbox(tmp_path):
    """Fake HOME plus a bin dir with stub `uv` and `tt` on an isolated PATH."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    uv_log = tmp_path / "uv.log"
    make_exe(fakebin, "uv", f'#!/bin/sh\necho "$@" >> "{uv_log}"\nexit 0\n')
    tt_log = tmp_path / "tt.log"
    make_exe(
        fakebin,
        "tt",
        f'#!/bin/sh\necho "$@" >> "{tt_log}"\n'
        'if [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\nexit 0\n',
    )
    env = {
        "HOME": str(home),
        "PATH": f"{fakebin}:/usr/bin:/bin",
    }
    return home, env, uv_log


def run_install(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALL), *args], env=env, capture_output=True, text=True, timeout=30
    )


def test_syntax_is_posix_clean():
    proc = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_install_scaffolds_and_wires(sandbox):
    home, env, uv_log = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr

    # the CLI was installed from the repo clone via uv
    assert f"tool install --force {REPO}" in uv_log.read_text()

    config = home / ".config" / "tinytalk" / "config.toml"
    assert "[defaults]" in config.read_text()

    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(MARKER) == 1
    assert 'eval "$(tt init zsh)"' in zshrc


def test_second_run_changes_nothing(sandbox):
    home, env, _ = sandbox
    assert run_install(env, "--yes").returncode == 0
    config = home / ".config" / "tinytalk" / "config.toml"
    zshrc = home / ".zshrc"
    config_before, zshrc_before = config.read_text(), zshrc.read_text()

    proc = run_install(env, "--yes")
    assert proc.returncode == 0
    assert config.read_text() == config_before
    assert zshrc.read_text() == zshrc_before
    assert zshrc.read_text().count(MARKER) == 1


def test_existing_config_and_zshrc_content_untouched(sandbox):
    home, env, _ = sandbox
    config_dir = home / ".config" / "tinytalk"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("# my precious config\n")
    (home / ".zshrc").write_text("# my precious zshrc\n")

    assert run_install(env, "--yes").returncode == 0
    assert (config_dir / "config.toml").read_text() == "# my precious config\n"
    zshrc = (home / ".zshrc").read_text()
    assert zshrc.startswith("# my precious zshrc\n")
    assert zshrc.count(MARKER) == 1  # appended once, nothing replaced


def test_install_warms_grounding_cache(sandbox, tmp_path):
    _, env, _ = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "ground --refresh" in (tmp_path / "tt.log").read_text()
    assert "warmed the tool snapshot" in proc.stdout


def test_failing_ground_does_not_fail_install(sandbox):
    home, env, _ = sandbox
    make_exe(
        Path(env["PATH"].split(os.pathsep)[0]),
        "tt",
        '#!/bin/sh\nif [ "$1" = "ground" ]; then exit 1; fi\n'
        'if [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\nexit 0\n',
    )
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert (home / ".zshrc").read_text().count(MARKER) == 1  # later steps still ran


def test_no_rc_flag_skips_zshrc(sandbox):
    home, env, _ = sandbox
    assert run_install(env, "--yes", "--no-rc").returncode == 0
    assert not (home / ".zshrc").exists()


def test_prompt_defaults_to_no(sandbox):
    home, env, _ = sandbox
    proc = subprocess.run(
        ["sh", str(INSTALL)],
        env=env,
        input="\n",  # user just presses Enter → default No
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".zshrc").exists()


def test_rc_step_self_skips_without_init_zsh(sandbox, tmp_path):
    home, env, _ = sandbox
    # a tt build whose `init zsh` fails (pre-#57 skeleton)
    make_exe(
        Path(env["PATH"].split(os.pathsep)[0]),
        "tt",
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "tt 0.0.1"; exit 0; fi\nexit 1\n',
    )
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".zshrc").exists()
    assert "doesn't support 'init zsh' yet" in proc.stdout


def test_fails_actionably_without_uv_or_pipx(sandbox, tmp_path):
    home, env, _ = sandbox
    emptybin = tmp_path / "emptybin"
    emptybin.mkdir()
    env["PATH"] = f"{emptybin}:/usr/bin:/bin"  # no uv, no pipx, no tt
    proc = subprocess.run(
        ["sh", str(INSTALL)],
        env=env,
        input="n\n",  # decline the uv bootstrap offer
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "uv" in proc.stderr
    assert "astral.sh" in proc.stderr


def test_yes_bootstraps_uv_when_missing(tmp_path):
    """No uv/pipx anywhere: --yes fetches the (stubbed) uv installer and proceeds."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    uv_log = tmp_path / "uv.log"
    # what the stubbed `curl` serves: an installer that drops uv + tt into ~/.local/bin
    installer = tmp_path / "fake-uv-install.sh"
    installer.write_text(
        'mkdir -p "$HOME/.local/bin"\n'
        f'printf \'#!/bin/sh\\necho "$@" >> "{uv_log}"\\nexit 0\\n\' > "$HOME/.local/bin/uv"\n'
        'printf \'#!/bin/sh\\nif [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\\nexit 0\\n\''
        ' > "$HOME/.local/bin/tt"\n'
        'chmod +x "$HOME/.local/bin/uv" "$HOME/.local/bin/tt"\n'
    )
    make_exe(fakebin, "curl", f'#!/bin/sh\ncat "{installer}"\n')
    env = {"HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin"}

    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert f"tool install --force {REPO}" in uv_log.read_text()
    # ~/.local/bin wasn't on the user's PATH → the PATH block got wired, once,
    # with $HOME kept symbolic — and before the widget block so its eval resolves tt
    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(PATH_MARKER) == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in zshrc
    assert zshrc.index(PATH_MARKER) < zshrc.index(MARKER)

    proc = run_install(env, "--yes")  # second run: nothing duplicated
    assert proc.returncode == 0, proc.stderr
    assert (home / ".zshrc").read_text() == zshrc


def test_path_wiring_when_tt_lands_off_path(tmp_path):
    """uv exists but installs to a dir outside PATH → consented marker block."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    uv_log = tmp_path / "uv.log"
    make_exe(
        fakebin,
        "uv",
        f'#!/bin/sh\necho "$@" >> "{uv_log}"\n'
        'if [ "$1" = tool ] && [ "$2" = dir ]; then echo "$HOME/toolbin"; exit 0; fi\n'
        'if [ "$1" = tool ] && [ "$2" = install ]; then\n'
        '  mkdir -p "$HOME/toolbin"\n'
        '  printf \'#!/bin/sh\\nif [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\\nexit 0\\n\''
        ' > "$HOME/toolbin/tt"\n'
        '  chmod +x "$HOME/toolbin/tt"\nfi\nexit 0\n',
    )
    env = {"HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin"}  # no tt on PATH

    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "installed: tt 0.0.1" in proc.stdout
    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(PATH_MARKER) == 1
    assert 'export PATH="$HOME/toolbin:$PATH"' in zshrc

    # --no-rc never touches the rc file, even when tt is off PATH
    home2 = tmp_path / "home2"
    home2.mkdir()
    env2 = dict(env, HOME=str(home2))
    proc = run_install(env2, "--yes", "--no-rc")
    assert proc.returncode == 0, proc.stderr
    assert not (home2 / ".zshrc").exists()
    assert "add it yourself" in proc.stdout


def test_unknown_flag_fails(sandbox):
    _, env, _ = sandbox
    proc = run_install(env, "--frobnicate")
    assert proc.returncode == 2
    assert "unknown option" in proc.stderr
