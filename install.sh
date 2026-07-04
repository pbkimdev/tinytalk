#!/bin/sh
# TinyTalk installer (#58) — install the CLI, scaffold config, wire PATH + the zsh widget.
#
#   ./install.sh [--yes] [--no-rc]
#
#   --yes    don't prompt (auto-accepts the uv bootstrap and ~/.zshrc edits; for scripts/CI)
#   --no-rc  never touch ~/.zshrc
#
# This script installs and configures only. It never runs a generated command
# and never edits your shell config without asking — same posture as TinyTalk.

set -eu

YES=0
NO_RC=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --no-rc) NO_RC=1 ;;
    -h|--help) echo "usage: ./install.sh [--yes] [--no-rc]"; exit 0 ;;
    *) echo "install.sh: unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }

ask() { # ask "question?" → yes (0) / no (1); --yes auto-accepts
  if [ "$YES" = 1 ]; then return 0; fi
  printf '%s [y/N] ' "$1"
  read -r REPLY || REPLY=n
  case "$REPLY" in y|Y|yes) return 0 ;; *) return 1 ;; esac
}

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
ORIG_PATH=$PATH # the PATH the user's shells actually have — step 4 decides against this
RC_CHANGED=0

# 1. Make sure an installer tool exists — offer to bootstrap uv if there is none.
if ! command -v uv >/dev/null 2>&1 && ! command -v pipx >/dev/null 2>&1; then
  say "TinyTalk needs uv (preferred) or pipx; neither is on \$PATH."
  if ask "fetch and run the official uv installer (https://astral.sh/uv)?"; then
    # UV_NO_MODIFY_PATH: uv's installer edits shell rc files on its own; we
    # suppress that so every rc change stays consent-gated under our markers
    # (step 4 wires the same bin dir).
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh \
        || { say "install.sh: the uv installer failed." >&2; exit 1; }
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh \
        || { say "install.sh: the uv installer failed." >&2; exit 1; }
    else
      say "install.sh: need curl or wget to fetch the uv installer." >&2
      exit 1
    fi
    PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH" # make uv visible to this run
    if ! command -v uv >/dev/null 2>&1; then
      say "install.sh: uv installed but not found — open a new shell and re-run." >&2
      exit 1
    fi
  else
    say "install.sh: need uv (preferred) or pipx to install TinyTalk." >&2
    say "  get uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
fi

# 2. Install the CLI from this clone, and ask the installer where binaries land.
if command -v uv >/dev/null 2>&1; then
  say "installing TinyTalk with uv from $REPO_DIR ..."
  uv tool install --force "$REPO_DIR"
  BIN_DIR=$(uv tool dir --bin 2>/dev/null || true)
else
  say "uv not found; installing TinyTalk with pipx from $REPO_DIR ..."
  pipx install --force "$REPO_DIR"
  BIN_DIR=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
fi
[ -n "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"

# 3. Find the installed binary and verify it runs.
if [ -x "$BIN_DIR/tt" ]; then
  TT="$BIN_DIR/tt"
elif command -v tt >/dev/null 2>&1; then
  TT=tt
else
  say "install.sh: tt did not land in $BIN_DIR or on \$PATH — install failed?" >&2
  exit 1
fi
say "installed: $("$TT" --version)"

# 4. If the user's shells won't find tt (its dir isn't on the pre-install PATH),
# offer to wire it into their rc file — consent-gated, marker-guarded, idempotent.
# bash gets ~/.bashrc (plus ~/.bash_profile when it exists, since macOS login
# shells skip .bashrc); anything else gets .zshrc, matching the zsh-first widget.
PATH_MARKER="# tt PATH (added by install.sh)"
case "$BIN_DIR" in
  "$HOME"/*) BIN_LINE="export PATH=\"\$HOME${BIN_DIR#"$HOME"}:\$PATH\"" ;;
  *) BIN_LINE="export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
case "${SHELL:-}" in
  */bash)
    PATH_RC="$HOME/.bashrc"
    if [ -f "$HOME/.bash_profile" ]; then PATH_RC2="$HOME/.bash_profile"; else PATH_RC2=""; fi
    ;;
  *) PATH_RC="$ZSHRC"; PATH_RC2="" ;;
