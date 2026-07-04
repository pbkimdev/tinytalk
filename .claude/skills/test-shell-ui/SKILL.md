---
name: test-shell-ui
description: Drive the TinyTalk zsh widget in a real terminal (tmux) with a stubbed `tt` binary to verify interactive UI behavior or reproduce display bugs. Use when changing tinytalk/shell/tt.zsh, reproducing prompt/redraw glitches or line collisions, or proving a widget fix end-to-end.
---

# Test the shell UI (tmux + stubbed tt)

ZLE display behavior — the AI badge, buffer replacement, `zle -M` message placement,
scrolling — only exists in a real terminal. `zsh -n` and the pytest suite cannot see a
redraw that overdraws the previous line. This skill drives the widget in a tmux pane,
with a fake `tt` on PATH so runs are fast, deterministic, and offline.

## Quick start

```sh
.claude/skills/test-shell-ui/scripts/drive.zsh        # user's real zsh config (starship etc.)
.claude/skills/test-shell-ui/scripts/drive.zsh -f     # clean `zsh -f`, no user config
.claude/skills/test-shell-ui/scripts/drive.zsh path/to/experimental-widget.zsh
```

`drive.zsh` runs the canonical scenario (two AI requests back-to-back with the prompt on
the bottom row), prints screen captures per stage, and exits PASS/FAIL. Run it under both
the real config and `-f`: prompt frameworks and syntax highlighters change redraw behavior.

```sh
.claude/skills/test-shell-ui/scripts/recall.zsh       # prompt-mode ↑/↓ recall (#D1)
.claude/skills/test-shell-ui/scripts/recall.zsh -f    # clean `zsh -f`
```

`recall.zsh` proves the ↑/↓ recall widget: inside AI mode the arrows walk the deduped
commands from `tt history --porcelain` (the stub answers it — see below) into BUFFER, and
leaving AI mode restores the default arrow bindings. Run it under the real config too —
that catches restoration against a real third-party binding (e.g. atuin's `atuin-up-search`).

To test other flows (destructive commands, backend failure), point `TT_STUB_RESPONSES`
at a directory of files `1`, `2`, … — each is printed verbatim as that call's
`tt --widget` output; a missing file makes the stub exit 1 (the widget's error path).
Copy the scenario section of `drive.zsh` and adapt the keystrokes/checks.

## Files

- `scripts/drive.zsh` — tmux driver: spawns the shell, stubs PATH, sends keystrokes,
  captures screens, asserts nothing was overdrawn.
- `scripts/recall.zsh` — tmux driver for the ↑/↓ recall widget (#D1): asserts the arrows
  walk the stubbed history into BUFFER and that leaving AI mode restores the default arrows.
- `scripts/tt-stub` — fake `tt`: emits canned `tt_command=…` / `tt_danger=…` /
  `tt_explanation=…` after `TT_STUB_DELAY` seconds (default 1) to simulate backend latency,
  and answers `history --porcelain` with a canned NUL-delimited newest-first command list.

## The strategy (reuse these rules when writing new scenarios)

1. **Real terminal, real config.** Use tmux (`-L tt-ui` private socket; `kill-server`
   when done) so user sessions are untouched. Verify against the user's actual `~/.zshrc`
   as well as `zsh -f` — bugs can appear in either.
2. **Stub the binary inside the pane.** `path=(<stubdir> $path)` must be typed *in* the
   pane after the rc files ran; PATH set via the environment gets clobbered by `.zshrc`
   and the real binary answers instead. Always confirm with `which tt`.
3. **Put the prompt on the bottom row** (`seq 8` first). zle scroll/anchor bugs only
   trigger when a growing message area forces the screen to scroll.
4. **Never type ahead.** Wait out the stub delay before the next keystroke; queued input
   gets kernel-echoed as stray lines and corrupts the scenario.
5. **Assert what is *still there*, not what looks right.** A bad redraw silently eats
   earlier lines (`8` became `8   echo "Hello…`). Check that every previously printed
   line survives in the final capture (`capture-pane -S -50` includes scrollback).
6. **Capture mid-flight states too** (e.g. during the thinking indicator), not just the
   end state.
7. **For colors/attributes**, capture escape sequences with `tmux capture-pane -e` and
   grep the raw SGR bytes (e.g. `[36m` for the cyan badge) — plain captures strip them.

## Known failure mode this catches

A mid-widget `zle -M` message that forces a scroll leaves zle's redraw anchor stale by
one row; the next buffer replacement then overdraws the previous line at prompt-width
offset. Fix pattern: keep progress indicators inside the edit region (`POSTDISPLAY` +
`zle -R`) and call `zle -M` only at the very end of the widget.
