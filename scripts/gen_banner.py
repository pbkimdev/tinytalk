#!/usr/bin/env python3
"""Dev-only generator for the baked ASCII banner in tinytalk/branding.py (#128).

pyfiglet stays out of the runtime deps — this script is invoked as needed with:

    uv run --with pyfiglet python scripts/gen_banner.py

Renders "TinyTalk" in the `smslant` figlet font (also tried `small`; `smslant` reads
more clearly as "TinyTalk" and both fit well under 80 columns) and rewrites the baked
art between the BEGIN/END markers in tinytalk/branding.py, leaving the rest of that
file untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pyfiglet

FONT = "smslant"
BEGIN = f"# BEGIN GENERATED ART (scripts/gen_banner.py, font={FONT})"
END = "# END GENERATED ART"
BRANDING = Path(__file__).parent.parent / "tinytalk" / "branding.py"


def main() -> None:
    lines = pyfiglet.figlet_format("TinyTalk", font=FONT).rstrip("\n").split("\n")
    body = "\n".join(f"    {line!r}," for line in lines)
    block = f"{BEGIN}\n_ART_LINES: tuple[str, ...] = (\n{body}\n)\n{END}"

    text = BRANDING.read_text()
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    BRANDING.write_text(text[:start] + block + text[stop:])
    subprocess.run(["uv", "run", "ruff", "format", str(BRANDING)], check=True)


if __name__ == "__main__":
    main()
