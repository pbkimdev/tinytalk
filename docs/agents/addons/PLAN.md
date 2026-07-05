# Plan — heavy backends as downloadable add-ons

Move AWS Bedrock and the Claude Agent SDK out of the `tt` release binary and into add-ons that
`tt auth` downloads when the user selects that backend. Goal: shrink the base binary from **107 MB
→ ~30 MB** and cut one-file extraction cost, without changing how the backends behave once set up.

## Why (findings)

`tt` is a PyInstaller **one-file** binary. One-file mode re-extracts the whole payload to a temp
`_MEI…` dir on every launch — measured **~277 MB unpacked**, 6–10 s cold, even for `tt init zsh`
(which only prints a static script). Bundle profile:

| Component | Size | Kind |
|---|---|---|
| `claude_agent_sdk/_bundled/claude` | **220 MB** | native, **per-platform** `claude` CLI binary |
| `botocore` + `boto3` + deps | **24 MB** | pure-Python, cross-platform |
| everything else (httpx, prompt_toolkit, questionary, codex…) | ~10 MB | mixed |

Both backends are already lazy/optional in source (`tinytalk[bedrock]` extra; `bedrock.py` imports
`boto3` only inside `_build_client`). The release **forces** them in via
`.github/workflows/release.yml`: `uv sync --extra bedrock` + `--collect-all boto3 botocore
claude_agent_sdk`.

**Companion change (DONE, separate commit):** `install.sh` and the wired `.zshrc` block now cache
`tt init zsh` output and `source` it, regenerating only when the binary is newer — a new shell no
longer runs the binary (0.13 s vs ~6 s). That fixed per-shell startup; this plan fixes download
size and `?`-press latency.

## Design decisions (locked)

