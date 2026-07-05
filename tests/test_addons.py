"""Add-on resolver tests (sub-task A). No network: add-ons are faked on a temp filesystem
with synthetic packages, so the sys.path-injection and missing-add-on branches are exercised
against real import machinery."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import sys
import tarfile
from contextlib import contextmanager

import pytest

from tinytalk import __version__, addons


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point the add-on tree at a temp XDG_DATA_HOME and undo any sys.path edits after."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    before = list(sys.path)
    yield tmp_path
    sys.path[:] = before


def test_addon_dir_is_version_stamped(xdg):
    assert addons.addon_dir("bedrock") == xdg / "tinytalk" / "addons" / "bedrock" / __version__


def test_addon_dir_defaults_to_local_share(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    d = addons.addon_dir("claude")
    assert d.parts[-4:] == ("tinytalk", "addons", "claude", __version__)
    assert str(d).startswith(str(__import__("pathlib").Path.home()))


def test_ensure_short_circuits_when_module_already_importable(xdg):
    # A stdlib module stands in for an already-installed boto3: no add-on dir needed.
    addons._ensure_on_path("bedrock", "json")  # must not raise


def test_ensure_injects_addon_onto_path(xdg, monkeypatch):
    mod = "tt_fake_bedrock_pkg"
    site = addons.addon_dir("bedrock")
    (site / mod).mkdir(parents=True)
    (site / mod / "__init__.py").write_text("VALUE = 42\n")
    monkeypatch.delitem(sys.modules, mod, raising=False)

    addons._ensure_on_path("bedrock", mod)  # injects site onto sys.path
    assert str(site) in sys.path
    imported = __import__(mod)
    assert imported.VALUE == 42
    monkeypatch.delitem(sys.modules, mod, raising=False)


def test_ensure_missing_raises_addon_missing(xdg):
    with pytest.raises(addons.AddonMissing) as exc:
        addons._ensure_on_path("bedrock", "tt_absent_pkg_xyz")
    assert "bedrock" in str(exc.value)


def test_claude_cli_path_none_on_source_install(xdg):
    # Not frozen + no add-on → let the SDK resolve `claude` from $PATH.
    assert addons.claude_cli_path() is None


def test_claude_cli_path_returns_installed_binary(xdg):
    exe = addons.addon_dir("claude") / "claude"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)  # claude_cli_path/is_installed require the exec bit, not mere existence
    assert addons.claude_cli_path() == str(exe)


def test_claude_cli_path_missing_on_frozen_raises(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    with pytest.raises(addons.AddonMissing) as exc:
        addons.claude_cli_path()
    assert "claude" in str(exc.value)


def test_addon_missing_message_adapts_to_frozen(monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: False)
    assert "is not installed" in str(addons.AddonMissing("bedrock"))
    assert "uv sync --extra bedrock" in str(addons.AddonMissing("bedrock"))
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    # Frozen branch points at the wizard menu label, not the bare add-on name (OQ2).
    assert "tt auth" in str(addons.AddonMissing("bedrock"))
    assert "AWS Bedrock" in str(addons.AddonMissing("bedrock"))


# --- asset naming/url (byte-for-byte guard against the doubled-`tt-` 404) ------------


def test_asset_name_bedrock_is_platform_independent():
    assert addons.asset_name("bedrock") == "tt-bedrock-addon.tar.gz"


def test_asset_name_claude_per_platform(monkeypatch):
    for tag in ("macos-arm64", "linux-x86_64", "linux-arm64"):
        monkeypatch.setattr(addons, "PLATFORM_TAG", tag)
        assert addons.asset_name("claude") == f"tt-claude-addon-{tag}.tar.gz"


def test_asset_name_claude_raises_without_platform(monkeypatch):
    monkeypatch.setattr(addons, "PLATFORM_TAG", None)
    with pytest.raises(addons.AddonInstallError):
        addons.asset_name("claude")


def test_asset_url_carries_v_prefix():
    assert addons.asset_url("bedrock") == (
        f"https://github.com/pbkimdev/tinytalk/releases/download/"
        f"v{__version__}/tt-bedrock-addon.tar.gz"
    )


# --- install_addon: download → verify → atomic unpack --------------------------------
# No network: `_fake_opener` mirrors the httpx.stream context-manager seam, yielding
# `(len(data), iter([data]))` and serving the sha256 sidecar or the tarball by URL.


def _make_tar(entries: dict, *, mode: int = 0o644) -> bytes:
    """Gzipped tar bytes from {arcname: content}; `mode` applies to every entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for arcname, data in entries.items():
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _fake_opener(tar_bytes: bytes, *, sha256: str | None = None, calls: list | None = None):
    digest = sha256 or hashlib.sha256(tar_bytes).hexdigest()

    @contextmanager
    def opener(url: str):
        if calls is not None:
            calls.append(url)
        if url.endswith(".sha256"):
            data = f"{digest}  {url.rsplit('/', 1)[-1]}\n".encode()
        else:
            data = tar_bytes
        yield len(data), iter([data])

    return opener


