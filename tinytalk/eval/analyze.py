"""Bench analysis layer — mine a recorded run for model-improvement insight.

Read-only over recorded exports (`results.json`, and optional repeated `runs/`);
never calls a model or the validator. Turns the single strict-pass AND into four
metric families that say *where* a model breaks, so a fix has a target:

  1. delivery  — did the model hand back a runnable command, or garble the
                 structured-output JSON so the answer was dropped in transit?
                 (delivery_rate + a fault taxonomy; the 12B-QAT's dominant weakness).
  2. layers    — decompose strict-pass into delivered -> runs -> intent, with a
                 per-prompt first-failing layer and an exact per-assertion breakdown.
                 (2)+(3) collapse into one "intent" layer on purpose: assertion KIND
                 does not map to a layer (`regex:`/`contains:` encode tool-choice AND
                 result-shape depending on the target), so the honest, actionable unit
                 is the per-assertion pass rate, not an unsound (2)/(3) split.
  3. slices    — EN/KO, per-target, per-category, and an `escape_heavy` hypothesis
                 (targets whose canonical answer must emit backslash escapes inside
                 the JSON command — where delivery faults concentrate).
  4. stability — across N repeated temperature-0 runs: flip_rate / command_flip_rate.
                 An improvement is "real" only vs. the run-to-run noise band.

CLI: ``tt eval analyze [data_dir]`` (dispatched in cli.main like ``eval publish``).
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tinytalk.eval.publish import resolve_paths
from tinytalk.eval.report import load_reports
from tinytalk.eval.runner import BackendReport, PromptResult
from tinytalk.eval.suite import SUITE

# 25 golden targets -> 6 functional categories (covers exactly {p.target for p in SUITE};
# guarded by test_category_map_complete). A static map, not an EvalPrompt field: category is
# an analysis concern, not something the runner or product needs.
CATEGORY: dict[str, str] = {
    "count-lines-code": "text-processing",
    "extract-columns": "text-processing",
    "replace-in-files": "text-processing",
    "unique-frequency": "text-processing",
    "watch-log": "text-processing",
    "grep-recursive-ext": "text-processing",
    "find-large-files": "text-processing",
    "json-extract": "structured-parsing",
    "extract-ips": "structured-parsing",
    "ini-section": "structured-parsing",
    "awk-group-sum": "structured-parsing",
    "log-top-errors": "pipelines",
    "csv-columns-transform": "pipelines",
    "loop-backup-copies": "pipelines",
    "diff-sorted": "pipelines",
    "parallel-compress": "pipelines",
    "cert-expiry": "networking",
    "dns-trace": "networking",
    "ssh-stream-copy": "networking",
    "k8s-crashloop": "kubernetes",
    "k8s-restart-count": "kubernetes",
    "git-delete-branch": "git-and-fs",
    "git-find-deleted": "git-and-fs",
    "archive-create": "git-and-fs",
    "delete-node-modules": "git-and-fs",
}

# A FALSIFIABLE HYPOTHESIS, defined by the linguistic property (the canonical answer must
# emit regex/sed/awk backslash escapes — \d \b \. escaped brackets — inside the JSON command
# string), NOT by the failure set. If backslash-in-JSON was the true cause, a fixed model's
# escape_heavy delivery should recover. Kept a curated subset, never derived per-run.
ESCAPE_HEAVY: frozenset[str] = frozenset(
    {"count-lines-code", "extract-ips", "ini-section", "k8s-restart-count"}
)

# Targets in suite order, deduplicated across the EN/KO pair.
_TARGETS: list[str] = list(dict.fromkeys(p.target for p in SUITE))


def _pct(num: int, den: int) -> float:
    return 100.0 * num / den if den else 0.0


# --- Metric #1: delivery -----------------------------------------------------

def classify_delivery_fault(r: PromptResult) -> str | None:
    """Bucket a dropped answer, or None if the command was delivered.

    Gate on format_ok FIRST: a delivered command is never a delivery fault even when
    it carries an ``error`` — a gate-rejection ("git: unknown option", "{}: not
    installed") is an INTENT/RUNS miss on a delivered row, not a dropped answer.
    Then decode-vs-transport: the malformed-JSON buckets are reachable only when the
    decode marker is present, so a transport/SDK fault can never be mislabeled envelope.
    """
    if r.format_ok:
        return None
    error = r.error or ""
    if error == "":
        return "unknown"  # invariant guard: format_ok==False should always carry an error
    if "could not decode completion" not in error:
        return "transport"  # SDK/provider fault, no completion decoded
    # Decode marker present -> a real garbling signal, so a decode-then-transport drop
    # (T1 garbled JSON, T2 died on transport) is bucketed as envelope, not transport.
    if "Invalid \\escape" in error:
        return "unescaped_backslash"  # \b \. \d \; in regex/sed not JSON-escaped
    if any(k in error for k in ("Expecting", "Unterminated", "Extra data")):
        return "malformed_json"  # truncation / stray token / structure break
    return "decode_other"


@dataclass(frozen=True)
class DeliveryStats:
    rate: float  # == BackendReport.format_ok_pct, reframed: % of prompts answered intact
    faults: dict[str, int]  # bucket -> count, over dropped rows only (nonzero buckets)
    fault_ids: dict[str, list[str]]  # bucket -> prompt_ids, so each dropped prompt is openable
    escape_fault_pct: float  # % of ALL prompts lost specifically to unescaped backslash


def _delivery_stats(results: list[PromptResult]) -> DeliveryStats:
    faults: dict[str, int] = {}
    fault_ids: dict[str, list[str]] = {}
    for r in results:
        bucket = classify_delivery_fault(r)
        if bucket is None:
            continue
        faults[bucket] = faults.get(bucket, 0) + 1
        fault_ids.setdefault(bucket, []).append(r.prompt_id)
    delivered = sum(1 for r in results if r.format_ok)
    return DeliveryStats(
        rate=_pct(delivered, len(results)),
        faults=faults,
        fault_ids=fault_ids,
        escape_fault_pct=_pct(faults.get("unescaped_backslash", 0), len(results)),
    )


# --- Metric #2: layers -------------------------------------------------------

def first_failing_layer(r: PromptResult) -> str:
    """The first of Paul's success gates a prompt trips, purely from recorded booleans.

    Order delivered -> parses -> binaries -> intent -> pass; short-circuits so a dropped
    row (format_ok==False, with default-False parses/binaries) is attributed ONLY to
    'delivered', never double-counted. first_failing['pass'] == the strict-pass count.
    """
    if not r.format_ok:
        return "delivered"
    if not r.parses:
        return "parses"
    if not r.binaries_exist:
        return "binaries"
    if not r.assertions_pass:
        return "intent"
    return "pass"


@dataclass(frozen=True)
class LayerStats:
    delivered_pct: float  # L0: a command came back at all
    runs_given_delivered_pct: float  # L1 | delivered: parses AND every binary/flag real
    intent_given_runs_pct: float  # L2 | runs: assertions pass (sensible + result, fused)
    first_failing: dict[str, int]  # partitions all rows; 'pass' == strict-pass count
    intent_failures: dict[str, list[str]]  # prompt_id -> failing "kind:value" assertion keys
    per_assertion: dict[str, list[int]]  # "kind:value" -> [passed, total], delivered rows only


def _layer_stats(results: list[PromptResult]) -> LayerStats:
    n = len(results)
    delivered = [r for r in results if r.format_ok]
    runs = [r for r in delivered if r.parses and r.binaries_exist]
    intent_pass = [r for r in runs if r.assertions_pass]

    first_failing: dict[str, int] = {}
    for r in results:
        layer = first_failing_layer(r)
        first_failing[layer] = first_failing.get(layer, 0) + 1

    intent_failures: dict[str, list[str]] = {
        r.prompt_id: [k for k, ok in r.assertions.items() if not ok]
        for r in results
        if first_failing_layer(r) == "intent"
    }

    per_assertion: dict[str, list[int]] = {}
    for r in delivered:  # a dropped row has no scored assertions; exclude from totals
        for key, ok in r.assertions.items():
            cell = per_assertion.setdefault(key, [0, 0])
            cell[0] += int(ok)
            cell[1] += 1

    return LayerStats(
        delivered_pct=_pct(len(delivered), n),
        runs_given_delivered_pct=_pct(len(runs), len(delivered)),
        intent_given_runs_pct=_pct(len(intent_pass), len(runs)),
        first_failing=first_failing,
        intent_failures=intent_failures,
        per_assertion=per_assertion,
    )


# --- Metric #3: slices -------------------------------------------------------

@dataclass(frozen=True)
class SliceStats:
    strict_en: float
    strict_ko: float
    ko_minus_en: float
    per_target: list[list]  # [target, en_pass: bool, ko_pass: bool, ko_minus_en: int]
    by_category: dict[str, dict[str, float]]  # category -> {strict, delivery, ko_minus_en}
    escape_heavy: dict[str, float]  # {delivery, strict} over the escape_heavy prompts
    rest: dict[str, float]  # {delivery, strict} over everything else


def _group_stats(rows: list[PromptResult]) -> dict[str, float]:
    return {
        "delivery": _pct(sum(1 for r in rows if r.format_ok), len(rows)),
        "strict": _pct(sum(1 for r in rows if BackendReport._strict(r)), len(rows)),
    }


def _slice_stats(report: BackendReport) -> SliceStats:
    by_id = {r.prompt_id: r for r in report.results}
    per_target: list[list] = []
    for target in _TARGETS:
        en = by_id.get(f"{target}-en")
        ko = by_id.get(f"{target}-ko")
        if en is None or ko is None:
            continue  # partial export (a subset sweep): only pair-complete targets are comparable
        en_pass = BackendReport._strict(en)
        ko_pass = BackendReport._strict(ko)
        per_target.append([target, en_pass, ko_pass, int(ko_pass) - int(en_pass)])

    by_category: dict[str, dict[str, float]] = {}
    for category in sorted(set(CATEGORY.values())):
        rows = [r for r in report.results if CATEGORY.get(r.target) == category]
        g = _group_stats(rows)
        ens = [r for r in rows if r.lang == "en"]
        kos = [r for r in rows if r.lang == "ko"]
        # A KO-EN gap needs both languages present; on a partial export one side may be empty,
        # and scoring "absent" as 0% would fake a regression — report no gap instead.
        ko_en = (
            _pct(sum(BackendReport._strict(r) for r in kos), len(kos))
            - _pct(sum(BackendReport._strict(r) for r in ens), len(ens))
            if ens and kos
            else 0.0
        )
        by_category[category] = {**g, "ko_minus_en": ko_en}

    heavy = [r for r in report.results if r.target in ESCAPE_HEAVY]
    rest = [r for r in report.results if r.target not in ESCAPE_HEAVY]
    return SliceStats(
        strict_en=report.strict_pass_pct_en,
        strict_ko=report.strict_pass_pct_ko,
        ko_minus_en=report.strict_pass_pct_ko - report.strict_pass_pct_en,
        per_target=per_target,
        by_category=by_category,
        escape_heavy=_group_stats(heavy),
        rest=_group_stats(rest),
    )


# --- Metric #5: stability ----------------------------------------------------

@dataclass(frozen=True)
class StabilityStats:
    backend: str
    n_runs: int
    per_run_strict: list[float]
    median_strict: float
    min_strict: float
    max_strict: float
    flip_rate: float  # verdict not unanimous across runs
    command_flip_rate: float  # command text (None distinct) not identical across runs
    flipped: list[str]  # prompt_ids whose strict verdict flipped
    command_flipped: list[str]  # prompt_ids whose command text drifted


def analyze_stability(runs: list[BackendReport], *, min_n: int = 3) -> StabilityStats:
    """flip_rate / command_flip_rate across N repeats of one backend.

    Requires >= min_n runs and an identical prompt_id set in every run — a mismatch is a
    hard error (you cannot compute a flip over a prompt absent from a replicate), not a
    silent degrade. Invariant flip_rate <= command_flip_rate holds for the real protocol
    (same-machine, back-to-back runs): scoring is a pure function of the command *given
    fixed host state*, so a verdict flip forces a command flip and the gap is cosmetic
    drift. (Only a PATH change between runs could flip a verdict without a command change —
    outside the protocol; not enforced, so analysis never crashes on it.)
    """
    if len(runs) < min_n:
        raise ValueError(f"stability needs >= {min_n} runs, got {len(runs)}")
    backend = runs[0].backend
    keysets = [frozenset(r.prompt_id for r in run.results) for run in runs]
    if any(ks != keysets[0] for ks in keysets):
        raise ValueError(f"{backend}: stability runs have mismatched prompt_id sets")

    by_run = [{r.prompt_id: r for r in run.results} for run in runs]
    prompt_ids = sorted(keysets[0])
    flipped, command_flipped = [], []
    for pid in prompt_ids:
        verdicts = {BackendReport._strict(run[pid]) for run in by_run}
        commands = {run[pid].command for run in by_run}
        if len(verdicts) > 1:
            flipped.append(pid)
        if len(commands) > 1:
            command_flipped.append(pid)

    per_run_strict = [run.strict_pass_pct for run in runs]
    n_prompts = len(prompt_ids)
    return StabilityStats(
        backend=backend,
        n_runs=len(runs),
        per_run_strict=per_run_strict,
        median_strict=statistics.median(per_run_strict),
        min_strict=min(per_run_strict),
        max_strict=max(per_run_strict),
        flip_rate=_pct(len(flipped), n_prompts),
        command_flip_rate=_pct(len(command_flipped), n_prompts),
        flipped=flipped,
        command_flipped=command_flipped,
    )


# --- top-level analysis ------------------------------------------------------

@dataclass(frozen=True)
class BackendAnalysis:
    backend: str
    model: str
    n: int
    delivery: DeliveryStats
    layers: LayerStats
    slices: SliceStats


@dataclass(frozen=True)
class Analysis:
    run_date: str
    backends: list[BackendAnalysis]
    stability: list[StabilityStats] = field(default_factory=list)


def analyze_report(report: BackendReport) -> BackendAnalysis:
    return BackendAnalysis(
        backend=report.backend,
        model=report.model,
        n=len(report.results),
        delivery=_delivery_stats(report.results),
        layers=_layer_stats(report.results),
        slices=_slice_stats(report),
    )


def analyze(
    reports: list[BackendReport],
    stability_runs: dict[str, list[BackendReport]] | None = None,
    *,
    run_date: str = "",
    min_n: int = 3,
) -> Analysis:
    stability: list[StabilityStats] = []
    for backend in sorted((stability_runs or {})):
        runs = stability_runs[backend]
        if len(runs) >= min_n:
            stability.append(analyze_stability(runs, min_n=min_n))
    return Analysis(
        run_date=run_date,
        backends=[analyze_report(r) for r in reports],
        stability=stability,
    )


def analysis_to_dict(a: Analysis) -> dict:
    return asdict(a)


# --- rendering ---------------------------------------------------------------

def render_analysis(a: Analysis) -> str:
    out: list[str] = [f"tt bench analysis — {a.run_date}".rstrip(" —")]

    out.append("\n## delivery — did an intact command come back?")
    out.append(f"{'backend':<24} {'delivered':>10} {'esc-fault':>10}  faults")
    for b in a.backends:
        d = b.delivery
        faults = ", ".join(f"{k} {v}" for k, v in sorted(d.faults.items())) or "—"
        out.append(
            f"{b.backend:<24} {d.rate:>9.1f}% {d.escape_fault_pct:>9.1f}%  {faults}"
        )

    out.append("\n## layers — where strict-pass is lost (delivered -> runs -> intent)")
    out.append(f"{'backend':<24} {'deliv':>7} {'runs|d':>7} {'int|r':>7}  first-failing")
    for b in a.backends:
        L = b.layers
        hist = " ".join(f"{k}:{v}" for k, v in sorted(L.first_failing.items()))
        out.append(
            f"{b.backend:<24} {L.delivered_pct:>6.0f}% {L.runs_given_delivered_pct:>6.0f}% "
            f"{L.intent_given_runs_pct:>6.0f}%  {hist}"
        )
        for pid, keys in L.intent_failures.items():
            out.append(f"{'':<24}   intent-miss {pid}: {', '.join(keys)}")

    out.append("\n## per-assertion — checks that fail (passed/total, delivered rows)")
    for b in a.backends:
        misses = sorted(
            ((k, p, t) for k, (p, t) in b.layers.per_assertion.items() if p < t),
            key=lambda x: (x[2] - x[1]),
            reverse=True,
        )
        out.append(f"  [{b.backend}] " + ("all assertions pass" if not misses else ""))
        for key, p, t in misses:
            out.append(f"    {p}/{t}  {key}")

    out.append("\n## slices — EN/KO, category, escape_heavy hypothesis")
    for b in a.backends:
        s = b.slices
        out.append(
            f"  [{b.backend}] EN {s.strict_en:.0f}  KO {s.strict_ko:.0f}  "
            f"KO-EN {s.ko_minus_en:+.0f}"
        )
        out.append(f"    {'category':<20} {'strict':>7} {'deliv':>7} {'KO-EN':>7}")
        for cat, g in sorted(s.by_category.items()):
            out.append(
                f"    {cat:<20} {g['strict']:>6.0f}% {g['delivery']:>6.0f}% "
                f"{g['ko_minus_en']:>+6.0f}"
            )
        out.append(
            f"    escape_heavy: delivery {s.escape_heavy['delivery']:.0f}% "
            f"strict {s.escape_heavy['strict']:.0f}%  vs  rest: delivery "
            f"{s.rest['delivery']:.0f}% strict {s.rest['strict']:.0f}%  "
            f"(hypothesis: backslash-in-JSON dropouts)"
        )

    if a.stability:
        out.append("\n## stability — N repeated temperature-0 runs")
        out.append(
            f"{'backend':<24} {'N':>2} {'median':>7} {'[min,max]':>13} "
            f"{'flip':>6} {'cmd-flip':>9}"
        )
        for st in a.stability:
            out.append(
                f"{st.backend:<24} {st.n_runs:>2} {st.median_strict:>6.0f}% "
                f"{f'[{st.min_strict:.0f},{st.max_strict:.0f}]':>13} "
                f"{st.flip_rate:>5.0f}% {st.command_flip_rate:>8.0f}%"
            )
            if st.flipped:
                out.append(f"{'':<24}   verdict-flipped: {', '.join(st.flipped)}")
        out.append(
            "  improvement is 'real' only if a backend's median beats another's by more "
            "than the noise band (non-overlapping [min,max], or gap > half-spread)."
        )
    return "\n".join(out)


# --- CLI ---------------------------------------------------------------------

def _load_stability_runs(paths: list[str]) -> dict[str, list[BackendReport]]:
    runs: dict[str, list[BackendReport]] = {}
    for path in sorted(paths):
        for report in load_reports(Path(path)):
            runs.setdefault(report.backend, []).append(report)
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tt eval analyze",
        description="Mine a recorded bench run for model-improvement insight (read-only).",
    )
    parser.add_argument("data_dir", nargs="?", type=Path, help="run dir (default docs/bench/<date>)")
    parser.add_argument("--run-date", metavar="YYYY-MM-DD", help="run date when data_dir omitted")
    parser.add_argument("--backend", metavar="NAME", help="restrict to one backend")
    parser.add_argument(
        "--runs",
        metavar="GLOB",
        help="repeated-run exports for stability (default <data_dir>/stability/*.json)",
    )
    parser.add_argument("--out", metavar="PATH", help="analysis.json path (default <data_dir>/analysis.json)")
    parser.add_argument("--json", action="store_true", help="print analysis.json to stdout instead of a file")
    parser.add_argument("--min-n", type=int, default=3, help="min repeats for a stability row (default 3)")
    args = parser.parse_args(argv)

    try:
        data_dir, run_date = resolve_paths(args.data_dir, args.run_date)
        reports = load_reports(data_dir / "results.json")
        if args.backend:
            reports = [r for r in reports if r.backend == args.backend]
            if not reports:
                raise ValueError(f"backend {args.backend!r} not in results.json")
        runs_glob = args.runs or str(data_dir / "stability" / "*.json")
        stability_runs = _load_stability_runs(_glob.glob(runs_glob))
        if args.backend:
            stability_runs = {k: v for k, v in stability_runs.items() if k == args.backend}
        result = analyze(reports, stability_runs, run_date=run_date, min_n=args.min_n)
    except (OSError, ValueError, KeyError) as exc:
        print(f"analyze: {exc}", file=sys.stderr)
        return 1

    payload = analysis_to_dict(result)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_analysis(result))
        out_path = Path(args.out) if args.out else data_dir / "analysis.json"
        out_path.write_text(json.dumps(payload, indent=2), "utf-8")
        print(f"\nanalysis written to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
