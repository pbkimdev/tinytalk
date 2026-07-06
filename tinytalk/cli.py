"""Command-line entry point for TinyTalk.

`tt "<request>"` runs config → tier controller → validated suggestion. The
command goes to stdout (script-friendly; the zsh widget reads it), explanation
and danger to stderr. TinyTalk never auto-runs the commands it generates; it
always hands control back to the user.

Heavy imports happen after argument parsing so `--version`/`--help` stay fast
(PRD §15 cold-start budget).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from tinytalk import __version__

# How many recent records `tt history` reads before deduping — shared by the porcelain
# widget feed and the plaintext viewer.
_PORCELAIN_LIMIT = 1000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt",
        description="Turn plain English at the shell into a real, validated command.",
        epilog=(
            "commands:\n"
            "  auth        interactively set up a provider backend\n"
            "  config      change a setting in config.toml (e.g. `tt config explanation off`)\n"
            "  eval        benchmark configured backends (see `tt eval publish` for the docs page)\n"
            "  ground      inspect or rebuild the system grounding cache\n"
            "  history     browse and reuse past commands\n"
            '  init zsh    print the zsh integration script (eval "$(tt init zsh)")\n'
            "  prompt      print the assembled model prompt for a request (no model call)\n"
            "\n"
            "run `tt <command> --help` for command options"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"tt {__version__}")
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    parser.add_argument(
        "--backend", metavar="NAME", help="backend from config (default: defaults.backend)"
    )
    parser.add_argument("--json", action="store_true", help="emit the full suggestion as JSON")
    parser.add_argument(
        "--widget",
        action="store_true",
        help="emit shell-evalable tt_* assignments (used by the zsh widget)",
    )
    parser.add_argument(
        "request",
        nargs="*",
        help="what you want to do, in plain English",
    )
    return parser


def build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt eval",
        description="Benchmark configured backends over the built-in prompt suite.",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    parser.add_argument("--backends", metavar="A,B", help="backends to score (default: all)")
    parser.add_argument(
        "--prompts",
        metavar="ID,ID",
        help="run a subset of the suite (full ids, or bare targets to get every language)",
    )
    parser.add_argument("--export", metavar="PATH", help="write results to a .json or .csv file")
    parser.add_argument(
        "--report", metavar="PATH", help="write a self-contained HTML report of the results"
    )
    parser.add_argument(
        "--report-from",
        metavar="JSON",
        help="re-render --report from a previous --export .json instead of running",
    )
    parser.add_argument(
        "--data-preview",
        action="store_true",
        help="eval-only: include read-only fixture file previews in scored prompts",
    )
    return parser


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt auth",
        description="Interactively set up a provider backend (PRD-provider-setup.md).",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    return parser


def build_ground_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt ground",
        description="Inspect or rebuild the persistent system grounding cache (#88).",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    parser.add_argument("--refresh", action="store_true", help="force a snapshot rebuild")
    return parser


def build_prompt_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt prompt",
        description="Print the assembled system + user prompt for a request — no model call. "
        "The prompt surface lives in tinytalk/prompts.py (#102).",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    parser.add_argument("request", nargs="+", help="the request to assemble prompts for")
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt config",
        description="Change a setting in config.toml (edit the file by hand for anything else).",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)"
    )
    sub = parser.add_subparsers(dest="setting", required=True)
    explanation = sub.add_parser("explanation", help="show/hide the '# ...' explanation line")
    explanation.add_argument("value", choices=["on", "off"])
    return parser


def build_history_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt history",
        description="Browse and reuse past commands (dated-JSONL store under XDG_STATE_HOME).",
    )
    parser.add_argument(
        "--porcelain",
        action="store_true",
        help="emit recent (deduped) records as `<danger>\\t<command>` NUL-delimited for the zsh recall widget",
    )
    parser.add_argument(
        "--preview",
        metavar="N",
        type=int,
        help=argparse.SUPPRESS,  # internal: render the record at view index N for the fzf preview
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if len(argv) >= 2 and argv[0] == "eval" and argv[1] == "dashboard":
        from tinytalk.eval.dashboard import main as dashboard_main

        return dashboard_main(argv[2:])
    if len(argv) >= 2 and argv[0] == "eval" and argv[1] == "analyze":
        from tinytalk.eval.analyze import main as analyze_main

        return analyze_main(argv[2:])
    if len(argv) >= 2 and argv[0] == "eval" and argv[1] == "publish":
        from tinytalk.eval.publish import main as publish_main

        return publish_main(argv[2:])
    if argv[:1] == ["eval"]:
        return _eval(build_eval_parser().parse_args(argv[1:]))
    if argv[:1] == ["init"]:
        return _init(argv[1:])
    if argv[:1] == ["auth"]:
        return _auth(build_auth_parser().parse_args(argv[1:]))
    if argv[:1] == ["ground"]:
        return _ground(build_ground_parser().parse_args(argv[1:]))
    if argv[:1] == ["config"]:
        return _config(build_config_parser().parse_args(argv[1:]))
    if argv[:1] == ["prompt"]:
        return _prompt(build_prompt_parser().parse_args(argv[1:]))
    if argv[:1] == ["history"]:
        return _history(build_history_parser().parse_args(argv[1:]))
    args = build_parser().parse_args(argv)
    request_text = " ".join(args.request).strip()
    if not request_text:
        build_parser().print_help()
        return 0
    return _run(args, request_text)


def _init(argv: list[str]) -> int:
    """`tt init zsh` — print the shell integration script for eval/source."""
    if argv in (["--help"], ["-h"]):
        print("usage: tt init zsh")
        return 0
    if argv != ["zsh"]:
        print("usage: tt init zsh", file=sys.stderr)
        return 2
    from importlib.resources import files

    print((files("tinytalk") / "shell" / "tt.zsh").read_text(encoding="utf-8"), end="")
    return 0


def _eval(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tinytalk.config import ConfigError, load_config
    from tinytalk.eval.runner import export, render_leaderboard, render_matrix, run_eval

    if args.report_from and not args.report:
        print("tt: --report-from requires --report PATH", file=sys.stderr)
        return 2
    try:
        if args.report_from:
            from tinytalk.eval.report import load_reports

            reports = load_reports(Path(args.report_from))
        else:
            config = load_config(Path(args.config) if args.config else None)
            backends = args.backends.split(",") if args.backends else sorted(config.backends)
            prompt_ids = args.prompts.split(",") if args.prompts else None
            reports = run_eval(
                config,
                backends,
                prompt_ids=prompt_ids,
                cwd=os.getcwd(),
                data_preview=args.data_preview,
            )
    except (OSError, ConfigError, ValueError) as exc:
        print(f"tt: {exc}", file=sys.stderr)
        return 1
    print(render_leaderboard(reports))
    print()
    print(render_matrix(reports))
    if args.export and not args.report_from:
        export(reports, Path(args.export))
        print(f"\nresults written to {args.export}", file=sys.stderr)
    if args.report:
        Path(args.report).write_text(_render_report(reports), "utf-8")
        print(f"report written to {args.report}", file=sys.stderr)
    return 0


def _render_report(reports) -> str:
    import datetime
    import platform

    from tinytalk.eval.report import RunMeta, render_report

    meta = RunMeta(
        run_date=datetime.date.today().isoformat(),
        machine=f"{platform.system()} {platform.machine()}",
    )
    return render_report(reports, meta)


def _auth(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tinytalk.auth import QuestionaryIO, run_auth_wizard
    from tinytalk.config import ConfigError, default_config_path, load_config

    config_path = Path(args.config) if args.config else default_config_path()
    result = run_auth_wizard(config_path, QuestionaryIO())
    if result is None:
        print("tt auth: cancelled", file=sys.stderr)
        return 1
    try:
        config = load_config(config_path)
    except ConfigError as exc:  # should never happen — surface loudly if it does
        print(f"tt: the written config failed validation: {exc}", file=sys.stderr)
        return 1
    # A returned slot that is absent from the loaded config was removed, not set up.
    action = "saved to" if result in config.backends else "removed from"
    print(f"tt: backend {result!r} {action} {config_path}")
    line = f"default backend: {config.default_backend}"
    if config.escalation_backend:
        line += f"; fallback: {config.escalation_backend}"
    print(line)
    print('Try it: tt "show me disk usage"')
    return 0


def _config(args: argparse.Namespace) -> int:
    """`tt config explanation on|off` — flip a [defaults] setting in config.toml, preserving
    everything else in the file (tomlkit read-modify-write, same pattern as `tt auth`)."""
    from pathlib import Path

    import tomlkit

    from tinytalk.config import ConfigError, default_config_path, load_config

    config_path = Path(args.config) if args.config else default_config_path()
    text = config_path.read_text() if config_path.exists() else ""
    doc = tomlkit.parse(text) if text else tomlkit.document()
    if "defaults" not in doc:
        doc["defaults"] = tomlkit.table()
    doc["defaults"]["explanation"] = args.value == "on"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc))

    try:
        load_config(config_path)
    except ConfigError as exc:  # should never happen — surface loudly if it does
        print(f"tt: the written config failed validation: {exc}", file=sys.stderr)
        return 1
    state = "shown" if args.value == "on" else "hidden"
    print(f"tt: explanation {state} ({config_path})")
    return 0


def _ground(args: argparse.Namespace) -> int:
    import time
    from pathlib import Path

    from tinytalk import groundcache
    from tinytalk.cache import default_cache_dir
    from tinytalk.config import ConfigError, load_config
    from tinytalk.grounding import CURATED_TOOLS, installed_binaries

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"tt: {exc}", file=sys.stderr)
        return 1
    if not config.cache_enabled:
        print("grounding cache disabled ([cache] enabled = false)")
        return 0
    cache_dir = config.cache_dir or default_cache_dir()
    path = os.environ.get("PATH", "")
    snap = None
    if not args.refresh:
        snap = groundcache.load_snapshot(cache_dir, path=path, tt_version=__version__)
    if snap is None:
        started = time.perf_counter()
        snap = groundcache.build_snapshot(
            path, installed_binaries(path), version_candidates=frozenset(CURATED_TOOLS)
        )
        groundcache.save_snapshot(cache_dir, snap, tt_version=__version__)
        status = f"rebuilt in {time.perf_counter() - started:.2f}s"
    else:
        age_min = int((time.time() - snap.created_at) // 60)
        age = f"{age_min}m" if age_min < 120 else f"{age_min // 60}h"
        status = f"fresh (built {age} ago, tt {__version__})"
    curated = sum(1 for name in CURATED_TOOLS if name in snap.binaries)
    print(f"grounding cache: {groundcache.snapshot_path(cache_dir, path)}")
    print(f"status: {status}")
    print(
        f"binaries: {len(snap.binaries)}   curated installed: {curated}   "
        f"versioned: {len(snap.versions)}"
    )
    return 0


def _prompt(args: argparse.Namespace) -> int:
    """`tt prompt` — show exactly what a real request would send, without sending it."""
    from pathlib import Path

    from tinytalk.cache import default_cache_dir
    from tinytalk.config import ConfigError, load_config
    from tinytalk.grounding import SystemGrounding
    from tinytalk.prompts import user_message
    from tinytalk.tiers import TierRequest

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"tt: {exc}", file=sys.stderr)
        return 1
    cache_dir = (config.cache_dir or default_cache_dir()) if config.cache_enabled else None
    grounding = SystemGrounding(cache_dir=cache_dir)
    session_context = os.environ.get("TT_SESSION_CONTEXT", "")
    if session_context:
        from tinytalk.redact import redact

        session_context = redact(session_context)
    request = TierRequest(
        prompt=" ".join(args.request).strip(),
        cwd=os.getcwd(),
        session_context=session_context,
        language=config.language,
    )
    print("=== system ===")
    print(grounding.system_prompt(request))
    print("=== user ===")
    print(user_message(request.prompt, cwd=request.cwd, session_context=request.session_context))
    return 0


def _history(args: argparse.Namespace) -> int:
    """`tt history` — browse and reuse past commands. `--porcelain` feeds the zsh widget one
    `<danger>\\t<command>` record per deduped recent command, NUL-delimited (newest-first). The human viewer is **fzf-first**: an
    interactive picker with a full-record preview pane, whose selection prints the command. When
    fzf is absent (or output is not a terminal) it falls back to a numbered plaintext listing
    (id, time, prompt→command, cost). `--preview N` renders the record at view index N for the
    picker's preview pane. Dedup collapses the VIEW on the exact-normalized command, keeping the
    newest; the store keeps everything (store-all, dedup-the-view)."""
    from tinytalk.history import HistoryStore, dedup

    records = [r for r in dedup(HistoryStore().read_recent(_PORCELAIN_LIMIT)) if r.command.strip()]
    if args.preview is not None:  # fzf preview-pane callback: render the record at this view index
        if 0 <= args.preview < len(records):
            print(_history_preview(records[args.preview]))
        return 0
    if args.porcelain:
        for record in records:
            # The widget gates destructive recalls on the field before the FIRST tab; a
            # record with no classifier verdict over-warns as caution rather than run unguarded.
            danger = record.danger_final or "caution"
            sys.stdout.write(
                f"{danger}\t{record.command}\0"
            )  # NUL-terminated: `read -r -d ''` safe
        return 0
    if not records:
        print("tt: no history yet", file=sys.stderr)  # friendly empty state (spec-C1)
        return 0
    if _use_fzf():
        try:
            command = _fzf_pick(records)
        except OSError:
            pass  # fzf vanished between the which() check and exec — fall back to plaintext
        else:
            if command:  # empty on abort/no-match: print nothing, still a clean exit
                print(command)
            return 0
    for record in records:
        print(_history_line(record))
    return 0


def _use_fzf() -> bool:
    """Whether `tt history` should open the fzf picker: only for an interactive terminal with
    fzf installed. Piped or redirected output (scripts, `| less`) and a missing fzf both fall
    back to the plaintext listing (spec-C2)."""
    import shutil

    return sys.stdout.isatty() and shutil.which("fzf") is not None


def _self_invocation() -> str:
    """The shell-quoted command the fzf preview pane uses to call back into `tt`. When argv[0]
    is a `.py` file (`python -m tinytalk`, direct-script runs) the preview shell can't execute
    it, so re-invoke via the interpreter; otherwise argv[0] is the installed `tt` script or the
    frozen binary, both of which self-invoke as-is (the frozen build has no importable runner,
    so it must NOT go through `-m`)."""
    import shlex

    if sys.argv[0].endswith(".py"):
        return shlex.join([sys.executable, "-m", "tinytalk"])
    return shlex.quote(sys.argv[0])


def _fzf_pick(records) -> str | None:
    """Run the fzf picker over `records`; return the selected command verbatim, or `None` if the
    user aborted. Raises `OSError` if fzf can't be executed so the caller can fall back.

    Each line is `<index>\\t<row>`: fzf shows/searches the row (`--with-nth 2..`) while the hidden
    view-index (field 1) drives the preview command and the post-selection lookup — so the printed
    command is the stored one byte-for-byte, even though the display row collapses whitespace to
    stay one line. Keying on the view index (not the record `id`, which history.py documents as
    NON-unique) keeps selection and preview from cross-mapping to another record's command. The
    preview pane shells back to `tt history --preview <index>`."""
    import subprocess

    by_index = {index: record for index, record in enumerate(records)}
    lines = "".join(
        f"{index}\t{_history_fzf_row(record)}\n" for index, record in enumerate(records)
    )
    prog = _self_invocation()
    proc = subprocess.run(
        [
            "fzf",
            "--delimiter",
            "\t",
            "--with-nth",
            "2..",
            "--prompt",
            "history> ",
            "--preview",
            f"{prog} history --preview {{1}}",
            "--preview-window",
            "down,50%,wrap",
        ],
        input=lines,
        text=True,
        stdout=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None  # aborted, interrupted, or no match
    index_str = proc.stdout.splitlines()[0].split("\t", 1)[0]
    try:
        record = by_index.get(int(index_str))
    except ValueError:
        return None
    return record.command if record is not None else None


def _history_line(record) -> str:
    """One plaintext viewer row — `id`, time, prompt→command, cost. The fzf-less fallback;
    an unparseable timestamp falls back to the raw stored value rather than dropping the row."""
    import datetime

    try:
        when = datetime.datetime.fromisoformat(record.ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        when = record.ts
    return f"{record.id:>4}  {when}  {record.prompt} → {record.command}  ${record.cost_usd:.6f}"


def _history_fzf_row(record) -> str:
    """The visible fzf row (field 2+): time + prompt→command, collapsed to a single line so a
    tab or newline in the data can't spawn phantom fields. Search matches this; the command is
    still recovered verbatim by its view index on selection."""
    import datetime

    try:
        when = datetime.datetime.fromisoformat(record.ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        when = record.ts
    return " ".join(f"{when}  {record.prompt} → {record.command}".split())


def _history_preview(record) -> str:
    """The fzf preview pane — the full record for one command: prompt, command, explanation,
    model, danger, tokens, cost, time (spec-C2)."""
    import datetime

    try:
        when = datetime.datetime.fromisoformat(record.ts).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        when = record.ts
    usage = record.usage or {}
    tokens = (
        f"{usage.get('total_tokens', 0)}  "
        f"(prompt {usage.get('prompt_tokens', 0)}, completion {usage.get('completion_tokens', 0)})"
    )
    model = f"{record.backend} / {record.model}".strip(" /") or "—"
    rows = [
        ("prompt", record.prompt),
        ("command", record.command),
        ("explanation", record.explanation),
        ("model", f"{model}  (tier {record.tier}, {record.outcome})"),
        ("danger", record.danger_final or "—"),
        ("tokens", tokens),
        ("cost", f"${record.cost_usd:.6f}"),
        ("time", f"{when}  ({record.latency_ms} ms)"),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)


def _emit_widget(**pairs: object) -> None:
    import shlex

    print("\n".join(f"{key}={shlex.quote(str(value))}" for key, value in pairs.items()))


def _price_nonzero(price) -> bool:
    """A model priced at any non-zero rate — the `billable` rule's price predicate."""
    return bool(
        price.input_per_mtok
        or price.output_per_mtok
        or price.cached_input_per_mtok
        or price.cache_write_per_mtok
    )