def test_install_addon_happy_path_bedrock(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    calls: list = []
    tar = _make_tar({"boto3/__init__.py": b"VALUE = 7\n"})
    addons.install_addon("bedrock", opener=_fake_opener(tar, calls=calls))

    dest = addons.addon_dir("bedrock")
    assert (dest / "boto3" / "__init__.py").read_text() == "VALUE = 7\n"
    assert addons.is_installed("bedrock")
    # Both the sha256 sidecar and the tarball were fetched.
    assert any(u.endswith(".sha256") for u in calls)
    assert any(not u.endswith(".sha256") for u in calls)
    # The unpacked dir is a usable sys.path import root.
    sys.path.insert(0, str(dest))
    importlib.invalidate_caches()
    spec = importlib.util.find_spec("boto3")
    assert spec is not None and spec.origin.startswith(str(dest))
    sys.modules.pop("boto3", None)


def test_install_addon_claude_keeps_exec_bit(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    monkeypatch.setattr(addons, "PLATFORM_TAG", "macos-arm64")
    tar = _make_tar({"claude": b"#!/bin/sh\necho ok\n"}, mode=0o755)
    addons.install_addon("claude", opener=_fake_opener(tar))

    exe = addons.addon_dir("claude") / "claude"
    assert exe.is_file() and os.access(exe, os.X_OK)
    assert addons.is_installed("claude")


def test_install_addon_checksum_mismatch_leaves_nothing(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    tar = _make_tar({"boto3/__init__.py": b"x\n"})
    with pytest.raises(addons.AddonInstallError) as exc:
        addons.install_addon("bedrock", opener=_fake_opener(tar, sha256="deadbeef" * 8))
    assert "tt-bedrock-addon.tar.gz" in str(exc.value)

    dest = addons.addon_dir("bedrock")
    assert not dest.exists()
    assert not (dest.parent / (dest.name + ".partial")).exists()
    assert list(dest.parent.iterdir()) == []  # no leftover temp tarball either


def test_install_addon_skips_when_already_installed(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    (addons.addon_dir("bedrock") / "boto3").mkdir(parents=True)  # is_installed → True
    calls: list = []
    addons.install_addon("bedrock", opener=_fake_opener(b"", calls=calls))
    assert calls == []  # opener never called


def test_install_addon_noop_on_source_install(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: False)
    calls: list = []
    addons.install_addon("bedrock", opener=_fake_opener(b"", calls=calls))
    assert calls == []
    assert not addons.addon_dir("bedrock").exists()


def test_install_addon_rejects_unsafe_tar_entry(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    tar = _make_tar({"../escape.txt": b"nope\n"})  # filter='data' rejects the `..`
    with pytest.raises(addons.AddonInstallError):
        addons.install_addon("bedrock", opener=_fake_opener(tar))

    dest = addons.addon_dir("bedrock")
    assert not dest.exists()
    assert not (dest.parent / "escape.txt").exists()  # nothing escaped
    assert list(dest.parent.iterdir()) == []


def test_install_addon_replaces_stale_dest(xdg, monkeypatch):
    monkeypatch.setattr(addons, "_is_frozen", lambda: True)
    dest = addons.addon_dir("bedrock")
    dest.mkdir(parents=True)
    (dest / "stale.txt").write_text("old\n")  # present but not a boto3 root → is_installed False
    assert not addons.is_installed("bedrock")

    addons.install_addon("bedrock", opener=_fake_opener(_make_tar({"boto3/__init__.py": b"new\n"})))
    assert addons.is_installed("bedrock")
    assert not (dest / "stale.txt").exists()
    assert (dest / "boto3" / "__init__.py").read_text() == "new\n"
