#!/bin/sh
# TinyTalk installer — download the prebuilt `tt` binary, wire PATH + the zsh widget.
#
#   curl --proto '=https' --tlsv1.2 -LsSf https://raw.githubusercontent.com/pbkimdev/tinytalk/main/install.sh | sh
#
#   --yes         don't prompt (auto-accepts PATH + ~/.zshrc edits; for scripts/CI)
#   --no-rc       never touch your shell rc files
#   --bin-dir DIR install the binary here (default: ~/.local/bin)
#   --version TAG install a specific release tag (default: latest)
#
# The binary is self-contained: no Python, no uv, nothing to build. This script
# installs and configures only — it never runs a generated command, and never
# edits your shell config without asking. Same posture as TinyTalk itself.

set -eu

REPO="pbkimdev/tinytalk"
YES=0
NO_RC=0
BIN_DIR=""
VERSION="latest"

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --no-rc) NO_RC=1 ;;
    --bin-dir) shift; BIN_DIR="${1:-}" ;;
    --bin-dir=*) BIN_DIR="${1#*=}" ;;
    --version) shift; VERSION="${1:-latest}" ;;
    --version=*) VERSION="${1#*=}" ;;
    -h|--help)
      echo "usage: install.sh [--yes] [--no-rc] [--bin-dir DIR] [--version TAG]"; exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "install.sh: $*" >&2; exit 1; }

# Prompts read from the terminal, not stdin — stdin is the script itself under
# `curl | sh`. With no controlling tty (fully headless), an unanswered prompt is
# a "no", which keeps the never-touch-rc-without-consent posture intact.
ask() { # ask "question?" -> yes (0) / no (1)
  [ "$YES" = 1 ] && return 0
  [ -e /dev/tty ] || return 1
  printf '%s [y/N] ' "$1" >/dev/tty
  read -r REPLY </dev/tty || REPLY=n
  case "$REPLY" in y|Y|yes) return 0 ;; *) return 1 ;; esac
}

# 1. Pick the release asset for this OS/arch.
os=$(uname -s 2>/dev/null || echo unknown)
arch=$(uname -m 2>/dev/null || echo unknown)
case "$os" in
  Darwin) OS=macos ;;
  Linux)  OS=linux ;;
  *) die "unsupported OS: $os (TinyTalk ships macOS and Linux binaries)" ;;
esac
case "$arch" in
  arm64|aarch64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=x86_64 ;;
  *) die "unsupported architecture: $arch" ;;
esac
ASSET="tt-$OS-$ARCH.tar.gz"   # a --onedir bundle (launcher + _internal/), tarred

# 2. Resolve the download URL. `TT_RELEASE_BASE` overrides the host (mirrors/testing);
# `TT_BINARY` short-circuits the download to a local file (used by CI/self-tests).
BASE="${TT_RELEASE_BASE:-https://github.com/$REPO/releases}"
if [ "$VERSION" = "latest" ]; then
  URL="$BASE/latest/download/$ASSET"
else
  URL="$BASE/download/$VERSION/$ASSET"
fi

need() { command -v "$1" >/dev/null 2>&1; }
TMP=$(mktemp) || die "could not create a temp file"
trap 'rm -f "$TMP" "$TMP.sha256"' EXIT

if [ -n "${TT_BINARY:-}" ]; then
  say "using local bundle $TT_BINARY (TT_BINARY set)"
  cp "$TT_BINARY" "$TMP" || die "could not read $TT_BINARY"
else
  say "downloading $ASSET ($VERSION) ..."
  if need curl; then
    curl --proto '=https' --tlsv1.2 -fL# "$URL" -o "$TMP" \
      || die "download failed: $URL"
    # Best-effort checksum: verify only if the release publishes <asset>.sha256.
    if curl --proto '=https' --tlsv1.2 -fsSL "$URL.sha256" -o "$TMP.sha256" 2>/dev/null; then
      want=$(cut -d' ' -f1 <"$TMP.sha256")
      if need sha256sum; then got=$(sha256sum "$TMP" | cut -d' ' -f1)
      elif need shasum; then got=$(shasum -a 256 "$TMP" | cut -d' ' -f1)
      else got=""; fi
      [ -z "$got" ] || [ "$got" = "$want" ] || die "checksum mismatch for $ASSET"
      [ -z "$got" ] || say "checksum: ok"
    fi
  elif need wget; then
    wget -qO "$TMP" "$URL" || die "download failed: $URL"
  else
    die "need curl or wget to download the binary"
  fi
