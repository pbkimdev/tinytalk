#!/bin/sh
# TinyTalk uninstaller — the exact reverse of scripts/install.sh.
#
#   curl --proto '=https' --tlsv1.2 -LsSf https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/uninstall.sh | sh
#
#   --yes         don't prompt (auto-confirm; for scripts/CI)
#   --keep-config keep ~/.config/tinytalk (removed by default)
#   --no-rc       never touch your shell rc files
#   --bin-dir DIR where the binary was installed (default: ~/.local/bin)
#
# When the installed `tt` still runs, this delegates the file/keyring removal to
# `tt uninstall` (the single source of truth — it clears the keyring, config,
# cache, add-ons, the unpacked bundle and the launcher symlink). It then strips
# the two "added by install.sh" blocks from your rc files, which `tt uninstall`
# can only point at. If the binary is already gone, it removes what it can by
# hand and tells you the keyring may still hold credentials.

set -eu

REPO="pbkimdev/tinytalk"
YES=0
KEEP_CONFIG=0
NO_RC=0
BIN_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --keep-config) KEEP_CONFIG=1 ;;
    --no-rc) NO_RC=1 ;;
    --bin-dir) shift; BIN_DIR="${1:-}" ;;
    --bin-dir=*) BIN_DIR="${1#*=}" ;;
    -h|--help)
      echo "usage: uninstall.sh [--yes] [--keep-config] [--no-rc] [--bin-dir DIR]"
      exit 0
      ;;
    *) echo "uninstall.sh: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }

# Prompts read from the terminal, not stdin (stdin is the script itself under
# `curl | sh`). No tty → treat as "no", so an unattended pipe never wipes files.
ask() { # ask "question?" -> yes (0) / no (1)
  [ "$YES" = 1 ] && return 0
  [ -e /dev/tty ] || return 1
  printf '%s [y/N] ' "$1" >/dev/tty
  read -r REPLY </dev/tty || REPLY=n
  case "$REPLY" in y|Y|yes) return 0 ;; *) return 1 ;; esac
}

PATH_MARKER="# tt PATH (added by install.sh)"
ZSH_MARKER="# tt zsh integration (added by install.sh)"

# The same locations install.sh writes to.
[ -n "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/tinytalk"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/tinytalk"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tinytalk"

# Locate the binary: prefer the one in BIN_DIR, else whatever is on PATH.
TT="$BIN_DIR/tt"
[ -x "$TT" ] || TT="$(command -v tt 2>/dev/null || true)"

if ! ask "Remove TinyTalk (files, config${KEEP_CONFIG:+ kept}, and keyring entries)?"; then
  say "uninstall: cancelled"
  exit 1
fi

# 1. Remove files + keyring. `tt uninstall` owns exactly what install.sh created,
# so let it do the work when the binary still runs.
UNINSTALL_ARGS="--yes"
[ "$KEEP_CONFIG" = 1 ] && UNINSTALL_ARGS="$UNINSTALL_ARGS --keep-config"
if [ -n "$TT" ] && [ -x "$TT" ] && "$TT" uninstall --help >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  "$TT" uninstall $UNINSTALL_ARGS || say "uninstall: 'tt uninstall' reported an error; cleaning up by hand"
else
  say "uninstall: no working 'tt' found — removing files directly"
  rm -f "$BIN_DIR/tt" && say "removed $BIN_DIR/tt"
  rm -rf "$LIB_DIR/tt" "$LIB_DIR/addons" && say "removed $LIB_DIR/{tt,addons}"
  rmdir "$LIB_DIR" 2>/dev/null || true
  rm -rf "$CACHE_DIR" && say "removed $CACHE_DIR"
  if [ "$KEEP_CONFIG" = 1 ]; then
    say "config: left $CONFIG_DIR in place"
  else
    rm -rf "$CONFIG_DIR" && say "removed $CONFIG_DIR"
  fi
  say "keyring: could not clear stored credentials without a working 'tt' —"
  say "  reinstall and run 'tt uninstall', or delete the 'tinytalk' entries in your keychain."
fi

# 2. Strip the install.sh blocks from every rc file that has them (a bash user's
# PATH block can live in ~/.bashrc or ~/.bash_profile). This is the part
# `tt uninstall` can only tell you to do by hand.
strip_blocks() { # strip_blocks FILE
  file="$1"
  [ -f "$file" ] || return 0
  grep -qF "$PATH_MARKER" "$file" || grep -qF "$ZSH_MARKER" "$file" || return 0
  tmp="$file.tt-uninstall.$$"
  awk -v pm="$PATH_MARKER" -v zm="$ZSH_MARKER" '
    # skip==1: inside the PATH block (marker + one export line)
    # skip==2: inside the zsh block (marker .. the "unset" sentinel)
    skip == 1 { skip = 0; next }
    skip == 2 { if ($0 == "unset _tt_cache _tt_bin") skip = 0; next }
    $0 == pm { held = 0; skip = 1; next }   # held: drop the blank install.sh put before the marker
    $0 == zm { held = 0; skip = 2; next }
    $0 == "" { if (held) print ""; held = 1; next }
    { if (held) { print ""; held = 0 } print }
    END { if (held) print "" }
  ' "$file" > "$tmp"
  if cmp -s "$file" "$tmp"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$file" && say "rc: removed the tt blocks from $file"
  fi
}

if [ "$NO_RC" = 1 ]; then
  say "rc: skipped (--no-rc); remove the 'added by install.sh' blocks yourself"
else
  strip_blocks "${ZDOTDIR:-$HOME}/.zshrc"
  strip_blocks "$HOME/.bashrc"
  strip_blocks "$HOME/.bash_profile"
fi

say ""
say "done. TinyTalk has been removed. Open a new shell to drop the stale PATH."
