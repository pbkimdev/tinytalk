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
typeset -g _TT_SAVED_HISTCHARS=""
typeset -g _TT_BADGE="TinyTalk"
typeset -g _TT_WAVE_FD=""
typeset -gi _TT_WAVE_PHASE=0
# 256-color spectrum for the badge, palindromic so the cycle has no seam.
typeset -ga _TT_SPECTRA=(51 45 39 33 63 99 135 171 207 213 207 171 135 99 63 39 45)

# Prompt-mode ↑/↓ recall (#D1). Inside AI mode the arrows walk past commands from
# `tt history --porcelain` (NUL-delimited, newest-first, already deduped by `tt`)
# into BUFFER — the Atuin model. Loaded once per AI session; index 0 = the live
# prompt, 1 = newest command, N = oldest.
typeset -gi _TT_RECALL_LOADED=0
typeset -gi _TT_RECALL_IDX=0
typeset -g _TT_RECALL_SAVED_BUFFER=""
typeset -ga _TT_RECALL_ITEMS=()
# The arrow escape sequences we take over (normal + application cursor modes) and the
# widget each maps to; `_TT_RECALL_SAVED_BINDINGS` holds the defaults to restore on leave.
typeset -ga _TT_RECALL_KEYS=('^[[A' '^[OA' '^[[B' '^[OB')
typeset -ga _TT_RECALL_WIDGETS=(_tt_recall_up _tt_recall_up _tt_recall_down _tt_recall_down)
typeset -ga _TT_RECALL_SAVED_BINDINGS=()

