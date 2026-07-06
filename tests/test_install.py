"""Installer (#58, binary-downloader rewrite): unpack the --onedir bundle, symlink
its launcher onto PATH, wire PATH + the ? widget.

Hermetic — the download is short-circuited with `TT_BINARY` (a local bundle) or a
stubbed `curl`, so these tests never touch the network. The bundle mirrors what the
release workflow ships (`tar -C dist tt`): a `tt/tt` launcher plus `tt/_internal/`.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
MARKER = "# tt zsh integration (added by install.sh)"
PATH_MARKER = "# tt PATH (added by install.sh)"
INSTALLED = "installed: tt 0.0.1"  # the launcher stub reports this version

# A stub `tt` launcher for inside the bundle. It logs every invocation to {log}
# so a test can assert what the installer called it with. `{log}` is the only
# brace — the body is filled via str.format, so keep other shell braces out.
DEFAULT_LAUNCHER = (
    "#!/bin/sh\n"
    'echo "$@" >> "{log}"\n'
    'if [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\n'
    "exit 0\n"
)


def make_exe(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_bundle(
    tmp_path: Path, tt_log: Path, *, tag: str = "base", launcher: str | None = None
) -> Path:
    """Build a fake --onedir bundle tarball (tt/tt launcher + tt/_internal/ libs)."""
    stage = tmp_path / f"stage-{tag}"
    internal = stage / "tt" / "_internal"
    internal.mkdir(parents=True)
    (internal / "payload").write_text("libs\n")
    make_exe(stage / "tt", "tt", (launcher or DEFAULT_LAUNCHER).format(log=tt_log))
    bundle = tmp_path / f"tt-{tag}.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(stage / "tt", arcname="tt")
    return bundle


def serving_curl(bundle: Path, sha: Path) -> str:
    """A `curl` stub that serves the bundle, or its .sha256 sidecar, from local files."""
    return (
        "#!/bin/sh\n"
        'url=""; out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -o) shift; out="$1" ;;\n'
        '    http://*|https://*) url="$1" ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
        'case "$url" in\n'
        f'  *.sha256) cp "{sha}" "$out" ;;\n'
        f'  *) cp "{bundle}" "$out" ;;\n'
        "esac\n"
    )


@pytest.fixture
def sandbox(tmp_path):
    """Fake HOME with the default bin dir (~/.local/bin) already on PATH, and
    TT_BINARY pointing at a stub bundle — install runs fully offline and doesn't
    trigger PATH wiring (that path has its own tests)."""
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/usr/bin:/bin",
        "TT_BINARY": str(bundle),
    }
    return home, env, tt_log


def run_install(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALL), *args], env=env, capture_output=True, text=True, timeout=30
    )


def test_syntax_is_posix_clean():
    proc = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_install_unpacks_symlinks_and_wires(sandbox):
    home, env, _ = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert INSTALLED in proc.stdout

    # the onedir bundle was unpacked to the lib dir; the launcher was symlinked onto PATH
    launcher = home / ".local" / "share" / "tinytalk" / "tt" / "tt"
    link = home / ".local" / "bin" / "tt"
    assert launcher.is_file()
    assert link.is_symlink() and os.readlink(link) == str(launcher)

    assert not (home / ".config" / "tinytalk" / "config.toml").exists()
    assert "tt auth" in proc.stdout

    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(MARKER) == 1
    assert PATH_MARKER not in zshrc  # the bin dir was already on PATH — no PATH block
    assert '"$_tt_bin" init zsh' in zshrc  # the cached-init widget block, not a raw eval


def test_second_run_changes_nothing(sandbox):
    home, env, _ = sandbox
    assert run_install(env, "--yes").returncode == 0
    zshrc = home / ".zshrc"
    zshrc_before = zshrc.read_text()

    assert run_install(env, "--yes").returncode == 0
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


def test_install_warms_grounding_cache(sandbox):
    home, env, tt_log = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "ground --refresh" in tt_log.read_text()
    assert "warmed the tool snapshot" in proc.stdout


def test_failing_ground_does_not_fail_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    launcher = (
        "#!/bin/sh\n"
        'echo "$@" >> "{log}"\n'
        'case "$1" in\n'
        '  --version) echo "tt 0.0.1" ;;\n'
        "  ground) exit 1 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    bundle = make_bundle(tmp_path, tt_log, tag="groundfail", launcher=launcher)
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/bin:/bin", "TT_BINARY": str(bundle)}
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


def test_rc_step_self_skips_without_init_zsh(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    # a tt build whose `init zsh` fails (pre-#57 skeleton): only --version works
    launcher = (
        "#!/bin/sh\n"
        'echo "$@" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "tt 0.0.1"; exit 0; fi\n'
        "exit 1\n"
    )
    bundle = make_bundle(tmp_path, tt_log, tag="noinit", launcher=launcher)
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/bin:/bin", "TT_BINARY": str(bundle)}
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".zshrc").exists()
    assert "doesn't support 'init zsh'" in proc.stdout


def test_downloads_and_verifies_checksum(tmp_path):
    """No TT_BINARY: install fetches via (stubbed) curl and verifies the sha256 sidecar."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    sha = tmp_path / "bundle.sha256"
    sha.write_text(f"{digest}  {bundle.name}\n")
    make_exe(fakebin, "curl", serving_curl(bundle, sha))
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "checksum: ok" in proc.stdout
    assert INSTALLED in proc.stdout
    assert (home / ".local" / "share" / "tinytalk" / "tt" / "tt").is_file()


