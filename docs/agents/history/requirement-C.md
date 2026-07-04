# Requirement C — `tt history` viewer

> Binding context: [`DECISIONS.md`](./DECISIONS.md) (§ Viewer), [`tasks.json`](./tasks.json)
> (`req-C`, `spec-C1`, `spec-C2`). Do not re-litigate the locked decisions. One spec = one
> self-contained commit (per [AGENTS.md](../../../AGENTS.md)); build order within this scope is
> **C1 → C2** (serialized on `tinytalk/cli.py`).

## Context

Scope A persists every prompt→command outcome as dated-JSONL records under `XDG_STATE_HOME`
(`tinytalk/history.py`, `spec-A1`) and writes one record on every `tt` invocation (`spec-A3`). That
store is the substrate; Scope C is the **human front door** to it: a `tt history` subcommand that
lets the user **browse, audit, and reuse** past commands (the Atuin/Bash model — you find a past
command and get it back verbatim, ready to run).

Two consumers read the same store: this viewer (Scope C) and the prompt-mode ↑/↓ recall widget
(Scope D, which shells out to the `--porcelain` machine format from `spec-A3`). Scope C is the
interactive, human-readable path — it is **not** the porcelain and does not replace it.

Per DECISIONS § Viewer, the viewer is **fzf-first** with a full-record preview, and degrades to a
**plaintext numbered fallback** when `fzf` is absent. Dedup is **store-all, dedup-the-view only**,
exact-normalized (the same normalization as `cache.py:cache_key`, exposed as a helper by `spec-A1`);
**no vector/embedding dedup**. Selecting an entry **yields the command** (print/copy) — a subprocess
cannot inject text into the parent shell, so the viewer only emits the command string; injecting it
into the buffer is the widget's job (Scope D).

## Scope

- The view path on A3's existing `history` branch in `cli.main` that reads recent records via
  `history.read_recent` and shows them newest-first, with duplicates collapsed **in the view only**
  (`spec-C1`).
- A plaintext numbered listing — `id`, time, `prompt → command`, cost — used both directly (when
  `fzf` is absent) and as the fallback for the fzf path (`spec-C1`).
- An `fzf`-first interactive picker with a preview pane showing the full record, whose selection
  prints the chosen command to stdout, falling back to the C1 plaintext path when `fzf` is missing
  (`spec-C2`).

## Out of scope / Deferred

- The machine-readable recall format (`tt history --porcelain`) — owned by `spec-A3`.
- The prompt-mode ↑/↓ recall widget and any buffer injection — Scope D (`spec-D1`).
- Writing, retention, or rotation of records — Scopes A and B.
- Vector/semantic dedup — shelved in DECISIONS.
- Clipboard copy (`pbcopy`/`xclip`) as a distinct action — stdout is the substrate; see open
  questions.
- Editing, deleting, re-running, or filtering/searching records from the viewer — not in DECISIONS.

## Dependencies

- **Blocked by:** `spec-A1` (store + `read_recent` + the view-dedup helper), `spec-A3` (records
  actually written, the `history` branch / `build_history_parser` / `_history` skeleton with
  `--porcelain`, and `tests/test_cli_history.py` created). `spec-C2` is additionally blocked by
  `spec-C1`.
- **Blocks:** nothing outside Scope C. (Scope D reads the store independently via the porcelain.)

## Definition of Done

- `tt history` lists retained records newest-first with duplicates collapsed in the view; the
  plaintext path works with `fzf` absent; when `fzf` is present the user gets an interactive pick
  with a full-record preview and the selection emits the command. Verification: **unit** for the
  render / dedup / fallback / preview-format / selection-emit functions and the `cli.main` wiring;
  **manual** for the live interactive fzf pick (fzf is interactive and not unit-tested directly).

---

## spec-C1 — `tt history` command + view-dedup + plaintext fallback

**One commit.** Touches `tinytalk/cli.py`, `tests/test_cli_history.py` (extends the file created by
`spec-A3`).

### Context

The first slice of the viewer: **extend A3's existing `history` subcommand** (its dispatch branch,
`build_history_parser`, and `_history` handler already exist and serve the `--porcelain` path) with
the human-readable view — query the store, collapse duplicates in the view, and render the plaintext
numbered listing. This listing is the **fallback** that `spec-C2`'s fzf path reuses, so it must be
factored as a reusable function — C2 calls it when `fzf` is absent rather than re-implementing it. No
fzf in this commit; `tt history` here always renders plaintext.

