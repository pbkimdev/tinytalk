# TinyTalk zsh integration (#35, PRD §8).
# Install:  eval "$(tt init zsh)"   (or source this file from .zshrc)
#
# Press `?` on an empty line to toggle AI mode: your prompt gains a `TinyTalk`
# badge with a spectrum wave animating through its letters, and the `?` is
# never inserted. Type what you want and press Enter — the validated command
# replaces your editing buffer for review; TinyTalk never runs anything itself.
# Destructive commands are inserted commented out. Backspace on an empty line
# (or `?` again) leaves AI mode.

typeset -g _TT_AI_MODE=0
typeset -gi _TT_EXPL_ACTIVE=0
typeset -g _TT_SAVED_HISTCHARS=""
typeset -g _TT_BADGE="TinyTalk"
# Banner prefixed to a destructive command (generate and recall paths both use it): following
# the instruction literally — delete through the colon and space — leaves the runnable command.
typeset -g _TT_DESTRUCTIVE_PREFIX="# DESTRUCTIVE — review, then delete everything up to and including this colon and space: "
typeset -g _TT_WAVE_FD=""
typeset -gi _TT_WAVE_PHASE=0
# 256-color spectrum for the badge, palindromic so the cycle has no seam.
typeset -ga _TT_SPECTRA=(51 45 39 33 63 99 135 171 207 213 207 171 135 99 63 39 45)

typeset -gi _TT_RECALL_IDX=0
typeset -g _TT_RECALL_SAVED_BUFFER=""
typeset -ga _TT_RECALL_ITEMS=()
typeset -ga _TT_RECALL_DANGERS=()  # parallel to _TT_RECALL_ITEMS: safe|caution|destructive
typeset -g _TT_RECALL_STATE="idle"
typeset -g _TT_RECALL_FD=""
typeset -g _TT_RECALL_DIR=""
typeset -g _TT_RECALL_LOAD_BUFFER=""
# The arrow escape sequences we take over (normal + application cursor modes) and the
# widget each maps to; `_TT_RECALL_SAVED_BINDINGS` holds the defaults to restore on leave.
typeset -ga _TT_RECALL_KEYS=('^[[A' '^[OA' '^[[B' '^[OB')
typeset -ga _TT_RECALL_WIDGETS=(_tt_recall_up _tt_recall_up _tt_recall_down _tt_recall_down)
typeset -ga _TT_RECALL_SAVED_BINDINGS=()

# One region_highlight span per badge letter (P = PREDISPLAY offsets), colors
# shifted by the phase so the spectrum travels through the text.
_tt_wave_paint() {
  emulate -L zsh
  local -i i idx n=$#_TT_SPECTRA
  region_highlight=("${(@)region_highlight:#*memo=ttbadge*}")
  for (( i = 0; i < $#_TT_BADGE; i++ )); do
    (( idx = ((i - _TT_WAVE_PHASE) % n + n) % n + 1 ))
    region_highlight+=("P$i $((i + 1)) fg=${_TT_SPECTRA[idx]},bold memo=ttbadge")
  done
}

# Idle animation: a ticker feeds one line per frame into a pipe; the zle -F
# widget handler advances the wave on each. The ticker dies on SIGPIPE as
# soon as _tt_wave_stop closes the fd — no pid to track.
_tt_wave_start() {
  emulate -L zsh
  [[ -n "$_TT_WAVE_FD" ]] && return 0
  exec {_TT_WAVE_FD}< <(
    typeset -i have_zselect=0
    zmodload zsh/zselect 2>/dev/null && have_zselect=1
    while print tick 2>/dev/null; do
      if (( have_zselect )); then zselect -t 10 2>/dev/null; else command sleep 0.1; fi
    done
  )
  zle -F -w "$_TT_WAVE_FD" _tt_wave_tick
}

_tt_wave_stop() {
  emulate -L zsh
  [[ -n "$_TT_WAVE_FD" ]] || return 0
  zle -F "$_TT_WAVE_FD" 2>/dev/null
  exec {_TT_WAVE_FD}<&-
  _TT_WAVE_FD=""
  _TT_WAVE_PHASE=0
}

_tt_wave_tick() {
  emulate -L zsh
  local _junk
  IFS= read -r -u "$_TT_WAVE_FD" _junk || { _tt_wave_stop; return 0; }
  # Drain frames that queued while a widget (e.g. the thinking spinner) ran,
  # so the wave doesn't fast-forward afterwards.
  while IFS= read -r -t 0 -u "$_TT_WAVE_FD" _junk; do :; done
  (( _TT_WAVE_PHASE++ ))
  _tt_wave_paint
  zle -R
}
zle -N _tt_wave_tick

_tt_recall_note() {
  emulate -L zsh
  case "$_TT_RECALL_STATE" in
    loading) zle -M "tt: loading history…" ;;
    empty)   zle -M "tt: no history yet" ;;
    error)   zle -M "tt: history unavailable — run \`tt history\` to check" ;;
  esac
}

