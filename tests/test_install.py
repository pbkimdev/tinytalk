"""Installer (#58, binary-downloader rewrite; #131, tt-setup handoff): unpack the
--onedir bundle, symlink its launcher onto PATH, scaffold config, wire PATH, and
hand off zsh-widget/model/language setup to `tt setup`.

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
INSTALL = REPO / "scripts" / "install.sh"
PATH_MARKER = "# tt PATH (added by install.sh)"
INSTALLED = "installed: tt 0.0.1"  # the launcher stub reports this version

# A stub `tt` launcher for inside the bundle. It logs every invocation to {log}
# so a test can assert what the installer called it with. `{log}` is the only
# brace — the body is filled via str.format, so keep other shell braces out.
DEFAULT_LAUNCHER = (
    '#!/bin/sh\necho "$@" >> "{log}"\nif [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\nexit 0\n'
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
    # start_new_session detaches the controlling terminal, so /dev/tty exists but
    # won't open — every run here is deterministically "headless" even when pytest
    # itself runs from an interactive terminal (install.sh probes openability).
    return subprocess.run(
        ["sh", str(INSTALL), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        start_new_session=True,
    )


def test_syntax_is_posix_clean():
    proc = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_linux_release_build_is_gated_on_ubuntu_20_04():
    release = (REPO / ".github" / "workflows" / "release.yml").read_text()
    install = (REPO / ".github" / "workflows" / "install.yml").read_text()
    builder = (REPO / "scripts" / "build-binary.sh").read_text()

    assert "os: ubuntu-24.04" in release
    assert "os: ubuntu-24.04-arm" in release
    assert 'UV_PROJECT_ENVIRONMENT="$RUNNER_TEMP/tinytalk-build-venv"' in release
    assert "ubuntu:20.04 sh /work/scripts/verify-linux-binary.sh /opt/tinytalk" in release
    assert 'UV_PROJECT_ENVIRONMENT="$RUNNER_TEMP/tinytalk-build-venv"' in install
    assert "ubuntu:20.04 sh /work/scripts/verify-linux-binary.sh /opt/tinytalk" in install
    assert "UV_PYTHON_PREFERENCE=only-managed" in builder
    assert "manylinux_2_28" in builder
    assert '--python-platform "$UV_PYTHON_PLATFORM"' in builder
    assert "--specpath" not in builder
    assert "rm -f dist/tt/_internal/libgcc_s.so.1" in builder
    assert 'uv python install "$UV_PYTHON"' in builder


def test_install_unpacks_symlinks_scaffolds_and_wires(sandbox):
    home, env, _ = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert INSTALLED in proc.stdout

    # the onedir bundle was unpacked to the lib dir; the launcher was symlinked onto PATH
    launcher = home / ".local" / "share" / "tinytalk" / "tt" / "tt"
    link = home / ".local" / "bin" / "tt"
    assert launcher.is_file()
    assert link.is_symlink() and os.readlink(link) == str(launcher)

    config = home / ".config" / "tinytalk" / "config.toml"
    text = config.read_text()
    assert "[cache]\nenabled = true" in text
    assert "run `tt auth`" in text
    assert "\n[defaults]" not in text
    assert "\n[backends.local]" not in text
    assert "# [backends.local]" in text

    # the zsh widget and model setup are handed off to `tt setup` now, not written here
    assert not (home / ".zshrc").exists()
    assert "setup: run 'tt setup'" in proc.stdout


def test_second_run_changes_nothing(sandbox):
    home, env, _ = sandbox
    assert run_install(env, "--yes").returncode == 0
    config = home / ".config" / "tinytalk" / "config.toml"
    config_before = config.read_text()

    assert run_install(env, "--yes").returncode == 0
    assert config.read_text() == config_before
    assert not (home / ".zshrc").exists()  # install.sh never writes it under --yes


def test_existing_config_and_zshrc_content_untouched(sandbox):
    home, env, _ = sandbox
    config_dir = home / ".config" / "tinytalk"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("# my precious config\n")
    (home / ".zshrc").write_text("# my precious zshrc\n")

    assert run_install(env, "--yes").returncode == 0
    assert (config_dir / "config.toml").read_text() == "# my precious config\n"
    assert (home / ".zshrc").read_text() == "# my precious zshrc\n"  # left untouched


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
    assert "setup: run 'tt setup'" in proc.stdout  # later steps still ran


def test_install_retries_a_transient_first_launcher_failure(tmp_path):
    """A freshly unpacked launcher can fail once while the same binary works immediately after."""
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    first_run = tmp_path / "first-version-run"
    launcher = (
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ] && [ ! -f "{first_run}" ]; then\n'
        f'  touch "{first_run}"\n'
        '  echo "transient loader failure" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ "$1" = "--version" ]; then echo "tt 0.0.1"; fi\n'
        "exit 0\n"
    )
    bundle = make_bundle(tmp_path, tt_log, tag="transient-launch", launcher=launcher)
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/bin:/bin", "TT_BINARY": str(bundle)}

    proc = run_install(env, "--yes")

    assert proc.returncode == 0, proc.stderr
    assert INSTALLED in proc.stdout
    assert "first launch failed; retrying once" in proc.stderr


def test_failed_launcher_keeps_previous_install_and_reports_loader_error(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    old_launcher = '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "tt previous"; fi\nexit 0\n'
    old_bundle = make_bundle(tmp_path, tt_log, tag="previous", launcher=old_launcher)
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/usr/bin:/bin",
        "TT_BINARY": str(old_bundle),
    }
    assert run_install(env, "--yes").returncode == 0

    broken_launcher = '#!/bin/sh\necho "missing shared loader" >&2\nexit 127\n'
    broken_bundle = make_bundle(tmp_path, tt_log, tag="broken", launcher=broken_launcher)
    proc = run_install(dict(env, TT_BINARY=str(broken_bundle)), "--yes")

    assert proc.returncode == 1
    assert "missing shared loader" in proc.stderr
    installed = home / ".local" / "bin" / "tt"
    version = subprocess.run([installed, "--version"], capture_output=True, text=True)
    assert version.returncode == 0
    assert version.stdout.strip() == "tt previous"


def test_activation_failure_restores_previous_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    old_launcher = '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "tt previous"; fi\nexit 0\n'
    old_bundle = make_bundle(tmp_path, tt_log, tag="activation-previous", launcher=old_launcher)
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:/usr/bin:/bin",
        "TT_BINARY": str(old_bundle),
    }
    assert run_install(env, "--yes").returncode == 0

    stage_only_launcher = (
        "#!/bin/sh\n"
        'case "$0" in\n'
        '  */.tt-install.*/tt/tt) echo "tt staged"; exit 0 ;;\n'
        '  *) echo "activation path failed" >&2; exit 127 ;;\n'
        "esac\n"
    )
    candidate = make_bundle(tmp_path, tt_log, tag="activation-fail", launcher=stage_only_launcher)
    proc = run_install(dict(env, TT_BINARY=str(candidate)), "--yes")

    assert proc.returncode == 1
    assert "activation path failed" in proc.stderr
    assert "previous installation restored" in proc.stderr
    installed = home / ".local" / "bin" / "tt"
    version = subprocess.run([installed, "--version"], capture_output=True, text=True)
    assert version.returncode == 0
    assert version.stdout.strip() == "tt previous"


def test_no_rc_flag_skips_zshrc(sandbox):
    home, env, _ = sandbox
    assert run_install(env, "--yes", "--no-rc").returncode == 0
    assert not (home / ".zshrc").exists()


def test_setup_handoff_skips_for_yes_no_rc_or_headless(sandbox, tmp_path):
    # --yes means "non-interactive, auto-accept rc edits" for scripts/CI — it must NOT
    # launch the interactive `tt setup` wizard; --no-rc must not either.
    home, env, tt_log = sandbox
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "setup --from-install" not in tt_log.read_text()
    assert "setup: run 'tt setup'" in proc.stdout

    home2 = tmp_path / "home2"
    home2.mkdir()
    tt_log2 = tmp_path / "tt2.log"
    bundle2 = make_bundle(tmp_path, tt_log2, tag="no-rc")
    env2 = {
        "HOME": str(home2),
        "PATH": f"{home2}/.local/bin:/usr/bin:/bin",
        "TT_BINARY": str(bundle2),
    }
    proc2 = run_install(env2, "--no-rc")
    assert proc2.returncode == 0, proc2.stderr
    assert "setup --from-install" not in tt_log2.read_text()
    assert "setup: run 'tt setup'" in proc2.stdout

    # a genuinely headless run (no flags, run_install detaches the controlling tty)
    # must not invoke the wizard either — install.sh probes /dev/tty openability,
    # not mere existence, and falls through to the hint.
    home3 = tmp_path / "home3"
    home3.mkdir()
    tt_log3 = tmp_path / "tt3.log"
    bundle3 = make_bundle(tmp_path, tt_log3, tag="headless")
    env3 = {
        "HOME": str(home3),
        "PATH": f"{home3}/.local/bin:/usr/bin:/bin",
        "TT_BINARY": str(bundle3),
    }
    proc3 = run_install(env3)
    assert proc3.returncode == 0, proc3.stderr
    assert "setup --from-install" not in tt_log3.read_text()
    assert "setup: run 'tt setup'" in proc3.stdout


def test_prompt_defaults_to_no(tmp_path):
    """An unanswered ask() prompt (no /dev/tty reachable in this test harness)
    defaults to "no" — proven via the one ask() prompt install.sh still has
    (PATH wiring); the zsh-widget/setup prompt was replaced by the tt-setup handoff,
    which never blocks on a question (see test_setup_handoff_skips_for_yes_no_rc_or_headless)."""
    home = tmp_path / "home"
    home.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TT_BINARY": str(bundle)}
    proc = subprocess.run(
        ["sh", str(INSTALL), "--bin-dir", f"{home}/toolbin"],
        env=env,
        input="\n",  # user just presses Enter → default No
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".zshrc").exists()


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
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TT_BINARY": str(bundle)}

    proc = run_install(env, "--yes", "--bin-dir", f"{home}/toolbin")
    assert proc.returncode == 0, proc.stderr
    assert INSTALLED in proc.stdout
    zshrc = (home / ".zshrc").read_text()
    assert zshrc.count(PATH_MARKER) == 1
    assert 'export PATH="$HOME/toolbin:$PATH"' in zshrc  # $HOME kept symbolic

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
    assert not (
        home3 / ".zshrc"
    ).exists()  # PATH went to bash's rc files; no zsh widget block anymore

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


def _pinned_curl(fakebin: Path, bundle: Path, tag: str) -> None:
    """A `curl` stub that only serves the bundle for a URL containing `tag` —
    proves the installer resolved that exact release tag before downloading."""
    make_exe(
        fakebin,
        "curl",
        "#!/bin/sh\n"
        'url=""; out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in -o) shift; out="$1" ;; http://*|https://*) url="$1" ;; esac; shift; done\n'
        'case "$url" in\n'
        "  *.sha256) exit 22 ;;\n"
        f'  *{tag}*) cp "{bundle}" "$out" ;;\n'
        "  *) exit 22 ;;\n"
        "esac\n",
    )


def test_version_flag_pins_release_tag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    _pinned_curl(fakebin, bundle, "v0.2.0rc4")
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes", "--version", "v0.2.0rc4")
    assert proc.returncode == 0, proc.stderr
    assert "downloading" in proc.stdout and "(v0.2.0rc4)" in proc.stdout


def test_tt_version_env_pins_release_without_flag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tt_log = tmp_path / "tt.log"
    bundle = make_bundle(tmp_path, tt_log)
    _pinned_curl(fakebin, bundle, "v0.2.0rc4")
    env = {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{fakebin}:/usr/bin:/bin",
        "TT_VERSION": "v0.2.0rc4",
        "TT_RELEASE_BASE": "https://example.test/releases",
    }
    proc = run_install(env, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "downloading" in proc.stdout and "(v0.2.0rc4)" in proc.stdout