def test_checksum_mismatch_aborts(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    sha = tmp_path / "bad.sha256"
    sha.write_text(f"{'0' * 64}  {bundle.name}\n")  # wrong digest
    make_exe(fakebin, "curl", serving_curl(bundle, sha))
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes")
    assert proc.returncode == 1
    assert "checksum mismatch" in proc.stderr
    assert not (home / ".local" / "bin" / "tt").exists()  # aborted before install


def test_download_failure_is_actionable(tmp_path):
    """A failing download dies non-zero and names the URL it tried."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    make_exe(fakebin, "curl", "#!/bin/sh\nexit 22\n")  # curl's code for an HTTP 4xx/5xx
    env = {
        "HOME": str(home),
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes")
    assert proc.returncode == 1
    assert "download failed" in proc.stderr
    assert "example.test" in proc.stderr


def test_path_wiring_when_tt_lands_off_path(tmp_path):
    """Bundle installs into ~/toolbin (off PATH) → a consented, marker-guarded PATH block."""
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    # SHELL must be explicit: macOS ships /usr/bin/tt (teletype) and the installer
    # picks PATH_RC from SHELL; an inherited bash SHELL would wire ~/.bashrc instead.
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TT_BINARY": str(bundle),
        "SHELL": "/bin/zsh",
    }

    proc = run_install(env, "--yes", "--bin-dir", f"{home}/toolbin")
    assert proc.returncode == 0, proc.stderr
    assert INSTALLED in proc.stdout
    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(PATH_MARKER) == 1
    assert 'export PATH="$HOME/toolbin:$PATH"' in zshrc  # $HOME kept symbolic
    assert zshrc.index(PATH_MARKER) < zshrc.index(MARKER)  # PATH block precedes the widget

    # --no-rc never touches the rc file, even when tt is off PATH
    home2 = tmp_path / "home2"
    home2.mkdir()
    env2 = dict(env, HOME=str(home2))
    proc = run_install(env2, "--yes", "--no-rc", "--bin-dir", f"{home2}/toolbin")
    assert proc.returncode == 0, proc.stderr
    assert not (home2 / ".zshrc").exists()
    assert "add it yourself" in proc.stdout

    # a bash user gets ~/.bashrc (and ~/.bash_profile when it exists) for PATH, not ~/.zshrc
    home3 = tmp_path / "home3"
    home3.mkdir()
    (home3 / ".bash_profile").write_text("# my precious bash_profile\n")
    env3 = dict(env, HOME=str(home3), SHELL="/bin/bash")
    proc = run_install(env3, "--yes", "--bin-dir", f"{home3}/toolbin")
    assert proc.returncode == 0, proc.stderr
    bashrc = (home3 / ".bashrc").read_text()
    assert bashrc.count(PATH_MARKER) == 1
    assert 'export PATH="$HOME/toolbin:$PATH"' in bashrc
    profile = (home3 / ".bash_profile").read_text()
    assert profile.startswith("# my precious bash_profile\n")
    assert profile.count(PATH_MARKER) == 1
    zshrc3 = (home3 / ".zshrc").read_text()  # widget still wires zshrc, but no PATH block
    assert PATH_MARKER not in zshrc3
    assert zshrc3.count(MARKER) == 1

    proc = run_install(env3, "--yes", "--bin-dir", f"{home3}/toolbin")  # idempotent
    assert proc.returncode == 0, proc.stderr
    assert (home3 / ".bashrc").read_text() == bashrc
    assert (home3 / ".bash_profile").read_text() == profile


def test_zsh_user_path_lands_only_in_the_primary_shell(tmp_path):
    """A zsh user who also keeps a ~/.bashrc gets PATH only in ~/.zshrc — the
    installer wires the primary shell, not both. ~/.bashrc is left untouched and
    ~/.bash_profile is never created."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("# my precious bashrc\n")  # they use bash too
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TT_BINARY": str(bundle),
        "SHELL": "/bin/zsh",
    }

    assert run_install(env, "--yes", "--bin-dir", f"{home}/toolbin").returncode == 0
    assert (home / ".zshrc").read_text().count(PATH_MARKER) == 1
    assert (home / ".bashrc").read_text() == "# my precious bashrc\n"  # not wired
    assert not (home / ".bash_profile").exists()  # never created for an unused shell


