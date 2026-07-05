"""Local model provisioning for `tt auth` openai-compat setup.

This module is intentionally split into pure sizing/catalog logic and an effectful
`LocalOps` seam. Tests drive the provisioning path with fake ops; the concrete macOS/Linux
ops are best-effort wrappers around the documented oMLX/llama.cpp paths.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Hardware:
    system: str
    machine: str
    memory_gb: float
    source: str


@dataclass(frozen=True)
class ModelPlan:
    memclass: str
    model: str
    hf_repo: str
    draft_model: str | None
    draft_hf_repo: str | None
    min_gb: int
    approx_gb: float


class LocalSetupError(Exception):
    """Local backend provisioning cannot continue."""


class LocalOps(Protocol):
    def base_url(self) -> str: ...

    def server_serving(self, model: str) -> bool: ...

    def runtime_installed(self) -> bool: ...

    def install_runtime(self) -> None: ...

    def model_present(self, plan: ModelPlan) -> bool: ...

    def download_model(self, plan: ModelPlan) -> None: ...

    def service_installed(self) -> bool: ...

    def install_and_start_service(self, plan: ModelPlan) -> None: ...

    def start_service(self) -> None: ...

    def reload_models(self) -> None: ...


_GIB = 1024**3
_THRESHOLDS = ((24, "26b"), (12, "12b-dense"), (8, "12b-qat"), (0, "e4b"))

_MODEL_IDS = {
    "26b": "gemma-4-26B-A4B-it-MLX-8bit",
    "12b-dense": "gemma-4-12B-it-8bit",
    "12b-qat": "gemma-4-12B-it-qat-4bit",
    "e4b": "lmstudio-community--gemma-4-E4B-it-MLX-4bit",
}

# TODO(local-setup): Confirm HF repos/quant tags for 12B, 12B-QAT, assistant drafters, E4B,
# and Linux draft GGUFs. The model ids are bench-exact; unknown source repos are placeholders
# until the homelab provenance is supplied.
_CATALOG: dict[tuple[str, str], ModelPlan] = {
    ("Darwin", "26b"): ModelPlan(
        "26b",
        _MODEL_IDS["26b"],
        "unsloth/gemma-4-26b-a4b-it-MLX-8bit",
        None,
        None,
        24,
        25.2,
    ),
    ("Darwin", "12b-dense"): ModelPlan(
        "12b-dense",
        _MODEL_IDS["12b-dense"],
        "TODO-confirm-gemma-4-12B-it-8bit-MLX-repo",
        "gemma-4-12B-it-assistant-8bit",
        "TODO-confirm-gemma-4-12B-it-assistant-8bit-MLX-repo",
        12,
        12.0,
    ),
    ("Darwin", "12b-qat"): ModelPlan(
        "12b-qat",
        _MODEL_IDS["12b-qat"],
        "TODO-confirm-gemma-4-12B-it-qat-4bit-MLX-repo",
        "gemma-4-12B-it-assistant-4bit",
        "TODO-confirm-gemma-4-12B-it-assistant-4bit-MLX-repo",
        8,
        8.0,
    ),
    ("Darwin", "e4b"): ModelPlan(
        "e4b",
        _MODEL_IDS["e4b"],
        "TODO-confirm-lmstudio-community-gemma-4-E4B-it-MLX-4bit-repo",
        None,
        None,
        0,
        4.5,
    ),
    ("Linux", "26b"): ModelPlan(
        "26b",
        _MODEL_IDS["26b"],
        "unsloth/gemma-4-26b-a4b-it-GGUF:Q4_K_M",
        None,
        None,
        24,
        25.2,
    ),
    ("Linux", "12b-dense"): ModelPlan(
        "12b-dense",
        _MODEL_IDS["12b-dense"],
        "TODO-confirm-gemma-4-12B-it-8bit-GGUF-repo:Q8_0",
        "gemma-4-12B-it-assistant-8bit",
        "TODO-confirm-gemma-4-12B-it-assistant-8bit-GGUF-repo:Q8_0",
        12,
        12.0,
    ),
    ("Linux", "12b-qat"): ModelPlan(
        "12b-qat",
        _MODEL_IDS["12b-qat"],
        "TODO-confirm-gemma-4-12B-it-qat-4bit-GGUF-repo:Q4_K_M",
        "gemma-4-12B-it-assistant-4bit",
        "TODO-confirm-gemma-4-12B-it-assistant-4bit-GGUF-repo:Q4_K_M",
        8,
        8.0,
    ),
    ("Linux", "e4b"): ModelPlan(
        "e4b",
        _MODEL_IDS["e4b"],
        "TODO-confirm-gemma-4-E4B-it-GGUF-repo:Q4_K_M",
        None,
        None,
        0,
        4.5,
    ),
}


def detect_hardware(*, runner=None, proc_meminfo: str | None = None) -> Hardware:
    """Detect OS, machine, and usable local-model memory in GiB.

    `runner` is an injectable `list[str] -> str` command seam. Tests pass canned output; the
    default runner is only used from the real wizard path.
    """

    run = runner or _run_text
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin":
        raw = run(["sysctl", "-n", "hw.memsize"]).strip()
        return Hardware(system, machine, int(raw) / _GIB, "apple-unified")
    if system == "Linux":
        try:
            raw = run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
            values = [float(line.strip()) for line in raw.splitlines() if line.strip()]
            if values:
                return Hardware(system, machine, max(values) / 1024.0, "nvidia-smi")
        except Exception:
            pass
        # TODO(local-setup): Confirm whether AMD/Intel GPU VRAM should be detected in v1;
        # until then, non-NVIDIA Linux falls back conservatively to system RAM.
        text = proc_meminfo
        if text is None:
            text = Path("/proc/meminfo").read_text("utf-8")
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.MULTILINE)
        if not match:
            raise LocalSetupError("could not read MemTotal from /proc/meminfo")
        return Hardware(system, machine, int(match.group(1)) / (1024.0**2), "system-ram")
    raise LocalSetupError(f"unsupported local model platform: {system}/{machine}")


def select_memclass(memory_gb: float) -> str:
    for minimum, memclass in _THRESHOLDS:
        if memory_gb >= minimum:
            return memclass
    return "e4b"


def plan_for(hw: Hardware) -> ModelPlan:
    memclass = select_memclass(hw.memory_gb)
    try:
        return _CATALOG[(hw.system, memclass)]
    except KeyError as exc:
        raise LocalSetupError(
            f"no local model plan for {hw.system}/{hw.machine} memory-class {memclass}"
        ) from exc


def make_ops(hw: Hardware) -> LocalOps:
    if hw.system == "Darwin":
        return MacOMLXOps()
    if hw.system == "Linux":
        return LinuxLlamaCppOps()
    raise LocalSetupError(f"unsupported local model platform: {hw.system}/{hw.machine}")


def _is_placeholder_repo(repo: str | None) -> bool:
    # Unconfirmed homelab provenance is stamped with a TODO- sentinel; a ":" means the value is
    # a llama-server `-hf` selector, not a `hf download` repo id. Either way it can't be pulled.
    if not repo:
        return False
    return repo.startswith("TODO") or ":" in repo


def plan_ready(plan: ModelPlan) -> bool:
    """True only when this plan's weights can actually be downloaded — every source repo is a
    confirmed, pullable id. Plans with placeholder repos (pending homelab provenance) are not
    offered for managed download; the wizard falls back to the manual openai-compat flow."""
    if _is_placeholder_repo(plan.hf_repo):
        return False
    if plan.draft_hf_repo and _is_placeholder_repo(plan.draft_hf_repo):
        return False
    return True


def provision_local_backend(io, *, hw: Hardware | None = None, ops: LocalOps | None = None):
    """Best-effort managed local setup. Returns a BackendDraft on success, or None to fall back
    to the manual openai-compat flow. Declining a prompt, an unconfirmed model source, or ANY
    download/install/serve failure all degrade to None — this never crashes `tt auth`."""
    hw = hw or detect_hardware()
    plan = plan_for(hw)
    ops = ops or make_ops(hw)

    # An already-running server that serves the recommended model needs no download at all.
    if ops.server_serving(plan.model):
        return _draft_for(plan, ops.base_url())

    if not plan_ready(plan):
        print(
            f"tt auth: managed setup for {plan.model} isn't available yet on {hw.system} "
            "(model source not confirmed) — falling back to manual setup."
        )
        return None

    if not io.confirm(
        f"Detected {hw.memory_gb:.1f} GB ({hw.source}); set up {plan.model} "
        f"({plan.memclass}, ~{plan.approx_gb:g} GB)?",
        default=False,
    ):
        return None

    # Every effect past this point is best-effort: brew/hf/systemd failures (missing binary,
    # bad repo, no permission) degrade to the manual flow rather than aborting the wizard.
    try:
        if not ops.runtime_installed():
            if not io.confirm("Install the local inference runtime now?", default=False):
                return None
            ops.install_runtime()

        if not ops.model_present(plan):
            ops.download_model(plan)

        ops.reload_models()

        if not ops.service_installed():
            if not io.confirm("Start this local model server at login?", default=False):
                return None
            ops.install_and_start_service(plan)
        elif not ops.server_serving(plan.model):
            # Installed but not currently serving (e.g. stopped) — start it before giving up.
            ops.start_service()

        if not ops.server_serving(plan.model):
            raise LocalSetupError(f"{ops.base_url()} is not serving {plan.model} after setup")
    except (LocalSetupError, OSError, subprocess.SubprocessError) as exc:
        print(f"tt auth: managed local setup failed ({exc}) — falling back to manual setup.")
        return None

    return _draft_for(plan, ops.base_url())


def _draft_for(plan: ModelPlan, base_url: str):
    from tinytalk.auth import BackendDraft

    return BackendDraft(
        fields={
            "kind": "openai-compat",
            "base_url": base_url,
            "model": plan.model,
            "capabilities": [],
        },
        secret=None,
    )


def _run_text(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)


def _xdg_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "tinytalk"


class _OpenAIModelProbeMixin:
    def server_serving(self, model: str) -> bool:
        from tinytalk.provider.openai_compat import list_models

        try:
            models = asyncio.run(list_models(self.base_url(), api_key=None))
        except Exception:
            return False
        return model in models


class MacOMLXOps(_OpenAIModelProbeMixin):
    """Best-effort oMLX ops for macOS; all real effects stay behind this class."""

    def __init__(self, *, model_root: Path | None = None):
        self.model_root = model_root or (_xdg_data_dir() / "models")

    def base_url(self) -> str:
        # oMLX's documented default bind (README.md); confirmed over the :3333 bench-managed
        # server so a fresh install matches the public setup path.
        return "http://localhost:8000/v1"

    def runtime_installed(self) -> bool:
        return shutil.which("omlx") is not None and shutil.which("hf") is not None

    def install_runtime(self) -> None:
        _run_checked(["brew", "tap", "jundot/omlx", "https://github.com/jundot/omlx"])
        _run_checked(["brew", "install", "omlx"])
        if shutil.which("hf") is None:
            # No Homebrew formula ships the HF CLI (`hf`, from huggingface_hub); install it
            # via pipx like other Python-CLI tools in this project (see AGENTS.md).
            _run_checked(["brew", "install", "pipx"])
            _run_checked(["pipx", "install", "huggingface_hub[cli]"])

    def model_present(self, plan: ModelPlan) -> bool:
        paths = [self.model_root / plan.model]
        if plan.draft_model:
            paths.append(self.model_root / plan.draft_model)
        return all(path.exists() for path in paths)

    def download_model(self, plan: ModelPlan) -> None:
        self.model_root.mkdir(parents=True, exist_ok=True)
        _hf_download(plan.hf_repo, self.model_root / plan.model)
        if plan.draft_model and plan.draft_hf_repo:
            _hf_download(plan.draft_hf_repo, self.model_root / plan.draft_model)

    def service_installed(self) -> bool:
        return _run_status(["brew", "services", "info", "omlx"])

    def install_and_start_service(self, plan: ModelPlan) -> None:
        # TODO(local-setup): Confirm fresh-Mac drafter attachment and persistent oMLX config.
        _run_checked(["omlx", "serve", "--model-dir", str(self.model_root), "--help"])
        _run_checked(["brew", "services", "start", "omlx"])

    def start_service(self) -> None:
        _run_checked(["brew", "services", "start", "omlx"])

    def reload_models(self) -> None:
        # TODO(local-setup): Confirm the oMLX admin reload route/method. This uses the obvious
        # placeholder under the documented admin surface and degrades if the server is absent.
        _post_no_body("http://localhost:8000/admin/reload")


class LinuxLlamaCppOps(_OpenAIModelProbeMixin):
    """Best-effort llama.cpp ops for Linux systemd-user serving."""

    def __init__(self, *, model_root: Path | None = None, service_dir: Path | None = None):
        self.model_root = model_root or (_xdg_data_dir() / "models")
        self.service_dir = service_dir or (
            Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
            / "systemd"
            / "user"
        )

    def base_url(self) -> str:
        return "http://localhost:8080/v1"

    def runtime_installed(self) -> bool:
        return shutil.which("llama-server") is not None

    def install_runtime(self) -> None:
        # TODO(local-setup): Confirm brew-first vs tt-managed prebuilt llama.cpp install.
        _run_checked(["brew", "install", "llama.cpp"])

    def model_present(self, plan: ModelPlan) -> bool:
        return (self.model_root / _repo_dir_name(plan.hf_repo)).exists()

    def download_model(self, plan: ModelPlan) -> None:
        self.model_root.mkdir(parents=True, exist_ok=True)
        _hf_download(plan.hf_repo, self.model_root / _repo_dir_name(plan.hf_repo))
        if plan.draft_hf_repo:
            _hf_download(plan.draft_hf_repo, self.model_root / _repo_dir_name(plan.draft_hf_repo))

    def service_installed(self) -> bool:
        return (self.service_dir / "llama-server.service").exists()

    def install_and_start_service(self, plan: ModelPlan) -> None:
        self.service_dir.mkdir(parents=True, exist_ok=True)
        service = self.service_dir / "llama-server.service"
        draft = f" -md {plan.draft_hf_repo}" if plan.draft_hf_repo else ""
        service.write_text(
            "[Unit]\n"
            "Description=llama.cpp server\n\n"
            "[Service]\n"
            f"ExecStart=%h/.local/bin/llama-server -hf {plan.hf_repo} --port 8080 -c 8192{draft}\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n",
            "utf-8",
        )
        _run_checked(["systemctl", "--user", "daemon-reload"])
        _run_checked(["systemctl", "--user", "enable", "--now", "llama-server.service"])
        _run_checked(["loginctl", "enable-linger"])

    def start_service(self) -> None:
        _run_checked(["systemctl", "--user", "start", "llama-server.service"])

    def reload_models(self) -> None:
        return None


def _run_checked(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _run_status(cmd: list[str]) -> bool:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _hf_download(repo: str, dest: Path) -> None:
    _run_checked(["hf", "download", repo, "--local-dir", str(dest)])


def _post_no_body(url: str) -> None:
    req = urllib.request.Request(url, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def _repo_dir_name(repo: str) -> str:
    return repo.split(":", 1)[0].replace("/", "--")
