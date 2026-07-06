"""Ollama install + daemon setup for the `tt auth` local-backend path (Linux/WSL)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

# Kept for auth imports — Linux/WSL local defaults via Ollama.
LINUX_LLAMA_BASE = "http://localhost:11434/v1"
LINUX_LLAMA_MODEL = "llama3.2"
LINUX_OLLAMA_BASE = LINUX_LLAMA_BASE
LINUX_OLLAMA_MODEL = LINUX_LLAMA_MODEL

OLLAMA_INSTALL_URL = "https://ollama.com/install.sh"
OLLAMA_PORT = 11434
OLLAMA_MODEL = LINUX_OLLAMA_MODEL

Runner = Callable[..., subprocess.CompletedProcess[str]]


class WizardIO(Protocol):
    def confirm(self, message: str, default: bool = True) -> bool | None: ...


def _default_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False, **kwargs)


def ollama_path() -> Path | None:
    found = shutil.which("ollama")
    return Path(found) if found else None


def _command_error(proc: subprocess.CompletedProcess[str], cmd: list[str]) -> str:
    err = (proc.stderr or proc.stdout or "").strip()
    return err or f"{' '.join(cmd)} failed (exit {proc.returncode})"


def _api_get(path: str, *, timeout: float = 2.0) -> tuple[int, str] | None:
    url = f"http://127.0.0.1:{OLLAMA_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _server_healthy(timeout: float = 2.0) -> bool:
    got = _api_get("/v1/models", timeout=timeout)
    if got is not None and got[0] == 200:
        return True
    got = _api_get("/api/tags", timeout=timeout)
    return got is not None and got[0] == 200


def _model_pulled(model: str) -> bool:
    got = _api_get("/api/tags")
    if got is None or got[0] != 200:
        return False
    try:
        data = json.loads(got[1])
    except json.JSONDecodeError:
        return False
    names = {m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)}
    return model in names or any(n.startswith(f"{model}:") or n == model for n in names)


def wait_for_server(*, timeout_s: float = 120, poll_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _server_healthy():
            return True
        time.sleep(poll_s)
    return False


def _system_ollama_running(run: Runner) -> bool:
    if not shutil.which("systemctl"):
        return False
    proc = run(["systemctl", "is-active", "--quiet", "ollama"], timeout=30)
    return proc.returncode == 0


def start_ollama_service(run: Runner, io: WizardIO) -> str | None:
    if _system_ollama_running(run):
        return None
    if shutil.which("systemctl"):
        if io.confirm("Start the Ollama service with sudo?", default=True) is not True:
            return "ollama service is not running"
        proc = run(["sudo", "systemctl", "enable", "--now", "ollama"], timeout=120)
        if proc.returncode == 0:
            return None
        return _command_error(proc, ["sudo", "systemctl", "enable", "--now", "ollama"])
    # No systemd — best-effort background serve.
    if ollama_path() is None:
        return "ollama not installed"
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return None


def install_ollama(run: Runner, io: WizardIO) -> str | None:
    if ollama_path():
        return None
    if io.confirm("Install Ollama from ollama.com (uses curl | sh)?", default=True) is not True:
        return "ollama not installed"
    if not shutil.which("curl"):
        return "curl is required to install Ollama"
    proc = run(["sh", "-c", f"curl -fsSL {OLLAMA_INSTALL_URL} | sh"], timeout=1800)
    if proc.returncode != 0:
        return _command_error(proc, ["curl", "|", "sh", OLLAMA_INSTALL_URL])
    path = os.environ.get("PATH", "")
    for extra in ("/usr/local/bin", "/usr/bin"):
        if extra not in path.split(":"):
            os.environ["PATH"] = f"{extra}:{path}"
            path = os.environ["PATH"]
    if not ollama_path():
        return "Ollama install finished but `ollama` was not found on PATH"
    return None


def pull_ollama_model(run: Runner, model: str = OLLAMA_MODEL) -> str | None:
    if _model_pulled(model):
        return None
    proc = run(["ollama", "pull", model], timeout=7200)
    if proc.returncode != 0:
        return _command_error(proc, ["ollama", "pull", model])
    return None


def print_manual_linux_guide() -> None:
    print(
        "Local model (Ollama): install from ollama.com, pull a model, then confirm the API:\n"
        f"  curl -fsSL {OLLAMA_INSTALL_URL} | sh\n"
        f"  ollama pull {OLLAMA_MODEL}\n"
        "  curl -s localhost:11434/v1/models\n"
        "Ollama installs a systemd service on Linux — `sudo systemctl enable --now ollama`."
    )


def ensure_linux_ollama(io: WizardIO, *, run: Runner | None = None) -> bool:
    """Install Ollama when needed, start its service, and pull the default model."""
    runner = run or _default_runner

    if _server_healthy() and _model_pulled(OLLAMA_MODEL):
        print("tt auth: Ollama is already running with the default model.")
        return True

    if _server_healthy() and ollama_path():
        if io.confirm(f"Pull Ollama model {OLLAMA_MODEL!r}?", default=True) is True:
            err = pull_ollama_model(runner, OLLAMA_MODEL)
            if err:
                print(f"tt auth: model pull failed: {err}")
            elif _model_pulled(OLLAMA_MODEL):
                print(f"tt auth: Ollama model {OLLAMA_MODEL!r} is ready.")
                return True

    if ollama_path() and _server_healthy():
        return io.confirm("Continue without the default model pulled yet?", default=True) is True

    if ollama_path():
        err = start_ollama_service(runner, io)
        if err:
            print(f"tt auth: could not start Ollama: {err}")
        elif wait_for_server():
            if io.confirm(f"Pull Ollama model {OLLAMA_MODEL!r}?", default=True) is True:
                err = pull_ollama_model(runner, OLLAMA_MODEL)
                if err:
                    print(f"tt auth: model pull failed: {err}")
                elif _model_pulled(OLLAMA_MODEL):
                    print(f"tt auth: Ollama is up with {OLLAMA_MODEL!r}.")
                    return True
            return io.confirm("Continue and retry the connection test?", default=True) is True

    if io.confirm("Install Ollama and set it up as a background service?", default=True) is not True:
        print_manual_linux_guide()
        return True

    print("tt auth: installing Ollama...")
    err = install_ollama(runner, io)
    if err:
        print(f"tt auth: install failed: {err}")
        print_manual_linux_guide()
        return io.confirm("Continue without a running local server?", default=False) is True

    err = start_ollama_service(runner, io)
    if err:
        print(f"tt auth: could not start Ollama: {err}")
        print_manual_linux_guide()
        return io.confirm("Continue without a running local server?", default=False) is True

    print("tt auth: waiting for Ollama...")
    if not wait_for_server():
        print("tt auth: Ollama is not responding yet.")
        return io.confirm("Continue anyway and retry the connection test?", default=True) is True

    print(f"tt auth: pulling {OLLAMA_MODEL!r} (first download may take several minutes)...")
    err = pull_ollama_model(runner, OLLAMA_MODEL)
    if err:
        print(f"tt auth: model pull failed: {err}")
        return io.confirm("Continue anyway and pick a model manually?", default=True) is True

    print(f"tt auth: Ollama is up with {OLLAMA_MODEL!r}.")
    return True


# Back-compat alias while auth wiring lands.
ensure_linux_llama_cpp = ensure_linux_ollama
