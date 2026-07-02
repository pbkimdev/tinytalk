#!/usr/bin/env zsh
# Drive the CLITE zsh widget in a real terminal (tmux) and check for redraw collisions.
#
# Usage: drive.zsh [-f] [widget-file]
#   -f           clean shell (zsh -f), instead of the user's real interactive config
#   widget-file  defaults to clite/shell/clite.zsh at the repo root
# Env: COLS (default 90, keep >= 60 for the checks), ROWS (14), CLITE_STUB_DELAY (1),
#      CLITE_STUB_RESPONSES (dir of canned responses; see clite-stub).
#
# Scenario: source widget -> `seq 8` so the prompt sits on the bottom row (where zle
# scroll bugs live) -> AI request #1 -> accept & run it -> AI request #2 -> assert
# that every previously printed line is still intact in the final screen.
set -u
HERE=${0:A:h}
REPO=${HERE:h:h:h:h}

clean=0
[[ "${1:-}" == "-f" ]] && { clean=1; shift; }
WIDGET=${1:-$REPO/clite/shell/clite.zsh}
COLS=${COLS:-90} ROWS=${ROWS:-14}
DELAY=${CLITE_STUB_DELAY:-1}
WAIT=$((DELAY + 2))

BIN=$(mktemp -d)
ln -s $HERE/clite-stub $BIN/clite

t() { tmux -L clite-ui "$@" }
cleanup() { t kill-server 2>/dev/null; rm -rf $BIN; }
trap cleanup EXIT INT

t kill-server 2>/dev/null
shell="zsh -i"; (( clean )) && shell="zsh -f -i"
t new-session -d -s ui -x $COLS -y $ROWS "$shell"
sleep 3

# PATH must be prepended INSIDE the pane: the user's zshrc resets PATH, so anything
# injected via the environment silently loses to the real installed clite.
t send-keys -t ui -l "export CLITE_STUB_STATE=$BIN/count CLITE_STUB_DELAY=$DELAY; path=($BIN \$path); source $WIDGET; which clite"
t send-keys -t ui Enter
sleep 1
t capture-pane -t ui -p | grep -q "${BIN:t}" || { print "ABORT: stub is not the clite on PATH"; exit 1; }

t send-keys -t ui -l "seq 8"; t send-keys -t ui Enter
sleep 1

t send-keys -t ui -l '?'; sleep 0.3
t send-keys -t ui -l 'greet me'; sleep 0.3
t send-keys -t ui Enter
sleep 0.5
print "=== during thinking ==="
t capture-pane -t ui -p | grep -v '^ *$' | tail -2
sleep $WAIT
print "\n=== request 1 inserted for review ==="
t capture-pane -t ui -p | grep -v '^ *$'

t send-keys -t ui Enter          # accept & run the inserted command
sleep 1.5
t send-keys -t ui -l '?'; sleep 0.3
t send-keys -t ui -l 'top disk usage'; sleep 0.3
t send-keys -t ui Enter
sleep $WAIT
print "\n=== request 2 inserted ==="
mid2=$(t capture-pane -t ui -p -S -50)
print -r -- "$mid2" | grep -v '^ *$' | tail -4

# Request 3 carries the history-expansion hazard `.[!.]*` (#62): accepting it must
# run it (no `event not found`), and `!!` at the NEXT prompt must expand again.
t send-keys -t ui Enter          # accept & run request 2
sleep 1.5
t send-keys -t ui -l '?'; sleep 0.3
t send-keys -t ui -l 'print marker and hidden entries'; sleep 0.3
t send-keys -t ui Enter
sleep $WAIT
t send-keys -t ui Enter          # accept & run: `print -r ok-histexpand .[!.]*(N)`
sleep 1
# `!!` must expand at the next prompt (histchars restored). Do NOT accept the
# result: under hist_verify the recalled line re-expands its own `!` — a stock
# zsh footgun unrelated to the widget — so clear it instead.
t send-keys -t ui -l '!!'; t send-keys -t ui Enter
sleep 1
print "\n=== request 3 (histexpand hazard) + !! (final screen) ==="
final=$(t capture-pane -t ui -p -S -50)
t send-keys -t ui C-u; sleep 0.3   # drop any hist_verify re-presented buffer
print -r -- "$final" | grep -v '^ *$' | tail -6

# Collision checks: redraw bugs EAT earlier lines silently, so assert the old
# content is still there — "the new line looks right" proves nothing.
print
fail=0
for n in {1..8}; do
  print -r -- "$final" | grep -qE "^$n *$" \
    || { print "FAIL: seq line $n was overdrawn"; fail=1; }
done
print -r -- "$final" | grep -qE "^Hello! I'm CLITE — tell me what you'd like" \
  || { print "FAIL: request-1 output line was overdrawn"; fail=1; }
print -r -- "$mid2" | grep -qF "du -h -d 1 . | sort -rh | head -n 3" \
  || { print "FAIL: request-2 command not in the buffer"; fail=1; }
print -r -- "$mid2" | grep -qF "[safe] Lists disk usage" \
  || { print "FAIL: explanation message missing"; fail=1; }
print -r -- "$final" | grep -q "thinking" \
  && { print "FAIL: stale thinking spinner left on screen"; fail=1; }
print -r -- "$final" | grep -q "event not found" \
  && { print "FAIL: inserted command was history-expanded (#62)"; fail=1; }
print -r -- "$final" | grep -qF "command not found: !!" \
  && { print "FAIL: !! stayed literal at the next prompt; histchars not restored (#62)"; fail=1; }
# >=3 sightings = insert echo + its output + the !!-expanded line (echoed or re-run).
(( $(print -r -- "$final" | grep -c "ok-histexpand") >= 3 )) \
  || { print "FAIL: no evidence !! expanded after restore (#62)"; fail=1; }
(( fail )) && { print "RESULT: FAIL — display collision or missing UI element"; exit 1; }
print "RESULT: PASS — no lines eaten; badge, command, message, and histexpand guard all good"
