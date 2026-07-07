"""Marker-guarded rc-file block management (#129, interactive-installer epic #127).

Mirrors `scripts/install.sh`'s zsh-integration block byte-for-byte, and
`scripts/uninstall.sh`'s `strip_blocks` awk state machine closely enough that
either side can write or strip a block the other produced. That compatibility
is what lets `tt setup` (S3) take over rc-writing without touching either
shell script or breaking existing installs / the install-CI idempotency checks.
"""

from __future__ import annotations

from pathlib import Path

ZSH_MARKER = "# tt zsh integration (added by install.sh)"


def has_block(path: Path, marker: str) -> bool:
    """True if `path` has a line that exactly equals `marker`."""
    if not path.exists():
        return False
    return marker in path.read_text().splitlines()


def ensure_block(path: Path, marker: str, block: str) -> bool:
    """Append `marker` + `block`, once. Returns True if it wrote, False if
    `marker` was already present. Shape matches install.sh's appends: a blank
    separator line, the marker, then the block."""
    if has_block(path, marker):
        return False
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"  # don't glue the block onto the last line
    path.write_text(f"{existing}\n{marker}\n{block}")
    return True


def remove_block(path: Path, marker: str) -> bool:
    """Strip the marker-delimited block. Matches strip_blocks' `held`
    bookkeeping (scripts/uninstall.sh): the blank separator line install.sh
    puts before a block is dropped with it, and blank-line collapsing around
    the cut is deduped the same way. A block ends at the next blank line or
    EOF — true for every block this module writes."""
    if not has_block(path, marker):
        return False
    text = path.read_text()
    trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if trailing_newline:
        lines.pop()  # drop the "" left by split() after the final \n

    out: list[str] = []
    held = False
    skip = False
    for line in lines:
        if skip:
            if line != "":
                continue
            skip = False  # this blank line ends the block; fall through
        if line == marker:
            held = False  # the blank line just before the marker is dropped
            skip = True
            continue
        if line == "":
            if held:
                out.append("")
            held = True
            continue
        if held:
            out.append("")
            held = False
        out.append(line)
    if held:
        out.append("")

    new_text = "\n".join(out)
    if out and trailing_newline:
        new_text += "\n"
    path.write_text(new_text)
    return True


def zsh_integration_block() -> tuple[str, str]:
    """The exact (marker, block) pair scripts/install.sh writes for the `?`
    widget (its zsh-wiring step). No launcher path is baked into the block —
    it re-resolves `tt` at each new shell via `command -v tt`, same as
    install.sh."""
    block = (
        '_tt_cache="${XDG_CACHE_HOME:-$HOME/.cache}/tinytalk/init.zsh"\n'
        "_tt_bin=$(command -v tt)\n"
        "if [[ -n $_tt_bin && ( ! -s $_tt_cache || $_tt_bin -nt $_tt_cache ) ]]; then\n"
        '  command mkdir -p "${_tt_cache:h}" && "$_tt_bin" init zsh >| $_tt_cache 2>/dev/null\n'
        "fi\n"
        "[[ -s $_tt_cache ]] && source $_tt_cache\n"
        "unset _tt_cache _tt_bin\n"
    )
    return ZSH_MARKER, block
