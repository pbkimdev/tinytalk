#!/usr/bin/env zsh
set -u
HERE=${0:A:h}
REPO=${HERE:h:h:h:h}
WIDGET=${1:-$REPO/tinytalk/shell/tt.zsh}
COLS=${COLS:-90} ROWS=${ROWS:-14}

BIN=$(mktemp -d)
ln -s $HERE/tt-stub $BIN/tt

t() { tmux -L tt-ui-async "$@" }
cleanup() { t kill-server 2>/dev/null; rm -rf $BIN; }
trap cleanup EXIT INT

fail=0

start_pane() {
  t kill-server 2>/dev/null
  t new-session -d -s ui -x $COLS -y $ROWS "zsh -f -i"
  sleep 2
  t send-keys -t ui -l "path=($BIN \$path); ${1:+export $1; }source $WIDGET; which tt"
  t send-keys -t ui Enter
  sleep 1
  t capture-pane -t ui -p | grep -q "${BIN:t}" || { print "ABORT: stub is not the tt on PATH"; exit 1; }
  t send-keys -t ui -l '?'
  sleep 0.5
}

cap() { t capture-pane -t ui -p | grep -v '^ *$' | tail -3 }

print "=== slow load (TT_HISTORY_DELAY=2) ==="
start_pane "TT_HISTORY_DELAY=2"
t send-keys -t ui Up
sleep 0.8
mid=$(cap)
print -r -- "$mid"
print -r -- "$mid" | grep -qF 'loading history' \
  || { print "FAIL: first ↑ did not show the loading note (widget may be blocking)"; fail=1; }
print -r -- "$mid" | grep -qF 'git status --short' \
  && { print "FAIL: command appeared before the slow load finished"; fail=1; }
sleep 2.2
done_=$(cap)
print -r -- "--- after load ---"; print -r -- "$done_"
print -r -- "$done_" | grep -qF 'git status --short' \
  || { print "FAIL: buffer did not auto-fill with the newest command after the load"; fail=1; }
print -r -- "$done_" | grep -qF 'loading history' \
  && { print "FAIL: the loading note lingered after the command filled the buffer"; fail=1; }

print "\n=== failing tt (TT_HISTORY_FAIL=1) ==="
start_pane "TT_HISTORY_FAIL=1"
t send-keys -t ui Up
sleep 1.5
err=$(cap)
print -r -- "$err"
print -r -- "$err" | grep -qF 'history unavailable' \
  || { print "FAIL: a failing tt did not surface the 'history unavailable' error"; fail=1; }

print "\n=== empty store (TT_HISTORY_EMPTY=1) ==="
start_pane "TT_HISTORY_EMPTY=1"
t send-keys -t ui Up
sleep 1.5
empty=$(cap)
print -r -- "$empty"
print -r -- "$empty" | grep -qF 'no history yet' \
  || { print "FAIL: an empty store did not surface the 'no history yet' note"; fail=1; }

print "\n=== leave mid-load: no stray filesystem error (TT_HISTORY_DELAY=2) ==="
start_pane "TT_HISTORY_DELAY=2"
t send-keys -t ui Up; sleep 0.5
t send-keys -t ui BSpace; sleep 0.3
t send-keys -t ui -l 'echo marker'; t send-keys -t ui Enter; sleep 0.4
sleep 2.5
leave=$(t capture-pane -t ui -p -S -20)
print -r -- "$leave" | grep -v '^ *$' | tail -3
print -r -- "$leave" | grep -qiF 'no such file' \
  && { print "FAIL: leaving mid-load leaked a 'no such file' error onto the terminal"; fail=1; }

print "\n=== rejected generate does not get hijacked by a pending recall load ==="
REJECT=$(mktemp -d)
start_pane "TT_STUB_RESPONSES=$REJECT TT_STUB_DELAY=1 TT_HISTORY_DELAY=2"
t send-keys -t ui -l 'make me a sandwich'; sleep 0.3
t send-keys -t ui Up; sleep 0.5
t send-keys -t ui Enter; sleep 1.4
t send-keys -t ui -l ''; sleep 2.2
hij=$(cap)
print -r -- "$hij"
print -r -- "$hij" | grep -qF 'git status --short' \
  && { print "FAIL: a pending recall load hijacked the buffer after a rejected generate"; fail=1; }
rm -rf "$REJECT"

print
(( fail )) && { print "RESULT: FAIL — async recall load/feedback broken"; exit 1; }
print "RESULT: PASS — async load shows loading/error/empty feedback and never blocks the line"
