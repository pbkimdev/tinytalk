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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt",
        description="Turn plain English at the shell into a real, validated command.",
        epilog=(
            "commands:\n"
            "  auth        interactively set up a provider backend\n"
            "  eval        benchmark configured backends over the built-in prompt suite\n"
            "  init zsh    print the zsh integration script (eval \"$(tt init zsh)\")\n"
            "\n"
            "run `tt <command> --help` for command options"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"tt {__version__}")
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)")
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
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)")
    parser.add_argument("--backends", metavar="A,B", help="backends to score (default: all)")
    parser.add_argument("--prompts", metavar="ID,ID", help="run a subset of the suite")
    parser.add_argument("--export", metavar="PATH", help="write results to a .json or .csv file")
    return parser


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt auth",
        description="Interactively set up a provider backend (PRD-provider-setup.md).",
    )
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/tinytalk)")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv[:1] == ["eval"]:
        return _eval(build_eval_parser().parse_args(argv[1:]))
    if argv[:1] == ["init"]:
        return _init(argv[1:])
    if argv[:1] == ["auth"]:
        return _auth(build_auth_parser().parse_args(argv[1:]))
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

    try:
        config = load_config(Path(args.config) if args.config else None)
        backends = args.backends.split(",") if args.backends else sorted(config.backends)
        prompt_ids = args.prompts.split(",") if args.prompts else None
        reports = run_eval(config, backends, prompt_ids=prompt_ids, cwd=os.getcwd())
    except (ConfigError, ValueError) as exc:
        print(f"tt: {exc}", file=sys.stderr)
        return 1
    print(render_leaderboard(reports))
    print()
    print(render_matrix(reports))
    if args.export:
        export(reports, Path(args.export))
        print(f"\nresults written to {args.export}", file=sys.stderr)
    return 0


def _auth(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tinytalk.auth import QuestionaryIO, run_auth_wizard
    from tinytalk.config import ConfigError, default_config_path, load_config

    config_path = Path(args.config) if args.config else default_config_path()
    result = run_auth_wizard(config_path, QuestionaryIO())
    if result is None:
        print("tt auth: cancelled", file=sys.stderr)
        return 1
    print(f"tt: backend {result!r} saved to {config_path}")
    try:
        config = load_config(config_path)
    except ConfigError as exc:  # should never happen — surface loudly if it does
        print(f"tt: the written config failed validation: {exc}", file=sys.stderr)
        return 1
    line = f"default backend: {config.default_backend}"
    if config.escalation_backend:
        line += f"; fallback: {config.escalation_backend}"
    print(line)
    print('Try it: tt "show me disk usage"')
    return 0


def _emit_widget(**pairs: object) -> None:
    import shlex

    print("\n".join(f"{key}={shlex.quote(str(value))}" for key, value in pairs.items()))


def _run(args: argparse.Namespace, request_text: str) -> int:
    import asyncio
    from pathlib import Path

    from tinytalk.cache import ExactCache
    from tinytalk.config import ConfigError, load_config
    from tinytalk.grounding import SystemGrounding
    from tinytalk.provider.factory import make_provider
    from tinytalk.tiers import NoValidCommand, TierController, TierRequest
    from tinytalk.validate import CommandValidator

    backend_name = args.backend or ""
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
        grounding = SystemGrounding()
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
        request = TierRequest(prompt=request_text, cwd=os.getcwd(), session_context=session_context)
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
        return 1
    except Exception as exc:  # provider/transport faults — keep the shell usable
        subject = f"backend {backend_name!r}" if backend_name else "backend"
        message = f"{subject} failed: {type(exc).__name__}: {exc}"
        print(f"tt: {message}", file=sys.stderr)
        if args.widget:
            _emit_widget(tt_error_kind="transport", tt_error_message=message, tt_backend=backend_name)
        return 1

    if args.widget:
        _emit_widget(
            tt_command=result.suggestion.command,
            tt_danger=result.validation.danger,
            tt_explanation=result.suggestion.explanation,
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
        print(
            f"# {result.suggestion.explanation}  [danger: {result.validation.danger}]",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
