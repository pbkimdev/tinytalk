# Plan — #58 one-command installer (`install.sh`)

> Phase: plan · Branch: `agentic/issue-58` · Size: S · Risk: low
> Deliverable: `install.sh` (repo root) + `tests/test_install.py`. One self-contained commit.

## Goal & scope

Collapse first-machine setup from four manual steps (`uv tool install`, hand-write
`~/.config/clite/config.toml`, wire the zsh `?` widget, discover `clite init zsh` exists)
into a single `./install.sh` run from a clone. It **installs and configures only** — it
never runs a generated command, never `eval`/`source`s anything into the running shell,
and never edits a dotfile without consent (VISION "hands control back"; PRD §2 "No auto-run").

**In scope**
- `install.sh` at the repo root, POSIX `sh` (`set -eu`), runnable as `./install.sh`:
  1. Resolve the repo dir from the script's own path; install the CLI from it —
     `uv tool install --force "<repodir>"`, falling back to `pipx install --force "<repodir>"`;
     if neither exists, exit non-zero with an actionable message (link to uv install docs).
  2. Verify `clite --version` resolves; if `clite` isn't on `$PATH`, print exactly what to
     add (`export PATH="$HOME/.local/bin:$PATH"`, and mention `uv tool update-shell`).
  3. Scaffold `~/.config/clite/config.toml` **only if missing** (never overwrite), from the
     committed `config.toml.example` when present, else an embedded byte-identical copy.
  4. Offer to append a marker-guarded `eval "$(clite init zsh)"` block to `~/.zshrc` —
     consent-prompt by default, `--yes` non-interactive, `--no-rc` to skip; **idempotent**
     (marker guard ⇒ re-running never duplicates). Self-skip with a note when the installed
     `clite` doesn't support `init zsh` yet (support-checked by exit status).
  5. Print next steps (`? <request>`, `clite eval`) and the uninstall hint
     (`uv tool uninstall clite` / `pipx uninstall clite` + remove the rc block). Execute nothing.
- `tests/test_install.py` — pytest drives the script via `subprocess` against a sandbox
  `HOME` + a hermetic stub `bin/` (fake `uv`/`pipx`/`clite`), asserting install invocation,
  config scaffold, no-overwrite, rc idempotency, `--no-rc`, self-skip, no-installer failure,
  and POSIX syntax.

**Out of scope / deferred** (verbatim from the issue): installing uv/pipx themselves; a
`curl | sh` remote one-liner (repo is private → run from a clone); shells other than zsh;
Homebrew formula / PyPI publishing; a `--uninstall` flag.

## Key design decisions (please confirm the starred ones on review)

1. **⭐ Config default: `local`, not "Claude default".** The issue prose says "Claude backend
   default", but PRD §2/§10 and VISION mandate **local-first default**, and the config that
   actually landed in #30 (`config.toml.example` + `clite/config.py:DEFAULT_CONFIG`) uses
   `backend = "local"` with a `claude` cloud backend table present, cache on, and a price
   table. Resolving the contradiction in favour of PRD/VISION + #30 consistency: the
   scaffolded config is **byte-identical to the committed `config.toml.example`**, which
   already contains every element the issue's list asks for (Ollama-ready `local` entry ✓,
   `claude` backend present ✓, `[cache] enabled = true` ✓, `[prices."…"]` table ✓). This
   keeps a single source of truth and avoids a second, drifting copy.

