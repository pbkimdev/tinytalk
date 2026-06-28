# Plan — S3: Validation & Safety (#34)

> Sub-issue of #25 (Python re-platform). Implements PRD §7 "Validation & safety ladder"
> (`docs/agents/PRD.md:98`) and the safety invariant (`docs/agents/PRD.md:113`). v1 thin-slice
> scope per PRD §13 (`docs/agents/PRD.md:182`): ladder steps 1–3 + danger classifier + a tiny,
> off-by-default native dry-run allowlist.

## Goal & scope

Add a **self-contained Python validation package** that takes the command CLITE is about to hand
the user and returns a verdict: does it parse, are its binaries real, and — independently of what
the model claimed — how dangerous is it. The package is the trust gate between the engine (#26)
and shell insertion (#35).

**In scope**
- New package `clite/validation/` exposing a pure function
  `validate(command: str, *, declared_danger=None, grounding=None, posture=None) -> ValidationResult`.
- An operator-aware command splitter (segments + words + redirections + nested substitutions) —
  *not* a `shlex` pass; `shlex` does not split on `;|&&` and mishandles `#`.
- Ladder step 1 **parse** (`zsh -n` → fall back `bash -n` → `unverified`), step 2 **binaries
  exist** (PATH/`shutil.which` + shell-builtin set + grounding hook), step 3 **flags exist**
  (best-effort; no-op when grounding is empty), step 5 **danger classification** (rule-based,
  authoritative).
- A minimal step 4 **native dry-run** allowlist that is **off by default**, posture-gated, and
  only ever uses a tool's own `-n`/`--dry-run` flag.
- `Danger` enum, `ValidationResult`/`Issue` dataclasses, and a `Grounding` protocol — exported so
  #26 (contract) and #35 (shell integration) import them rather than redefine them.
- Thorough, **hermetic** unit tests (no real shell, no network; filesystem only via `tmp_path`).

**Explicitly out of scope**
- Inserting into / reaching into the zsh buffer or deciding *how* to surface a verdict — that is
  S4 (#35). S3 stops at the `ValidationResult`.
- Building the grounding cache / help-text fetch — that is S2 (#33). S3 consumes grounding behind
  a small interface and degrades to a no-op when it is absent, so #34 is **not blocked** on #33.
- The model call / structured-output contract object — that is #26. S3 takes `command: str` and an
  optional `declared_danger`; it does not depend on the full contract type.
- Full native-dry-run execution coverage, secret redaction of session history, the eval harness.

### Dependency & branching note (sequencing)
S3 is **Python** and lands on the re-platformed tree. The Python scaffold (`clite/` package,
`pyproject.toml`, `tests/`) currently lives only on the unmerged pivot (`#24`,
`origin/pivot/python-replatform`); `main` still holds Go code that #24 deletes. **Hard
dependency: #24 merges first.** This plan branch (`agentic/issue-34`) is cut from `main` and
carries only `.agentic/plan.md` for review. When `implement` runs, it must base the code on the
#24 scaffold — rebase `agentic/issue-34` onto the merged pivot (or onto `origin/pivot/python-replatform`
if #24 is not yet on `main`) before adding the package, so `clite/validation/` sits beside the
existing `clite/cli.py` and `pyproject.toml`. Do **not** add Python files onto the Go-only `main`.

## Definition of Done

Measurable acceptance criteria — all proven by `pytest` (hermetic, no shell/network required):

1. **Destructive is always flagged.** For every command in the destructive corpus
   (`rm -rf /`, `dd if=… of=…`, `mkfs.*`, `truncate`, `git push --force`, `git reset --hard`,
   fork bomb `:(){ :|:& };:`, `sudo` that writes, `find … -delete`, `find … -exec rm …`,
   truncating redirect `>` over an existing file), `validate(...).danger == Danger.DESTRUCTIVE`
   and `.auto_run_blocked is True`. **Zero destructive false-negatives** is the hard invariant.
2. **Danger only escalates, never downgrades.** `max(model-declared, rule-derived)`: a command the
   model labels `"safe"` that is actually destructive comes back `DESTRUCTIVE`; a `"destructive"`
   declaration is never lowered by S3.
3. **Hidden destructive intent is caught.** Destructive commands inside compound lines
   (`echo hi; rm -rf /`, `ls && dd …`), command substitutions (`$(rm -rf /)`, `` `rm -rf /` ``),
   and **even when the surrounding line fails to parse** are still `DESTRUCTIVE` — danger
   classification runs unconditionally, independent of parse/binary outcome.
4. **Invalid commands are rejected.** A command that fails `zsh -n`/`bash -n` returns `ok is False`
   with a `parse` error issue; a command referencing a non-existent binary returns `ok is False`
   with a `binaries` error issue (shell builtins like `echo`/`cd` are not false-rejected).
5. **Graceful degradation.** With no shell available, syntax is `"unverified"` (never crash, never
   silently pass). With no grounding, flag-checking is a no-op (no spurious issues) and `ok` is
   unaffected. The whole test suite runs with **no `zsh` installed** (confirmed: CI has only
   bash/sh).
6. **S3 never auto-runs the user's command.** The only subprocess S3 spawns is the read-only
   `… -n` syntax check (stdin-fed, never executes) and, only when posture explicitly permits, an
   allowlisted native dry-run. Default posture executes nothing but the syntax check.

Smallest verification level that proves it: **unit tests** on the pure `validate()` API and its
helpers. No integration/e2e needed for this issue (the consumer seam is #35).

## Steps (ordered; TDD — test first per unit)

Package layout (new files under `clite/validation/`):

1. **`result.py` — types.**
   - `class Danger(IntEnum)`: `SAFE=0`, `CAUTION=1`, `DESTRUCTIVE=2` (IntEnum so `max()` gives the
     escalate-only semantics for free); `to_str()`/`from_str()` map to the contract strings
     `"safe"|"caution"|"destructive"` (PRD §5).
   - `@dataclass(frozen=True) class Issue`: `step` (`"parse"|"binaries"|"flags"|"danger"|"dry_run"`),
     `severity` (`"error"|"warning"|"info"`), `message`, `word: str | None`.
   - `@dataclass(frozen=True) class ValidationResult`: `command`, `ok: bool`, `danger: Danger`,
     `auto_run_blocked: bool`, `syntax: str` (`"ok"|"invalid"|"unverified"`),
     `issues: tuple[Issue, ...]`, `reasons: tuple[str, ...]`; `rejected` property = `not ok`.

2. **`lexer.py` — operator-aware splitter.** `split(command: str) -> list[Segment]` where each
   `Segment` carries `words: list[str]`, `redirects: list[Redirect]`, and `subcommands:
   list[str]` (text pulled from `$(...)`/backticks for recursive classification). Respects single/
   double quoting and backslash escaping; splits top-level on `; | || && & \n`; records redirection
   operator+target (`>`, `>>`, `<`, `<<`). Best-effort and crash-proof: malformed input yields a
   best-effort segment list, never an exception. *Tests first* (`test_lexer.py`): quoting keeps
   `echo "rm -rf /"` as one arg; `a; b && c | d` → four segments; `$(rm x)` and `` `rm x` ``
   surface `rm x` as a subcommand; `cmd > out` records the redirect.

3. **`parse.py` — syntax check.** `check_syntax(command, *, runner=_default_runner) -> str`. Default
   runner tries `shutil.which("zsh")` then `"bash"`, runs `[shell, "-n"]` with the command on
   **stdin** (so it is parsed, never executed) and a short timeout; rc 0 → `"ok"`, non-zero →
   `"invalid"`, neither shell present / runner error → `"unverified"`. `runner` is injectable so
   *tests are hermetic* (`test_parse.py`): fake runner returns ok/invalid; "no shell" → `"unverified"`.

4. **`binaries.py` — existence + grounding hook.** `Grounding` `Protocol`
   (`known_binary(name) -> bool | None`, `known_flags(binary) -> set[str] | None`). `BUILTINS`
   set (`cd echo : [ test true false export local read printf …`) so builtins are never flagged.
   `check_binaries(segments, *, which=shutil.which, grounding=None) -> list[Issue]`: for each
   segment's command word, if builtin → skip; elif `which(word)` resolves → ok; elif grounding
   says known → ok; else `Issue("binaries","error",…)`. Injectable `which` keeps tests hermetic
   (`test_binaries.py`).

5. **`flags.py` — best-effort flag check.** `check_flags(segments, *, grounding=None) -> list[Issue]`.
   When grounding has `known_flags(binary)`, extract `-x`/`--long` tokens for that binary and emit
   `warning` issues for unknown options; when grounding is `None`/empty → return `[]` (no-op).
   Warnings only — never affect `ok`. *Tests* (`test_flags.py`): degrades to no-op without grounding.

6. **`danger.py` — rule-based classifier (the core).**
   `classify(segments, command) -> tuple[Danger, list[str]]`. For every segment **and** every
   recursively-split subcommand, derive a per-command danger and take the **max** across all of
   them. Rules (over-warn; unknown command → `CAUTION`, never `SAFE`):
   - **DESTRUCTIVE**: `rm` with recursive+force (`-rf`/`-fr`/`-r -f`/`--recursive --force`) or `rm`
     targeting `/`/broad globs; `dd` with `of=`; `mkfs*`, `wipefs`, `shred`, `fdisk`, `parted`;
     `truncate`; `git push --force/-f`, `git reset --hard`, `git filter-branch`; fork bomb
     (regex `:\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:` plus generalized form); `find` with
     `-delete` or `-exec rm`; truncating redirect `>` whose target exists (best-effort
     `os.path.exists`, read-only); any `sudo` wrapping a mutating/destructive command.
   - **CAUTION**: mutating but recoverable — `mv`, `cp`, `chmod`, `chown`, `ln`, `mkdir`, `touch`,
     `kill`, package installs (`pip/npm/brew/apt … install`), `git commit/checkout/reset`(non-hard),
     append `>>`, single `>` to a new path; bare `sudo`; unknown command (conservative default).
   - **SAFE**: read-only allowlist — `ls du cat grep egrep fgrep find`(without `-delete`/`-exec rm`)
     `head tail echo printf pwd which whoami stat wc sort uniq cut awk sed`(no `-i`) `tr file df free top ps`.
   - **Backstop scan**: a final regex pass over **command-context** tokens (not the contents of
     quoted string args) for known destructive signatures, so a lexer miss still escalates danger.
     This is the belt-and-suspenders for the zero-false-negative invariant.
   *Tests first* (`test_danger.py`): the full safe/caution/destructive corpus from DoD §1–3,
   including the quoted-string negative case (`echo "rm -rf /"` is not destructive) and the
   redirect boundary (`> newfile` → `CAUTION`, never `SAFE`; `> existing` → `DESTRUCTIVE`).

7. **`dryrun.py` — minimal allowlist (off by default).** `ALLOWLIST = {"rsync": "-n", "git": …}`
   and `maybe_dry_run(segment, *, posture) -> Issue | None` that returns `None` unless posture
   explicitly enables dry-run **and** the command is on the allowlist. v1 ships the allowlist +
   gating logic and the native-flag selection; *tests* cover allowlist matching and the
   default-off behavior (no execution). Full execution coverage is intentionally minimal per PRD §13.

8. **`__init__.py` / `ladder.py` — orchestrate `validate()`.** Run: `syntax = check_syntax(...)`;
   `segments = lexer.split(command)`; collect `binaries` + `flags` issues; **always**
   `derived, reasons = danger.classify(...)`; `danger = max(derived, Danger.from_str(declared_danger) or SAFE)`;
   `ok = (syntax != "invalid") and not any(i.severity=="error")`;
   `auto_run_blocked = danger == Danger.DESTRUCTIVE`. Critically, danger classification is **not**
   short-circuited by a parse or binary failure (DoD §3). Export `validate, Danger, ValidationResult,
   Issue, Grounding` from `clite/validation/__init__.py`.

9. **`tests/validation/` + `test_validate.py`** — ladder integration over the pure API (DoD §1–6):
   destructive→blocked, invalid→`ok False`, destructive-and-invalid→still `DESTRUCTIVE`, declared
   vs derived max, safe-valid→`ok True`/`SAFE`/not blocked, no-shell→`unverified`, no-grounding→no
   flag noise. `pyproject.toml` already has `pytest>=8` in the dev group — no dependency changes;
   pure stdlib (`enum`, `dataclasses`, `subprocess`, `shutil`, `os`, `re`, `typing`).

## Test strategy (high-value, TDD)

Write the failing test before each unit (steps 2–8). Focus on the safety-critical surface, not
permutations:
- **`test_danger.py`** (highest value): curated safe / caution / destructive corpora; compound &
  substitution hiding; **declared-vs-derived escalation** (never downgrade); quoted-string
  negative. This is where the zero-false-negative invariant is proven.
- **`test_validate.py`**: the ladder wiring — `ok`, `auto_run_blocked`, `syntax`, and that danger
  survives parse/binary failure.
- **`test_parse.py` / `test_binaries.py` / `test_flags.py` / `test_lexer.py`**: each helper with
  injected `runner`/`which`/`grounding` so the suite is hermetic and runs with no zsh installed.
All tests stdlib-only; filesystem touched only through `tmp_path` (for the `>`-over-existing case).

## Risks & rollback

- **Lexer/grammar gaps → destructive false-negative (highest risk).** Shell grammar is large; a
  missed construct could slip a destructive command through. *Mitigations*: unknown command
  defaults to `CAUTION` (never `SAFE`); recurse into `$(...)`/backticks; broad destructive
  patterns; and the **backstop regex scan** so a structural miss still escalates. The corpus test
  is the regression guard — extend it whenever a gap is found.
- **Over-warning (false-positives) → UX noise.** Accepted and explicitly preferred by PRD §7
  ("better to over-warn"); thresholds are tunable in `danger.py`.
- **`zsh` absent in CI.** Handled by the `zsh→bash→unverified` fallback and injectable runner;
  tests never depend on a shell.
- **Sequencing on #24.** If implement runs before #24 merges, base on
  `origin/pivot/python-replatform` (see branching note); never add Python onto the Go-only `main`.
- **Rollback**: the change is purely additive (a new package + tests; `clite/cli.py` untouched),
  so reverting the single squash commit fully removes it with no behavioral regression.

## Verification summary

A verifier subagent checked this plan against (1) PRD/issue requirements and (2) feasibility of the
referenced files/APIs. Rounds and residual risks are recorded in the PR description.
