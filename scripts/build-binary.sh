#!/bin/sh
# Build TinyTalk's PyInstaller onedir bundle with uv's portable managed Python.
# Linux still builds on Ubuntu 24.04, while the portable runtime avoids inheriting
# the host's newer glibc requirement.

set -eu

: "${UV_PYTHON:=3.12}"
export UV_PYTHON
UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_PREFERENCE

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) UV_PYTHON_PLATFORM=x86_64-manylinux_2_28 ;;
  Linux:aarch64|Linux:arm64) UV_PYTHON_PLATFORM=aarch64-manylinux_2_28 ;;
  *) UV_PYTHON_PLATFORM="" ;;
esac
[ -z "$UV_PYTHON_PLATFORM" ] || export UV_PYTHON_PLATFORM

uv python install "$UV_PYTHON"
if [ -n "$UV_PYTHON_PLATFORM" ]; then
  # UV_PYTHON_PLATFORM selects the managed interpreter download, but uv sync
  # requires the target explicitly to choose baseline-compatible Linux wheels.
  uv sync --frozen --extra codex --python-platform "$UV_PYTHON_PLATFORM"
else
  uv sync --frozen --extra codex
fi
uv run --with pyinstaller pyinstaller --onedir --name tt --clean --noconfirm \
  --specpath build \
  --collect-submodules tinytalk \
  --add-data "tinytalk/shell/tt.zsh:tinytalk/shell" \
  --collect-all httpx --collect-all httpcore --collect-all certifi \
  --collect-all anyio --collect-all h11 \
  --collect-all keyring \
  --collect-all questionary --collect-all prompt_toolkit \
  --collect-submodules claude_agent_sdk --collect-all openai_codex \
  --collect-all boto3 --collect-all botocore --collect-all s3transfer \
  packaging/tt_entry.py

# PyInstaller copies the build host's libgcc on Linux. Depending on Ubuntu 24.04's
# copy would raise the artifact's glibc floor, so use the target distribution's
# ABI-compatible system libgcc instead. The Ubuntu 20.04 runtime gate verifies it.
case "$(uname -s)" in
  Linux) rm -f dist/tt/_internal/libgcc_s.so.1 ;;
esac
