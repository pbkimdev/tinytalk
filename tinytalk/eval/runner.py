"""Eval runner (#32, PRD §11) — score backends on this machine, validation-only.

Each backend gets its own tier controller with no cache (measure the model, not
the cache) and no cross-backend escalation (per-model scores stay per-model).
Nothing is ever executed; commands are scored by the validation ladder and the
deterministic assertion DSL.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tinytalk.config import Config
from tinytalk.cost import cost as _cost  # lifted into cost.py; kept importable here for report.py
from tinytalk.eval.oracle import CASES as ORACLE_CASES
from tinytalk.eval.oracle import oracle_pass as _oracle_pass
from tinytalk.eval.preview import build_file_preview
from tinytalk.eval.suite import SUITE, EvalPrompt, check_assertion
from tinytalk.grounding import SystemGrounding
from tinytalk.provider.base import Completion, CompletionRequest, Provider, ToolCall, Usage
from tinytalk.provider.factory import make_provider
from tinytalk.tiers import NoValidCommand, TierController, TierRequest
from tinytalk.validate import CommandValidator


# Deliberately not from the suite: warmup work must never overlap a scored prompt.
_WARMUP_PROMPT = "print the current working directory"
_EVAL_MAX_TOKENS = 8192


@dataclass(frozen=True)
class PromptResult:
    prompt_id: str
    lang: str = "en"
    target: str = ""
    prompt_text: str = ""
    command: str | None = None
    error: str | None = None
    format_ok: bool = False
    parses: bool = False
    binaries_exist: bool = False
    expected_assertions: list[str] = field(default_factory=list)
    assertions: dict[str, bool] = field(default_factory=dict)
    assertions_pass: bool = False
    oracle_pass: bool | None = None
    danger: str | None = None
    danger_expected: str = "safe"
    danger_correct: bool = False
    tier: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    attempts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BackendReport:
    backend: str
    model: str
    local: bool = False  # served from this machine (localhost base_url)
    results: list[PromptResult] = field(default_factory=list)

    def _pct(self, predicate) -> float:
        if not self.results:
            return 0.0
        return 100.0 * sum(1 for r in self.results if predicate(r)) / len(self.results)

    @property
    def format_ok_pct(self) -> float:
        return self._pct(lambda r: r.format_ok)

    @property
    def parses_pct(self) -> float:
        return self._pct(lambda r: r.parses)

    @property
    def binaries_pct(self) -> float:
        return self._pct(lambda r: r.binaries_exist)

    @property
    def assertions_pct(self) -> float:
        return self._pct(lambda r: r.assertions_pass)

    @property
    def oracle_pass_pct(self) -> float:
        results = [r for r in self.results if r.target in ORACLE_CASES]
        if not results:
            return 0.0
        return 100.0 * sum(1 for r in results if r.oracle_pass is True) / len(results)

    @staticmethod
    def _strict(r: PromptResult) -> bool:
        return r.format_ok and r.parses and r.binaries_exist and r.assertions_pass

    @property
    def strict_pass_pct(self) -> float:
        return self._pct(self._strict)

    def _strict_pct_for(self, lang: str) -> float:
        results = [r for r in self.results if r.lang == lang]
        if not results:
            return 0.0
        return 100.0 * sum(1 for r in results if self._strict(r)) / len(results)

    @property
    def strict_pass_pct_en(self) -> float:
        return self._strict_pct_for("en")

    @property
    def strict_pass_pct_ko(self) -> float:
        return self._strict_pct_for("ko")

    @property
    def danger_pct(self) -> float:
        return self._pct(lambda r: r.danger_correct)

    @property
    def total_tokens(self) -> int:
        return sum(r.prompt_tokens + r.completion_tokens for r in self.results)

    @property
    def median_latency_s(self) -> float:
        latencies = [r.latency_s for r in self.results if r.error is None]
        return statistics.median(latencies) if latencies else 0.0

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)


def run_eval(
    config: Config,
    backend_names: list[str],
    *,
    suite: tuple[EvalPrompt, ...] = SUITE,
    prompt_ids: list[str] | None = None,
    cwd: str = ".",
    progress: bool = True,
    warmup: bool = True,
    data_preview: bool = False,
) -> list[BackendReport]:
    with _isolated_eval_state():
        if prompt_ids:
            # A bare target (e.g. "disk-usage-top") selects the prompt in every language.
            unknown = set(prompt_ids) - {p.id for p in suite} - {p.target for p in suite}
            if unknown:
                raise ValueError(f"unknown prompt ids: {', '.join(sorted(unknown))}")
            suite = tuple(p for p in suite if p.id in prompt_ids or p.target in prompt_ids)
        grounding = SystemGrounding()
        validator = CommandValidator(grounding, cwd=cwd, run_dry_run=False)  # never execute (PRD §11)
        return [
            asyncio.run(
                _run_backend(
                    config,
                    name,
                    suite,
                    grounding,
                    validator,
                    cwd=cwd,
                    progress=progress,
                    warmup=warmup,
                    data_preview=data_preview,
                )
            )
            for name in backend_names
        ]


@contextlib.contextmanager
def _isolated_eval_state():
    """Run evals with empty TinyTalk cache/history roots.

    The eval controller already passes ``cache=None`` so scored prompts never hit
    the T0 exact suggestion cache. This environment guard makes the invariant
    harder to regress: any future eval-side cache or history access lands in a
    fresh temporary XDG root instead of the user's interactive state.
    """
    old_cache = os.environ.get("XDG_CACHE_HOME")
    old_state = os.environ.get("XDG_STATE_HOME")
    with tempfile.TemporaryDirectory(
        prefix="tt-eval-cache-"
    ) as cache_root, tempfile.TemporaryDirectory(prefix="tt-eval-state-") as state_root:
        os.environ["XDG_CACHE_HOME"] = cache_root
        os.environ["XDG_STATE_HOME"] = state_root
        try:
            yield
        finally:
            _restore_env("XDG_CACHE_HOME", old_cache)
            _restore_env("XDG_STATE_HOME", old_state)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


async def _run_backend(
    config: Config,
    name: str,
    suite: tuple[EvalPrompt, ...],
    grounding: SystemGrounding,
    validator: CommandValidator,
    *,
    cwd: str,
    progress: bool,
    warmup: bool,
    data_preview: bool,
) -> BackendReport:
    backend_cfg = config.backend(name)
    recorder = EvalRecorder(make_provider(backend_cfg))
    # temperature=0: greedy decoding so scores are reproducible (bench protocol, #90).
    # max_tokens keeps local OpenAI-compatible servers from using enormous defaults
    # when a model starts narrating instead of returning the compact JSON contract.
    controller = TierController(
        recorder,
        grounding=grounding,
        validator=validator,
        request_opts={"temperature": 0.0, "max_tokens": _EVAL_MAX_TOKENS},
    )
    price = config.price(backend_cfg.model)
    report = BackendReport(backend=name, model=backend_cfg.model, local=_is_local(backend_cfg))

    if warmup:
        # One discarded request eats model load / cold start so it never pollutes the
        # first scored latency. Errors are non-fatal: the scored loop reports its own.
        try:
            recorder.reset()
            await controller.suggest(TierRequest(prompt=_WARMUP_PROMPT, cwd=cwd))
        except Exception as exc:
            if progress:
                print(f"  [{name}] warmup failed (ignored): {exc}", file=sys.stderr)

    for prompt in suite:
        result = await _run_prompt(
            controller,
            recorder,
            validator,
            prompt,
            price,
            cwd,
            data_preview=data_preview,
        )
        report.results.append(result)
        if progress:
            mark = "✓" if result.assertions_pass else ("!" if result.format_ok else "✗")
            print(
                f"  [{name}] {mark} {prompt.id}: {result.command or result.error}",
                file=sys.stderr,
            )
    return report


class EvalRecorder:
    """Provider wrapper that records eval-only request/response transcripts."""

    def __init__(self, provider: Provider):
        self._provider = provider
        self.name = provider.name
        self.capabilities = provider.capabilities
        self.attempts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.attempts = []

    async def complete(self, request: CompletionRequest) -> Completion:
        attempt: dict[str, Any] = {"request": _request_snapshot(request)}
        start = time.perf_counter()
        try:
            completion = await self._provider.complete(request)
        except Exception as exc:
            attempt["latency_s"] = round(time.perf_counter() - start, 3)
            attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
            self.attempts.append(attempt)
            raise
        attempt["latency_s"] = round(time.perf_counter() - start, 3)
        attempt["response"] = _completion_snapshot(completion)
        self.attempts.append(attempt)
        return completion


def _is_local(backend_cfg) -> bool:
    host = urlparse(backend_cfg.base_url or "").hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


async def _run_prompt(
    controller: TierController,
    recorder: EvalRecorder,
    validator: CommandValidator,
    prompt: EvalPrompt,
    price,
    cwd: str,
    *,
    data_preview: bool = False,
) -> PromptResult:
    recorder.reset()
    start = time.perf_counter()
    suggestion, tier, usage, error = None, None, Usage(), None
    file_context = build_file_preview(prompt.target) if data_preview and prompt.target in ORACLE_CASES else ""
    try:
        tier_result = await controller.suggest(
            TierRequest(prompt=prompt.text, cwd=cwd, file_context=file_context)
        )
        suggestion, tier, usage = tier_result.suggestion, tier_result.tier, tier_result.usage
    except NoValidCommand as exc:
        suggestion = exc.last  # rejected by the gate — still score what came back
        usage = exc.usage
        error = str(exc)
    except Exception as exc:  # transport/SDK fault for this prompt only
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - start

    base = PromptResult(
        prompt_id=prompt.id,
        lang=prompt.lang,
        target=prompt.target,
        prompt_text=prompt.text,
        error=error,
        expected_assertions=list(prompt.assertions),
        danger_expected=prompt.expected_danger,
        latency_s=round(latency, 3),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=_cost(usage, price),
        attempts=list(recorder.attempts),
    )
    if suggestion is None:
        return base

    ladder = validator.report(suggestion)
    assertions = {a: check_assertion(a, suggestion.command) for a in prompt.assertions}
    oracle_result = None
    if prompt.target in ORACLE_CASES:
        oracle_result = _oracle_pass(prompt.target, suggestion.command)
    return PromptResult(
        **{
            **asdict(base),
            "command": suggestion.command,
            "format_ok": True,
            "parses": ladder.parses,
            "binaries_exist": ladder.binaries_exist,
            "assertions": assertions,
            "assertions_pass": all(assertions.values()),
            "oracle_pass": oracle_result,
            "danger": ladder.danger,
            "danger_correct": ladder.danger == prompt.expected_danger,
            "tier": tier,
        }
    )


def _request_snapshot(request: CompletionRequest) -> dict[str, Any]:
    return {
        "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        "tools": [asdict(t) for t in request.tools],
        "response_format": request.response_format.value,
        "grammar": request.grammar,
        "reasoning_effort": request.reasoning_effort,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }


def _completion_snapshot(completion: Completion) -> dict[str, Any]:
    raw = _safe_json(completion.raw)
    return {
        "model": completion.model,
        "text": completion.text,
        "tool_calls": [_tool_call_snapshot(t) for t in completion.tool_calls],
        "usage": asdict(completion.usage),
        "thinking": _extract_thinking(raw),
        "raw": raw,
    }


def _tool_call_snapshot(tool_call: ToolCall) -> dict[str, str]:
    return {"id": tool_call.id, "name": tool_call.name, "arguments": tool_call.arguments}


def _safe_json(value: object) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return _object_snapshot(value)


def _object_snapshot(value: object) -> dict[str, Any]:
    if value is None:
        return {"type": "NoneType", "repr": "None"}
    data: dict[str, Any] = {"type": type(value).__name__, "repr": repr(value)}
    attrs: dict[str, Any] = {}
    for name in ("result", "structured_output", "usage", "final_response", "reasoning", "thinking"):
        if hasattr(value, name):
            attrs[name] = _safe_json(getattr(value, name))
    if attrs:
        data["attrs"] = attrs
    return data


def _extract_thinking(raw: Any) -> Any:
    found: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {
                    "thinking",
                    "reasoning",
                    "reasoning_content",
                    "reasoning_text",
                    "chain_of_thought",
                }:
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    if not found:
        return None
    return found[0] if len(found) == 1 else found


def render_leaderboard(reports: list[BackendReport]) -> str:
    header = (
        f"{'backend':<24} {'pass':>6} {'EN':>5} {'KO':>5} {'format':>7} {'parses':>7} "
        f"{'bins':>6} {'assert':>7} {'danger':>7} {'tokens':>8} {'p50 lat':>8} {'cost':>9}"
    )
    lines = [header, "-" * len(header)]
    ranked = sorted(reports, key=lambda r: (-r.strict_pass_pct, r.total_cost_usd))
    for r in ranked:
        lines.append(
            f"{r.backend:<24} {r.strict_pass_pct:>5.0f}% {r.strict_pass_pct_en:>4.0f}% "
            f"{r.strict_pass_pct_ko:>4.0f}% {r.format_ok_pct:>6.0f}% {r.parses_pct:>6.0f}% "
            f"{r.binaries_pct:>5.0f}% {r.assertions_pct:>6.0f}% {r.danger_pct:>6.0f}% "
            f"{r.total_tokens:>8} {r.median_latency_s:>7.2f}s ${r.total_cost_usd:>8.4f}"
        )
    return "\n".join(lines)


def render_matrix(reports: list[BackendReport]) -> str:
    ids = [r.prompt_id for r in reports[0].results] if reports else []
    width = max((len(i) for i in ids), default=10)
    header = f"{'prompt':<{width}} " + " ".join(f"{r.backend[:14]:>14}" for r in reports)
    lines = [header, "-" * len(header)]
    for i, prompt_id in enumerate(ids):
        cells = []
        for r in reports:
            res = r.results[i]
            cells.append(f"{'pass' if res.assertions_pass else 'FAIL':>14}")
        lines.append(f"{prompt_id:<{width}} " + " ".join(cells))
    return "\n".join(lines)


def export(reports: list[BackendReport], path: Path) -> None:
    """Write results as .json or .csv, chosen by extension (PRD §11)."""
    if path.suffix == ".json":
        payload = [
            {
                "backend": r.backend,
                "model": r.model,
                "local": r.local,
                "results": [asdict(x) for x in r.results],
            }
            for r in reports
        ]
        path.write_text(json.dumps(payload, indent=2), "utf-8")
        return
    if path.suffix == ".csv":
        fields = [f for f in PromptResult.__dataclass_fields__ if f != "assertions"]
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["backend", "model", *fields])
            for r in reports:
                for x in r.results:
                    row = asdict(x)
                    writer.writerow([r.backend, r.model, *(row[f] for f in fields)])
        return
    raise ValueError(f"unsupported export format: {path.suffix!r} (use .json or .csv)")
