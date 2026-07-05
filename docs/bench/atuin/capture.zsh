#!/usr/bin/env zsh
# Capture Atuin AI's generated command for each of TinyTalk's 14 behavioral
# fixtures, via `atuin ai inline` — the exact CLI the `?` widget wraps.
#
# `atuin ai inline --hook "<request>"` seeds the request from its argument,
# generates, renders a small TUI, and on Tab (insert) prints
# `__atuin_ai_insert__:<command>` to stderr and exits. We drive it in a tmux pane
# (it needs a PTY + one keypress), poll stdout for the suggestion, press Tab, and
# parse that token. No `?` binding (so no TinyTalk `?` conflict), no typing the
# request, no buffer scraping — and each prompt is its own process, so state can't
# bleed between prompts. Nothing is ever executed.
#
# Result: {target: command} JSON for `python -m tinytalk.eval.atuin report`.
#
# Usage:
#   docs/bench/atuin/capture.zsh -o atuin-commands.json          # live Atuin
#   docs/bench/atuin/capture.zsh --stub docs/bench/atuin/responses -o out.json
#
#   --stub DIR   offline self-test: put a fake `atuin` (docs/bench/atuin/stubbin)
#                on PATH serving canned commands from DIR (files 1,2,… in fixture
#                order) — verifies the harness with no Hub login.
#   -o FILE      output JSON (default atuin-commands.json)
#
# LIVE PREREQUISITE: be logged in to Atuin Hub (any successful `?` once is enough).
# Knobs: ATUIN_GEN_WAIT (25s max wait per prompt) · COLS (120) · ROWS (30)
set -u
HERE=${0:A:h}
REPO=${HERE:h:h:h}

OUT=atuin-commands.json
STUB=""
while (( $# )); do
  case "$1" in
    -o) OUT=$2; shift 2 ;;
    --stub) STUB=$2; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) print "unknown arg: $1"; exit 2 ;;
  esac
done

COLS=${COLS:-120} ROWS=${ROWS:-30}
GEN_WAIT=${ATUIN_GEN_WAIT:-25}

WORK=$(mktemp -d)
HOOK=$WORK/hook            # atuin's stderr — carries __atuin_ai_insert__:<command>
PF=$WORK/prompt           # current prompt (passed via $(cat) to dodge shell quoting)
CAPDIR=$WORK/caps; mkdir -p "$CAPDIR"
t() { tmux -L tt-atuin "$@" }
cleanup() { t kill-server 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT INT

prompts=$(cd "$REPO" && uv run --quiet python -m tinytalk.eval.atuin prompts 2>/dev/null) \
  || { print "ABORT: could not load prompts (python -m tinytalk.eval.atuin prompts)"; exit 1; }
[[ -n "$prompts" ]] || { print "ABORT: empty prompt list"; exit 1; }

t kill-server 2>/dev/null

# One FRESH tmux session per prompt: a slow `atuin ai inline` can't leave a stale
# overlay or a lingering process for the next prompt to type into, and a fresh
# screen means the "Suggested command" poll can't match a previous prompt's text.
# (These were the misalignment/blank bugs of a single persistent pane.)
capture_one() {
  local target=$1 text=$2
  : > "$HOOK"
  print -rn -- "$text" > "$PF"
  t kill-session -t cap 2>/dev/null
  t new-session -d -s cap -x "$COLS" -y "$ROWS" "zsh -i -f"
  sleep 1.2
  if [[ -n "$STUB" ]]; then
    t send-keys -t cap -l "path=(${HERE}/stubbin \$path); rehash; export ATUIN_STUB_RESPONSES=${STUB:A} ATUIN_STUB_STATE=$WORK/n"
    t send-keys -t cap Enter
    sleep 0.3
  fi
  # Seed the request via arg (no typing); TUI -> pane (for the poll), token -> $HOOK.
  t send-keys -t cap -l "atuin ai inline --hook \"\$(cat $PF)\" 2>$HOOK"
  t send-keys -t cap Enter
  local i
  for (( i = 1; i <= GEN_WAIT; i++ )); do
    sleep 1
    t capture-pane -t cap -p | grep -q 'Suggested command' && break
  done
  sleep 0.4
  t send-keys -t cap Tab            # insert -> emits the token to stderr, then exits
  sleep 1.2
  t kill-session -t cap 2>/dev/null # guarantee the process is gone before the next prompt
  # Extract the command from the token to EOF (commands can be multi-line, e.g.
  # a python3 -c "…" heredoc), stripping terminal escapes.
  local cmd
  cmd=$(tr -d '\0' < "$HOOK" \
    | sed $'s/\x1b\\[[0-9;?]*[a-zA-Z]//g; s/\x1b[>=]//g; s/\x1b[()][AB0]//g' \
    | awk '/__atuin_ai_insert__:/{f=1} f' \
    | sed '1s/.*__atuin_ai_insert__://')
  cmd=${cmd%$'\r'}
  if [[ -n "${cmd//[[:space:]]/}" ]]; then
    print -rn -- "$cmd" > "$CAPDIR/$target"
    print "  ✓ $target: $cmd"
  else
    print "  — $target: (no command captured)"
  fi
}

print "Capturing ${STUB:+(stub) }Atuin AI commands via 'atuin ai inline'…"
print -r -- "$prompts" | while IFS=$'\t' read -r target text; do
  [[ -n "$target" ]] || continue
  capture_one "$target" "$text"
done

# Assemble {target: command} JSON from the per-target files (no shell quoting).
python3 - "$CAPDIR" "$OUT" <<'PY'
import json, os, sys
capdir, out = sys.argv[1], sys.argv[2]
data = {name: open(os.path.join(capdir, name)).read() for name in sorted(os.listdir(capdir))}
with open(out, "w") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(f"\nWrote {len(data)} commands to {out}")
PY

print "Next: python -m tinytalk.eval.atuin report --captured $OUT --results docs/bench/<date>/results.json"
