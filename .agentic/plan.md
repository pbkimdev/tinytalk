# Plan — #33 · S2: capability grounding

> Phase: `plan`. This file is the review surface and what `implement` will execute.
> Parent epic: **#25 (clite v1 — Python re-platform)**. Predecessor: **#24 (Go→Python pivot)**.

## Platform note (read first)

Issue #33 is a child of **#25**, the roadmap that **re-platforms clite from Go to Python**
(rationale in #24: clite needs in-process Claude Agent SDK + OpenAI Codex SDK, neither of
which has a Go binding). #25 explicitly *"Supersedes the Go-era issues"*. Therefore this work
is **Python**, built into the `clite/` package that #24 scaffolds — **not** Go.

**Sequencing dependency:** the Python scaffold lives on PR **#24** (`pivot/python-replatform`)
and is **not yet merged to `main`**; `main` still holds the Go tree (`go.mod`,
`internal/provider/**`). `implement` for #33 therefore needs the Python scaffold present.
Resolve by **landing #24 first**, then implementing #33 on top. If #24 is still open when
implement runs, base the implementation branch on `pivot/python-replatform` (rebase onto `main`
once #24 merges). `AGENTS.md` on `main` still says "Go" — that line is stale relative to the
#24/#25 pivot and updates when #24 lands; do not treat it as a directive to write Go here.

The plan doc itself commits cleanly on top of `main`; only the *code* in `implement` needs the
scaffold. This plan routes to `pending` (no `auto` label) so the owner approves direction and
sequencing before any code is written.

---

## Goal & scope

**Goal.** Give CLITE a grounding layer that knows, for *this host*, which binaries are installed
and which flags they actually support — and renders that into (a) a prompt block generation can
inject, and (b) a lookup the validation/generation path can query — so generated commands only
use flags that exist on the host (PRD §6 grounding, §7 ladder steps 2–3, §4 tiers T1/T2).

**In scope**
- **Binary detection (PATH grounding):** resolve a tool name to its host path; answer "is it
  installed?". Backs validation ladder step 2 ("binaries exist").
- **Flag/option discovery (on-demand):** fetch a tool's real `--help` (fallbacks `-h`, then
  `man`), parse out the option tokens it advertises, cache per tool. Backs ladder step 3 and
  tier T2's "fetch real `--help` for those tools". This is what makes flags *host-true* — BSD
  `du` vs GNU `du` divergence (PRD §15) is captured because we read the host's own help text.
- **OS facts:** a light fingerprint (`system`, GNU-vs-BSD `flavor`, `shell`) for the prompt block.
- **Grounding assembly (feed into generation):** a `Grounding` object exposing `prompt_block(tools)`
  (compact text for the T1/T2 system prompt) and `has_flag(tool, flag)` / `has_binary(tool)`
  lookups — the consumable seam generation (S1) and validation (S3) will call.
- **Deterministic unit tests** for all lookups, using injected fakes (no reliance on host binaries).

**Out of scope (explicitly)**
- The **generation pipeline / harness** itself (epic **S1**, not yet in the tree). #33 delivers the
  grounding *API and prompt block* generation will consume; wiring grounding into a live model call
  is S1's job. No `cli.py` behavior change is required for #33.
- The **validation & safety ladder / danger classifier** (epic **S3**). We provide the
  `has_binary` / `has_flag` primitives the ladder uses; we do not build the ladder.
- A full **curated static toolset catalog** seeded from `tldr` (PRD §6 / §14 list this under S2). We
  allow an optional one-line `purpose` per tool but do not seed a catalog here. **Note for the owner:**
  this deliberately *splits* the curated catalog out of S2 into a separate slice — it is being
  deferred, not silently dropped. #33 covers the host-capability (PATH + real-flag) half.
- **Persistent (on-disk) spec/doc cache** (epic **S5**). #33 caches in-process only.
- Full `$PATH` spec indexing, web doc lookup, semantic cache (PRD non-goals / deferred).

## Definition of Done

"Working" = the grounding lookups are correct and proven by deterministic unit tests that never
depend on which binaries happen to exist on the CI host. Smallest verification level: `pytest`.

Concretely, DoD holds when:
1. `clite/grounding.py` exists exposing: `Grounding.has_binary`, `Grounding.resolve`,
   `Grounding.spec`, `Grounding.has_flag`, `Grounding.os_facts`, `Grounding.prompt_block`, and a
   pure `parse_flags(help_text) -> set[str]` function. Pure stdlib only (no new runtime deps —
   `pyproject.toml` `dependencies` stays `[]`).
