"""Persistent grounding snapshot (#88): roundtrip, staleness matrix, corrupt fallback."""

from __future__ import annotations

import json
import os
import time

import pytest

from tinytalk import groundcache
from tinytalk.grounding import SystemGrounding, installed_binaries
from tests.test_grounding import make_exe

TT = "0.0.1-test"


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    make_exe(d, "ls")
    make_exe(d, "rg")
    return d


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


def save_fresh(bin_dir, cache_dir):
    path = str(bin_dir)
    snap = groundcache.build_snapshot(path, installed_binaries(path))
    groundcache.save_snapshot(cache_dir, snap, tt_version=TT)
    return path, snap


def rewrite(cache_dir, snapshot_for, **overrides):
    file = groundcache.snapshot_path(cache_dir, snapshot_for)
    data = json.loads(file.read_text("utf-8"))
    data.update(overrides)
    file.write_text(json.dumps(data), "utf-8")


def test_snapshot_roundtrip(bin_dir, cache_dir):
    # a dead PATH entry is tolerated at build time and at the staleness check
    path = f"{bin_dir}{os.pathsep}/does/not/exist"
    snap = groundcache.build_snapshot(path, installed_binaries(path))
    groundcache.save_snapshot(cache_dir, snap, tt_version=TT)
    loaded = groundcache.load_snapshot(cache_dir, path=path, tt_version=TT)
    assert loaded == snap
    assert {"ls", "rg"} <= loaded.binaries


def test_missing_file_is_a_miss(bin_dir, cache_dir):
    assert groundcache.load_snapshot(cache_dir, path=str(bin_dir), tt_version=TT) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"tt_version": "9.9.9"},
        {"os": "OtherOS-1.0-riscv"},
        {"path": "/somewhere/else"},
        {"created_at": time.time() - groundcache._SNAPSHOT_TTL_S - 60},
        {"binaries": "not-a-list"},
    ],
)
def test_stale_or_corrupt_fields_miss(bin_dir, cache_dir, overrides):
    path, _ = save_fresh(bin_dir, cache_dir)
    rewrite(cache_dir, path, **overrides)
    assert groundcache.load_snapshot(cache_dir, path=path, tt_version=TT) is None


def test_truncated_json_is_a_miss(bin_dir, cache_dir):
    path, _ = save_fresh(bin_dir, cache_dir)
    groundcache.snapshot_path(cache_dir, path).write_text("{ not json", "utf-8")
    assert groundcache.load_snapshot(cache_dir, path=path, tt_version=TT) is None


def test_new_binary_in_a_path_dir_invalidates(bin_dir, cache_dir):
    path, _ = save_fresh(bin_dir, cache_dir)
    make_exe(bin_dir, "just-installed")
    now = time.time()
    os.utime(bin_dir, (now + 5, now + 5))  # deterministic mtime bump
    assert groundcache.load_snapshot(cache_dir, path=path, tt_version=TT) is None


def test_grounding_serves_warm_cache_without_rescanning(bin_dir, cache_dir, monkeypatch):
    path = str(bin_dir)
    first = SystemGrounding(path=path, cache_dir=cache_dir)
    assert "ls" in first.binaries

    def boom(_path):
        raise AssertionError("PATH was rescanned despite a fresh snapshot")

    monkeypatch.setattr("tinytalk.grounding.installed_binaries", boom)
    second = SystemGrounding(path=path, cache_dir=cache_dir)
    assert second.binaries == first.binaries


def test_grounding_rebuilds_over_corrupt_cache(bin_dir, cache_dir):
    path = str(bin_dir)
    file = groundcache.snapshot_path(cache_dir, path)
    file.parent.mkdir(parents=True)
    file.write_text("garbage", "utf-8")
    g = SystemGrounding(path=path, cache_dir=cache_dir)
    assert "ls" in g.binaries
    assert "ls" in json.loads(file.read_text("utf-8"))["binaries"]  # healed on disk


def test_grounding_upgrade_invalidates(bin_dir, cache_dir):
    path = str(bin_dir)
    SystemGrounding(path=path, cache_dir=cache_dir)  # persists under the real __version__
    assert groundcache.load_snapshot(cache_dir, path=path, tt_version="next") is None


def test_no_cache_dir_never_touches_disk(bin_dir, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(groundcache, "load_snapshot", lambda *a, **k: calls.append("load"))
    monkeypatch.setattr(groundcache, "save_snapshot", lambda *a, **k: calls.append("save"))
    g = SystemGrounding(path=str(bin_dir))
    assert calls == []
    assert "ls" in g.binaries
    assert g.versions == {}


def test_unwritable_cache_dir_degrades_to_live_scan(bin_dir, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the cache dir should be")
    g = SystemGrounding(path=str(bin_dir), cache_dir=blocker)
    assert "ls" in g.binaries
