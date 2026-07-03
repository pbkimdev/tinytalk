"""Capability grounding (#33): PATH scan, curated prompt, on-demand help fetch."""

from __future__ import annotations

import os
import stat

from tinytalk.grounding import SystemGrounding, host_facts, installed_binaries
from tinytalk.tiers import TierRequest


def make_exe(directory, name, body='#!/bin/sh\necho "usage: fake [-x] [-y FILE]"\n'):
    path = directory / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_installed_binaries_scans_path(tmp_path):
    bin_a, bin_b = tmp_path / "a", tmp_path / "b"
    bin_a.mkdir()
    bin_b.mkdir()
    make_exe(bin_a, "ls")
    make_exe(bin_b, "du")
    (bin_a / "not-executable").write_text("data")
    fake_path = f"{bin_a}{os.pathsep}{bin_b}{os.pathsep}/does/not/exist"
    names = installed_binaries(fake_path)
    assert {"ls", "du"} <= names
    assert "not-executable" not in names


def test_system_prompt_reflects_host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "ls")
    make_exe(bin_dir, "du")
    g = SystemGrounding(path=str(bin_dir))
    prompt = g.system_prompt(TierRequest(prompt="x"))
    assert "- ls:" in prompt
    assert "- du:" in prompt
    assert "- rg:" not in prompt  # not installed in the fake PATH
    assert "OS:" in prompt
    assert '"danger"' in prompt  # contract shape included


def test_system_prompt_includes_kubernetes_and_bash_tools_only_when_installed(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "kubectl", "helm", "bash", "tee", "printf"):
        make_exe(bin_dir, name)
    g = SystemGrounding(path=str(bin_dir))
    prompt = g.system_prompt(TierRequest(prompt="x"))
    assert "- docker: containers/images;" in prompt
    assert "- kubectl: Kubernetes CLI;" in prompt
    assert "- helm: Kubernetes package manager;" in prompt
    assert "- bash: Bash shell;" in prompt
    assert "- tee: copy stdin to files and stdout;" in prompt
    assert "- printf: format text portably;" in prompt
    assert "- rg:" not in prompt


def test_preference_rules_gate_on_installed_tools(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "rg")
    make_exe(bin_dir, "grep")
    prompt = SystemGrounding(path=str(bin_dir)).system_prompt(TierRequest(prompt="x"))
    assert "Tool preferences on this system" in prompt
    assert "prefer `rg` over `grep -r`" in prompt
    assert "prefer `fd`" not in prompt  # fd not installed — never advertise it


def test_preference_block_absent_when_no_rule_gates(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "ls")
    prompt = SystemGrounding(path=str(bin_dir)).system_prompt(TierRequest(prompt="x"))
    assert "Tool preferences" not in prompt


def test_host_facts_names_userland_flavor():
    facts = host_facts()
    assert "userland" in facts


def test_enrich_fetches_real_help_and_flags_missing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_exe(bin_dir, "faketool")
    g = SystemGrounding(path=str(bin_dir))
    extra = g.enrich(("faketool", "notinstalled", "bad name; rm -rf /"), ())
    assert "usage: fake [-x] [-y FILE]" in extra
    assert "NOT installed" in extra
    assert "notinstalled" in extra
    # the invalid name is never treated as a tool (and never executed)
    assert "rm -rf" not in extra.split("NOT installed")[1].split("\n")[0]


def test_help_text_is_memoized(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "count"
    make_exe(
        bin_dir,
        "counting",
        f'#!/bin/sh\necho x >> "{counter}"\necho "usage: counting things and words"\n',
    )
    g = SystemGrounding(path=str(bin_dir))
    first = g.help_text("counting")
    second = g.help_text("counting")
    assert first == second
    assert first is not None and "usage: counting" in first
    assert counter.read_text().count("x") == 1


def test_help_text_refuses_unknown_or_invalid_names(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    g = SystemGrounding(path=str(bin_dir))
    assert g.help_text("nonexistent") is None
    assert g.help_text("evil; rm -rf /") is None
    assert g.help_text("../../bin/sh") is None
