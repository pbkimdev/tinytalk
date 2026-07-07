"""Uninstaller (scripts/uninstall.sh): the reverse of install.sh.

Hermetic — a fully pinned env (HOME plus every XDG_* dir inside the sandbox) keeps
every `rm -rf` boxed in tmp_path, and a stub `tt` on PATH stands in for the real
binary. The uninstaller delegates file/keyring removal to `tt uninstall` when the
binary runs, and strips the "added by install.sh" rc blocks itself.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UNINSTALL = REPO / "scripts" / "uninstall.sh"
PATH_MARKER = "# tt PATH (added by install.sh)"
ZSH_MARKER = "# tt zsh integration (added by install.sh)"

# The exact blocks install.sh appends — the uninstaller must strip these and nothing else.
PATH_BLOCK = f'\n{PATH_MARKER}\nexport PATH="$HOME/.local/bin:$PATH"\n'
ZSH_BLOCK = (
    f"\n{ZSH_MARKER}\n"
    '_tt_cache="${XDG_CACHE_HOME:-$HOME/.cache}/tinytalk/init.zsh"\n'
    "_tt_bin=$(command -v tt)\n"
    "if [[ -n $_tt_bin && ( ! -s $_tt_cache || $_tt_bin -nt $_tt_cache ) ]]; then\n"
    '  command mkdir -p "${_tt_cache:h}" && "$_tt_bin" init zsh >| $_tt_cache 2>/dev/null\n'
    "fi\n"
    "[[ -s $_tt_cache ]] && source $_tt_cache\n"
    "unset _tt_cache _tt_bin\n"
)

# A stub `tt` that logs its args and answers `uninstall --help` so the script
# detects a working binary and delegates to it.
STUB_TT = (
    "#!/bin/sh\n"
    'echo "$@" >> "{log}"\n'
    'case "$1 $2" in "uninstall --help") exit 0 ;; esac\n'
    "exit 0\n"
)


def make_exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def sandbox(tmp_path):
    """A boxed HOME with every XDG_* pinned inside it, so nothing the uninstaller
    removes can escape tmp_path. Returns (home, env, tt_log)."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "share" / "tinytalk" / "tt").mkdir(parents=True)
    (home / ".local" / "share" / "tinytalk" / "addons").mkdir(parents=True)
    (home / ".config" / "tinytalk").mkdir(parents=True)
    (home / ".cache" / "tinytalk").mkdir(parents=True)
    tt_log = tmp_path / "tt.log"
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/usr/bin:/bin",
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TT_LOG": str(tt_log),
    }
    return home, env, tt_log


def run(env: dict, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(UNINSTALL), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


def install_stub_tt(home: Path) -> None:
    make_exe(home / ".local" / "bin" / "tt", STUB_TT.format(log="$TT_LOG"))


def test_syntax_is_posix_clean():
    proc = subprocess.run(["sh", "-n", str(UNINSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_delegates_to_tt_and_strips_rc_blocks(sandbox):
    home, env, tt_log = sandbox
    install_stub_tt(home)
    (home / ".zshrc").write_text("# my zshrc\nalias foo=bar\n" + PATH_BLOCK + ZSH_BLOCK + "export EDITOR=vim\n")

    proc = run(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    # file/keyring removal is delegated to `tt uninstall --yes`
    assert "uninstall --yes" in tt_log.read_text()
    # the rc blocks are gone, the user's own lines survive
    zshrc = (home / ".zshrc").read_text()
    assert PATH_MARKER not in zshrc and ZSH_MARKER not in zshrc
    assert "alias foo=bar" in zshrc and "export EDITOR=vim" in zshrc


def test_fallback_removes_files_and_warns_about_keyring(sandbox):
    home, env, _ = sandbox  # no stub tt → the binary is "gone"
    (home / ".zshrc").write_text(PATH_BLOCK)

    proc = run(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".local" / "bin" / "tt").exists()
    assert not (home / ".local" / "share" / "tinytalk" / "tt").exists()
    assert not (home / ".local" / "share" / "tinytalk" / "addons").exists()
    assert not (home / ".cache" / "tinytalk").exists()
    assert not (home / ".config" / "tinytalk").exists()
    assert "keyring" in proc.stdout.lower()
    assert PATH_MARKER not in (home / ".zshrc").read_text()


def test_keep_config_leaves_config_in_fallback(sandbox):
    home, env, _ = sandbox
    proc = run(env, "--yes", "--keep-config")
    assert proc.returncode == 0, proc.stderr
    assert (home / ".config" / "tinytalk").is_dir()  # kept
    assert not (home / ".cache" / "tinytalk").exists()  # still removed


def test_no_rc_leaves_rc_files_untouched(sandbox):
    home, env, _ = sandbox
    original = "# precious\n" + PATH_BLOCK
    (home / ".zshrc").write_text(original)
    proc = run(env, "--yes", "--no-rc")
    assert proc.returncode == 0, proc.stderr
    assert (home / ".zshrc").read_text() == original  # markers left in place


def test_second_run_is_a_clean_noop(sandbox):
    home, env, _ = sandbox
    (home / ".zshrc").write_text("# precious\n" + ZSH_BLOCK)
    assert run(env, "--yes").returncode == 0
    after_first = (home / ".zshrc").read_text()
    assert ZSH_MARKER not in after_first
    assert run(env, "--yes").returncode == 0  # nothing left to do
    assert (home / ".zshrc").read_text() == after_first


def test_prompt_defaults_to_no(sandbox):
    home, env, _ = sandbox
    proc = run(env, stdin="\n")  # just press Enter with no tty → cancel
    assert proc.returncode == 1
    assert "cancelled" in proc.stdout
    assert (home / ".config" / "tinytalk").is_dir()  # nothing removed


def test_unknown_flag_fails(sandbox):
    _, env, _ = sandbox
    proc = run(env, "--frobnicate")
    assert proc.returncode == 2
    assert "unknown option" in proc.stderr