# One region_highlight span per badge letter (P = PREDISPLAY offsets), colors
# shifted by the phase so the spectrum travels through the text.
_tt_wave_paint() {
  local -i i idx n=$#_TT_SPECTRA
  region_highlight=("${(@)region_highlight:#P<0-7> <1-8> *}")  # the 8 letter spans below
  for (( i = 0; i < $#_TT_BADGE; i++ )); do
    (( idx = ((i - _TT_WAVE_PHASE) % n + n) % n + 1 ))
    region_highlight+=("P$i $((i + 1)) fg=${_TT_SPECTRA[idx]},bold")
  done
}

# Idle animation: a ticker feeds one line per frame into a pipe; the zle -F
# widget handler advances the wave on each. The ticker dies on SIGPIPE as
# soon as _tt_wave_stop closes the fd — no pid to track.
_tt_wave_start() {
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
  [[ -n "$_TT_WAVE_FD" ]] || return 0
  zle -F "$_TT_WAVE_FD" 2>/dev/null
  exec {_TT_WAVE_FD}<&-
  _TT_WAVE_FD=""
  _TT_WAVE_PHASE=0
}

_tt_wave_tick() {
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

# Shell out to the porcelain ONCE per AI session and cache the array; every later
# ↑/↓ walks the cache, so navigating costs no subprocess. Best-effort: a missing or
# failing `tt` just yields an empty list and the arrows become no-ops.
_tt_recall_load() {
  (( _TT_RECALL_LOADED )) && return 0
  _TT_RECALL_LOADED=1
  _TT_RECALL_ITEMS=()
  local item
  while IFS= read -r -d '' item; do
    _TT_RECALL_ITEMS+=("$item")
  done < <(command tt history --porcelain 2>/dev/null)
  return 0
}

# ↑: stash the in-progress prompt on the first step, then walk toward older commands.
_tt_recall_up() {
  _tt_recall_load
  (( $#_TT_RECALL_ITEMS )) || return 0
  (( _TT_RECALL_IDX == 0 )) && _TT_RECALL_SAVED_BUFFER="$BUFFER"
  (( _TT_RECALL_IDX < $#_TT_RECALL_ITEMS )) && (( _TT_RECALL_IDX++ ))
  BUFFER="$_TT_RECALL_ITEMS[_TT_RECALL_IDX]"
  CURSOR=$#BUFFER
}
zle -N _tt_recall_up

# ↓: walk toward newer commands; stepping past the newest restores the stashed prompt.
_tt_recall_down() {
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
  (( $#_TT_RECALL_SAVED_BINDINGS )) && return 0  # already active — don't clobber the saved defaults
  local -i i
  for (( i = 1; i <= $#_TT_RECALL_KEYS; i++ )); do
    local b="$(bindkey -- "$_TT_RECALL_KEYS[i]")"
    _TT_RECALL_SAVED_BINDINGS[i]="${b##* }"       # "seq" widget  ->  widget (undefined-key if unbound)
    bindkey -- "$_TT_RECALL_KEYS[i]" "$_TT_RECALL_WIDGETS[i]"
  done
}

_tt_recall_unbind() {
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
  _TT_AI_MODE=1
  _TT_RECALL_LOADED=0   # re-query the porcelain once on this session's first ↑ (freshness)
  _TT_RECALL_IDX=0
  _tt_recall_bind
  PREDISPLAY="$_TT_BADGE "
  _tt_wave_paint
  _tt_wave_start
}

_tt_ai_off() {
  _TT_AI_MODE=0
  _TT_RECALL_IDX=0
  _tt_recall_unbind
  _tt_wave_stop
  PREDISPLAY=""
  POSTDISPLAY=""
  region_highlight=("${(@)region_highlight:#P<0-7> <1-8> *}")
}

# `?` at the start of an empty line toggles AI mode; anywhere else it is a
# literal `?`.
_tt_question() {
  if [[ -z "$BUFFER" ]]; then
    if (( _TT_AI_MODE )); then _tt_ai_off; else _tt_ai_on; fi
  else
    zle .self-insert
  fi
}
zle -N _tt_question
bindkey '?' _tt_question

_tt_backspace() {
  if (( _TT_AI_MODE )) && [[ -z "$BUFFER" ]]; then
    _tt_ai_off
  else
    zle .backward-delete-char
  fi
}
zle -N _tt_backspace
bindkey '^?' _tt_backspace
bindkey '^H' _tt_backspace

_tt_accept_line() {
  if (( _TT_AI_MODE && _TT_RECALL_IDX > 0 )); then
    # A past command was walked into BUFFER verbatim — the user reviewed it while
    # stepping through the recall, so Enter runs it directly, not as a new NL prompt.
    # Neutralize any `!` exactly like the generated-command path (#62); line-init
    # restores histchars at the next prompt.
    [[ -z "$_TT_SAVED_HISTCHARS" ]] && _TT_SAVED_HISTCHARS=$histchars
    histchars=$'\x01'"${histchars[2,3]}"
    _tt_ai_off
    zle .accept-line
    return
  fi
  if (( _TT_AI_MODE )) && [[ -n "$BUFFER" ]]; then
    # The spinner lives in PREDISPLAY (inside the edit region): a mid-widget
    # `zle -M` message can force a scroll that leaves zle's redraw anchor stale by
    # one row, so the replaced buffer overdraws the previous line. `zle -M` is only
    # safe at the very end of the widget, as part of one final redisplay.
    setopt localoptions nomonitor nonotify
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
    region_highlight+=("0 $#BUFFER fg=8")
    {
      while [[ ! -e "$tmp.rc" ]]; do
        PREDISPLAY="$_TT_BADGE ${frames[i]} "
        (( _TT_WAVE_PHASE++ ))
        _tt_wave_paint
        zle -R
        (( i = i % $#frames + 1 ))
        if (( have_zselect )); then zselect -t 12 2>/dev/null; else command sleep 0.12; fi
      done
      rc="$(<"$tmp.rc")" out="$(<"$tmp")"
    } always {
      kill $pid 2>/dev/null
      rm -f "$tmp" "$tmp.rc"
      PREDISPLAY="$_TT_BADGE "
      region_highlight=("${(@)region_highlight:#0 $#BUFFER *}")
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
      BUFFER="# DESTRUCTIVE — review, then remove the #: $tt_command"
    else
      BUFFER="$tt_command"
    fi
    CURSOR=${#BUFFER}
    zle -M "[$tt_danger] $tt_explanation"
    return 0
  fi
  zle .accept-line
}
zle -N accept-line _tt_accept_line

# A fresh prompt never starts in AI mode (covers Ctrl-C mid-request).
autoload -Uz add-zle-hook-widget
_tt_line_init() {
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
  (( _TT_AI_MODE )) && _tt_wave_paint
  return 0
}
add-zle-hook-widget line-pre-redraw _tt_line_pre_redraw
