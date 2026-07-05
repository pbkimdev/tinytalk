# C — release workflow: strip backends, publish add-ons

**File:** `.github/workflows/release.yml`  ·  **Commit:** one  ·  **Depends on:** A, B (runtime already
knows how to consume the add-ons).

> Locked against `DECISIONS.md §C`. Cross-cutting names are byte-identical with sub-task D: platform
> tokens are `macos-arm64` / `linux-x86_64` / `linux-arm64` (no `tt-` prefix); the per-platform claude
> asset is `tt-claude-addon-<platform>.tar.gz`; the bedrock asset is `tt-bedrock-addon.tar.gz`
> (cross-platform, no token).

## Goal
The base `tt` binary ships **without** boto3/botocore and **without** the 220 MB
`claude_agent_sdk/_bundled/claude`. The same release publishes the two backends as separate,
checksummed add-on assets that `tt auth` (sub-task D) downloads.

## Changes to the `build` job

1. **Stop installing bedrock into the build env** so PyInstaller cannot pull it in:
   `uv sync --frozen --extra codex` (drop `--extra bedrock`). Keep `--extra codex` — codex stays
   bundled (out of scope).

2. **Add a `platform:` bare token to each matrix entry** — `asset` minus the `tt-` prefix
   (`macos-arm64`, `linux-x86_64`, `linux-arm64`). The claude tarball is named from `matrix.platform`,
   never `matrix.asset`, so its name never comes out doubled (`tt-claude-addon-tt-…` → 404).

3. **Assert the tag matches the baked version** (early, right after the sync so `import tinytalk`
   resolves; delta I7). Fails the build before the expensive PyInstaller step if the tag drifts:
   ```sh
   want="v$(uv run python -c 'import tinytalk; print(tinytalk.__version__)')"
   got="${{ inputs.tag || github.ref_name }}"
   test "$want" = "$got" || { echo "tag $got != $want"; exit 1; }
   ```

4. **PyInstaller flags:**
   - Remove `--collect-all boto3 --collect-all botocore`.
   - Change `--collect-all claude_agent_sdk` → `--collect-submodules claude_agent_sdk`. `collect-all`
     drags the `_bundled/` **data** (the 220 MB binary); `collect-submodules` takes the Python
     modules only. `_bundled/` is data, not a submodule, and the SDK version comes from `_version.py`,
     so `--collect-submodules` suffices — no post-build delete fallback is needed. The binary is
     supplied at runtime via `cli_path` (sub-task B).
   - Add `--exclude-module boto3 --exclude-module botocore --exclude-module s3transfer` (delta I5).
     This is a build-time exclusion: PyInstaller's modulegraph would otherwise still pull boto3 in from
     the lazy `import boto3` inside `_build_client`. It does **not** block the runtime add-on, which is
     imported off `sys.path` from the add-on dir, not from the frozen archive.

5. **Smoke test** (`--version` / `init zsh` / `--help`) stays; add a deterministic gate that fails the
   build if an excluded backend slipped in or the binary is oversized:
   ```sh
   uv run --with pyinstaller pyi-archive_viewer -brl dist/tt > toc.txt
   grep -Eiq '(^|[/.])botocore|(^|[/.])boto3|_bundled/claude' toc.txt && { echo 'FAIL: excluded backend present'; exit 1; } || true
   sz=$(stat -f%z dist/tt 2>/dev/null || stat -c%s dist/tt); test "$sz" -lt 47185920   # < ~45 MB
   ```

## New: build + upload the add-ons

Both are built from the **same frozen lock** as the binary, so versions match exactly.

### Claude add-on — per-platform, INLINE in each matrix job (delta OQ3)
The wheel for each platform ships its own native `claude`. Copy the single binary after the smoke step,
naming the tarball from `matrix.platform`:
```sh
# claude_agent_sdk (a core dep, with its _bundled/claude) is already in .venv from the base
# build's `uv sync` — no re-sync needed (Phase-3 simplify s1).
CLAUDE=$(uv run python -c "import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec('claude_agent_sdk').origin).parent/'_bundled'/'claude')")
install -m 0755 "$CLAUDE" dist/claude
tar -C dist -czf "out/tt-claude-addon-${{ matrix.platform }}.tar.gz" claude
```
Unpacks to a single `claude` file → `claude_cli_path()` expects `<addon_dir>/claude`.

### Bedrock add-on — dedicated `build-bedrock-addon` job (delta I5)
A standalone `ubuntu-24.04` job with its **own** venv, so boto3 never enters the base binary's build
environment. Pure-Python, so a single artifact works on every platform's frozen 3.12. Copy the exact
installed trees (no re-resolve):
```sh
uv sync --frozen --extra bedrock            # bring boto3 et al. into .venv at locked versions
uv run python - <<'PY'                       # copy the exact installed trees, no re-resolve
import importlib.util, pathlib, shutil
dst = pathlib.Path("dist/addon-bedrock"); dst.mkdir(parents=True, exist_ok=True)
for m in ("boto3","botocore","s3transfer","jmespath","dateutil","urllib3","six"):
    src = pathlib.Path(importlib.util.find_spec(m).origin)
    src = src.parent if src.name == "__init__.py" else src   # six is a single module
    (shutil.copytree(src, dst/src.name) if src.is_dir() else shutil.copy2(src, dst/src.name))
PY
tar -C dist/addon-bedrock -czf out/tt-bedrock-addon.tar.gz .
```
Asset layout: the tarball unpacks to a dir that goes straight on `sys.path` (so `boto3/`,
`botocore/`, … at the tar root — note the `-C … .`).

### Checksums + upload (delta I6)
Each job runs a loop that emits `<file>.sha256` for **every** file in `out/` (skipping anything already
ending in `.sha256`), so every tarball is checksummed, not just the binary:
```sh
cd out
for f in *; do
  case "$f" in *.sha256) continue;; esac
  if command -v sha256sum >/dev/null; then sha256sum "$f" > "$f.sha256"; else shasum -a 256 "$f" > "$f.sha256"; fi
done
```
Each job uploads with a **unique** `upload-artifact@v4` name (`${{ matrix.asset }}` per platform;
`tt-bedrock-addon` for the bedrock job). The `release` job `needs: [build, build-bedrock-addon]` and
globs `artifacts/*` with `merge-multiple`, so every asset rides to the GitHub Release.

## Verification gate (manual / CI)
- [ ] A base binary built with the new flags is **≤ ~45 MB** (was 107 MB) — enforced by the size gate.
- [ ] The `pyi-archive_viewer` TOC carries **no `botocore/`**, **no `boto3/`**, **no `_bundled/claude`**
      — enforced by the grep gate.
- [ ] Release assets include `tt-bedrock-addon.tar.gz(+.sha256)` and, per platform,
      `tt-claude-addon-<platform>.tar.gz(+.sha256)`.
- [ ] Untar the bedrock asset onto a `sys.path` entry in a clean venv → `import boto3` works.
- [ ] The claude asset's `claude -v` runs on its platform.

## Notes / risks
- Platform tokens (`macos-arm64`, `linux-x86_64`, `linux-arm64`) are shared with sub-task D's
  `PLATFORM_TAG` in `addons.py`. They must stay byte-identical; the claude asset name is built from
  `matrix.platform` here and `PLATFORM_TAG` there.
- Do **not** add UPX (a no-op off Windows and it breaks Apple-Silicon codesign) or `--strip`.
- The size/exclusion gates can only be exercised by a real PyInstaller build (release CI), not in a
  spec/lint pass.
