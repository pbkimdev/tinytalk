"""Command-line entry point for CLITE.

`clite "<request>"` runs config → tier controller → validated suggestion. The
command goes to stdout (script-friendly; the zsh widget reads it), explanation
and danger to stderr. CLITE never auto-runs the commands it generates; it
always hands control back to the user.

Heavy imports happen after argument parsing so `--version`/`--help` stay fast
(PRD §15 cold-start budget).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from clite import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clite",
        description="Turn plain English at the shell into a real, validated command.",
    )
    parser.add_argument("--version", action="version", version=f"clite {__version__}")
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/clite)")
    parser.add_argument(
        "--backend", metavar="NAME", help="backend from config (default: defaults.backend)"
    )
    parser.add_argument("--json", action="store_true", help="emit the full suggestion as JSON")
    parser.add_argument(
        "--widget",
        action="store_true",
        help="emit shell-evalable clite_* assignments (used by the zsh widget)",
    )
    parser.add_argument(
        "request",
        nargs="*",
        help="what you want to do, in plain English",
    )
    return parser


def build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clite eval",
        description="Benchmark configured backends over the built-in prompt suite.",
    )
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/clite)")
    parser.add_argument("--backends", metavar="A,B", help="backends to score (default: all)")
    parser.add_argument("--prompts", metavar="ID,ID", help="run a subset of the suite")
    parser.add_argument("--export", metavar="PATH", help="write results to a .json or .csv file")
    return parser


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clite auth",
        description="Interactively set up a provider backend (PRD-provider-setup.md).",
    )
    parser.add_argument("--config", metavar="PATH", help="config file (default: ~/.config/clite)")
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
    """`clite init zsh` — print the shell integration script for eval/source."""
    if argv != ["zsh"]:
        print("usage: clite init zsh", file=sys.stderr)
        return 2
    from importlib.resources import files

    print((files("clite") / "shell" / "clite.zsh").read_text(encoding="utf-8"), end="")
    return 0


def _eval(args: argparse.Namespace) -> int:
    from pathlib import Path

    from clite.config import ConfigError, load_config
    from clite.eval.runner import export, render_leaderboard, render_matrix, run_eval

    try:
        config = load_config(Path(args.config) if args.config else None)
        backends = args.backends.split(",") if args.backends else sorted(config.backends)
        prompt_ids = args.prompts.split(",") if args.prompts else None
        reports = run_eval(config, backends, prompt_ids=prompt_ids, cwd=os.getcwd())
    except (ConfigError, ValueError) as exc:
        print(f"clite: {exc}", file=sys.stderr)
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

    from clite.auth import QuestionaryIO, run_auth_wizard
    from clite.config import ConfigError, default_config_path, load_config

    config_path = Path(args.config) if args.config else default_config_path()
    result = run_auth_wizard(config_path, QuestionaryIO())
    if result is None:
        print("clite auth: cancelled", file=sys.stderr)
        return 1
    print(f"clite: backend {result!r} saved to {config_path}")
    try:
        config = load_config(config_path)
    except ConfigError as exc:  # should never happen — surface loudly if it does
        print(f"clite: the written config failed validation: {exc}", file=sys.stderr)
        return 1
    line = f"default backend: {config.default_backend}"
    if config.escalation_backend:
        line += f"; fallback: {config.escalation_backend}"
    print(line)
    print('Try it: clite "show me disk usage"')
    return 0


def _run(args: argparse.Namespace, request_text: str) -> int:
    import asyncio
    from pathlib import Path

    from clite.cache import ExactCache
    from clite.config import ConfigError, load_config
    from clite.grounding import SystemGrounding
    from clite.provider.factory import make_provider
    from clite.tiers import NoValidCommand, TierController, TierRequest
    from clite.validate import CommandValidator

    try:
        config = load_config(Path(args.config) if args.config else None)
        backend_cfg = config.backend(args.backend)
        provider = make_provider(backend_cfg)
        escalation = None
        if config.escalation_backend and config.escalation_backend != backend_cfg.name:
            escalation_cfg = config.backend(config.escalation_backend)
            escalation = lambda: make_provider(escalation_cfg)  # noqa: E731 — deferred, lazy import
        grounding = SystemGrounding()
        controller = TierController(
            provider,
            escalation=escalation,
            cache=ExactCache(config.cache_dir) if config.cache_enabled else None,
            grounding=grounding,
            validator=CommandValidator(grounding, cwd=os.getcwd()),
        )
        session_context = os.environ.get("CLITE_SESSION_CONTEXT", "")
        if session_context:
            from clite.redact import redact

            session_context = redact(session_context)
        request = TierRequest(prompt=request_text, cwd=os.getcwd(), session_context=session_context)
        result = asyncio.run(controller.suggest(request))
    except ConfigError as exc:
        print(f"clite: {exc}", file=sys.stderr)
        if args.widget:
            _emit_widget_error(
                "config",
                "clite: configuration error; run `clite` on the CLI for details",
            )
        return 1
    except NoValidCommand as exc:
        print(f"clite: no valid command: {exc}", file=sys.stderr)
        if args.widget and exc.kind == "transport":
            backend_name = (
                backend_cfg.name if "backend_cfg" in locals() else args.backend or "default"
            )
            _emit_widget_error(
                "transport",
                _backend_fault_message(backend_name, exc.problems),
            )
        if args.json and exc.last is not None:
            print(json.dumps({"ok": False, "problems": list(exc.problems)}))
        return 1
    except Exception as exc:  # provider/transport faults — keep the shell usable
        print(f"clite: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.widget:
        print(
            "\n".join(
                (
                    f"clite_command={shlex.quote(result.suggestion.command)}",
                    f"clite_danger={shlex.quote(result.validation.danger)}",
                    f"clite_explanation={shlex.quote(result.suggestion.explanation)}",
                )
            )
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


def _emit_widget_error(kind: str, message: str) -> None:
    print(
        "\n".join(
            (
                f"clite_error={shlex.quote(kind)}",
                f"clite_message={shlex.quote(_one_line(message))}",
            )
        )
    )


def _backend_fault_message(backend: str, problems: tuple[str, ...]) -> str:
    detail = _one_line(problems[-1] if problems else "provider failed")
    return f"clite: backend {backend!r} failed: {detail}; check the server or defaults.backend"


def _one_line(value: str) -> str:
    return " ".join(value.split())


if __name__ == "__main__":
    raise SystemExit(main())
