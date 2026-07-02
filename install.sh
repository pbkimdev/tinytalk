#!/bin/sh
# CLITE installer (#58) — install the CLI, scaffold config, wire the zsh widget.
#
#   ./install.sh [--yes] [--no-rc]
#
#   --yes    don't prompt before editing ~/.zshrc (for scripts/CI)
#   --no-rc  never touch ~/.zshrc
#
# This script installs and configures only. It never runs a generated command
# and never edits your shell config without asking — same posture as CLITE.

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

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# 1. Install the CLI from this clone.
if command -v uv >/dev/null 2>&1; then
  say "installing clite with uv from $REPO_DIR ..."
  uv tool install --force "$REPO_DIR"
elif command -v pipx >/dev/null 2>&1; then
  say "uv not found; installing clite with pipx from $REPO_DIR ..."
  pipx install --force "$REPO_DIR"
else
  say "install.sh: need uv (preferred) or pipx to install clite." >&2
  say "  get uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# 2. Verify the binary resolves.
if command -v clite >/dev/null 2>&1; then
  say "installed: $(clite --version)"
else
  say "clite installed but not on \$PATH — add this to your shell profile:" >&2
  say '  export PATH="$HOME/.local/bin:$PATH"' >&2
fi

# 3. Scaffold the config — only if missing, never overwrite.
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/clite"
CONFIG="$CONFIG_DIR/config.toml"
if [ -f "$CONFIG" ]; then
  say "config: $CONFIG already exists — left untouched"
else
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG" <<'EOF'
# CLITE config — backends, posture, cache, prices (see clite/config.py).
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

# 4. Wire the ? widget into .zshrc — consent-gated, marker-guarded, idempotent.
MARKER="# clite zsh integration (added by install.sh)"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
if [ "$NO_RC" = 1 ]; then
  say "zsh: skipped (--no-rc); enable any time with: eval \"\$(clite init zsh)\""
elif ! clite init zsh >/dev/null 2>&1; then
  say "zsh: this clite build doesn't support 'init zsh' yet — skipped."
  say "     update the clone and re-run ./install.sh to wire the ? widget."
elif [ -f "$ZSHRC" ] && grep -qF "$MARKER" "$ZSHRC"; then
  say "zsh: $ZSHRC already wired — left untouched"
else
  if [ "$YES" = 1 ]; then
    REPLY=y
  else
    printf 'wire the ? widget into %s? [y/N] ' "$ZSHRC"
    read -r REPLY || REPLY=n
  fi
  case "$REPLY" in
    y|Y|yes)
      {
        printf '\n%s\n' "$MARKER"
        printf 'eval "$(clite init zsh)"\n'
      } >>"$ZSHRC"
      say "zsh: added the ? widget to $ZSHRC (takes effect in new shells)"
      ;;
    *) say "zsh: skipped; enable any time with: eval \"\$(clite init zsh)\"" ;;
  esac
fi

say ""
say 'done. try:   clite "list files by size"      or, in a new shell:   ? show my disk usage'
say "benchmark:   clite eval"
say "uninstall:   uv tool uninstall clite   (and remove the marker block from $ZSHRC)"