### Scope

- **Extend A3's existing `_history` handler and `build_history_parser()`** — do **not** add a new
  `history` branch or re-create the parser/epilog entry (those are A3's). Add the default (no
  `--porcelain`) view path to `_history` for the interactive+plaintext render, alongside A3's
  porcelain path. Keep heavy imports inside the handler (cold-start budget, per the `cli.py` module
  docstring).
- `_history` loads config only if needed for the store location, resolves the history dir via
  `history.default_state_dir()` (from `spec-A1`), and reads records newest-first via
  `history.read_recent(...)`.
- Collapse duplicates **in the view only** using the view-dedup helper exposed by `history.py`
  (`spec-A1`, same normalization rule as `cache.py:cache_key`): keep the **newest** occurrence
  (records already arrive newest-first), drop later repeats. The on-disk store is untouched.
- Render a plaintext, one-line-per-record numbered listing with columns **`id`**, **time**,
  **`prompt → command`**, **cost** (`cost_usd`), newest first. Factor the rendering into a standalone
  function (e.g. `_render_plaintext(records) -> str`) that `spec-C2` reuses as its fallback.
- Empty store (or missing history dir) → a friendly one-line message (e.g. `tt: no history yet`) and
  exit `0`; do not error.

### Out of scope / Deferred

- Any `fzf` invocation, preview pane, or interactive selection — `spec-C2`.
- Any new record fields or store APIs — consume `spec-A1` as-is.
- The `history` dispatch skeleton + `--porcelain` flag are A3's; C1 builds on them.

### Dependencies

- **Blocked by:** `spec-A1`, `spec-A3`.
- **Blocks:** `spec-C2`.

### Definition of Done

Verification: **unit** (`tests/test_cli_history.py`, run with `uv run python -m pytest`).

### Acceptance checks

- [ ] `tt history` on a populated store prints records newest-first, one per line, with columns
      `id`, time, `prompt → command`, and cost.
- [ ] Two records whose commands are equal under the `spec-A1` view-dedup helper (e.g. differing only
      in surrounding/collapsible whitespace or case, per the `cache_key` normalization) collapse to a
      single line; the on-disk JSONL is unchanged (store-all preserved).
- [ ] The dedup keeps the newest occurrence (its `id`/time), not an older duplicate.
- [ ] `tt history` on an empty/absent store prints a friendly message and exits `0`.
- [ ] The plaintext rendering lives in a standalone function that a later spec can call directly
      (no fzf/tty coupling).

### Sub-tasks

- [ ] Extend A3's `_history` with the default view path (A3 owns the branch, `build_history_parser`,
      epilog entry): resolve dir, `read_recent`, view-dedup, render, empty-state.
- [ ] `_render_plaintext(records)` reusable renderer.
- [ ] Tests: newest-first ordering, dedup-collapses-view + store-unchanged, keep-newest,
      empty-state.

---

## spec-C2 — fzf interactive picker + preview

**One commit.** Touches `tinytalk/cli.py`, `tests/test_cli_history.py`. Serial after `spec-C1`
(both edit `cli.py`).

### Context

Layer the interactive picker on top of C1. When `fzf` is on `PATH`, `tt history` opens an fzf picker
over the (view-deduped) records with a preview pane showing the **full record**; choosing an entry
prints its command to stdout. When `fzf` is absent, it falls back to C1's plaintext listing
unchanged. Selection emits **only the command** — the reusable substrate — because a subprocess
cannot inject into the parent shell (that is Scope D's job).

### Scope

- Detect `fzf` via `shutil.which("fzf")` (the established binary-detection pattern in this repo,
  e.g. `grounding.py`, `validate.py`). Absent → call C1's `_render_plaintext` path and return.
- Present → spawn `fzf` over the deduped records (one selectable line per record, newest-first),
  with a **preview** pane rendering the highlighted record's full detail: **prompt, command,
  explanation, model, tokens (`usage.total_tokens`), cost (`cost_usd`), time (`ts`)** (per DECISIONS
  § Viewer / `spec-C2`).
- Factor the per-record preview text into a standalone function (e.g.
  `_render_preview(record) -> str`) so it is unit-testable independently of fzf. The mechanism that
  feeds it to fzf's `--preview` (a hidden preview subcommand keyed by `id`, or an equivalent
  per-line preview source) is an implementation choice, provided the observable behavior holds.
