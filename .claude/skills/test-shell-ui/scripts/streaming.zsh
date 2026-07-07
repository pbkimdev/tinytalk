#!/usr/bin/env zsh
# Prove the #61 streaming preview in a real terminal:
#   1. while `tt --widget` streams, the spinner is replaced by a growing, dimmed
#      "⋯ <partial>" preview in the non-editable display region (POSTDISPLAY);
#   2. at stream end the buffer reconciles to the VALIDATED command — not the
#      stale preview — byte-for-byte (fixture 1.stream deliberately differs);
#   3. safety: Enter pressed MID-STREAM never runs the preview. The keystroke
#      queues until the widget returns; by then a destructive command is
#      comment-gated in BUFFER, so the queued Enter accepts an inert banner
#      line and the destructive command never executes (sentinel survives).
#
# Usage: streaming.zsh [-f]   (-f = clean `zsh -f`, else the user's real config)
# Env: COLS (default 90), ROWS (14), TT_STUB_DELAY (3 — keep >= 3 so several
#      preview growth steps are observable).
set -u
HERE=${0:A:h}
REPO=${HERE:h:h:h:h}

clean=0
[[ "${1:-}" == "-f" ]] && { clean=1; shift; }
WIDGET=${1:-$REPO/tinytalk/shell/tt.zsh}
COLS=${COLS:-90} ROWS=${ROWS:-14}
DELAY=${TT_STUB_DELAY:-3}
WAIT=$((DELAY + 2))

BIN=$(mktemp -d)
FIX=$BIN/responses
SENT=$BIN/sentinel
mkdir -p $FIX
ln -s $HERE/tt-stub $BIN/tt
touch $SENT

# Request 1: the streamed preview (1.stream) deliberately DIFFERS from the final
# validated command (1) — the reconciled buffer must show the validated one.
cat >$FIX/1 <<'EOF'
tt_command='du -h -d 1 . | sort -rh | head -n 3'
tt_danger=safe
tt_explanation='Disk usage one level deep, largest first, top 3.'
EOF
print -n 'du -sh STALE PREVIEW NEVER FINAL' >$FIX/1.stream
# Request 2: destructive; no .stream so the stub streams the command itself.
cat >$FIX/2 <<EOF
tt_command='rm -f $SENT'
tt_danger=destructive
tt_explanation='Deletes the sentinel file.'
EOF

t() { tmux -L tt-ui "$@" }
cleanup() { t kill-server 2>/dev/null; rm -rf $BIN; }
trap cleanup EXIT INT

t kill-server 2>/dev/null
shell="zsh -i"; (( clean )) && shell="zsh -f -i"
t new-session -d -s ui -x $COLS -y $ROWS "$shell"
sleep 3

t send-keys -t ui -l "export TT_STUB_STATE=$BIN/count TT_STUB_DELAY=$DELAY TT_STUB_RESPONSES=$FIX; path=($BIN \$path); source $WIDGET; which tt"
t send-keys -t ui Enter
sleep 1
t capture-pane -t ui -p | grep -q "${BIN:t}" || { print "ABORT: stub is not the tt on PATH"; exit 1; }

t send-keys -t ui -l "seq 8"; t send-keys -t ui Enter
sleep 1

# --- request 1: progressive fill, then reconcile to the validated command ----
t send-keys -t ui -l '?'; sleep 0.3
t send-keys -t ui -l 'top disk usage'; sleep 0.3
t send-keys -t ui Enter
sleep 1.4
mid1=$(t capture-pane -t ui -p)
mid1e=$(t capture-pane -t ui -p -e)
sleep 1.2
mid2=$(t capture-pane -t ui -p)
print "=== mid-stream (preview growing) ==="
print -r -- "$mid2" | grep -v '^ *$' | tail -3
sleep $WAIT
fin1=$(t capture-pane -t ui -p -S -50)
print "\n=== request 1 reconciled ==="
print -r -- "$fin1" | grep -v '^ *$' | tail -3

# --- request 2: destructive + Enter pressed MID-STREAM ----------------------
t send-keys -t ui C-u; sleep 0.3            # clear the reviewed command; stay testable
t send-keys -t ui -l '?'; sleep 0.3
t send-keys -t ui -l 'delete the sentinel'; sleep 0.3
t send-keys -t ui Enter
sleep 1.2
t send-keys -t ui Enter                     # MID-STREAM Enter: the safety probe
sleep $WAIT; sleep 1.5
fin2=$(t capture-pane -t ui -p -S -50)
print "\n=== request 2 (destructive, mid-stream Enter) ==="
print -r -- "$fin2" | grep -v '^ *$' | tail -4

print
fail=0
# 1. Preview appeared while streaming: marker + growing stale-preview text.
print -r -- "$mid1" | grep -qF "⋯ du" \
  || { print "FAIL: no dimmed preview marker during streaming"; fail=1; }
print -r -- "$mid2" | grep -qF "STALE" \
  || { print "FAIL: preview did not grow across captures"; fail=1; }
# 2. Preview is rendered dim — visually distinct. tmux emits the fg=8 span as
#    SGR 90 (bright black) immediately before the ⋯ marker.
print -r -- "$mid1e" | grep -qF -- $'\x1b[90m⋯' \
  || { print "FAIL: preview not dimmed (no fg=8/SGR-90 span on the ⋯ line)"; fail=1; }
# 3. Spinner glyph gone once the preview took over.
print -r -- "$mid2" | grep -q '[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]' \
  && { print "FAIL: spinner still shown while preview streams"; fail=1; }
# 4. Reconciled buffer is the VALIDATED command, byte-for-byte; preview gone.
print -r -- "$fin1" | grep -qF "du -h -d 1 . | sort -rh | head -n 3" \
  || { print "FAIL: validated command missing after reconcile"; fail=1; }
last1=$(print -r -- "$fin1" | grep -v '^ *$' | tail -3)
print -r -- "$last1" | grep -qF "STALE" \
  && { print "FAIL: stale preview text survived reconciliation"; fail=1; }
print -r -- "$last1" | grep -qF "⋯" \
  && { print "FAIL: preview marker left on screen after reconcile"; fail=1; }
print -r -- "$fin1" | grep -qF "[safe] Disk usage" \
  || { print "FAIL: explanation trailer missing"; fail=1; }
# 5. Safety: the destructive command is comment-gated and NEVER executed.
print -r -- "$fin2" | grep -qF "# DESTRUCTIVE" \
  || { print "FAIL: destructive banner missing"; fail=1; }
[[ -e $SENT ]] \
  || { print "FAIL: sentinel deleted — mid-stream Enter ran a destructive command"; fail=1; }
# 6. Nothing above was overdrawn by the preview redraws.
for n in {1..8}; do
  print -r -- "$fin1" | grep -qE "^$n *$" \
    || { print "FAIL: seq line $n was overdrawn"; fail=1; }
done

(( fail )) && { print "RESULT: FAIL — streaming preview or safety invariant broken"; exit 1; }
print "RESULT: PASS — progressive dimmed preview, validated reconcile, destructive gate held"