fi
# 3. Unpack the --onedir bundle into a lib dir and symlink its launcher onto PATH.
# The bundle runs straight from its unpacked directory — no per-launch self-extraction
# (the --onefile cost that made every `tt` invocation stutter for seconds), so `tt` and
# the ? recall widget start near-instantly. $BIN_DIR/tt is a symlink to the real launcher.
[ -n "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR" || die "could not create $BIN_DIR"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/tinytalk"
mkdir -p "$LIB_DIR" || die "could not create $LIB_DIR"
rm -rf "$LIB_DIR/tt"   # drop any previous version's tree before unpacking the new one
tar -xzf "$TMP" -C "$LIB_DIR" || die "could not unpack the bundle into $LIB_DIR"
[ -x "$LIB_DIR/tt/tt" ] || die "the bundle is missing its tt launcher"
# tar restores the archive's build-time mtime onto the launcher, but the .zshrc cache
# block regenerates `init.zsh` only when the launcher is newer than the cache (`-nt`).
# Stamp it to now so an upgrade always refreshes the cached widget instead of keeping
# the previous version's.
touch "$LIB_DIR/tt/tt"
TT="$BIN_DIR/tt"
ln -sf "$LIB_DIR/tt/tt" "$TT" || die "could not link $TT -> $LIB_DIR/tt/tt"
"$TT" --version >/dev/null 2>&1 || die "the installed binary did not run"
say "installed: $("$TT" --version)  ->  $TT -> $LIB_DIR/tt/tt"

# 4. Put BIN_DIR on PATH if the user's shells won't find `tt` — consent-gated,
# marker-guarded, idempotent. bash gets ~/.bashrc (+ ~/.bash_profile if present);
# anything else gets ~/.zshrc, matching the zsh-first widget.
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
PATH_MARKER="# tt PATH (added by install.sh)"
case "$BIN_DIR" in
  "$HOME"/*) BIN_LINE="export PATH=\"\$HOME${BIN_DIR#"$HOME"}:\$PATH\"" ;;
  *) BIN_LINE="export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
case "${SHELL:-}" in
  */bash)
    PATH_RC="$HOME/.bashrc"
    [ -f "$HOME/.bash_profile" ] && PATH_RC2="$HOME/.bash_profile" || PATH_RC2="" ;;
  *) PATH_RC="$ZSHRC"; PATH_RC2="" ;;
esac
path_wired() { [ -f "$1" ] && grep -qF "$PATH_MARKER" "$1"; }
wire_path() {
  if path_wired "$1"; then
    say "PATH: $1 already wired — open a new shell to pick it up"
  else
    { printf '\n%s\n' "$PATH_MARKER"; printf '%s\n' "$BIN_LINE"; } >>"$1"
    say "PATH: added $BIN_DIR to $1 (takes effect in new shells)"
  fi
}
if command -v tt >/dev/null 2>&1 && [ "$(command -v tt)" = "$TT" ]; then
  : # already resolvable and it's ours
elif echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  : # BIN_DIR already on PATH
elif [ "$NO_RC" = 1 ]; then
  say "PATH: skipped (--no-rc); add it yourself:  $BIN_LINE"
elif path_wired "$PATH_RC" && { [ -z "$PATH_RC2" ] || path_wired "$PATH_RC2"; }; then
  say "PATH: already wired — open a new shell to pick it up"
elif ask "tt is in $BIN_DIR but not on your \$PATH — add it to $PATH_RC${PATH_RC2:+ and $PATH_RC2}?"; then
  wire_path "$PATH_RC"
  [ -n "$PATH_RC2" ] && wire_path "$PATH_RC2" || true
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
# TinyTalk config — backends, posture, cache, prices. Run `tt auth` to set up a
# cloud backend, or point the local backend below at your own server.
[defaults]
backend = "local"
posture = "local"

# Local backend via an OpenAI-compatible server (oMLX, llama.cpp, Ollama, ...).
# Start your server, then adjust base_url/model to match. No API key needed.
[backends.local]
kind = "openai-compat"
base_url = "http://localhost:8000/v1"   # llama.cpp defaults to 8080
model = "gemma-4-26B-A4B-it-MLX-8bit"

[cache]
enabled = true
EOF
  say "config: wrote starter $CONFIG (edit backends to taste)"
fi

# 6. Warm the grounding snapshot (best-effort) so the first request skips the scan.
"$TT" ground --refresh >/dev/null 2>&1 || true
say "grounding: warmed the tool snapshot (tt ground --refresh)"

# 7. Wire the ? widget into ~/.zshrc — consent-gated, marker-guarded, idempotent.
MARKER="# tt zsh integration (added by install.sh)"
if [ "$NO_RC" = 1 ]; then
  say "zsh: skipped (--no-rc); enable any time with: eval \"\$(tt init zsh)\""
elif ! "$TT" init zsh >/dev/null 2>&1; then
  say "zsh: this build doesn't support 'init zsh' — skipped."
elif [ -f "$ZSHRC" ] && grep -qF "$MARKER" "$ZSHRC"; then
  say "zsh: $ZSHRC already wired — left untouched"
elif ask "wire the ? widget into $ZSHRC?"; then
  # Source a cached copy of `tt init zsh`, regenerated only when the launcher is
  # newer than the cache — so a new shell sources a file instead of forking `tt`
  # (`-nt` follows the $BIN_DIR/tt symlink to the unpacked launcher's mtime).
  cat >>"$ZSHRC" <<EOF

$MARKER
_tt_cache="\${XDG_CACHE_HOME:-\$HOME/.cache}/tinytalk/init.zsh"
_tt_bin=\$(command -v tt)
if [[ -n \$_tt_bin && ( ! -s \$_tt_cache || \$_tt_bin -nt \$_tt_cache ) ]]; then
  command mkdir -p "\${_tt_cache:h}" && "\$_tt_bin" init zsh >| \$_tt_cache 2>/dev/null
fi
[[ -s \$_tt_cache ]] && source \$_tt_cache
unset _tt_cache _tt_bin
EOF
  say "zsh: added the ? widget to $ZSHRC (takes effect in new shells)"
else
  say "zsh: skipped; enable any time with: eval \"\$(tt init zsh)\""
fi

say ""
say 'done. try:   tt "list files by size"      or, in a new shell:   ? show my disk usage'
say "set up a cloud model:   tt auth"
say "uninstall:   rm $TT && rm -rf $LIB_DIR/tt   (and remove the 'added by install.sh' blocks from your rc files)"
