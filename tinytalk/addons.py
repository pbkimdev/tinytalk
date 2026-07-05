"""Runtime resolver for downloadable provider add-ons (Bedrock, Claude Agent SDK).

The release `tt` is a PyInstaller `--onedir` binary. To keep it small, its two heaviest
backends live *outside* the bundle and are downloaded by `tt auth` when the user sets that
backend up. This module is the runtime seam that locates
an installed add-on and makes it usable:

- **Bedrock** ships as a pure-Python tree (`boto3`/`botocore`/…). Prepend the unpacked dir
  to `sys.path` before `import boto3`.
- **Claude Agent SDK**'s heavy part is a native `claude` CLI binary. Return its path so the
  caller can hand it to `ClaudeAgentOptions(cli_path=...)`.

Add-ons are version-stamped — `.../addons/<name>/<tt_version>/` — so a `tt` upgrade fetches a
matching add-on and never mixes versions. On a source/dev install nothing is downloaded:
`boto3` is already importable and `claude` is on `$PATH`, so every entry point here no-ops
and returns the source behavior. This module deliberately depends on nothing but the stdlib
and `__version__`, so providers translate `AddonMissing` into their own error type.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import platform
import shutil
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tinytalk import __version__


def default_addons_dir() -> Path:
    """Root of the version-stamped add-on tree (`$XDG_DATA_HOME/tinytalk/addons`)."""
    xdg = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(xdg).expanduser() / "tinytalk" / "addons"


def addon_dir(name: str) -> Path:
    """Where the `name` add-on for *this* `tt` version is unpacked."""
    return default_addons_dir() / name / __version__


def _is_frozen() -> bool:
    """True inside the PyInstaller binary; False on a source/dev install."""
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")


# How to install a backend's deps from a source checkout (frozen builds say "run tt auth"
# instead). Keyed by add-on name; claude ships as a core dep, so it has no extra.
_SOURCE_HINT = {
    "bedrock": "`uv sync --extra bedrock` (or pip install 'tinytalk[bedrock]')",
    "claude": "run from a source install where the `claude` CLI is on your $PATH",
}

# Wizard menu labels — for the "run tt auth, choose <label>" hint and the download lead-in.
_ADDON_LABELS = {"bedrock": "AWS Bedrock", "claude": "Claude Agent SDK"}


class AddonMissing(Exception):
    """A backend's add-on isn't installed. Message adapts to frozen vs source install."""

    def __init__(self, name: str):
        self.name = name
        if _is_frozen():
            label = _ADDON_LABELS.get(name, name)
            msg = (
                f"the {name} backend needs a one-time add-on — run `tt auth`, "
                f"choose {label}, and tt will download it"
            )
        else:
            hint = _SOURCE_HINT.get(name, f"`uv sync --extra {name}`")
            msg = f"{name} support is not installed; {hint}"
        super().__init__(msg)


def _ensure_on_path(name: str, module: str) -> None:
    """Make `module` importable, pulling in the `name` add-on's dir if needed.

    Returns silently once `module` resolves (source install, or add-on already on the path);
    raises `AddonMissing` if it can't be made importable.
    """
    if importlib.util.find_spec(module) is not None:
        return
    site = addon_dir(name)
    if site.is_dir():
        entry = str(site)
        if entry not in sys.path:
            sys.path.insert(0, entry)
        importlib.invalidate_caches()
        if importlib.util.find_spec(module) is not None:
            return
    raise AddonMissing(name)


def ensure_bedrock_importable() -> None:
    """Guarantee `import boto3` will work, or raise `AddonMissing("bedrock")`."""
    _ensure_on_path("bedrock", "boto3")


def claude_cli_path() -> str | None:
    """Path to the add-on `claude` binary, or None to let the SDK resolve it itself.

    Source installs return None (the SDK finds `claude` on `$PATH` or its own bundled copy).
    A frozen build with no add-on raises `AddonMissing("claude")` rather than letting the SDK
    surface a raw `CLINotFoundError`.
    """
    if is_installed("claude"):
        return str(addon_dir("claude") / "claude")
    if _is_frozen():
        raise AddonMissing("claude")
    return None


# --- add-on download + install (driven by `tt auth`) --------------------------------
# Linux reports `aarch64` where macOS reports `arm64`, so the platform token comes from
# an explicit (system, machine) lookup rather than string-munging `platform.machine()`.
_PLATFORM_TAGS = {
    ("Darwin", "arm64"): "macos-arm64",
    ("Linux", "x86_64"): "linux-x86_64",
    ("Linux", "aarch64"): "linux-arm64",
}
PLATFORM_TAG: str | None = _PLATFORM_TAGS.get((platform.system(), platform.machine()))

_MIB = 1024 * 1024


class AddonInstallError(Exception):
    """Downloading, verifying, or unpacking an add-on failed. Message names the asset + reason."""


def asset_name(name: str) -> str:
    """Release asset filename for the `name` add-on (bedrock is cross-platform; claude is per-tag)."""
    if name == "claude":
        if PLATFORM_TAG is None:
            raise AddonInstallError(
                f"no prebuilt claude add-on for this platform "
                f"({platform.system()}/{platform.machine()})"
            )
        return f"tt-claude-addon-{PLATFORM_TAG}.tar.gz"
    return "tt-bedrock-addon.tar.gz"


def asset_url(name: str) -> str:
    """GitHub Release download URL for this `tt` version's `name` add-on."""
    return (
        f"https://github.com/pbkimdev/tinytalk/releases/download/"
        f"v{__version__}/{asset_name(name)}"
    )