esac
path_wired() { [ -f "$1" ] && grep -qF "$PATH_MARKER" "$1"; }
wire_path() {
  if path_wired "$1"; then
    say "PATH: $1 already wired — open a new shell to pick it up"
  else
    {
      printf '\n%s\n' "$PATH_MARKER"
      printf '%s\n' "$BIN_LINE"
    } >>"$1"
    RC_CHANGED=1
    say "PATH: added $BIN_DIR to $1 (takes effect in new shells)"
  fi
}
if ( PATH="$ORIG_PATH"; command -v tt ) >/dev/null 2>&1; then
  : # already resolvable from the user's own PATH
elif [ "$NO_RC" = 1 ]; then
  say "PATH: skipped (--no-rc); add it yourself:  $BIN_LINE"
elif path_wired "$PATH_RC" && { [ -z "$PATH_RC2" ] || path_wired "$PATH_RC2"; }; then
  say "PATH: already wired — open a new shell to pick it up"
elif ask "tt is in $BIN_DIR but not on your \$PATH — add it to $PATH_RC${PATH_RC2:+ and $PATH_RC2}?"; then
  wire_path "$PATH_RC"
  if [ -n "$PATH_RC2" ]; then wire_path "$PATH_RC2"; fi
else
  say "PATH: skipped; add it yourself:  $BIN_LINE"
fi

# 5. Scaffold the config — only if missing, never overwrite.
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tinytalk"
CONFIG="$CONFIG_DIR/config.toml"
if [ -f "$CONFIG" ]; then
  say "config: $CONFIG already exists — left untouched"
else
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG" <<'EOF'
# TinyTalk config — backends, posture, cache, prices (see tinytalk/config.py).
[defaults]
backend = "claude"
posture = "hybrid"

[backends.claude]
kind = "claude-agent-sdk"
model = "claude-sonnet-5"

# Local backend via Ollama — install ollama and `ollama pull qwen3:8b` to use,
# then switch defaults.backend to "local".
[backends.local]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "qwen3:8b"

[cache]
enabled = true

[prices."claude-sonnet-5"]
input_per_mtok = 3.0
output_per_mtok = 15.0
EOF
  say "config: wrote starter $CONFIG (edit backends to taste)"
fi

# 6. Warm the grounding snapshot (best-effort) so the first request skips the
# PATH scan. Run under a zsh login shell when possible so PATH matches the
# widget's; a mismatch is safe — a different PATH just rebuilds on first use.
if command -v zsh >/dev/null 2>&1; then
  zsh -lc "export PATH=\"$BIN_DIR:\$PATH\"; command -v tt >/dev/null 2>&1 && tt ground --refresh" >/dev/null 2>&1 || true
else
  "$TT" ground --refresh >/dev/null 2>&1 || true
fi
say "grounding: warmed the tool snapshot (tt ground --refresh)"

# 7. Wire the ? widget into .zshrc — consent-gated, marker-guarded, idempotent.
MARKER="# tt zsh integration (added by install.sh)"
if [ "$NO_RC" = 1 ]; then
  say "zsh: skipped (--no-rc); enable any time with: eval \"\$(tt init zsh)\""
elif ! "$TT" init zsh >/dev/null 2>&1; then
  say "zsh: this tt build doesn't support 'init zsh' yet — skipped."
  say "     update the clone and re-run ./install.sh to wire the ? widget."
elif [ -f "$ZSHRC" ] && grep -qF "$MARKER" "$ZSHRC"; then
  say "zsh: $ZSHRC already wired — left untouched"
elif ask "wire the ? widget into $ZSHRC?"; then
  {
    printf '\n%s\n' "$MARKER"
    printf 'eval "$(tt init zsh)"\n'
  } >>"$ZSHRC"
  RC_CHANGED=1
  say "zsh: added the ? widget to $ZSHRC (takes effect in new shells)"
else
  say "zsh: skipped; enable any time with: eval \"\$(tt init zsh)\""
fi

say ""
say 'done. try:   tt "list files by size"      or, in a new shell:   ? show my disk usage'
if [ "$RC_CHANGED" = 1 ]; then
  say "note: open a new shell (or: source $ZSHRC) to pick up the rc changes"
fi
say "benchmark:   tt eval  (docs/bench/RUNBOOK.md)"
say "uninstall:   uv tool uninstall tinytalk   (and remove the 'added by install.sh' blocks from your rc files)"
