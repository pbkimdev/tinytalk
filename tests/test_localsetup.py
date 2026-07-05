from __future__ import annotations

import pytest

from tinytalk import localsetup
from tinytalk.localsetup import Hardware


class ScriptedIO:
    def __init__(self, answers):
        self.answers = list(answers)

    def confirm(self, message, default=True):
        if not self.answers:
            raise AssertionError(f"no scripted answer left for prompt: {message}")
        return self.answers.pop(0)


class FakeOps:
    def __init__(
        self,
        *,
        serving: bool = False,
        runtime: bool = True,
        model: bool = False,
        service: bool = False,
    ):
        self.serving = serving
        self.runtime = runtime
        self.model = model
        self.service = service
        self.calls: list[str] = []

    def base_url(self):
        self.calls.append("base_url")
        return "http://localhost:3333/v1"

    def server_serving(self, model):
        self.calls.append(f"server_serving:{model}")
        return self.serving

    def runtime_installed(self):
        self.calls.append("runtime_installed")
        return self.runtime

    def install_runtime(self):
        self.calls.append("install_runtime")
        self.runtime = True

    def model_present(self, plan):
        self.calls.append(f"model_present:{plan.memclass}")
        return self.model

    def download_model(self, plan):
        self.calls.append(f"download_model:{plan.model}")
        self.model = True

    def service_installed(self):
        self.calls.append("service_installed")
        return self.service

    def install_and_start_service(self, plan):
        self.calls.append("install_and_start_service")
        self.service = True
        self.serving = True

    def start_service(self):
        self.calls.append("start_service")
        self.serving = True

    def reload_models(self):
        self.calls.append("reload_models")


@pytest.mark.parametrize(
    ("gb", "memclass"),
    [(24, "26b"), (23.9, "12b-dense"), (12, "12b-dense"), (11.9, "12b-qat"), (8, "12b-qat"), (7, "e4b")],
)
def test_select_memclass_boundaries(gb, memclass):
    assert localsetup.select_memclass(gb) == memclass