1. **Add-on location:** `${XDG_DATA_HOME:-~/.local/share}/tinytalk/addons/<name>/<tt_version>/`
   where `<name>` ∈ {`bedrock`, `claude`}. Version-stamped so a `tt` upgrade re-fetches a matching
   add-on and never mixes ABIs/versions. Old version dirs are ignored (optionally GC'd later).

2. **Bedrock mechanism (pure-Python):** ship one cross-platform `tt-bedrock-addon.tar.gz` = the
   frozen `boto3`+`botocore`+`s3transfer`+`jmespath`+`python-dateutil`+`urllib3`+`six` source tree.
   At runtime, before `import boto3`, prepend the unpacked dir to `sys.path`. Source .py only (the
   frozen interpreter compiles `.pyc` on first import) → one artifact works on all platforms.

3. **Claude mechanism (native binary):** keep the tiny `claude_agent_sdk` **Python** package in the
   base binary; exclude only `_bundled/claude` (220 MB). Ship the `claude` executable **per
   platform** as `tt-claude-addon-<platform>.tar.gz`. Point the SDK at the downloaded path via
   `ClaudeAgentOptions(cli_path=<addon>/claude)`. The SDK already resolves `cli_path` → its bundled
   copy → `$PATH` (`subprocess_cli.py:63,84,89`), so no SDK patch is needed.

4. **Frozen-vs-source detection:** resolver no-ops on a source/dev install (`boto3` already
   importable / `claude` on PATH). Only the frozen binary (`getattr(sys, "frozen", False)` /
   `sys._MEIPASS`) consults the add-on dir. So `uv sync --extra bedrock` dev flow is unchanged.

5. **Download:** use the already-bundled `httpx`. Fetch the release asset for the **binary's own
   version** (`from tinytalk import __version__`) from the GitHub release
   `.../releases/download/v<version>/<asset>`, verify against the published `.sha256`, unpack
   atomically (temp dir → rename), never leave a half-unpacked add-on on failure.

6. **When it downloads:** inside `tt auth`, right after the user picks `bedrock` /
   `claude-agent-sdk` and **before** the live credential probe (the wizard probes both during
   setup: `_probe_bedrock` → `list_foundation_models` → `import boto3`). Prompt + progress + clear
   errors.

7. **Missing add-on at runtime** (e.g. config hand-edited to bedrock without setup): the provider
   raises a friendly "run `tt auth` to install the Bedrock add-on" message and `tt` exits non-zero
   — no raw `ImportError` / `CLINotFoundError` / traceback.

## Sub-tasks (one commit each; each has a verification gate)

Status: **A done ✅ · B done ✅** · C/D/E pending.

Each pending sub-task has a standalone spec in this dir: **[C-release.md](C-release.md)**,
**[D-auth.md](D-auth.md)**, **[E-docs.md](E-docs.md)**. A/B are done; their spec is the implemented
code + tests. The blocks below are the summary — the spec files are authoritative for C/D/E.

### A — runtime resolver  ·  `tinytalk/addons.py`  (+ unit tests)  ✅ DONE
- Dir layout + `<tt_version>` stamping; `_is_frozen()`.
- `ensure_bedrock_importable()` → prepend add-on dir to `sys.path`; raise `AddonMissing` if absent
  in a frozen build; no-op if `boto3` already importable.
- `claude_cli_path() -> str | None` → resolved `claude` path, or None (let SDK fall back on source
  installs).
- **Gate (unit):** path/version-stamp logic, frozen/source branches, and the missing-add-on error
  are covered against a real temp-dir layout, no network.

### B — provider wiring  ·  `bedrock.py`, `claude_agent.py`  ✅ DONE
- `bedrock._build_client` / `list_foundation_models`: call `ensure_bedrock_importable()` before
  `import boto3`; map `AddonMissing` to the friendly error.
- `claude_agent`: pass `cli_path=claude_cli_path()` into `ClaudeAgentOptions` when set.
- **Gate (regression):** existing bedrock/claude provider tests pass with the add-on present; source
  path unaffected.

### C — release workflow  ·  `.github/workflows/release.yml`  → spec: [C-release.md](C-release.md)
- Base binary: drop `--extra bedrock`; drop `--collect-all boto3 botocore`;
  `--collect-all claude_agent_sdk` → `--collect-submodules` (drops the 220 MB `_bundled/claude`).
- New jobs: build + upload `tt-bedrock-addon.tar.gz` (cross-platform, from the frozen lock) and
  `tt-claude-addon-<platform>.tar.gz` (matrix), each with `.sha256`.
- **Gate (manual/CI):** a locally built base binary is ≤ ~40 MB and its `_MEI` tree contains no
  `botocore/` and no `_bundled/claude`; add-on assets exist with checksums.

### D — auth integration  ·  `addons.py` + `auth.py`  → spec: [D-auth.md](D-auth.md)
- `install_addon(name)`: no-op on source/already-installed; else download (httpx) → verify sha256 →
  atomic unpack. Called at the top of `_setup_bedrock` / `_setup_claude_agent_sdk`, before the probe.
- Injectable `opener` seam → unit-test happy path, checksum mismatch, path-traversal, no-op branches
  without network.
- **Gate (manual, needs C assets):** frozen binary with no add-on, `tt auth` → Bedrock downloads +
  unpacks then the live probe succeeds; same for Claude Agent SDK.

### E — docs  ·  `README.md`  → spec: [E-docs.md](E-docs.md)
- Install / provider tables note Bedrock and Claude-Agent-SDK pull a one-time add-on at `tt auth`;
  document the manual-fetch fallback (URL + checksum + unpack path) for offline installs.
- **Gate:** doc review only.

## Out of scope / deferred
- PyInstaller `--onefile` → `--onedir` (separate startup lever; unneeded once base is ~30 MB).
- The `install.sh`/`.zshrc` init-cache change (already done, separate commit).
- Trimming `codex` or other deps.
- Offline add-on install beyond the documented manual path; background auto-upgrade of add-ons.

## Build order
A → B in parallel-ish (B depends on A's API), then C (release), then D (needs the assets from C to
test end-to-end — until C publishes, test D against a locally built add-on tarball + `file://` or a
local HTTP server), then E. Land each sub-task as its own commit.

## Key references
- `.github/workflows/release.yml:46-60` — sync extras + `--collect-all`.
- `tinytalk/provider/bedrock.py:198-219` (`_build_client`), `:222-240` (`list_foundation_models`).
- `tinytalk/provider/claude_agent.py:121-127` (`ClaudeAgentOptions`).
- `claude_agent_sdk/_internal/transport/subprocess_cli.py:63,84,89` — cli_path → bundled → PATH.
- `tinytalk/auth.py:397-458` (`_setup_bedrock`), `:337-359` (`_setup_claude_agent_sdk`),
  `:551-568` (`_probe_bedrock`).
- README §install; provider table.
