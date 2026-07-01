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
        "request",
        nargs="*",
        help="what you want to do, in plain English",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    request_text = " ".join(args.request).strip()
    if not request_text:
        build_parser().print_help()
        return 0
    return _run(args, request_text)


def _run(args: argparse.Namespace, request_text: str) -> int:
    import asyncio
    from pathlib import Path

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
            grounding=grounding,
            validator=CommandValidator(grounding, cwd=os.getcwd()),
        )
        request = TierRequest(prompt=request_text, cwd=os.getcwd())
        result = asyncio.run(controller.suggest(request))
    except ConfigError as exc:
        print(f"clite: {exc}", file=sys.stderr)
        return 1
    except NoValidCommand as exc:
        print(f"clite: no valid command: {exc}", file=sys.stderr)
        if args.json and exc.last is not None:
            print(json.dumps({"ok": False, "problems": list(exc.problems)}))
        return 1
    except Exception as exc:  # provider/transport faults — keep the shell usable
        print(f"clite: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
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