_tt_recall_start_load() {
  emulate -L zsh
  setopt nomonitor nonotify
  _TT_RECALL_DIR="$(mktemp -d 2>/dev/null)"
  if [[ -z "$_TT_RECALL_DIR" ]]; then
    _TT_RECALL_STATE="error"
    _tt_recall_note
    return 0
  fi
  _TT_RECALL_STATE="loading"
  _TT_RECALL_LOAD_BUFFER="$BUFFER"
  local dir="$_TT_RECALL_DIR"
  exec {_TT_RECALL_FD}< <(
    { command tt history --porcelain >"$dir/items"; print -n $? >"$dir/rc" } 2>/dev/null
  )
  zle -F -w "$_TT_RECALL_FD" _tt_recall_ready
  _tt_recall_note
}

_tt_recall_ready() {
  emulate -L zsh
  zle -F "$_TT_RECALL_FD" 2>/dev/null
  exec {_TT_RECALL_FD}<&-
  _TT_RECALL_FD=""
  local dir="$_TT_RECALL_DIR"
  local rc="$(<"$dir/rc")"
  _TT_RECALL_ITEMS=()
  _TT_RECALL_DANGERS=()
  local item
  # Each record is `<danger>\t<command>`; split on the FIRST tab only — the command
  # itself may contain tabs and must land in the buffer verbatim.
  while IFS= read -r -d '' item; do
    _TT_RECALL_DANGERS+=("${item%%$'\t'*}")
    _TT_RECALL_ITEMS+=("${item#*$'\t'}")
  done <"$dir/items"
  rm -rf "$dir"
  _TT_RECALL_DIR=""
  (( _TT_AI_MODE )) || { _TT_RECALL_STATE="idle"; return 0; }
  if [[ "$rc" != 0 ]]; then
    _TT_RECALL_STATE="error"
    _tt_recall_note
  elif (( ! $#_TT_RECALL_ITEMS )); then
    _TT_RECALL_STATE="empty"
    _tt_recall_note
  else
    _TT_RECALL_STATE="ready"
    # The ↑ that kicked off the load couldn't step (no data yet); now it landed, so
    # replay that deferred first step through the same widget — unless the user has
    # typed since (BUFFER moved off the buffer we forked on).
    [[ "$BUFFER" == "$_TT_RECALL_LOAD_BUFFER" ]] && _tt_recall_up
    zle -M ""
  fi
  zle -R
}
zle -N _tt_recall_ready

_tt_recall_cancel_load() {
  emulate -L zsh
  if [[ -n "$_TT_RECALL_FD" ]]; then
    zle -F "$_TT_RECALL_FD" 2>/dev/null
    exec {_TT_RECALL_FD}<&-
    _TT_RECALL_FD=""
  fi
  [[ -n "$_TT_RECALL_DIR" ]] && rm -rf "$_TT_RECALL_DIR"
  _TT_RECALL_DIR=""
  _TT_RECALL_STATE="idle"
}

_tt_recall_up() {
  emulate -L zsh
  case "$_TT_RECALL_STATE" in
    ready)
      (( $#_TT_RECALL_ITEMS )) || return 0
      (( _TT_RECALL_IDX == 0 )) && _TT_RECALL_SAVED_BUFFER="$BUFFER"
      (( _TT_RECALL_IDX < $#_TT_RECALL_ITEMS )) && (( _TT_RECALL_IDX++ ))
      BUFFER="$_TT_RECALL_ITEMS[_TT_RECALL_IDX]"
      CURSOR=$#BUFFER
      ;;
    idle) _tt_recall_start_load ;;
    *)    _tt_recall_note ;;
  esac
}
zle -N _tt_recall_up

# ↓: walk toward newer commands; stepping past the newest restores the stashed prompt.
_tt_recall_down() {
  emulate -L zsh
  [[ "$_TT_RECALL_STATE" == ready ]] || return 0
  (( _TT_RECALL_IDX == 0 )) && return 0
  (( _TT_RECALL_IDX-- ))
  if (( _TT_RECALL_IDX == 0 )); then
    BUFFER="$_TT_RECALL_SAVED_BUFFER"
  else
    BUFFER="$_TT_RECALL_ITEMS[_TT_RECALL_IDX]"
  fi
  CURSOR=$#BUFFER
}
zle -N _tt_recall_down

# Take over the arrow keys for recall, remembering each key's default widget so leaving
# AI mode can put it back exactly (unbound keys restore to unbound).
_tt_recall_bind() {
  emulate -L zsh
  (( $#_TT_RECALL_SAVED_BINDINGS )) && return 0  # already active — don't clobber the saved defaults
  local -i i
  for (( i = 1; i <= $#_TT_RECALL_KEYS; i++ )); do
    local b="$(bindkey -- "$_TT_RECALL_KEYS[i]")"
    _TT_RECALL_SAVED_BINDINGS[i]="${b##* }"       # "seq" widget  ->  widget (undefined-key if unbound)
    bindkey -- "$_TT_RECALL_KEYS[i]" "$_TT_RECALL_WIDGETS[i]"
  done
}

_tt_recall_unbind() {
  emulate -L zsh
  (( $#_TT_RECALL_SAVED_BINDINGS )) || return 0
  local -i i
  for (( i = 1; i <= $#_TT_RECALL_KEYS; i++ )); do
    local w="$_TT_RECALL_SAVED_BINDINGS[i]"
    if [[ -z "$w" || "$w" == "undefined-key" ]]; then
      bindkey -r -- "$_TT_RECALL_KEYS[i]"
    else
      bindkey -- "$_TT_RECALL_KEYS[i]" "$w"
    fi
  done
  _TT_RECALL_SAVED_BINDINGS=()
}

_tt_ai_on() {
  emulate -L zsh
  _TT_AI_MODE=1
  _TT_RECALL_STATE="idle"   # re-query the porcelain on this session's first ↑ (freshness)
  _TT_RECALL_IDX=0
  _tt_recall_bind
  PREDISPLAY="$_TT_BADGE "
  _tt_wave_paint
  _tt_wave_start
}

_tt_ai_off() {
  emulate -L zsh
  _TT_AI_MODE=0
  _TT_RECALL_IDX=0
  _tt_recall_cancel_load
  _tt_recall_unbind
  _tt_wave_stop
  PREDISPLAY=""
  POSTDISPLAY=""
  region_highlight=("${(@)region_highlight:#*memo=ttbadge*}")
}

# `?` at the start of an empty line toggles AI mode; anywhere else it is a
# literal `?`.
_tt_question() {
  emulate -L zsh
  if [[ -z "$BUFFER" ]]; then
    if (( _TT_AI_MODE )); then _tt_ai_off; else _tt_ai_on; fi
  else
    zle .self-insert
  fi
}
zle -N _tt_question
bindkey '?' _tt_question

_tt_backspace() {
  emulate -L zsh
  if (( _TT_AI_MODE )) && [[ -z "$BUFFER" ]]; then
    _tt_ai_off
  else
    # Use the named widget so shell plugins such as zsh-autosuggestions can
    # wrap Backspace and keep their displayed suggestion state in sync.
    zle backward-delete-char
  fi
}
zle -N _tt_backspace
bindkey '^?' _tt_backspace
bindkey '^H' _tt_backspace

# Once a validated command lands in BUFFER, its explanation rides below in
# POSTDISPLAY — a dimmed, non-editable trailer one blank line down (POSTDISPLAY,
# not `zle -M`, because region_highlight can dim it and `zle -M` renders ANSI
# literally). `paint` recomputes the span offset from the live BUFFER so it tracks
# edits and re-asserts after syntax highlighters; `clear` drops it before the line
# is accepted so it never freezes into scrollback.
_tt_expl_paint() {
  emulate -L zsh
  region_highlight=("${(@)region_highlight:#*memo=ttexpl*}")
  local -i estart=$(( $#PREDISPLAY + $#BUFFER ))
  region_highlight+=("P$estart $(( estart + $#POSTDISPLAY )) fg=8 memo=ttexpl")
}
_tt_expl_clear() {
  emulate -L zsh
  (( _TT_EXPL_ACTIVE )) || return 0
  _TT_EXPL_ACTIVE=0
  POSTDISPLAY=""
  region_highlight=("${(@)region_highlight:#*memo=ttexpl*}")
}

_tt_accept_line() {
  emulate -L zsh
  _tt_expl_clear
  if (( _TT_AI_MODE && _TT_RECALL_IDX > 0 )) \
      && [[ "$BUFFER" == "$_TT_RECALL_ITEMS[_TT_RECALL_IDX]" ]]; then
    # A past command was walked into BUFFER verbatim — the user reviewed it while
    # stepping through the recall, so Enter runs it directly, not as a new NL prompt.
    # If BUFFER diverged from the selected item, the user edited after recalling —
    # fall through to generate. This is a safety gate, not just prompt routing: the
    # danger in _TT_RECALL_DANGERS classifies the ORIGINAL text, so running an edited
    # buffer here would execute it under a stale classification (a safe `ls` edited
    # into `ls; rm -rf ~` would run as "safe"); generate re-validates the actual text.
    # Neutralize any `!` exactly like the generated-command path (#62); line-init
    # restores histchars at the next prompt.
    [[ -z "$_TT_SAVED_HISTCHARS" ]] && _TT_SAVED_HISTCHARS=$histchars
    histchars=$'\x01'"${histchars[2,3]}"
    if [[ "$_TT_RECALL_DANGERS[_TT_RECALL_IDX]" == "destructive" ]]; then
      # History stores every *suggestion* — including destructive ones the user never
      # ran — so a recalled destructive command gets the same commented-out review
      # gate as a freshly generated one instead of executing on a single Enter.
      BUFFER="$_TT_DESTRUCTIVE_PREFIX$BUFFER"
      CURSOR=$#BUFFER
      _tt_ai_off
      return 0
    fi
    _tt_ai_off
    zle .accept-line
    return
  fi
  _tt_recall_cancel_load
  if (( _TT_AI_MODE )) && [[ -n "$BUFFER" ]]; then
    # The spinner lives in PREDISPLAY (inside the edit region): a mid-widget
    # `zle -M` message can force a scroll that leaves zle's redraw anchor stale by
    # one row, so the replaced buffer overdraws the previous line. `zle -M` is only
    # safe at the very end of the widget, as part of one final redisplay.
    setopt nomonitor nonotify
    local -i have_zselect=0
    zmodload zsh/zselect 2>/dev/null && have_zselect=1
    local tmp="$(mktemp)" out rc=1
    {
      TT_SESSION_CONTEXT="$(fc -ln -20 2>/dev/null)" \
        command tt --widget -- "$BUFFER" >"$tmp" 2>/dev/null
      print -n $? >"$tmp.rc"
    } &
    local pid=$!
    local -a frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
    local -i i=1
    region_highlight+=("0 $#BUFFER fg=8 memo=ttdim")
    {
      # -s, not -e: the redirection creates the rc file before the status byte
      # lands; an empty read would make a failed run look like success.
      while [[ ! -s "$tmp.rc" ]]; do
        (( _TT_WAVE_PHASE++ ))
        PREDISPLAY="$_TT_BADGE ${frames[i]} "
        POSTDISPLAY=""
        _tt_wave_paint
        (( i = i % $#frames + 1 ))
        zle -R
        if (( have_zselect )); then zselect -t 12 2>/dev/null; else command sleep 0.12; fi
      done
      rc="$(<"$tmp.rc")" out="$(<"$tmp")"
    } always {
      kill $pid 2>/dev/null
      rm -f "$tmp" "$tmp.rc"
      PREDISPLAY="$_TT_BADGE "
      POSTDISPLAY=""
      region_highlight=("${(@)region_highlight:#*memo=ttdim*}")
    }
    local tt_command tt_danger tt_explanation tt_error_kind tt_error_message tt_backend
    if [[ $rc -ne 0 ]]; then
      if [[ -n "$out" && "$out" == tt_* ]]; then
        eval "$out"   # shlex-quoted assignments emitted by `tt --widget`
        if [[ -n "$tt_error_kind" ]]; then
          if [[ "$tt_error_kind" == "transport" ]]; then
            zle -M "tt: ${tt_error_message:-backend failed; check \`tt\` on the CLI}"
          else
            zle -M "tt: ${tt_error_message:-no valid command; try rephrasing}"
          fi
          return 0
        fi
      fi
      zle -M "tt: command failed (check \`tt\` on the CLI)"
      return 0
    fi
    if [[ -z "$out" ]]; then
      zle -M "tt: command failed (check \`tt\` on the CLI)"
      return 0
    fi
    eval "$out"   # shlex-quoted assignments emitted by `tt --widget`
    _tt_ai_off
    # The inserted command is machine text: an unquoted `!` in it (e.g. the POSIX
    # glob `.[!.]*`) must not history-expand when the user accepts the line (#62).
    # `unsetopt banghist` would be undone by this function's localoptions the
    # moment the widget returns, so swap the event character out via $histchars
    # (a variable, immune to localoptions); line-init restores it at the next prompt.
    [[ -z "$_TT_SAVED_HISTCHARS" ]] && _TT_SAVED_HISTCHARS=$histchars
    histchars=$'\x01'"${histchars[2,3]}"
    if [[ "$tt_danger" == "destructive" ]]; then
      BUFFER="$_TT_DESTRUCTIVE_PREFIX$tt_command"
    else
      BUFFER="$tt_command"
    fi
    CURSOR=${#BUFFER}
    POSTDISPLAY=$'\n\n'"[$tt_danger]${tt_explanation:+ $tt_explanation}"
    _TT_EXPL_ACTIVE=1
    _tt_expl_paint
    return 0
  fi
  zle .accept-line
}
zle -N accept-line _tt_accept_line

# A fresh prompt never starts in AI mode (covers Ctrl-C mid-request).
autoload -Uz add-zle-hook-widget
_tt_line_init() {
  emulate -L zsh
  _tt_expl_clear
  (( _TT_AI_MODE )) && _tt_ai_off
  if [[ -n "$_TT_SAVED_HISTCHARS" ]]; then
    histchars=$_TT_SAVED_HISTCHARS
    _TT_SAVED_HISTCHARS=""
  fi
  return 0
}
add-zle-hook-widget line-init _tt_line_init

# Syntax highlighters (e.g. fast-syntax-highlighting) rebuild region_highlight
# inside every wrapped widget, wiping the badge's letter spans until the next
# ~100ms tick — the wave blinks on each keystroke (#84). This hook runs after
# the widget and before redisplay, so repainting here always wins.
_tt_line_pre_redraw() {
  emulate -L zsh
  (( _TT_AI_MODE )) && _tt_wave_paint
  (( _TT_EXPL_ACTIVE )) && _tt_expl_paint
  return 0
}
add-zle-hook-widget line-pre-redraw _tt_line_pre_redraw
