"""Ollama install helpers for `tt auth`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import tinytalk.local_llm as local_llm


class YesIO:
    def confirm(self, message: str, default: bool = True) -> bool:
        return True


class NoIO:
    def confirm(self, message: str, default: bool = True) -> bool:
        return False


def _ok(cmd, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_server_healthy_accepts_v1_models(monkeypatch):
    monkeypatch.setattr(
        local_llm,
        "_api_get",
        lambda path, timeout=2.0: (200, '{"data":[]}') if path == "/v1/models" else None,
    )
    assert local_llm._server_healthy() is True


def test_model_pulled_reads_api_tags(monkeypatch):
    payload = json.dumps({"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.setattr(local_llm, "_api_get", lambda path, timeout=2.0: (200, payload))
    assert local_llm._model_pulled("llama3.2") is True


def test_ensure_linux_ollama_short_circuits_when_ready(monkeypatch, capsys):
    monkeypatch.setattr(local_llm, "_server_healthy", lambda timeout=2.0: True)
    monkeypatch.setattr(local_llm, "_model_pulled", lambda model: True)
    assert local_llm.ensure_linux_ollama(YesIO(), run=_ok) is True
    assert "already running" in capsys.readouterr().out


def test_ensure_linux_ollama_installs_starts_and_pulls(monkeypatch, capsys):
    monkeypatch.setattr(local_llm, "_server_healthy", lambda timeout=2.0: False)
    seen = {"path": 0, "pull": 0}

    def fake_path():
        seen["path"] += 1
        return None if seen["path"] == 1 else Path("/usr/bin/ollama")

    monkeypatch.setattr(local_llm, "ollama_path", fake_path)
    monkeypatch.setattr(local_llm, "install_ollama", lambda run, io: None)
    monkeypatch.setattr(local_llm, "start_ollama_service", lambda run, io: None)
    monkeypatch.setattr(local_llm, "wait_for_server", lambda **kw: True)
    monkeypatch.setattr(
        local_llm,
        "pull_ollama_model",
        lambda run, model: seen.__setitem__("pull", seen["pull"] + 1) or None,
    )
    monkeypatch.setattr(local_llm, "_model_pulled", lambda model: seen["pull"] > 0)

    assert local_llm.ensure_linux_ollama(YesIO(), run=_ok) is True
    assert seen["pull"] == 1


def test_ensure_linux_ollama_manual_fallback_when_user_declines_install(monkeypatch, capsys):
    monkeypatch.setattr(local_llm, "_server_healthy", lambda timeout=2.0: False)
    monkeypatch.setattr(local_llm, "ollama_path", lambda: None)
    calls = {"install": 0}

    def boom(run, io):
        calls["install"] += 1
        return None

    monkeypatch.setattr(local_llm, "install_ollama", boom)
    assert local_llm.ensure_linux_ollama(NoIO(), run=_ok) is True
    assert calls["install"] == 0
    assert "ollama pull" in capsys.readouterr().out
