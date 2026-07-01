# CLITE zsh integration (#35, PRD §8).
# Install:  eval "$(clite init zsh)"   (or source this file from .zshrc)
#
# Type `? <what you want>` and press Enter: the validated command replaces your
# editing buffer for review — CLITE never runs anything itself. Destructive
# commands are inserted commented out.

_clite_accept_line() {
  if [[ "$BUFFER" == \?* ]]; then
    local request="${BUFFER#\?}"
    request="${request# }"
    if [[ -n "$request" ]]; then
      zle -M "clite: thinking…"
      local out
      out="$(CLITE_SESSION_CONTEXT="$(fc -ln -20 2>/dev/null)" \
             command clite --widget -- "$request" 2>/dev/null)"
      if [[ $? -ne 0 || -z "$out" ]]; then
        zle -M "clite: no valid command (try rephrasing; check \`clite\` on the CLI)"
        return 0
      fi
      local clite_command clite_danger clite_explanation
      eval "$out"   # shlex-quoted assignments emitted by `clite --widget`
      if [[ "$clite_danger" == "destructive" ]]; then
        BUFFER="# DESTRUCTIVE — review, then remove the #: $clite_command"
      else
        BUFFER="$clite_command"
      fi
      CURSOR=${#BUFFER}
      zle -M "[$clite_danger] $clite_explanation"
      return 0
    fi
  fi
  zle .accept-line
}
zle -N accept-line _clite_accept_line

# Visible prompt-mode indicator while the line starts with `?`.
_clite_indicator() {
  if [[ "$BUFFER" == \?* ]]; then
    POSTDISPLAY=$'\n[clite prompt mode]'
  elif [[ "$POSTDISPLAY" == $'\n[clite prompt mode]' ]]; then
    POSTDISPLAY=""
  fi
}
zle -N zle-line-pre-redraw _clite_indicator