def _usage_dict(usage) -> dict:
    """`Usage` → the persisted token dict; total falls back to prompt+completion (an
    openai-compat quirk: `total=0` with the parts set) so `billable` reads a real count."""
    total = usage.total_tokens or (usage.prompt_tokens + usage.completion_tokens)
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": total,
        "cached_prompt_tokens": usage.cached_prompt_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
    }


def _resolve_model(config, backend: str, backend_cfg) -> str:
    """Model behind a result — `config.backend(name).model` per the pinned rule. A real
    provider names itself (`openai-compat:<model>`), not by the config key, so an unknown
    name falls back to the primary backend's model rather than dropping the whole record."""
    from tinytalk.config import ConfigError

    try:
        return config.backend(backend).model
    except ConfigError:
        return backend_cfg.model


def _enrich_attempts(config, backend_cfg, detail) -> tuple[list[dict], dict[str, float]]:
    """One dict per format-attempt (spec-A2 ledger), enriched with the per-attempt model and
    its own cost — "cost computed per-attempt, then summed" (DECISIONS §Usage fidelity) made
    persistable. Also returns the element-wise sum of the per-attempt cost breakdowns: the
    record's headline cost split, priced per-attempt so it stays **exact under a mixed-price
    escalation** (a free local T1 and a priced cloud T2 are each billed at their own rate,
    not all the accumulated tokens at the winning backend's single rate)."""
    from tinytalk.cost import cost_breakdown

    entries: list[dict] = []
    totals = {"fresh": 0.0, "cached": 0.0, "write": 0.0, "output": 0.0}
    for attempt in detail:
        model = _resolve_model(config, attempt.backend, backend_cfg)
        breakdown = cost_breakdown(attempt.usage, config.price(model))
        for bucket, value in breakdown.items():
            totals[bucket] += value
        entries.append(
            {
                "tier": attempt.tier,
                "backend": attempt.backend,
                "model": model,
                "format_reached": attempt.format_reached.value,
                "usage": _usage_dict(attempt.usage),
                "cost_usd": round(sum(breakdown.values()), 6),
                "latency_ms": attempt.latency_ms,
                "result": attempt.result,
            }
        )
    return entries, {bucket: round(value, 6) for bucket, value in totals.items()}


