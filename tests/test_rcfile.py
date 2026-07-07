"""rcfile (#129, interactive-installer epic #127): marker-guarded rc-file blocks.

The module must write and strip blocks byte-identically to what
scripts/install.sh writes and scripts/uninstall.sh strips today — that's the
compatibility key that lets `tt setup` take over rc-writing later (S3) without
breaking existing installs or the install-CI idempotency assertions. The
cross-check test below proves it by running the *real* scripts/uninstall.sh
against a file rcfile.py wrote.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from tinytalk.rcfile import ZSH_MARKER, ensure_block, has_block, remove_block, zsh_integration_block

REPO = Path(__file__).resolve().parent.parent
UNINSTALL = REPO / "scripts" / "uninstall.sh"

MARKER = "# marker"
BLOCK = "line one\nline two\n"


def test_has_block_truthiness(tmp_path):
    path = tmp_path / ".rc"
    assert has_block(path, MARKER) is False  # missing file
    path.write_text("something else\n")
    assert has_block(path, MARKER) is False
    path.write_text(f"something else\n{MARKER}\nmore\n")
    assert has_block(path, MARKER) is True


def test_ensure_block_creates_missing_file(tmp_path):
    path = tmp_path / ".rc"
    assert not path.exists()
    assert ensure_block(path, MARKER, BLOCK) is True
    assert path.read_text() == f"\n{MARKER}\n{BLOCK}"


def test_ensure_block_does_not_glue_onto_last_line(tmp_path):
    path = tmp_path / ".rc"
    path.write_text("alias foo=bar")  # no trailing newline
    ensure_block(path, MARKER, BLOCK)
    assert path.read_text() == f"alias foo=bar\n\n{MARKER}\n{BLOCK}"


def test_ensure_block_twice_writes_once(tmp_path):
    path = tmp_path / ".rc"
    path.write_text("# precious\n")
    assert ensure_block(path, MARKER, BLOCK) is True
    once = path.read_text()
    assert ensure_block(path, MARKER, BLOCK) is False
    assert path.read_text() == once
    assert once.count(MARKER) == 1


def test_remove_block_restores_original_when_block_is_at_end(tmp_path):
    path = tmp_path / ".rc"
    original = "# precious\nalias foo=bar\n"
    path.write_text(original)
    assert ensure_block(path, MARKER, BLOCK) is True
    assert path.read_text() != original

    assert remove_block(path, MARKER) is True
    assert path.read_text() == original


def test_remove_block_leaves_a_later_block_untouched(tmp_path):
    """Content before AND after the removed block: a second, distinct block
    appended afterwards (each ensure_block call gets its own leading blank
    separator, matching install.sh's convention) must survive intact."""
    path = tmp_path / ".rc"
    original = "# precious\n"
    path.write_text(original)
    ensure_block(path, MARKER, BLOCK)
    other_marker, other_block = "# other", "other line\n"
    ensure_block(path, other_marker, other_block)

    assert remove_block(path, MARKER) is True
    text = path.read_text()
    assert MARKER not in text
    assert has_block(path, other_marker)
    assert text == f"{original}\n{other_marker}\n{other_block}"


def test_remove_block_missing_marker_is_a_noop(tmp_path):
    path = tmp_path / ".rc"
    path.write_text("# precious\n")
    assert remove_block(path, MARKER) is False
    assert path.read_text() == "# precious\n"


def test_remove_block_missing_file_is_a_noop(tmp_path):
    path = tmp_path / ".rc"
    assert remove_block(path, MARKER) is False
    assert not path.exists()


def test_zsh_integration_block_matches_install_sh_output():
    marker, block = zsh_integration_block()
    assert marker == ZSH_MARKER == "# tt zsh integration (added by install.sh)"
    assert block == (
        '_tt_cache="${XDG_CACHE_HOME:-$HOME/.cache}/tinytalk/init.zsh"\n'
        "_tt_bin=$(command -v tt)\n"
        "if [[ -n $_tt_bin && ( ! -s $_tt_cache || $_tt_bin -nt $_tt_cache ) ]]; then\n"
        '  command mkdir -p "${_tt_cache:h}" && "$_tt_bin" init zsh >| $_tt_cache 2>/dev/null\n'
        "fi\n"
        "[[ -s $_tt_cache ]] && source $_tt_cache\n"
        "unset _tt_cache _tt_bin\n"
    )


def test_marker_matches_scripts_uninstall_sh():
    """Source-consistency check: the marker rcfile.py writes must be the same
    literal string uninstall.sh keys off of. (install.sh no longer writes the
    block itself — `tt setup` does, via this module; #131.)"""
    uninstall_text = UNINSTALL.read_text()
    assert f'"{ZSH_MARKER}"' in uninstall_text


def _make_exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_block_written_by_zsh_integration_block_is_stripped_cleanly_by_uninstall_sh(tmp_path):
    """Round-trip through the *real* scripts/uninstall.sh: ensure_block writes
    the zsh block, uninstall.sh's strip_blocks removes it, and the rc file
    comes back byte-identical to what it was before."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "share" / "tinytalk" / "tt").mkdir(parents=True)
    (home / ".local" / "share" / "tinytalk" / "addons").mkdir(parents=True)
    (home / ".config" / "tinytalk").mkdir(parents=True)
    (home / ".cache" / "tinytalk").mkdir(parents=True)

    zshrc = home / ".zshrc"
    original = "# my precious zshrc\nalias foo=bar\n"
    zshrc.write_text(original)

    marker, block = zsh_integration_block()
    assert ensure_block(zshrc, marker, block) is True
    assert zshrc.read_text() != original

    # A stub `tt` so uninstall.sh delegates cleanly instead of needing a real binary.
    _make_exe(
        home / ".local" / "bin" / "tt",
        '#!/bin/sh\ncase "$1 $2" in "uninstall --help") exit 0 ;; esac\nexit 0\n',
    )

    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/usr/bin:/bin",
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }
    proc = subprocess.run(
        ["sh", str(UNINSTALL), "--yes"], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert zshrc.read_text() == original
