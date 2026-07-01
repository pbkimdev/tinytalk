# CLITE zsh integration (#35, PRD §8).
# Install:  eval "$(clite init zsh)"   (or source this file from .zshrc)
#
# Press `?` on an empty line to toggle AI mode: your prompt gains a colored
# `AI` badge and the `?` is never inserted. Type what you want and press
# Enter — the validated command replaces your editing buffer for review;
# CLITE never runs anything itself. Destructive commands are inserted
# commented out. Backspace on an empty line (or `?` again) leaves AI mode.

typeset -g _CLITE_AI_MODE=0

_clite_ai_on() {
  _CLITE_AI_MODE=1
  PREDISPLAY="AI "
  region_highlight+=("P0 2 fg=cyan,bold")
}

_clite_ai_off() {
  _CLITE_AI_MODE=0
  PREDISPLAY=""
  POSTDISPLAY=""
  region_highlight=("${(@)region_highlight:#P0 2 *}")
}

# `?` at the start of an empty line toggles AI mode; anywhere else it is a
# literal `?`.
_clite_question() {
  if [[ -z "$BUFFER" ]]; then
    if (( _CLITE_AI_MODE )); then _clite_ai_off; else _clite_ai_on; fi
  else
    zle .self-insert
  fi
}
zle -N _clite_question
bindkey '?' _clite_question

_clite_backspace() {
  if (( _CLITE_AI_MODE )) && [[ -z "$BUFFER" ]]; then
    _clite_ai_off
  else
    zle .backward-delete-char
  fi
}
zle -N _clite_backspace
bindkey '^?' _clite_backspace
bindkey '^H' _clite_backspace

_clite_accept_line() {
  if (( _CLITE_AI_MODE )) && [[ -n "$BUFFER" ]]; then
    # The spinner lives in POSTDISPLAY (inside the edit region): a mid-widget
    # `zle -M` message can force a scroll that leaves zle's redraw anchor stale by
    # one row, so the replaced buffer overdraws the previous line. `zle -M` is only
    # safe at the very end of the widget, as part of one final redisplay.
    setopt localoptions nomonitor nonotify
    local -i have_zselect=0
    zmodload zsh/zselect 2>/dev/null && have_zselect=1
    local tmp="$(mktemp)" out rc=1
    {
      CLITE_SESSION_CONTEXT="$(fc -ln -20 2>/dev/null)" \
        command clite --widget -- "$BUFFER" >"$tmp" 2>/dev/null
      print -n $? >"$tmp.rc"
    } &
    local pid=$!
    local -a frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
    local -i i=1
    {
      while [[ ! -e "$tmp.rc" ]]; do
        POSTDISPLAY=" ${frames[i]} thinking"
        zle -R
        (( i = i % $#frames + 1 ))
        if (( have_zselect )); then zselect -t 12 2>/dev/null; else command sleep 0.12; fi
      done
      rc="$(<"$tmp.rc")" out="$(<"$tmp")"
    } always {
      kill $pid 2>/dev/null
      rm -f "$tmp" "$tmp.rc"
      POSTDISPLAY=""
    }
    if [[ $rc -ne 0 || -z "$out" ]]; then
      zle -M "clite: no valid command (try rephrasing; check \`clite\` on the CLI)"
      return 0
    fi
    local clite_command clite_danger clite_explanation
    eval "$out"   # shlex-quoted assignments emitted by `clite --widget`
    _clite_ai_off
    if [[ "$clite_danger" == "destructive" ]]; then
      BUFFER="# DESTRUCTIVE — review, then remove the #: $clite_command"
    else
      BUFFER="$clite_command"
    fi
    CURSOR=${#BUFFER}
    zle -M "[$clite_danger] $clite_explanation"
    return 0
  fi
  zle .accept-line
}
zle -N accept-line _clite_accept_line

# A fresh prompt never starts in AI mode (covers Ctrl-C mid-request).
autoload -Uz add-zle-hook-widget
_clite_line_init() {
  (( _CLITE_AI_MODE )) && _clite_ai_off
  return 0
}
add-zle-hook-widget line-init _clite_line_init
