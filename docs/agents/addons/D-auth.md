# D — spec: fetch the add-on during `tt auth`

**Files:** `tinytalk/addons.py` (download helper) + `tinytalk/auth.py` (wire-in)  ·  **Commit:** one
·  **Depends on:** A (resolver), C (assets must exist to test end-to-end; until then test against a
local tarball + `file://`).

## Problem
The wizard probes the backend **live** during setup — `_setup_bedrock` → `_probe_bedrock` →
`list_foundation_models` → `import boto3` (`auth.py:397,551`), and `_setup_claude_agent_sdk` →
`_probe_claude_agent` → a real SDK call. On a frozen binary neither add-on is present yet, so the
probe would fail. The add-on must be downloaded, verified, and unpacked **before** the probe runs.

## Behavior (spec)

1. When the user selects **bedrock** or **claude-agent-sdk** and the matching add-on is absent, `tt`
   prints a one-line lead-in (`"AWS Bedrock needs a one-time add-on. Downloading…"`) followed by a
   **live progress bar** (`\r`-updated, percent + MiB when `Content-Length` is known), downloads the
   asset, verifies its checksum, unpacks it, and only then continues to the credential probe.
2. On a **source install** (`_is_frozen()` false) nothing downloads — deps are already present; the
   step is a no-op.
3. If the add-on is **already installed** for this `tt` version, no download — reuse it.
4. On **any failure** (network, checksum mismatch, unpack) the wizard prints a clear error and aborts
   setup (`return None`) leaving **no half-unpacked add-on** on disk. The user can re-run `tt auth`.

## Interface (new, in `tinytalk/addons.py`)

```python
PLATFORM_TAG: str | None               # "macos-arm64" | "linux-x86_64" | "linux-arm64" | None
def asset_name(name: str) -> str       # "tt-bedrock-addon.tar.gz" | f"tt-claude-addon-{PLATFORM_TAG}.tar.gz"
                                       #   (claude raises AddonInstallError when PLATFORM_TAG is None)
def asset_url(name: str) -> str        # f"https://github.com/pbkimdev/tinytalk/releases/download/v{__version__}/{asset_name(name)}"
def is_installed(name: str) -> bool    # bedrock: (addon_dir/boto3).is_dir(); claude: <dir>/claude is_file() and X_OK
def install_addon(name: str, *, opener=None) -> None
    # no-op if not _is_frozen() or is_installed(name); else download → verify sha256 → atomic unpack.
    # `opener` is a context-manager seam (default: httpx.stream) so tests inject bytes without network.
```

`PLATFORM_TAG` derives from an explicit `(platform.system(), platform.machine())` lookup (Linux
reports `aarch64`, macOS reports `arm64`); `is_installed` matches the runtime import contract, not
"dir non-empty" (bedrock's sys.path root is the `boto3/` dir; claude needs an executable `claude`).

- **opener seam:** a context manager mirroring `httpx.stream` — it yields `(total, chunks)` where
  `total` is `Content-Length` (or `None`) and `chunks` is a byte iterator. The real opener uses
  `httpx.Client(follow_redirects=True, …)` (GitHub `releases/download/*` 302-redirects to
  `objects.githubusercontent.com` — **mandatory**) and calls `resp.raise_for_status()` so a 404 is a
  clear error, not a checksum failure. Tests inject a fake that yields `(len(data), iter([data]))` and
  picks the tarball vs the sha256 text by `url.endswith(".sha256")`.
- **Download:** stream `asset_url(name)+".sha256"` (small; first whitespace token = expected hex) then
  the tarball, to a temp file under `addon_dir(name).parent` (same filesystem → atomic rename), hashing
  while writing and updating the live progress bar from `total`.
- **Verify:** compare the streamed sha256 to the expected hex (case-insensitive). Mismatch → raise
  `AddonInstallError`.
- **Unpack atomically:** `partial = dest.parent / (dest.name + ".partial")` — a **string-built** sibling,
  never `.with_suffix(".partial")` (which would corrupt `0.1.0` → `0.1.partial`). Extract into `partial`
  with `tarfile.open(tmp).extractall(partial, filter="data")` — the PEP 706 data filter replaces the
  hand-rolled `..`/absolute guard, blocks symlink/device escapes, and keeps the 0755 exec bit. Then
  `if dest.exists(): shutil.rmtree(dest)` (os.replace onto a non-empty dir raises ENOTEMPTY) and
  `os.replace(partial, dest)`.
- **Cleanup:** a `finally` rmtrees `partial` and unlinks the temp tarball on **every** path, so no
  `.partial` and no temp file survive a failure. Any error (`httpx.HTTPError`, checksum, `tarfile.TarError`
  incl. `FilterError`, `OSError`) surfaces as `AddonInstallError` (subclass of `Exception`), message
  names the asset and reason.

## Wire-in (`tinytalk/auth.py`)

At the **top** of `_setup_bedrock` and `_setup_claude_agent_sdk`, before any probe:
```python
try:
    install_addon("bedrock")            # / "claude"
except AddonInstallError as exc:
    print(f"tt auth: {exc}")
    return None
```
Keep it out of the `WizardIO` seam (it's I/O, not prompting), but print through the same channel the
wizard already uses. No secret handling changes.

## OQ2 fixes folded into this commit (edit A code)
- `claude_cli_path()` gates on `exe.is_file() and os.access(exe, os.X_OK)` (not `exe.exists()`), matching
  `is_installed("claude")`.
- `AddonMissing.__init__`'s frozen branch points at the wizard **menu label** via
  `{"bedrock": "AWS Bedrock", "claude": "Claude Agent SDK"}` — "…run `tt auth`, choose {label}, and tt
  will download it".

## Testability
`install_addon(opener=…)` takes an injected context-manager opener yielding `(total, chunks)` (see the
opener seam above), so unit tests cover, with **no network**, on a temp `XDG_DATA_HOME` and
`_is_frozen` monkeypatched True:
- happy path: fake tar with a `boto3/__init__.py` → unpacked into `addon_dir`, `is_installed` true, and
  the dir is sys.path-usable; claude tar keeps the exec bit so `is_installed("claude")` holds;
- checksum mismatch → `AddonInstallError`, `addon_dir` absent, no `.partial`, no temp file left;
- already-installed → opener never called;
- source install (`_is_frozen` monkeypatched false) → opener never called, no-op;
- path-traversal / unsafe tar entry → `AddonInstallError` (filter='data' rejects), dest absent;
- pre-existing stale dest is replaced cleanly;
- `asset_name("claude")` equals `tt-claude-addon-<tag>.tar.gz` for each of the three tags, and
  `asset_url` carries the `v` prefix (byte-for-byte guard against the doubled-`tt-` 404).

## Acceptance (manual, needs C's real assets)
- [ ] Frozen binary, no add-on: `tt auth` → Bedrock downloads + unpacks, then
      `list_foundation_models` probe succeeds.
- [ ] Frozen binary, no add-on: `tt auth` → Claude Agent SDK downloads the platform `claude`, probe
      call succeeds.
- [ ] Kill the network mid-download → clear error, `tt auth` re-run succeeds, no stale `.partial`.
- [ ] Second `tt auth` for the same backend does not re-download.

## Out of scope
- A standalone `tt <backend> install` command (the wizard is the one path; manual fetch is documented
  in E). Background/auto-upgrade of an add-on outside `tt auth`.