2. `tests/test_grounding.py` passes under `pytest`, with **every** test injecting a fake
   `which`/`runner` (no real subprocess), including the key proof:
   - GNU host: `du --help` text ⇒ `has_flag("du","--max-depth")` **True** and
     `has_flag("du","-d")` **True**; an invented flag ⇒ **False**.
   - BSD host: `du` usage shows a grouped cluster (e.g. `[-Aclnx]`) plus `-d`/`-h`/`-s` and no
     long opts ⇒ `has_flag("du","-d")` **True**, an individual flag from the cluster (e.g.
     `has_flag("du","-c")`) **True**, and `has_flag("du","--max-depth")` **False**. (Same tool,
     divergent flags, host-true; pins grouped-cluster expansion.)
   - `prompt_block(["du","sort"])` contains the real flags for installed tools and marks an
     uninstalled tool as not available.
3. `pytest` and `ruff check` are clean. Dev deps live in `pyproject.toml`'s
   `[dependency-groups] dev` (uv-managed), so `implement`/CI run them via `uv run pytest` and
   `uv run ruff check` (or after installing the dev group) — not a bare pip-default invocation.
4. `git grep` shows no edits under `.github/` and no new runtime dependency.

> Note on the issue's "a generated command only uses flags that exist on the host": the *end-to-end*
> form of that assertion requires the S1 generation harness, which does not exist yet. #33 delivers
> and unit-proves the **gate** that enforces it — the host-true flag lookup + the prompt block that
> constrains the model. Full end-to-end enforcement lands when S1 wires this in. This split is called
> out so review can accept the right verification level.

## Steps (ordered; TDD — tests first per the `tdd` skill)

All paths are under the #24 Python scaffold (`clite/` package, `tests/` dir, flat like
`clite/cli.py` + `tests/test_cli.py`).

1. **Red: flag-parsing tests.** Add `tests/test_grounding.py` with `parse_flags` cases first:
   GNU `du`/`sort`/`head` help snippets, a BSD `du` usage line, `-n, --lines[=N]` comma+`=`
   forms, and prose that must *not* yield flags (e.g. "command-line", a lone `-`/`--`). Run
   `pytest` → fails (no module).
2. **Green: `parse_flags`.** Implement the pure tokenizer in `clite/grounding.py`:
   - Short opts: `-` + single alnum, incl. **expanding grouped usage clusters** (`[-Aclnx]` →
     `-A -c -l -n -x`, `[-hsx]`, `-h -d1`) — required so the BSD `du` proof resolves individual
     short flags.
   - Long opts: `--` + word with internal `-` (`--max-depth`), stripping `=VALUE` / `[=VALUE]`.
   - Split comma/space/pipe-separated option lists (`-d, --max-depth`, `-H | -L | -P`).
   - Tolerant + over-collecting by design (favor *not* rejecting a real flag over precision;
     ladder step 3 is "best-effort" per PRD §7). Make `parse_flags` get the tests green.
3. **Detection + runner seams.** Add `OSFacts` and `ToolSpec` dataclasses and the `Grounding`
   class with injectable seams: `which=shutil.which`, `runner=_default_runner`, plus optional
   `os_facts` / `purposes` overrides for tests. `_default_runner(argv, timeout)` wraps
   `subprocess.run` capturing **stdout+stderr combined** (many tools print help to stderr / exit
   non-zero), with a bounded timeout, and never touches the target command other than
   `--help`/`-h`/`man`. Add `has_binary`/`resolve` over `which`.