2. **⭐ Single source of truth for the starter config.** #30 keeps `config.toml.example`
   (repo root) byte-identical to `clite/config.py:DEFAULT_CONFIG` (guarded by a drift test).
   The installer runs from a clone, so `config.toml.example` is on disk — the installer
   **copies it** when present. **Note:** `config.toml.example` is *not* on `main` today (it
   arrives with #30, which #58 does not list as a blocker), so on this branch the **embedded
   heredoc is the operative copy**; the copy path activates automatically once #30 merges. The
   embedded heredoc must use a **single-quoted delimiter** (`<<'CFG'`) so the config body's
   `$XDG_CONFIG_HOME` / `$CLITE_CONFIG` / `~` tokens are written literally (byte-identity). A
   test asserts the embedded fallback equals `config.toml.example` **only when that file is
   present**, so the fallback can't silently drift once #30 lands.

3. **Branch is `agentic/issue-58`** (from `.agentic/context/object.json`). The human plan
   comment named `feat/install-script`; the engine contract's branch wins.

4. **rc wiring self-skips today.** `clite init zsh` is not on `main` yet (it lands with #35,
   currently only on `v1/core-pipeline`, where `clite init zsh` prints the widget script to
   stdout on success and `usage: clite init zsh` + non-zero otherwise). The installer
   support-checks with `clite init zsh >/dev/null 2>&1`; on today's skeleton that returns
   non-zero, so step 4 self-skips with a note. The installer writes only the literal
   `eval "$(clite init zsh)"` line into `~/.zshrc` for the user's next login — it never
   evaluates that line itself, honouring "never sources into the running shell".

## Definition of Done (measurable)

- `sh -n install.sh` is clean (POSIX-sh compatible).
- On a machine with a (stubbed) uv: `./install.sh --yes` installs from the repo dir, yields
  a resolvable `clite --version`, scaffolds a config, and writes **exactly one** rc block;
  a **second** `./install.sh --yes` changes nothing (config untouched, rc block still single).
- An existing `config.toml` is never overwritten (pre-existing bytes preserved).
- No uv and no pipx ⇒ non-zero exit with an actionable message.
- The script never executes a generated command and never `source`s/`eval`s clite output
  into the running shell (it only writes files + prints).
- `pytest tests/test_install.py` is green; `ruff check tests/test_install.py` clean.
- Manual smoke on the maintainer's Mac (real uv) — noted for the human, not automatable in CI.

## Steps (ordered; files/functions named)

1. **`install.sh`** (new, repo root) — `#!/bin/sh` + `set -eu`, organised as small functions:
   - `usage()` and arg parse: `--yes` (assume-yes / non-interactive), `--no-rc`, `-h|--help`;
     reject unknown flags with `usage` + non-zero.
   - `SCRIPT_DIR` via pure parameter expansion (`d=${0%/*}; [ "$d" = "$0" ] && d=.;
     SCRIPT_DIR=$(CDPATH= cd -- "$d" && pwd)`) — minimises external-tool deps for hermetic tests.
   - `install_cli()`: `command -v uv` ⇒ `uv tool install --force "$SCRIPT_DIR"`; elif
     `command -v pipx` ⇒ `pipx install --force "$SCRIPT_DIR"`; else `die` with the uv-install URL.
   - `verify_cli()`: `command -v clite` ⇒ run `clite --version`; else warn + exact PATH line.
   - `scaffold_config()`: `CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/clite"`; if
     `config.toml` exists → note "keeping existing" and return; else `mkdir -p`, then copy
     `"$SCRIPT_DIR/config.toml.example"` if present else write the embedded single-quoted
     heredoc (`<<'CFG'`, byte-identical to #30's `config.toml.example`).
   - `wire_zshrc()`: return early on `--no-rc` (note); support-check `clite init zsh`; if
     unsupported → note + return; `RC="$HOME/.zshrc"`; if marker already in `$RC`
     (`[ -f "$RC" ] && grep -q "$MARKER" "$RC"` — guarded so a missing `.zshrc` doesn't trip
     `set -e`) → note "already wired" + return; consent (`--yes` ⇒ yes; non-tty & not `--yes` ⇒ skip with note;
     else prompt `[y/N]`); append the marker-guarded block:
     `# >>> clite (added by install.sh) >>>` / `eval "$(clite init zsh)"` /
     `# <<< clite (added by install.sh) <<<`.
   - `print_next_steps()`: `? <request>`, `clite eval`, uninstall hint (incl. removing the rc block).
   - `main "$@"` orchestrates the above in order.
2. **`tests/test_install.py`** (new) — `_sandbox(tmp_path)` helper building a fake `HOME`,
   a stub `bin/` (`uv`, `pipx`, `clite`) whose stubs append their argv to a log file, plus a
   `_run(args, *, env)` wrapper around `subprocess.run(["/bin/sh", INSTALL_SH, *args], …)`.
   Stub `clite` prints `clite 0.0.1` for `--version`; a `zsh`-supporting variant prints a
   dummy widget for `init zsh` (exit 0), an unsupporting variant prints usage + exit 1.
3. **Verify**: `sh -n install.sh`, `ruff check tests/`, `pytest tests/test_install.py`.

## Test strategy (high-value, TDD — write these first)

| Test | Asserts |
|---|---|
| `test_syntax_is_posix_clean` | `sh -n install.sh` exits 0 |
| `test_installs_from_repo_dir_via_uv` | stub-uv log shows `tool install --force <repodir>` |
| `test_falls_back_to_pipx_when_no_uv` | no uv on PATH → stub-pipx log shows `install --force <repodir>` |
| `test_no_installer_fails_with_message` | no uv & no pipx → non-zero exit, message names uv |
| `test_scaffolds_config_when_missing` | `~/.config/clite/config.toml` created; equals the embedded starter config (and, **only when `config.toml.example` is present on disk**, equals that file too) |
| `test_never_overwrites_existing_config` | pre-seed sentinel config → bytes unchanged after run |
| `test_rc_block_written_once_and_idempotent` | zsh-supporting stub, two `--yes` runs → marker block count == 1; pre-existing `.zshrc` content preserved |
| `test_no_rc_flag_skips_rc` | `--no-rc` → `.zshrc` absent/unchanged, exit 0 |
| `test_rc_self_skips_when_init_zsh_unsupported` | unsupporting stub → no rc block, note in output, exit 0 |
| `test_script_does_not_source_or_eval_at_runtime` | static guard: script body contains the literal `eval "$(clite init zsh)"` only inside the heredoc it writes, and no `source`/`.`-into-running-shell of rc/clite output |

The no-installer test uses a **hermetic** `PATH` (sandbox `bin` only, no real uv/pipx leak);
positive tests prepend the sandbox `bin` to the real `PATH` so stubs shadow real tools while
coreutils stay available.

## Risks & rollback

- **Config drift vs #30.** Mitigated by copying the committed `config.toml.example` at
  runtime and a parity test on the embedded fallback. If #30's schema changes later, the copy
  path stays correct automatically; only the fallback needs a refresh (the parity test flags it).
- **Issue prose vs local-first default.** Called out above (decision ⭐1) for human confirmation
  on the PR; the plan follows PRD/VISION + shipped #30.
- **rc step unverifiable end-to-end until #35 merges.** The support-check + self-skip make the
  installer correct on today's `main`; the "supported" path is exercised via a stub, and the
  real widget is covered by manual Mac smoke once #35 lands.
- **Stubs don't prove real `uv` behaviour.** DoD includes a manual Mac smoke; CI proves
  orchestration/idempotency/safety, not uv internals.
- **Rollback:** delete `install.sh` and `tests/test_install.py`. No product runtime code is
  touched, so removal is total and safe.

## Verification summary

**Rounds used: 1** (verdict PASS; no re-verify needed — the fixes below are precise and
already applied to this plan).

A verifier subagent checked the plan against (1) requirements and (2) feasibility:
- **Requirements:** all five Scope steps, all five Acceptance checks, and both DoD legs are
  covered by a named function/test or an explicit manual-smoke note; no scope creep. The one
  deviation — scaffolding `backend = "local"` rather than the issue's "Claude backend default"
  — is a *correct* resolution of an issue↔PRD contradiction (PRD §2 `docs/agents/PRD.md:31`
  "local-first default"; shipped #30 uses `backend = "local"`), surfaced as decision ⭐1 for
  human sign-off.
- **Feasibility (all three core claims CONFIRMED against files):** #30's `config.toml.example`
  is byte-identical to `clite/config.py:DEFAULT_CONFIG` with `backend = "local"` + `claude`
  table + `[cache] enabled = true` + `[prices]` (drift-guarded by #30's own test);
  `clite init zsh` prints the widget to stdout on `v1/core-pipeline` and returns non-zero on
  today's `main` skeleton, so the exit-status support-check self-skips correctly; branch is
  `agentic/issue-58` per `object.json`.
- **Fixes folded in from the verifier:** (a) `config.toml.example` is absent on `main`, so the
  embedded heredoc is the operative config copy on this branch and the scaffold test compares
  against it (parity vs `config.toml.example` only when that file is present); (b) the embedded
  heredoc uses a single-quoted delimiter to preserve `$…`/`~` byte-for-byte; (c) the rc
  marker `grep` is guarded (`[ -f "$RC" ] &&`) so a missing `~/.zshrc` doesn't trip `set -e`.

**Residual risk (low):** the rc "supported" path can't be exercised end-to-end until #35
lands `clite init zsh` on `main` — covered by a stub in CI and by the maintainer's manual Mac
smoke afterward. The starter config's true single-source-of-truth (copy from
`config.toml.example`) only fully engages once #30 merges; until then the byte-identical
embedded heredoc stands in.
