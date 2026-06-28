"""Command-line entry point for CLITE.

This is the re-platform skeleton — the engine that turns English into a
validated command is built per the issues. CLITE never auto-runs the commands
it generates; it always hands control back to the user.
"""

from __future__ import annotations

import argparse
import sys

from clite import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clite",
        description="Turn plain English at the shell into a real, validated command.",
    )
    parser.add_argument("--version", action="version", version=f"clite {__version__}")
    parser.add_argument(
        "request",
        nargs="*",
        help="what you want to do, in plain English",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    request = " ".join(args.request).strip()
    if not request:
        build_parser().print_help()
        return 0
    print(
        f"clite: not yet implemented (re-platform in progress) — received {request!r}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
