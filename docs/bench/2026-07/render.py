"""Build the published v3 bench page (#101) — docs/bench/2026-07/index.html.

Inputs: the five per-backend exports from the 2026-07-03 60-prompt run
(<backend>.json) and the five 10-target v3 sweeps (v3new-<backend>.json).
The 15 v3-kept targets are RE-SCORED from their recorded commands under the
fixed command extractor (#95: -exec descent, xargs flag args, process
substitution) and the v3 assertions — commands and token/latency/cost fields
are untouched; only parse/binaries/assertions/danger verdicts are recomputed.
"""

import pathlib
from dataclasses import replace

from tinytalk.contract import Danger, Suggestion
from tinytalk.eval.report import RunMeta, load_reports, render_report
from tinytalk.eval.runner import BackendReport, export
from tinytalk.eval.suite import SUITE, check_assertion
from tinytalk.grounding import SystemGrounding
from tinytalk.validate import CommandValidator

D = pathlib.Path("docs/bench/2026-07")
BACKENDS = ["local-gemma4-26b", "local-gemma4-e4b", "local-qwen36-35b", "sonnet5-low", "gpt55-low"]
PROMPTS = {p.id: p for p in SUITE}
ORDER = {p.id: i for i, p in enumerate(SUITE)}
VALIDATOR = CommandValidator(SystemGrounding(), run_dry_run=False)


def rescore(row):
    prompt = PROMPTS[row.prompt_id]
    if row.command is None:
        return row
    suggestion = Suggestion(
        command=row.command,
        explanation="",
        danger=Danger(row.danger) if row.danger else Danger.SAFE,
        confidence=1.0,
        needs=(),
    )
    ladder = VALIDATOR.report(suggestion)
    assertions = {a: check_assertion(a, row.command) for a in prompt.assertions}
    return replace(
        row,
        parses=ladder.parses,
        binaries_exist=ladder.binaries_exist,
        assertions=assertions,
        assertions_pass=all(assertions.values()),
        danger=ladder.danger,
        danger_correct=ladder.danger == prompt.expected_danger,
    )


reports = []
for name in BACKENDS:
    old = load_reports(D / f"{name}.json")[0]
    new = load_reports(D / f"v3new-{name}.json")[0]
    kept = [rescore(r) for r in old.results if r.prompt_id in PROMPTS]
    rows = sorted(kept + list(new.results), key=lambda r: ORDER[r.prompt_id])
    assert len(rows) == len(SUITE), (name, len(rows))
    reports.append(BackendReport(backend=name, model=old.model, local=old.local, results=rows))

export(reports, D / "results.json")

meta = RunMeta(
    run_date="2026-07-03",
    machine="Apple M5 Max, 128 GB",
    pricing_notes=(
        "Suite v3: the 15 v2 targets every model saturated were retired; the 15 kept targets are "
        "re-scored from their recorded 2026-07-03 commands under the fixed command extractor; the "
        "10 new hard targets ran fresh the same day.",
        "Sonnet 5 and GPT-5.5 run through their agent SDKs (Claude Code / Codex CLI logins): "
        "each request carries the CLI's own system context (~20-28k input tokens) and latency "
        "includes SDK startup — that overhead is part of TinyTalk-as-shipped on those backends.",
        "Sonnet 5 charts and the cost column use standard Anthropic rates ($3/$15 per MTok, "
        "cache-read $0.30) from 2026-09-01; per-prompt exports carry intro rates ($2/$10, "
        "cache-read $0.20) through 2026-08-31 — shown as a + (N% intro) note beside the "
        "standard sweep total. Its tokenizer counts ~30% more tokens for the same text.",
        "Cloud costs are list-rate equivalents; actual billing rode subscription logins.",
        "Local quotes: Gemma 4 26B A4B $0.06/$0.33 and Qwen 3.6 35B A3B $0.14/$1.00 (OpenRouter), "
        "Gemma 4 E4B $0.20/$0.20 (Fireworks) — captured 2026-07-03.",
    ),
)
(D / "index.html").write_text(render_report(load_reports(D / "results.json"), meta))
print("published", D / "index.html")