- On selection, print the chosen record's `command` verbatim to **stdout** (nothing else on stdout),
  exit `0`. On no selection / fzf cancel (Esc, non-zero exit), print nothing to stdout and exit `0`.
- `tt` must **not** run the selected command — it only emits it (non-negotiable per AGENTS.md).

### Out of scope / Deferred

- Buffer injection / widget behavior — Scope D.
- Multi-select, search-scoping flags, or re-running the command.
- Clipboard copy as a separate action — see open questions.

### Dependencies

- **Blocked by:** `spec-C1` (reuses `_render_plaintext` as the fallback and the deduped record list).
- **Blocks:** nothing.

### Definition of Done

Verification: **unit** for `fzf`-absent fallback, `_render_preview` output, and the
selection→command emission (drive with `fzf` stubbed / `shutil.which` patched — mirror the
stub-a-binary approach already used in the suite); **manual** for the live interactive pick with
preview (fzf is a real TTY program). Run tests with `uv run python -m pytest`.

### Acceptance checks

- [ ] With `fzf` absent (`shutil.which` returns `None`), `tt history` produces exactly C1's
      plaintext listing (byte-identical to the C1 path) and exits `0`.
- [ ] `_render_preview(record)` includes the record's prompt, command, explanation, model, total
      tokens, cost, and time.
- [ ] Given a stubbed fzf that "selects" a known line, `tt history` prints that record's `command`
      verbatim (and nothing else) to stdout and exits `0`.
- [ ] Given a stubbed fzf that cancels (non-zero exit / empty selection), stdout is empty and the
      exit code is `0`.
- [ ] `tt` never executes the selected command (no `subprocess`/shell run of the command string).
- [ ] Manual: on a machine with `fzf`, `tt history` opens an interactive picker; moving the cursor
      updates the preview with the full record; Enter prints the command.

### Sub-tasks

- [ ] `shutil.which("fzf")` gate → fallback to `_render_plaintext` when absent.
- [ ] `_render_preview(record)` full-record formatter.
- [ ] fzf invocation wiring (picker + `--preview`) and selection→stdout emission.
- [ ] Tests: fzf-absent fallback, preview content, selection emits command, cancel emits nothing.

---

## Open questions

These are genuinely underspecified in DECISIONS.md; flagged rather than guessed.

1. **View-dedup key field.** `spec-A1` owns the dedup helper; C1 only calls it. DECISIONS ties dedup
   to "the normalization in `cache.py:cache_key`" (lowercase + whitespace-collapse), while Scope D
   says ↑/↓ walk "deduped past **commands**". Confirm the helper keys on the (normalized) `command`
   field. Note: the same A1 `dedup_key` helper is now **also** used by the `spec-A3` porcelain (per the
   2026-07-04 review), so the viewer and the porcelain dedup identically — one helper, one
   normalization. Note the tension: `cache_key`'s normalization lowercases, which is lossy for
   case-sensitive commands (`LS -LA` vs `ls -la`). If commands should dedup case-sensitively, `A1`'s
   helper needs a command-appropriate variant — a decision that belongs to A1, surfaced here because
   C's view is where it becomes visible.
2. **How many records `tt history` shows.** `read_recent(n)` takes a count; DECISIONS gives no
   default and no user-facing `--limit`/`-n` flag. Options: read effectively all retained records
   (naturally bounded by Scope B's 7-day / 15 MB retention) vs. a fixed default window vs. a
   `--limit` flag. Proposed default for building: read all retained records; add `--limit` only if
   wanted.
3. **Clipboard copy vs stdout.** DECISIONS says selection "yields the command (print/copy)". Treated
   here as stdout-only (the substrate). Confirm whether a clipboard copy (`pbcopy`/`xclip`) is
   wanted as well, or if stdout is sufficient (the Scope D widget is the real reuse path).
4. **fzf preview mechanism.** Feeding `_render_preview` to fzf's `--preview` typically needs a hidden
   per-`id` preview subcommand (e.g. `tt history --preview <id>`) or an equivalent per-line source.
   The spec fixes the observable behavior and leaves the mechanism open; confirm whether a hidden
   preview flag on `tt history` is acceptable.