def test_detect_hardware_macos_sysctl(monkeypatch):
    monkeypatch.setattr(localsetup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(localsetup.platform, "machine", lambda: "arm64")

    hw = localsetup.detect_hardware(runner=lambda cmd: str(32 * 1024**3))

    assert hw == Hardware("Darwin", "arm64", 32.0, "apple-unified")


def test_detect_hardware_linux_nvidia(monkeypatch):
    monkeypatch.setattr(localsetup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(localsetup.platform, "machine", lambda: "x86_64")

    hw = localsetup.detect_hardware(runner=lambda cmd: "8192\n24576\n")

    assert hw == Hardware("Linux", "x86_64", 24.0, "nvidia-smi")


def test_detect_hardware_linux_proc_meminfo_fallback(monkeypatch):
    monkeypatch.setattr(localsetup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(localsetup.platform, "machine", lambda: "aarch64")

    def runner(cmd):
        raise FileNotFoundError(cmd[0])

    hw = localsetup.detect_hardware(
        runner=runner,
        proc_meminfo="MemTotal:       12582912 kB\n",
    )

    assert hw == Hardware("Linux", "aarch64", 12.0, "system-ram")


def test_plan_for_uses_bench_exact_model_ids():
    assert localsetup.plan_for(Hardware("Darwin", "arm64", 24, "test")).model == (
        "gemma-4-26B-A4B-it-MLX-8bit"
    )
    assert localsetup.plan_for(Hardware("Darwin", "arm64", 12, "test")).model == (
        "gemma-4-12B-it-8bit"
    )
    assert localsetup.plan_for(Hardware("Darwin", "arm64", 8, "test")).model == (
        "gemma-4-12B-it-qat-4bit"
    )
    assert localsetup.plan_for(Hardware("Darwin", "arm64", 7, "test")).model == (
        "lmstudio-community--gemma-4-E4B-it-MLX-4bit"
    )


def test_provision_local_backend_idempotent_when_server_already_serves_model():
    ops = FakeOps(serving=True)
    draft = localsetup.provision_local_backend(
        ScriptedIO([]),
        hw=Hardware("Darwin", "arm64", 24, "test"),
        ops=ops,
    )

    assert draft.fields == {
        "kind": "openai-compat",
        "base_url": "http://localhost:3333/v1",
        "model": "gemma-4-26B-A4B-it-MLX-8bit",
        "capabilities": [],
    }
    assert draft.secret is None
    assert "effort" not in draft.fields
    assert ops.calls == [
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "base_url",
    ]


def test_provision_local_backend_orders_effects_and_returns_keyless_draft():
    # 26B (24 GB) is the one plan with a confirmed, pullable repo, so it exercises the full
    # download/serve sequence; placeholder-repo plans are covered by the fall-back test below.
    ops = FakeOps(runtime=False, model=False, service=False)
    draft = localsetup.provision_local_backend(
        ScriptedIO([True, True, True]),
        hw=Hardware("Darwin", "arm64", 24, "test"),
        ops=ops,
    )

    assert draft.fields == {
        "kind": "openai-compat",
        "base_url": "http://localhost:3333/v1",
        "model": "gemma-4-26B-A4B-it-MLX-8bit",
        "capabilities": [],
    }
    assert draft.secret is None
    assert "effort" not in draft.fields
    assert ops.calls == [
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "runtime_installed",
        "install_runtime",
        "model_present:26b",
        "download_model:gemma-4-26B-A4B-it-MLX-8bit",
        "reload_models",
        "service_installed",
        "install_and_start_service",
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "base_url",
    ]


def test_provision_restarts_installed_but_stopped_service():
    # service_installed() only checks the unit/config exists — a stopped service must not
    # be left stopped just because it's already installed.
    ops = FakeOps(serving=False, runtime=True, model=True, service=True)
    draft = localsetup.provision_local_backend(
        ScriptedIO([True]),
        hw=Hardware("Darwin", "arm64", 24, "test"),
        ops=ops,
    )

    assert draft is not None
    assert ops.calls == [
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "runtime_installed",
        "model_present:26b",
        "reload_models",
        "service_installed",
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "start_service",
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "base_url",
    ]


def test_plan_ready_gates_placeholder_repos():
    assert localsetup.plan_ready(localsetup.plan_for(Hardware("Darwin", "arm64", 24, "t")))
    # 12B/QAT/E4B carry TODO- placeholder repos; Linux 26B is a llama-server -hf selector.
    assert not localsetup.plan_ready(localsetup.plan_for(Hardware("Darwin", "arm64", 12, "t")))
    assert not localsetup.plan_ready(localsetup.plan_for(Hardware("Linux", "x86_64", 24, "t")))


def test_provision_unconfirmed_source_falls_back_without_effects(capsys):
    # A placeholder-repo plan must not run brew/hf/systemd; it degrades to the manual flow.
    ops = FakeOps(runtime=False, model=False, service=False)
    result = localsetup.provision_local_backend(
        ScriptedIO([]),
        hw=Hardware("Darwin", "arm64", 12, "test"),
        ops=ops,
    )

    assert result is None
    assert ops.calls == ["server_serving:gemma-4-12B-it-8bit"]
    assert "falling back to manual" in capsys.readouterr().out


def test_provision_download_failure_falls_back_to_manual(capsys):
    ops = FakeOps(runtime=True, model=False, service=False)

    def boom(plan):
        ops.calls.append("download_model:boom")
        raise OSError("hf download failed")

    ops.download_model = boom
    result = localsetup.provision_local_backend(
        ScriptedIO([True]),
        hw=Hardware("Darwin", "arm64", 24, "test"),
        ops=ops,
    )

    assert result is None
    assert "download_model:boom" in ops.calls
    assert "falling back to manual" in capsys.readouterr().out


def test_provision_declined_recommendation_runs_no_effects():
    ops = FakeOps(runtime=False, model=False, service=False)

    assert (
        localsetup.provision_local_backend(
            ScriptedIO([False]),
            hw=Hardware("Darwin", "arm64", 24, "test"),
            ops=ops,
        )
        is None
    )
    assert ops.calls == ["server_serving:gemma-4-26B-A4B-it-MLX-8bit"]


def test_provision_declined_runtime_install_stops_before_effects():
    ops = FakeOps(runtime=False, model=False, service=False)

    assert (
        localsetup.provision_local_backend(
            ScriptedIO([True, False]),
            hw=Hardware("Darwin", "arm64", 24, "test"),
            ops=ops,
        )
        is None
    )
    assert ops.calls == [
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "runtime_installed",
    ]


def test_provision_declined_service_start_stops_before_start():
    ops = FakeOps(runtime=True, model=True, service=False)

    assert (
        localsetup.provision_local_backend(
            ScriptedIO([True, False]),
            hw=Hardware("Darwin", "arm64", 24, "test"),
            ops=ops,
        )
        is None
    )
    assert ops.calls == [
        "server_serving:gemma-4-26B-A4B-it-MLX-8bit",
        "runtime_installed",
        "model_present:26b",
        "reload_models",
        "service_installed",
    ]


def test_mac_install_runtime_also_installs_hf_cli(monkeypatch):
    # runtime_installed() requires both omlx and hf on PATH; install_runtime must provision
    # both, not just omlx, or `hf download` fails right after the user approves install.
    calls: list[list[str]] = []
    monkeypatch.setattr(localsetup, "_run_checked", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(localsetup.shutil, "which", lambda name: None)

    localsetup.MacOMLXOps().install_runtime()

    assert ["brew", "install", "pipx"] in calls
    assert ["pipx", "install", "huggingface_hub[cli]"] in calls


def test_mac_install_runtime_skips_hf_install_when_already_present(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(localsetup, "_run_checked", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(localsetup.shutil, "which", lambda name: "/usr/bin/hf")

    localsetup.MacOMLXOps().install_runtime()

    assert all("pipx" not in cmd for cmd in calls)
