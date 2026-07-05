"""Head-to-head: Atuin AI vs TinyTalk backends, on the behavioral oracle.

Atuin AI turns English into a shell command from its interactive `?` UI. It has
no API, so its commands are captured into a ``{target: command}`` JSON by
``docs/bench/atuin/capture.zsh`` (see that dir's README). This module grades that
map with the execution oracle and lines it up against TinyTalk's own backends,
re-graded by the *same* oracle from an existing bench ``results.json``.

Why the oracle and not the suite's assertion DSL: the DSL (``uses:awk`` etc.)
checks *how* TinyTalk's prompt made a model phrase a command — unfair to a
different tool that may reach the right answer a different way. The oracle
(``oracle_pass``) checks *what the command produces* when run against the fixture
input, which is tool-agnostic and the only fair cross-tool grader. Every row here
is the same 14 behavioral fixtures under that one grader.

    python -m tinytalk.eval.atuin prompts
    python -m tinytalk.eval.atuin report --captured atuin-commands.json \
        --results docs/bench/2026-07-05/results.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from tinytalk.eval.oracle import CASES, oracle_pass
from tinytalk.eval.suite import SUITE

ATUIN_LABEL = "atuin-ai"


def fixture_prompts() -> list[tuple[str, str]]:
    """The behavioral targets that have an execution oracle, English prompt each.

    Order follows the suite; this is exactly the set of fixtures every row is
    scored on (the keys of the oracle's ``CASES`` registry).
    """
    return [(p.target, p.text) for p in SUITE if p.lang == "en" and p.target in CASES]


@dataclass(frozen=True)
class Row:
    label: str
    passed: frozenset[str]  # targets whose command the oracle accepted
    commands: dict[str, str]  # target -> the command that was graded


def grade_commands(label: str, commands: dict[str, str]) -> Row:
    """Grade a ``{target: command}`` map with the oracle; unknown targets ignored."""
    targets = {t for t, _ in fixture_prompts()}
    passed = {
        target
        for target, command in commands.items()
        if target in targets and command and command.strip() and oracle_pass(target, command)
    }
    kept = {t: c for t, c in commands.items() if t in targets}
    return Row(label, frozenset(passed), kept)


def reference_rows(results_path: Path) -> list[Row]:
    """Re-grade each backend in a bench ``results.json`` with the oracle.

    Uses the stored generated command (English prompts only), so no model is
    re-run — the numbers come straight off disk but pass the same oracle Atuin's
    commands do, making the two directly comparable.
    """
    data = json.loads(results_path.read_text())
    targets = {t for t, _ in fixture_prompts()}
    rows: list[Row] = []
    for backend in data:
        commands = {
            record["target"]: record["command"]
            for record in backend["results"]
            if record.get("lang") == "en"
            and record.get("target") in targets
            and record.get("command")
        }
        rows.append(grade_commands(backend["backend"], commands))
    return rows


def render(rows: list[Row]) -> str:
    """Leaderboard + per-fixture ✓/✗ matrix, Atuin pinned first, rest by score."""
    targets = [t for t, _ in fixture_prompts()]
    total = len(targets)
    ordered = sorted(
        rows,
        key=lambda r: (r.label != ATUIN_LABEL, -len(r.passed), r.label),
    )

    width = max((len(r.label) for r in ordered), default=len("backend"))
    width = max(width, len("backend"))
    lines = [
        f"{'backend':<{width}}  {'oracle':>6}   pass",
        f"{'-' * width}  {'-' * 6}   ----",
    ]
    for row in ordered:
        n = len(row.passed)
        pct = 100 * n / total if total else 0.0
        lines.append(f"{row.label:<{width}}  {pct:>5.0f}%   {n:>2}/{total}")

    lines.append("")
    lines.append("per-fixture (✓ oracle pass, · fail, — not captured):")
    for target in targets:
        cells = []
        for row in ordered:
            if target not in row.commands:
                cells.append("—")
            elif target in row.passed:
                cells.append("✓")
            else:
                cells.append("·")
        header_cells = " ".join(f"{c}" for c in cells)
        lines.append(f"{target:<24} {header_cells}")
    lines.append("")
    lines.append("columns: " + "  ".join(f"{i + 1}={r.label}" for i, r in enumerate(ordered)))
    return "\n".join(lines)


def _cmd_prompts(_args: argparse.Namespace) -> int:
    for target, text in fixture_prompts():
        print(f"{target}\t{text}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    rows: list[Row] = []
    if args.captured:
        captured = json.loads(Path(args.captured).read_text())
        rows.append(grade_commands(args.label, captured))
    if args.results:
        rows.extend(reference_rows(Path(args.results)))
    if not rows:
        print("nothing to report: pass --captured and/or --results")
        return 2
    print(render(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tinytalk.eval.atuin",
        description="Compare Atuin AI against TinyTalk backends on the execution oracle.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prompts = sub.add_parser("prompts", help="print the 14 fixture targets + English prompts (TSV)")
    prompts.set_defaults(func=_cmd_prompts)

    report = sub.add_parser("report", help="grade captured Atuin commands and/or a bench results.json")
    report.add_argument("--captured", metavar="PATH", help="{target: command} JSON from capture.zsh")
    report.add_argument("--results", metavar="PATH", help="bench results.json to re-grade as reference rows")
    report.add_argument("--label", default=ATUIN_LABEL, help=f"label for --captured row (default {ATUIN_LABEL})")
    report.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