def is_installed(name: str) -> bool:
    """True when the `name` add-on for this `tt` version is unpacked and usable.

    Matches the runtime import contract, not merely a non-empty dir: bedrock needs its
    `boto3/` sys.path root, and claude needs an executable `claude` file.
    """
    d = addon_dir(name)
    if name == "claude":
        exe = d / "claude"
        return exe.is_file() and os.access(exe, os.X_OK)
    return (d / "boto3").is_dir()


@contextmanager
def _http_opener(url: str):
    """Default opener: stream `url`, yielding `(total_or_None, chunk_iterator)`.

    `follow_redirects=True` is mandatory — GitHub `releases/download/*` 302-redirects to
    `objects.githubusercontent.com`. `raise_for_status` turns a 404 into a clear error
    instead of a downstream checksum mismatch.
    """
    import httpx

    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0) or None
            yield total, resp.iter_bytes()


def _progress(asset: str, done: int, total: int | None) -> None:
    """Redraw one `\\r`-updated progress line on stdout (percent + MiB when size is known)."""
    done_mib = done / _MIB
    if total:
        line = f"  Downloading {asset}  {done * 100 // total}%  ({done_mib:.1f}/{total / _MIB:.1f} MiB)"
    else:
        line = f"  Downloading {asset}  ({done_mib:.1f} MiB)"
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


def _fetch_sha256(opener, url: str) -> str:
    """Return the expected hex digest from a `.sha256` sidecar (first whitespace token)."""
    with opener(url) as (_total, chunks):
        text = b"".join(chunks).decode("utf-8", "replace")
    tokens = text.split()
    if not tokens:
        raise AddonInstallError(f"{url}: empty checksum file")
    return tokens[0]


def install_addon(name: str, *, opener=None) -> None:
    """Download, verify, and unpack the `name` add-on for this `tt` version.

    A no-op on a source install or when the add-on is already present, so the wizard can
    call it unconditionally. `opener` is a context-manager seam mirroring `httpx.stream`
    (see `_http_opener`) so tests inject bytes without network. Any failure raises
    `AddonInstallError` and leaves nothing half-written on disk.
    """
    if not _is_frozen() or is_installed(name):
        return
    opener = opener or _http_opener
    asset = asset_name(name)
    dest = addon_dir(name)
    partial = dest.parent / (dest.name + ".partial")  # string-built: `.with_suffix` would eat `0.1.0`
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(partial, ignore_errors=True)
    print(f"{_ADDON_LABELS.get(name, name)} needs a one-time add-on. Downloading…")

    tmp: Path | None = None
    try:
        expected = _fetch_sha256(opener, asset_url(name) + ".sha256")
        fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".tmp")
        tmp = Path(tmp_path)
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as fh, opener(asset_url(name)) as (total, chunks):
            done = 0
            for chunk in chunks:
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                _progress(asset, done, total)
        sys.stdout.write("\n")
        sys.stdout.flush()

        actual = digest.hexdigest()
        if actual.lower() != expected.lower():
            raise AddonInstallError(
                f"{asset}: checksum mismatch (expected {expected}, got {actual})"
            )
        partial.mkdir()
        with tarfile.open(tmp) as tf:
            tf.extractall(partial, filter="data")  # PEP 706: blocks `..`/absolute/symlink escapes
        if dest.exists():
            shutil.rmtree(dest)  # os.replace onto a non-empty dir raises ENOTEMPTY
        os.replace(partial, dest)
    except AddonInstallError:
        raise
    except Exception as exc:  # httpx.HTTPError, tarfile.TarError (incl. FilterError), OSError, …
        raise AddonInstallError(f"{asset}: {exc}") from exc
    finally:
        shutil.rmtree(partial, ignore_errors=True)
        if tmp is not None:
            tmp.unlink(missing_ok=True)
