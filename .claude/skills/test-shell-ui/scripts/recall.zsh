#!/usr/bin/env zsh
# Drive the TinyTalk prompt-mode ↑/↓ recall widget (#D1) in a real terminal (tmux) with a
# stubbed `tt`, and assert both halves of spec-D1's done_when:
#   * inside AI mode, ↑/↓ walk the deduped past commands from `tt history --porcelain`
#     into BUFFER (the Atuin model), and ↓ past the newest restores the stashed prompt;
#   * leaving AI mode restores the default arrow bindings exactly.
#
# Usage: recall.zsh [-f] [widget-file]
#   -f           clean shell (zsh -f), instead of the user's real interactive config
#   widget-file  defaults to tinytalk/shell/tt.zsh at the repo root
# Env: COLS (default 90), ROWS (14). The stub answers `history --porcelain` with a canned
#      newest-first list (see tt-stub): git status --short / ls -la /var/log / grep … .
set -u
HERE=${0:A:h}
REPO=${HERE:h:h:h:h}

clean=0
[[ "${1:-}" == "-f" ]] && { clean=1; shift; }
WIDGET=${1:-$REPO/tinytalk/shell/tt.zsh}
COLS=${COLS:-90} ROWS=${ROWS:-14}

BIN=$(mktemp -d)
ln -s $HERE/tt-stub $BIN/tt

t() { tmux -L tt-ui "$@" }
cleanup() { t kill-server 2>/dev/null; rm -rf $BIN; }
trap cleanup EXIT INT

t kill-server 2>/dev/null
shell="zsh -i"; (( clean )) && shell="zsh -f -i"
t new-session -d -s ui -x $COLS -y $ROWS "$shell"
sleep 3

# PATH must be prepended INSIDE the pane (the zshrc resets it) so our stub answers.
t send-keys -t ui -l "path=($BIN \$path); source $WIDGET; which tt"
t send-keys -t ui Enter
sleep 1
t capture-pane -t ui -p | grep -q "${BIN:t}" || { print "ABORT: stub is not the tt on PATH"; exit 1; }

# Snapshot the default ↑ binding BEFORE AI mode; the widget must put this back on exit.
t send-keys -t ui -l "bindkey '^[[A' >| $BIN/before"; t send-keys -t ui Enter
sleep 0.5
before=$(<$BIN/before)
print "=== default up-arrow binding (before AI mode) ==="
print -r -- "$before"

# Enter AI mode (empty line -> `?`): the badge appears and ↑/↓ are taken over for recall.
t send-keys -t ui -l '?'; sleep 0.5
t send-keys -t ui Up;   sleep 0.6; up1=$(t capture-pane -t ui -p)   # newest command
t send-keys -t ui Up;   sleep 0.6; up2=$(t capture-pane -t ui -p)   # walk older
t send-keys -t ui Down; sleep 0.6; dn1=$(t capture-pane -t ui -p)   # walk back to newer
t send-keys -t ui Down; sleep 0.6; dn0=$(t capture-pane -t ui -p)   # past newest -> restore stash

# Leave AI mode (buffer is now the empty stash, so Backspace exits) and snapshot ↑ AFTER.
t send-keys -t ui BSpace; sleep 0.4
t send-keys -t ui -l "bindkey '^[[A' >| $BIN/after"; t send-keys -t ui Enter
sleep 0.5
after=$(<$BIN/after)

print "\n=== ↑ #1 (newest recalled) ===";   print -r -- "$up1" | grep -v '^ *$' | tail -2
print "\n=== ↑ #2 (older recalled) ===";     print -r -- "$up2" | grep -v '^ *$' | tail -2
print "\n=== ↓ (back toward newer) ===";     print -r -- "$dn1" | grep -v '^ *$' | tail -2
print "\n=== default up-arrow binding (after exit) ==="
print -r -- "$after"
print

fail=0
print -r -- "$up1" | grep -qF 'git status --short' \
  || { print "FAIL: ↑ did not recall the newest command into the buffer"; fail=1; }
print -r -- "$up2" | grep -qF 'ls -la /var/log' \
  || { print "FAIL: a second ↑ did not walk to the older command"; fail=1; }
print -r -- "$dn1" | grep -qF 'git status --short' \
  || { print "FAIL: ↓ did not walk back toward the newer command"; fail=1; }
print -r -- "$dn0" | grep -qF 'git status --short' \
  && { print "FAIL: ↓ past the newest left a stale command in the buffer (stash not restored)"; fail=1; }
[[ "$after" == "$before" ]] \
  || { print "FAIL: leaving AI mode did not restore the default up-arrow ($before -> $after)"; fail=1; }
print -r -- "$after" | grep -q '_tt_recall' \
  && { print "FAIL: recall widget still bound to ↑ after leaving AI mode"; fail=1; }

# --- destructive recall guard: Enter on a recalled destructive item must NOT run it ---
# The stub prepends a destructive record whose execution would create $DOOM; Enter must
# leave the commented banner in the buffer for review instead, and no marker file.
DOOM=$BIN/doomed
t kill-session -t ui 2>/dev/null
t new-session -d -s ui -x $COLS -y $ROWS "$shell"
sleep 3
t send-keys -t ui -l "path=($BIN \$path); export TT_HISTORY_DESTRUCTIVE='date > $DOOM'; source $WIDGET"
t send-keys -t ui Enter
sleep 1
t send-keys -t ui -l '?'; sleep 0.5
t send-keys -t ui Up;    sleep 0.8   # first ↑ loads the porcelain, then auto-fills the newest
t send-keys -t ui Enter; sleep 0.8
guard=$(t capture-pane -t ui -p -J)  # -J: the banner line wraps at $COLS
print "\n=== destructive recall + Enter (banner expected) ==="
print -r -- "$guard" | grep -v '^ *$' | tail -2
print -r -- "$guard" | grep -qF 'DESTRUCTIVE' \
  || { print "FAIL: destructive recall did not leave the commented banner in the buffer"; fail=1; }
print -r -- "$guard" | grep -qF "date > $DOOM" \
  || { print "FAIL: banner does not carry the recalled command for review"; fail=1; }
[[ -e $DOOM ]] \
  && { print "FAIL: destructive recalled command EXECUTED on Enter"; fail=1; }

(( fail )) && { print "RESULT: FAIL — recall walk, arrow restoration, or danger gate broken"; exit 1; }
print "RESULT: PASS — ↑/↓ walk the deduped porcelain; arrows restored; destructive recall gated"
