#!/bin/sh
# Run inside Ubuntu 20.04. Verify both the launcher and lazily loaded native
# extensions; `tt --version` alone does not load cryptography and similar wheels.

set -eu

ROOT=${1:?usage: verify-linux-binary.sh BUNDLE_DIR}
"$ROOT/tt" --version
"$ROOT/tt" --help >/dev/null
"$ROOT/tt" auth --help >/dev/null
test -d "$ROOT/_internal/boto3"

find "$ROOT" -type f \( -perm -111 -o -name '*.so' -o -name '*.so.*' \) -print |
  while IFS= read -r native; do
    linked=$(ldd "$native" 2>&1 || true)
    case "$linked" in
      *"not found"*)
        printf '%s\n%s\n' "incompatible native file: $native" "$linked" >&2
        exit 1
        ;;
    esac
  done
