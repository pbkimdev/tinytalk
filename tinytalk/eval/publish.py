"""Build a published bench page from per-backend exports (#101).

Run: ``tt eval publish --run-date YYYY-MM-DD`` (or ``python -m tinytalk.eval.publish``).

Inputs: prior full-suite exports (<backend>.json) and fresh subset sweeps
(<new-prefix><backend>.json). Kept targets are re-scored from their recorded
commands under the current command extractor; commands and token/latency/cost
fields are untouched — only parse/binaries/assertions/danger verdicts change.

Each run lives under ``docs/bench/<YYYY-MM-DD>/``. Optional ``run_meta.json`` in
that directory supplies ``RunMeta`` (machine string, pricing footnotes).
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import pathlib
import platform
import re
import sys
from dataclasses import replace

from tinytalk.config import ConfigError, load_config
from tinytalk.contract import Danger, Suggestion
from tinytalk.eval.report import RunMeta, load_reports, render_report
from tinytalk.eval.runner import BackendReport, PromptResult, export
from tinytalk.eval.suite import SUITE, check_assertion
from tinytalk.grounding import SystemGrounding
from tinytalk.validate import CommandValidator

BENCH_ROOT = pathlib.Path("docs/bench")
DEFAULT_BENCH_CONFIG = BENCH_ROOT / "bench.toml"
_RUN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SUITE_ORDER = {p.id: i for i, p in enumerate(SUITE)}
_SUITE_IDS = frozenset(_SUITE_ORDER)
_PROMPTS = {p.id: p for p in SUITE}
_VALIDATOR = CommandValidator(SystemGrounding(), run_dry_run=False)


def bench_data_dir(run_date: str) -> pathlib.Path:
    """Return ``docs/bench/<YYYY-MM-DD>`` for a run date."""
    if not _RUN_DATE_RE.fullmatch(run_date):
        raise ValueError(f"run date must be YYYY-MM-DD, got {run_date!r}")
    return BENCH_ROOT / run_date


def parse_run_date(name: str) -> str | None:
    return name if _RUN_DATE_RE.fullmatch(name) else None


def load_run_meta(data_dir: pathlib.Path, run_date: str, *, machine: str = "") -> RunMeta:
    path = data_dir / "run_meta.json"
    if path.is_file():
        payload = json.loads(path.read_text("utf-8"))
        notes = payload.get("pricing_notes", ())
        if isinstance(notes, list):
            notes = tuple(notes)
        return RunMeta(
            run_date=payload.get("run_date", run_date),
            machine=payload.get("machine", machine),
            pricing_notes=notes,
        )
    return RunMeta(
        run_date=run_date,
        machine=machine or f"{platform.system()} {platform.machine()}",
    )


def backends_from_config(config_path: pathlib.Path) -> tuple[str, ...]:
    config = load_config(config_path)
    return tuple(sorted(config.backends))


def load_backend_report(path: pathlib.Path) -> BackendReport:
    reports = load_reports(path)
    if not reports:
        raise ValueError(f"no backend reports in {path}")
    if len(reports) != 1:
        raise ValueError(f"expected one backend report in {path}, got {len(reports)}")
    return reports[0]


def backend_order_from_results(path: pathlib.Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    return tuple(entry["backend"] for entry in json.loads(path.read_text("utf-8")))


def expand_export_paths(spec: str | pathlib.Path) -> tuple[pathlib.Path, ...]:
    path = pathlib.Path(spec)
    if path.is_dir():
        paths = tuple(sorted(path.glob("*.json")))
    elif path.is_file():
        paths = (path,)
    else:
        paths = tuple(sorted(pathlib.Path(match) for match in glob.glob(str(spec))))
    if not paths:
        raise ValueError(f"no export files matched {spec}")
    return paths


def publish_from_exports(
    export_paths: tuple[pathlib.Path, ...],
    meta: RunMeta,
    out_dir: pathlib.Path,
    *,
    backend_order: tuple[str, ...] = (),
) -> None:
    """Publish complete per-backend exports without re-scoring or merging rows."""
    if not export_paths:
        raise ValueError("no export files supplied")
    order = {backend: i for i, backend in enumerate(backend_order)}
    entries = []
    seen = set()
    for path in export_paths:
        report = load_backend_report(path)
        if report.backend in seen:
            raise ValueError(f"duplicate backend export: {report.backend}")
        seen.add(report.backend)
        payload = json.loads(path.read_text("utf-8"))
        entries.append((order.get(report.backend, len(order)), report.backend, payload[0]))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    results_path.write_text(
        json.dumps([payload for _, _, payload in entries], indent=2),
        encoding="utf-8",
    )
    html = render_report(load_reports(results_path), meta)
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def rescore_row(row: PromptResult) -> PromptResult:
    prompt = _PROMPTS[row.prompt_id]
    if row.command is None:
        return row
    suggestion = Suggestion(
        command=row.command,
        explanation="",
        danger=Danger(row.danger) if row.danger else Danger.SAFE,
        confidence=1.0,
        needs=(),
    )
    ladder = _VALIDATOR.report(suggestion)
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


def merge_backend(old: BackendReport, new: BackendReport) -> BackendReport:
    kept = [rescore_row(r) for r in old.results if r.prompt_id in _SUITE_IDS]
    rows = sorted(kept + list(new.results), key=lambda r: _SUITE_ORDER[r.prompt_id])
    if len(rows) != len(SUITE):
        raise ValueError(f"{old.backend}: expected {len(SUITE)} rows, got {len(rows)}")
    return BackendReport(backend=old.backend, model=old.model, local=old.local, results=rows)


def publish(
    data_dir: pathlib.Path,
    backends: tuple[str, ...],
    meta: RunMeta,
    *,
    new_prefix: str = "v3new-",
) -> None:
    reports = []
    for name in backends:
        old = load_backend_report(data_dir / f"{name}.json")
        new = load_backend_report(data_dir / f"{new_prefix}{name}.json")
        reports.append(merge_backend(old, new))
    export(reports, data_dir / "results.json")
    html = render_report(load_reports(data_dir / "results.json"), meta)
    (data_dir / "index.html").write_text(html)


def resolve_paths(
    data_dir: pathlib.Path | None,
    run_date: str | None,
) -> tuple[pathlib.Path, str]:
    if data_dir is not None:
        resolved_date = run_date or parse_run_date(data_dir.name) or datetime.date.today().isoformat()
        return data_dir, resolved_date
    resolved_date = run_date or datetime.date.today().isoformat()
    return bench_data_dir(resolved_date), resolved_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge bench exports and render the published HTML page.",
    )
    parser.add_argument(
        "data_dir",
        nargs="?",
        type=pathlib.Path,
        help="run directory (default: docs/bench/<run-date>)",
    )
    parser.add_argument(
        "--run-date",
        metavar="YYYY-MM-DD",
        help="run date for docs/bench/<date>/ when data_dir is omitted",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=str(DEFAULT_BENCH_CONFIG),
        help=f"bench config for backend roster (default: {DEFAULT_BENCH_CONFIG})",
    )
    parser.add_argument(
        "--machine",
        metavar="TEXT",
        help="machine string for RunMeta when run_meta.json is absent",
    )
    parser.add_argument(
        "--new-prefix",
        default="v3new-",
        help="prefix for subset-sweep exports merged with the full run (default: v3new-)",
    )
    parser.add_argument(
        "--exports",
        metavar="DIR_OR_GLOB",
        nargs="+",
        help="publish complete per-backend exports without merge re-scoring",
    )
    args = parser.parse_args(argv)
    try:
        data_dir, run_date = resolve_paths(args.data_dir, args.run_date)
        meta = load_run_meta(data_dir, run_date, machine=args.machine or "")
        if args.exports:
            export_paths = tuple(
                path for spec in args.exports for path in expand_export_paths(spec)
            )
            publish_from_exports(
                export_paths,
                meta,
                data_dir,
                backend_order=backend_order_from_results(data_dir / "results.json"),
            )
        else:
            backends = backends_from_config(pathlib.Path(args.config))
            publish(data_dir, backends, meta, new_prefix=args.new_prefix)
    except (OSError, ConfigError, ValueError, KeyError) as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    out = data_dir / "index.html"
    print("published", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
