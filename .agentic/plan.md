# Plan — #30 Config loader (`config.toml`)

> Part of #25 (clite v1, Python re-platform). Core engine / "the spine."

## Goal & scope

Add a **Python** config loader that reads `~/.config/clite/config.toml`, validates it,
and resolves the **backend** (which provider to use) and the runtime **posture**
(`local` / `cloud`) from it. A missing or invalid config fails with a clear, actionable
error. Fully unit-tested.

This is the seam every later core piece consumes: the provider adapters (#26–#29) read
the resolved backend; the tier controller (#31) reads posture; eval (#32), safety (#34)
and caching (#36) read their own sections later.

**In scope**
- New module `clite/config.py`:
  - Locate the config file (explicit path arg → `$CLITE_CONFIG` → `$XDG_CONFIG_HOME/clite/config.toml`
    → `~/.config/clite/config.toml`).
  - Parse TOML with `tomllib` (3.11+) / `tomli` backport (3.10).
  - **Strictly** validate and resolve `backend` + `posture` (this issue's DoD).
  - Carry through the remaining PRD sections (`danger`, `cache`, `prices`) as
    lightly-typed pass-through so siblings can consume them — without owning their
    semantics (those belong to #34 / #36 / #32).
  - Typed return value (`Config` dataclass) + typed errors with actionable messages.
- `config.example.toml` at the repo root — the canonical schema reference, also echoed
  (minimal form) inside the "no config" error.
- `tomli` backport dependency in `pyproject.toml` (`python_version < "3.11"`).
- Unit tests in `tests/test_config.py`.

**Out of scope (explicitly)**
- Constructing/instantiating providers or SDK clients — the loader *selects* a backend
  (returns its resolved settings); building it belongs to #26–#29.
- Enforcing danger policy, cache behavior, or price math — only *parsing* those sections.
- Wiring config into the `clite` CLI run path (`clite/cli.py`) — nothing consumes it
  until the tier controller (#31); leaving `cli.py` untouched keeps the change scoped.
  A future `--config PATH` flag is noted for #31, not built here.
- Auto-creating the config file in the user's home (clite never writes there silently;
  the error only *shows* an example).

## Dependency & sequencing (must read)

`main` is **still Go**; the Python scaffold this loader builds on lives on the **open,
unmerged** PR #24 (`pivot/python-replatform`). Therefore:

- **#24 must merge before #30 is *implemented*.** The implement phase must run against a
  Python `main` (rebase `agentic/issue-30` onto the updated `main` so `clite/`,
  `pyproject.toml`, `tests/` are present).
- This PLAN commit is docs-only (`.agentic/plan.md`) and does not depend on the pivot,
  so it lands cleanly now. Routing is `pending` so a human can sequence #24 → #30.

## Definition of Done (measurable)

1. `clite/config.py` exposes `load_config(path=None) -> Config` and resolves both
   **backend** (kind + model + connection settings) and **posture**.
2. A **missing** config file raises `ConfigNotFoundError` whose message names the path
   searched and shows a minimal valid example.
3. **Invalid** input raises `ConfigError` with a message that names the file and the
   specific problem, for each of: malformed TOML; missing `[clite].backend`; selected
   backend has no `[backends.<name>]` table; invalid `posture`; unknown backend `kind`.
4. Default-path resolution honors `$CLITE_CONFIG` and `$XDG_CONFIG_HOME`.
5. `pytest` passes and `ruff check` is clean.

**Smallest verification level:** unit tests (the issue says "Unit-tested"). No network,
no real `$HOME` writes — `tmp_path` + `monkeypatch` only.

## Config schema (resolved here)

```toml
# ~/.config/clite/config.toml

[clite]
backend = "local"     # required: name of a [backends.<name>] table
posture = "local"     # optional: local | cloud  (default: "local", per PRD local-first)

[backends.local]
kind  = "openai_compatible"          # openai_compatible | claude_agent_sdk | openai_codex_sdk
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434/v1"
# api_key optional (local endpoints usually need none)

[backends.claude]
kind  = "claude_agent_sdk"
model = "claude-sonnet-4-6"

# Pass-through sections (parsed, not semantically enforced here)
[danger]
policy = "confirm"

[cache]
enabled = true
dir = "~/.cache/clite"

[prices."claude-sonnet-4-6"]
input = 3.0
output = 15.0
```

**Design decisions (flagged for review):**
- `backend` is a *named reference* into `[backends.*]` (lets the file hold several
  backends and switch with one line — supports the eval harness running ≥2 backends).
- `Backend` field names (`model`, `base_url`, `api_key`) mirror the already-merged #8
  OpenAI-compatible client (`openai.New(baseURL, apiKey, model)`) so the local adapter
  (#29) maps 1:1.
- `base_url` is optional at *load* time (the loader selects; it doesn't construct). An
  `openai_compatible` backend with no `base_url` is unusable, but enforcing that is left
  to the adapter (#29/#26) — the loader doesn't fail on it. (Flagged for review.)
- `posture` defaults to `"local"` when omitted (PRD §2 "local-first default"); only an
  *invalid* value errors.
- The loader strictly validates only `backend` + `posture` + the selected backend's
  `kind`/`model`. `danger`/`cache`/`prices` are passed through loosely so this change
  doesn't pre-empt #34/#36/#32. Field names above are the proposed contract siblings
  align to — the main thing to confirm in review.

## Public API (`clite/config.py`)

- `BackendKind` — allowed kinds: `openai_compatible`, `claude_agent_sdk`, `openai_codex_sdk`
  (an `Enum` or a validated `frozenset` of strings).
- `@dataclass(frozen=True) Backend` — `name: str`, `kind: str`, `model: str`,
  `base_url: str | None`, `api_key: str | None`.
- `@dataclass(frozen=True) Config` — `path: Path`, `backend: Backend`, `posture: str`,
  `danger: dict`, `cache: dict`, `prices: dict` (pass-through dicts for now).
- `class ConfigError(Exception)` — base.
- `class ConfigNotFoundError(ConfigError)` — missing file (lets callers offer to scaffold).
- `default_config_path() -> Path` — env-aware resolution order above.
- `load_config(path: str | os.PathLike | None = None) -> Config` — main entry point.

Error-message style (each prefixed `clite: `, naming the file/field and the fix):
- missing → `clite: no config at <path>. Create it, e.g.:\n\n  [clite]\n  backend = "local"\n  ...`
- bad TOML → `clite: <path>: invalid TOML: <tomllib message>`
- missing backend → `clite: <path>: [clite].backend is required (name of a [backends.<name>] table)`
- undefined backend → `clite: <path>: backend "x" selected but no [backends.x] table defined`
- bad posture → `clite: <path>: [clite].posture must be one of local, cloud (got "x")`
- bad kind → `clite: <path>: [backends.x].kind must be one of openai_compatible, claude_agent_sdk, openai_codex_sdk (got "y")`

## Steps (ordered)

1. **pyproject** — add the TOML backport to `[project.dependencies]`:
   `dependencies = ["tomli>=2.0; python_version < '3.11'"]`. (Keeps 3.10 support; 3.11+
   uses stdlib `tomllib`.)
2. **Tests first (TDD)** — write `tests/test_config.py` covering the DoD cases below; run
   to confirm they fail (red).
3. **Implement `clite/config.py`** — conditional import:
   ```python
   import sys
   if sys.version_info >= (3, 11):
       import tomllib
   else:
       import tomli as tomllib
   ```
   then `default_config_path()`, the dataclasses/errors, and `load_config()` doing:
   read **in binary mode** (`with open(path, "rb") as f: tomllib.load(f)` — `tomllib`
   requires a binary handle) — `ConfigNotFoundError` on missing file, `ConfigError` on
   `tomllib.TOMLDecodeError` → validate `[clite]`
   → resolve+validate selected `[backends.<name>]` → validate `posture` → pass through
   `danger`/`cache`/`prices` → return `Config`. Get to green.
4. **`config.example.toml`** — repo-root sample matching the schema above (doubles as the
   error example and the siblings' reference).
5. **Verify** — `uv run pytest` green, `uv run ruff check` clean.

## Test strategy (high-value, in `tests/test_config.py`)

Using `tmp_path` (write TOML files) + `monkeypatch` (env vars). One assertion theme each:

1. Valid local config → `Config` with `posture == "local"`, `backend.kind == "openai_compatible"`,
   `base_url` populated.
2. Valid config selecting a cloud backend (`posture = "cloud"`, `kind = "claude_agent_sdk"`)
   → resolved correctly.
3. `posture` omitted → defaults to `"local"`.
4. Missing file → `ConfigNotFoundError`; message contains the searched path.
5. Malformed TOML → `ConfigError` mentioning the file.
6. Missing `[clite].backend` → `ConfigError` naming the field.
7. Selected backend with no `[backends.<name>]` table → `ConfigError` naming the table.
8. Invalid `posture` → `ConfigError` listing `local, cloud`.
9. Unknown backend `kind` → `ConfigError` listing the allowed kinds.
10. `default_config_path()` honors `$CLITE_CONFIG`, then `$XDG_CONFIG_HOME`, else
    `~/.config/clite/config.toml`.
11. Explicit `load_config(path=...)` bypasses env/default lookup.

(Not exhaustive permutations — one representative case per validation branch.)

## Risks & rollback

- **`tomllib` absent on 3.10** → mitigated by the `tomli` backport + conditional import;
  exercised by CI's Python.
- **Schema drift vs siblings (#26–#29, #31, #32, #34, #36)** — the field names are a fresh
  contract. *Main residual risk.* Mitigation: strict validation only on this issue's DoD
  surface (backend+posture); everything else passes through; schema documented here and in
  `config.example.toml` for review/alignment. Cheap to adjust (one module + tests).
- **Scaffold dependency (#24 unmerged)** — covered under *Sequencing*; implement after the
  pivot merges; rebase the branch.
- **Rollback** — additive only (new module, new test, one sample file, one dependency line;
  `cli.py` untouched). Revert the implement commit; nothing else regresses.
