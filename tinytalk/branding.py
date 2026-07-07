"""Baked ASCII-art TinyTalk banner (#127 S1).

The art below is generated at dev time by `scripts/gen_banner.py` (pyfiglet stays a
dev-only tool — see that script) and baked in as a plain tuple of lines so the shipped
package carries no extra runtime dependency. `banner()` renders it with an optional
ANSI truecolor gradient and falls back to a single plain tagline line when the art
doesn't fit the terminal or color isn't appropriate.
"""

from __future__ import annotations

import os
import shutil
import sys

from tinytalk.i18n import N_, _

# BEGIN GENERATED ART (scripts/gen_banner.py, font=smslant)
_ART_LINES: tuple[str, ...] = (
    " _______          ______     ____  ",
    "/_  __(_)__  __ _/_  __/__ _/ / /__",
    " / / / / _ \\/ // // / / _ `/ /  '_/",
    "/_/ /_/_//_/\\_, //_/  \\_,_/_/_/\\_\\ ",
    "           /___/                   ",
)
# END GENERATED ART

_TAGLINE = N_("TinyTalk — plain English at the shell")

# Truecolor gradient endpoints (cyan -> violet), one color per line of the art.
_GRADIENT_START = (0x00, 0xAF, 0xFF)
_GRADIENT_END = (0xAF, 0x5F, 0xFF)


def _auto_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def _gradient(lines: tuple[str, ...]) -> str:
    """Color `lines` with a per-line truecolor gradient from start to end."""
    steps = max(len(lines) - 1, 1)
    colored = []
    for i, line in enumerate(lines):
        r, g, b = (
            round(start + (end - start) * i / steps)
            for start, end in zip(_GRADIENT_START, _GRADIENT_END)
        )
        colored.append(f"\x1b[38;2;{r};{g};{b}m{line}\x1b[0m")
    return "\n".join(colored)


def banner(width: int | None = None, color: bool | None = None) -> str:
    """The TinyTalk banner: baked ASCII art, gradient-colored when appropriate, or a
    single plain tagline line when the art doesn't fit the terminal or color isn't wanted."""
    if width is None:
        width = shutil.get_terminal_size().columns
    if color is None:
        color = _auto_color()

    art_width = max((len(line) for line in _ART_LINES), default=0)
    if not _ART_LINES or art_width > width:
        return _(_TAGLINE)

    return _gradient(_ART_LINES) if color else "\n".join(_ART_LINES)
