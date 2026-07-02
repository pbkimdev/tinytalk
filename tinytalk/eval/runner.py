"""Eval runner (#32, PRD §11) — score backends on this machine, validation-only.

Each backend gets its own tier controller with no cache (measure the model, not
the cache) and no cross-backend escalation (per-model scores stay per-model).
Nothing is ever executed; commands are scored by the validation ladder and the
deterministic assertion DSL.
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tinytalk.config import Config
from tinytalk.eval.suite import SUITE, EvalPrompt, check_assertion
from tinytalk.grounding import SystemGrounding
from tinytalk.provider.base import Usage
from tinytalk.provider.factory import make_provider
from tinytalk.tiers import NoValidCommand, TierController, TierRequest
from tinytalk.validate import CommandValidator


@dataclass(frozen=True)
class PromptResult:
    prompt_id: str
    command: str | None = None
    error: str | None = None
    format_ok: bool = False
    parses: bool = False
    binaries_exist: bool = False
    assertions: dict[str, bool] = field(default_factory=dict)
    assertions_pass: bool = False
    danger: str | None = None
    danger_expected: str = "safe"
    danger_correct: bool = False
    tier: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0


@dataclass
class BackendReport:
    backend: str
    model: str
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
) -> list[BackendReport]:
    if prompt_ids:
        unknown = set(prompt_ids) - {p.id for p in suite}
        if unknown:
            raise ValueError(f"unknown prompt ids: {', '.join(sorted(unknown))}")
        suite = tuple(p for p in suite if p.id in prompt_ids)
    grounding = SystemGrounding()
    validator = CommandValidator(grounding, cwd=cwd, run_dry_run=False)  # never execute (PRD §11)
    return [
        asyncio.run(
            _run_backend(config, name, suite, grounding, validator, cwd=cwd, progress=progress)
        )
        for name in backend_names
    ]


async def _run_backend(
    config: Config,
    name: str,
    suite: tuple[EvalPrompt, ...],
    grounding: SystemGrounding,
    validator: CommandValidator,
    *,
    cwd: str,
    progress: bool,
) -> BackendReport:
    backend_cfg = config.backend(name)
    provider = make_provider(backend_cfg)
    controller = TierController(provider, grounding=grounding, validator=validator)
    price = config.price(backend_cfg.model)
    report = BackendReport(backend=name, model=backend_cfg.model)

    for prompt in suite:
        result = await _run_prompt(controller, validator, prompt, price, cwd)
        report.results.append(result)
        if progress:
            mark = "✓" if result.assertions_pass else ("!" if result.format_ok else "✗")
            print(
                f"  [{name}] {mark} {prompt.id}: {result.command or result.error}",
                file=sys.stderr,
            )
    return report


async def _run_prompt(
    controller: TierController,
    validator: CommandValidator,
    prompt: EvalPrompt,
    price,
    cwd: str,
) -> PromptResult:
    start = time.perf_counter()
    suggestion, tier, usage, error = None, None, Usage(), None
    try:
        tier_result = await controller.suggest(TierRequest(prompt=prompt.text, cwd=cwd))
        suggestion, tier, usage = tier_result.suggestion, tier_result.tier, tier_result.usage
    except NoValidCommand as exc:
        suggestion = exc.last  # rejected by the gate — still score what came back
        error = str(exc)
    except Exception as exc:  # transport/SDK fault for this prompt only
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - start

    base = PromptResult(
        prompt_id=prompt.id,
        error=error,
        danger_expected=prompt.expected_danger,
        latency_s=round(latency, 3),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_usd=round(
            usage.prompt_tokens * price.input_per_mtok / 1e6
            + usage.completion_tokens * price.output_per_mtok / 1e6,
            6,
        ),
    )
    if suggestion is None:
        return base

    ladder = validator.report(suggestion)
    assertions = {a: check_assertion(a, suggestion.command) for a in prompt.assertions}
    return PromptResult(
        **{
            **asdict(base),
            "command": suggestion.command,
            "format_ok": True,
            "parses": ladder.parses,
            "binaries_exist": ladder.binaries_exist,
            "assertions": assertions,
            "assertions_pass": all(assertions.values()),
            "danger": ladder.danger,
            "danger_correct": ladder.danger == prompt.expected_danger,
            "tier": tier,
        }
    )


def render_leaderboard(reports: list[BackendReport]) -> str:
    header = (
        f"{'backend':<24} {'format':>7} {'parses':>7} {'bins':>6} {'assert':>7} "
        f"{'danger':>7} {'tokens':>8} {'p50 lat':>8} {'cost':>9}"
    )
    lines = [header, "-" * len(header)]
    ranked = sorted(reports, key=lambda r: (-r.assertions_pct, r.total_cost_usd))
    for r in ranked:
        lines.append(
            f"{r.backend:<24} {r.format_ok_pct:>6.0f}% {r.parses_pct:>6.0f}% "
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
            {"backend": r.backend, "model": r.model, "results": [asdict(x) for x in r.results]}
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