def _capture(
    args, request_text, latency_ms, *, config, backend_cfg, request, result=None, exc=None
):
    """Build one history record for this outcome and write it via the A1 sink.

    Best-effort: any capture-side error is swallowed so it never changes stdout/stderr or
    the exit code (mirrors cache.py). Called at the three `_run` write sites — the success
    block (`ok`/`cache_hit` by tier), the `NoValidCommand` handler (`no_command`/
    `transport_error` by `exc.kind`), and the generic fault handler (`transport_error`).
    """
    try:
        import platform

        from tinytalk.history import HistoryRecord, HistoryStore
        from tinytalk.provider.base import Usage
        from tinytalk.tiers import NoValidCommand

        if result is not None:
            outcome = "cache_hit" if result.tier == 0 else "ok"
            backend = result.backend
            usage = result.usage
            detail = result.attempts_detail
            suggestion = result.suggestion
        else:
            if isinstance(exc, NoValidCommand):
                outcome = "transport_error" if exc.kind == "transport" else "no_command"
            else:
                outcome = "transport_error"
            backend = getattr(exc, "backend", "") or backend_cfg.name
            usage = getattr(exc, "usage", None) or Usage()
            detail = getattr(exc, "attempts_detail", ())
            suggestion = None

        model = _resolve_model(config, backend, backend_cfg)
        price = config.price(model)
        usage_dict = _usage_dict(usage)
        # Headline cost is priced PER-ATTEMPT then summed (DECISIONS §Usage fidelity: cost is
        # "exact under escalation"), so a mixed-price escalation bills each tier at its own rate
        # rather than all accumulated tokens at the winning backend's rate; `breakdown` is the
        # matching per-rate split. For a single backend this equals pricing the accumulated
        # usage once. The four buckets still sum to cost_usd. (`price`/`model` above stay the
        # winning backend's, for the record's `model` field and the pinned `billable` rule.)
        attempts_detail, breakdown = _enrich_attempts(config, backend_cfg, detail)
        cost_usd = round(sum(breakdown.values()), 6)
        record = HistoryRecord(
            latency_ms=latency_ms,
            cwd=request.cwd if request is not None else os.getcwd(),
            mode="widget" if args.widget else "json" if args.json else "plain",
            backend=backend,
            model=model,
            provider_kind=backend_cfg.kind,
            posture=config.posture,
            os_fingerprint=f"{platform.system()}-{platform.release()}-{platform.machine()}",
            language=request.language if request is not None else config.language,
            prompt_surface_hash=result.prompt_surface_hash if result is not None else "",
            context_chars=len(request.session_context) if request is not None else 0,
            prompt=request_text,
            command=suggestion.command if suggestion is not None else "",
            explanation=suggestion.explanation if suggestion is not None else "",
            danger_model=suggestion.danger.value if suggestion is not None else "",
            danger_final=result.validation.danger if result is not None else "",
            confidence=suggestion.confidence if suggestion is not None else 0.0,
            needs=suggestion.needs if suggestion is not None else (),
            tier=result.tier if result is not None else 0,
            attempts=result.attempts if result is not None else len(detail),
            escalated=any(entry["tier"] == 2 for entry in attempts_detail),
            cache_hit=outcome == "cache_hit",
            outcome=outcome,
            billable=(
                outcome != "cache_hit" and usage_dict["total_tokens"] > 0 and _price_nonzero(price)
            ),
            usage=usage_dict,
            cost_usd=cost_usd,
            cost_breakdown=breakdown,
            attempts_detail=attempts_detail,
            error_kind=(
                None
                if result is not None
                else (exc.kind if isinstance(exc, NoValidCommand) else "transport")
            ),
            problems=(
                ()
                if result is not None
                else tuple(getattr(exc, "problems", ())) or (f"{type(exc).__name__}: {exc}",)
            ),
        )
        HistoryStore().append(record)
    except Exception:
        return  # history is best-effort; a capture fault never disturbs the request