4. **`spec()` + caching.** `spec(name)`: if not installed → `ToolSpec(installed=False)`; else fetch
   help (`--help` → `-h` → `man <tool>`, stop at first non-empty; run `man` non-interactively —
   set `MANPAGER=cat`/`PAGER=cat` (or `man --pager=cat`) so it can't block on a pager/TTY),
   `parse_flags` it, build
   `ToolSpec(installed, path, flags, help_discovered)`. Memoize per tool on the instance
   (on-disk cache deferred to S5). `has_flag(tool, flag)` = `flag in spec(tool).flags`.
5. **OS facts.** `os_facts()` from injected values or `platform.system()` + a small GNU-vs-BSD
   `flavor` heuristic (e.g. GNU coreutils accept `--version`; default `unknown` when undetectable)
   + `$SHELL` basename. Kept light; flag truth stays in the per-tool help, not here.
6. **`prompt_block(tools)`.** Render a compact, model-facing block: a `Host:` line from `os_facts`,
   then one line per requested tool listing its real flags (and optional `purpose`), with
   uninstalled tools clearly marked unavailable, plus an instruction to use only listed flags.
   This is the artifact generation (S1) injects — the "feed grounding into generation" deliverable.
7. **Round out tests.** Cover: injected-`which` detection; missing binary ⇒ not installed/no flags;
   `runner` called once across two `spec()` calls (cache); help-on-stderr + non-zero exit still
   parsed; runner timeout/exception ⇒ `installed=True, help_discovered=False, flags=∅`, no crash;
   `has_flag` true/false incl. unknown tool; `prompt_block` contents. Run `pytest` + `ruff check`.
8. **No CLI change.** Do **not** wire grounding into `cli.py` (that is S1). Optionally add a one-line
   module docstring noting the S1/S3 consumers. Keep the change additive: two files only.

## Test strategy (high-value, not exhaustive)

TDD via the `tdd` skill. The crown-jewel target is the pure `parse_flags` + the `has_flag` lookup,
because they are the gate behind "only real flags." Tests inject canned help text so they are
deterministic and cross-platform (pass on Linux CI and a Mac alike):

- **GNU vs BSD `du` divergence** — the headline correctness case (proves host-true flags).
- **parse_flags forms** — short, long, `=VALUE`/`[=VALUE]`, comma/pipe lists; prose yields nothing.
- **Detection** — injected `which`: installed vs not.
- **Help robustness** — stderr-only help with non-zero exit is still parsed; combine streams.
- **Failure containment** — runner timeout/exception ⇒ graceful empty spec, never raises.
- **Caching** — `runner` invoked once per tool across repeated `spec()`.
- **prompt_block** — lists real flags for installed tools, marks uninstalled ones.

No test shells out to a real binary; no network. Target: full `pytest` green + `ruff check` clean.

## Risks & rollback

- **Help-format fragility / over-collection.** Help text varies wildly; the tolerant parser may
  admit a token that is example text, not a real flag. Accepted for v1: over-collecting *allows* a
  flag rather than *rejecting* a real one (PRD §7 step 3 is explicitly best-effort), and the prompt
  block is advisory grounding. `ToolSpec.help_discovered` lets callers distinguish "no flags found"
  from "help unavailable" so S3 can choose strictness. Mitigation: keep `parse_flags` pure and
  table-tested so the heuristic is tunable in isolation.
- **Subprocess cost/safety.** Fetching help executes the tool with `--help`/`-h`/`man` only (read-
  only by convention), bounded by a timeout, streams combined, failures swallowed into an empty
  spec. We never run the generated command. Lookups are per-referenced-tool, not a `$PATH` sweep.
- **Sequencing on #24.** Largest risk is process, not code: #33 is Python but the scaffold is on the
  unmerged #24. Mitigation in the Platform note (land #24 first / base on its branch). This plan
  routes `pending` so the owner sequences before implement.
- **Rollback.** The change is purely additive — `clite/grounding.py` + `tests/test_grounding.py`,
  zero edits to existing files, zero new runtime deps. Revert = delete the two files. No migration,
  no data, no effect on `cli.py` or the provider seam.

## Verification summary

Internal verifier loop against (1) requirements — PRD §6/§7/§4 + issue #33 intent and parent #25 —
and (2) feasibility — referenced files/APIs exist and support the approach.

- **Rounds used: 1. Verdict: PASS, 0 blocking.** The verifier empirically confirmed every
  load-bearing claim: `shutil.which`, combined stdout+stderr capture, `subprocess` timeout /
  `FileNotFoundError` containment, the GNU-vs-BSD `du` flag divergence (GNU has `-d`/`--max-depth`;
  BSD has `-d` + a `[-Aclnx]`-style cluster and no long opts), and the regex flag parse.
- **Refinements folded in (non-blocking):** run tests via `uv run pytest` / `uv run ruff check`
  (dev deps live in `[dependency-groups]`); pin grouped short-cluster expansion with an explicit
  test assertion; flag the curated-catalog split to the owner so it isn't silently dropped; run
  `man` non-interactively (`MANPAGER=cat`).
- **Residual risk:** (1) sequencing — #33 is Python but the #24 scaffold is unmerged (mitigated:
  land #24 first / base on its branch; this plan routes `pending` for owner approval). (2)
  help-format heuristics over-collect by design — acceptable since the gate is best-effort (PRD §7)
  and `help_discovered` lets S3 choose strictness.