def test_unknown_flag_fails(sandbox):
    _, env, _ = sandbox
    proc = run_install(env, "--frobnicate")
    assert proc.returncode == 2
    assert "unknown option" in proc.stderr


def test_version_flag_pins_release_tag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    make_exe(
        fakebin,
        "curl",
        "#!/bin/sh\n"
        'url=""; out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in -o) shift; out="$1" ;; http://*|https://*) url="$1" ;; esac; shift; done\n'
        "case \"$url\" in\n"
        "  *.sha256) exit 22 ;;\n"
        f'  *v0.2.0rc3*) cp "{bundle}" "$out" ;;\n'
        "  *) exit 22 ;;\n"
        "esac\n",
    )
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes", "--version", "0.2.0rc3")
    assert proc.returncode == 0, proc.stderr
    assert "downloading tt-linux-x86_64.tar.gz (v0.2.0rc3)" in proc.stdout


def test_tt_version_env_pins_release_without_flag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    make_exe(
        fakebin,
        "curl",
        "#!/bin/sh\n"
        'url=""; out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in -o) shift; out="$1" ;; http://*|https://*) url="$1" ;; esac; shift; done\n'
        "case \"$url\" in\n"
        "  *.sha256) exit 22 ;;\n"
        f'  *v0.2.0rc3*) cp "{bundle}" "$out" ;;\n'
        "  *) exit 22 ;;\n"
        "esac\n",
    )
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_VERSION": "v0.2.0rc3",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "downloading tt-linux-x86_64.tar.gz (v0.2.0rc3)" in proc.stdout


def test_version_equals_form_pins_release_tag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    make_exe(
        fakebin,
        "curl",
        "#!/bin/sh\n"
        'url=""; out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in -o) shift; out="$1" ;; http://*|https://*) url="$1" ;; esac; shift; done\n'
        "case \"$url\" in\n"
        "  *.sha256) exit 22 ;;\n"
        f'  *v0.2.0rc3*) cp "{bundle}" "$out" ;;\n'
        "  *) exit 22 ;;\n"
        "esac\n",
    )
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes", "--version=v0.2.0rc3")
    assert proc.returncode == 0, proc.stderr
    assert "(v0.2.0rc3)" in proc.stdout