def _run(args: argparse.Namespace, request_text: str) -> int:
    import asyncio
    import time
    from pathlib import Path

    from tinytalk.cache import ExactCache, default_cache_dir
    from tinytalk.config import ConfigError, load_config
    from tinytalk.grounding import SystemGrounding
    from tinytalk.provider.factory import make_provider
    from tinytalk.tiers import NoValidCommand, TierController, TierRequest
    from tinytalk.validate import CommandValidator

    backend_name = args.backend or ""
    # Bound for the capture sites even if we fault before assembling them. A non-ConfigError
    # config-load failure (e.g. `--config <dir>` → IsADirectoryError) hits the generic handler
    # before `config`/`backend_cfg` are set, so they must exist for its guard to read.
    config = None
    backend_cfg = None
    request = None
    start = time.perf_counter()
    try:
        config = load_config(Path(args.config) if args.config else None)
        backend_cfg = config.backend(args.backend)
        backend_name = backend_cfg.name
        provider = make_provider(backend_cfg)
        escalation = None
        escalation_name = ""
        if config.escalation_backend and config.escalation_backend != backend_cfg.name:
            escalation_cfg = config.backend(config.escalation_backend)
            escalation_name = escalation_cfg.name
            escalation = lambda: make_provider(escalation_cfg)  # noqa: E731 — deferred, lazy import
        cache_dir = (config.cache_dir or default_cache_dir()) if config.cache_enabled else None
        grounding = SystemGrounding(cache_dir=cache_dir)
        controller = TierController(
            provider,
            escalation=escalation,
            escalation_name=escalation_name,
            cache=ExactCache(config.cache_dir) if config.cache_enabled else None,
            grounding=grounding,
            validator=CommandValidator(grounding, cwd=os.getcwd()),
        )
        session_context = os.environ.get("TT_SESSION_CONTEXT", "")
        if session_context:
            from tinytalk.redact import redact

            session_context = redact(session_context)
        request = TierRequest(
            prompt=request_text,
            cwd=os.getcwd(),
            session_context=session_context,
            language=config.language,
        )
        result = asyncio.run(controller.suggest(request))
    except ConfigError as exc:
        print(f"tt: {exc}", file=sys.stderr)
        return 1
    except NoValidCommand as exc:
        backend = exc.backend or backend_cfg.name
        if exc.kind == "transport":
            message = f"backend {backend!r} failed: {exc}"
            print(f"tt: {message}", file=sys.stderr)
        else:
            message = f"no valid command: {exc}"
            print(f"tt: {message}", file=sys.stderr)
        if args.widget:
            _emit_widget(tt_error_kind=exc.kind, tt_error_message=message, tt_backend=backend)
        elif args.json and exc.last is not None:
            print(json.dumps({"ok": False, "problems": list(exc.problems)}))
        _capture(
            args,
            request_text,
            round((time.perf_counter() - start) * 1000),
            config=config,
            backend_cfg=backend_cfg,
            request=request,
            exc=exc,
        )
        return 1
    except Exception as exc:  # provider/transport faults — keep the shell usable
        subject = f"backend {backend_name!r}" if backend_name else "backend"
        message = f"{subject} failed: {type(exc).__name__}: {exc}"
        print(f"tt: {message}", file=sys.stderr)
        if args.widget:
            _emit_widget(
                tt_error_kind="transport", tt_error_message=message, tt_backend=backend_name
            )
        # A config-load failure faults before config/backend_cfg exist; like ConfigError it
        # writes no record (the backend was never known) instead of crashing on unbound locals.
        if config is not None and backend_cfg is not None:
            _capture(
                args,
                request_text,
                round((time.perf_counter() - start) * 1000),
                config=config,
                backend_cfg=backend_cfg,
                request=request,
                exc=exc,
            )
        return 1

    if args.widget:
        _emit_widget(
            tt_command=result.suggestion.command,
            tt_danger=result.validation.danger,
            tt_explanation=result.suggestion.explanation if config.show_explanation else "",
        )
    elif args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    **result.suggestion.to_dict(),
                    "danger": result.validation.danger,
                    "tier": result.tier,
                    "backend": result.backend,
                }
            )
        )
    else:
        print(result.suggestion.command)
        prefix = f"# {result.suggestion.explanation}  " if config.show_explanation else "# "
        print(f"{prefix}[danger: {result.validation.danger}]", file=sys.stderr)
    _capture(
        args,
        request_text,
        round((time.perf_counter() - start) * 1000),
        config=config,
        backend_cfg=backend_cfg,
        request=request,
        result=result,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
