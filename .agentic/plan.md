# Plan — #35: S4 — shell integration (zsh)

> Part of #25 (epic S4 in [PRD.md](../docs/agents/PRD.md) §8/§13). This issue delivers **only**
> the `?`-prefix → editing-buffer mechanic and the CLI seam the shell talks to. The model engine
> (S1), grounding (S3 grounding), and validation/danger ladder (S3) are **out of scope** and arrive
> in their own issues.

## Goal & scope

When a zsh user types `? <plain English>` and presses Enter, CLITE must place the command CLITE
returns into the line editor (`$BUFFER`) for review — and **never execute it**. The user reads it,
maybe edits it, then presses Enter again to run it (a normal, non-`?` line).

To do that we need two things this issue provides:

1. **A CLI seam** — a real `clite ask` command with a tiny, documented stdin/stdout/stderr/exit-code
   contract, so the shell has a concrete thing to call and S1 has a clear place to plug the engine.
2. **The zsh integration** — a ZLE widget that intercepts `?`-prefixed lines on Enter, calls
   `clite ask`, replaces `$BUFFER` with the returned command, and surfaces notes/errors via
   `zle -M` — without ever invoking `accept-line` on the generated command.

### In scope
- `cmd/clite/main.go` — minimal binary entrypoint dispatching the `ask` subcommand.
- `internal/cli/ask.go` — the `ask` command implementing the I/O contract, behind a small injectable
  `Asker` seam (the engine is *not* implemented here; the default returns a clear "engine not wired
  yet" error so the binary is honest until S1 lands).
- `shell/clite.zsh` — the sourceable zsh integration (ZLE widget + key binding + a testable core
  function).
- Tests: a Go test for the CLI contract (runs in existing CI) and a zsh scripted check for the widget
  core + the never-execute safety invariant (run locally/manually; zsh is absent from CI and we may
  not touch `.github/`).
- A short usage note (`shell/README.md`) covering how to source the integration.

### Out of scope (explicit non-goals — separate issues / later S4 work)
- The NL→command engine, prompt template, structured-output parse, provider routing (**S1**).
- Curated toolset / grounding and the validation + danger-classification ladder (**S2/S3**).
- `preexec` session-history capture and secret redaction (PRD §8, separate follow-on).
- A visible `?`-mode prompt indicator / `RPROMPT` sign (PRD §8, separate follow-on).
- bash/fish integration (zsh only for v1).
- Modifying any CI workflow under `.github/`.

### DoD interpretation note
The issue's "places a *validated* command in the buffer" is read faithfully: the **shell layer**
faithfully transports the command CLITE emits into the buffer and never runs it. *Validation* of that
command is CLITE's (S3's) responsibility; once S1/S3 land, the command flowing through this seam is the
validated one. This issue is verifiable today at the transport level (against the seam / a stub `clite`)
and end-to-end once the engine exists.

## The CLI seam (contract) — `clite ask`

The shell calls the binary; the binary owns command generation. The contract:

- **Invocation:** `clite ask -- "<request>"`. All args after `ask` (and after an optional `--`) are
  joined with single spaces to form the request. If no request args are given, the request is read
  from **stdin** (entire stdin, trimmed). `--` guards requests that start with `-`.
- **stdout:** on success, exactly the command to insert into the buffer, followed by a single newline.
  stdout carries **only** the command — nothing else — because the widget copies stdout verbatim into
  `$BUFFER`.
- **stderr:** human-facing notes (future: explanation + danger label) on success, or the error message
  on failure. The widget shows stderr via `zle -M`. Never inserted into the buffer.
- **exit code:** `0` = success, command on stdout. Non-zero = failure; widget keeps the user's
  original `? …` line and shows stderr.
- **Engine seam:** `ask` depends on an `Asker` interface (`Ask(ctx, request) (Result, error)`,
  `Result{Command, Explanation string}`). The production default is `notWiredAsker`, which returns a
  clear error (`"clite: command engine not wired yet (see S1)"`) → non-zero exit. Tests inject a fake
  `Asker`. S1 replaces the default with the real engine; **no other code changes** are needed in the
  shell or the `ask` plumbing.

## Steps (ordered)

1. **Create the binary + CLI plumbing (the seam).**
   - `internal/cli/ask.go`:
     - `type Result struct { Command, Explanation string }`
     - `type Asker interface { Ask(ctx context.Context, request string) (Result, error) }`
     - `type notWiredAsker struct{}` returning the "not wired yet" error.
     - `func Run(ctx context.Context, args []string, stdin io.Reader, stdout, stderr io.Writer, a Asker) int`
       — parses the request (args-after-`--` joined by spaces, else stdin), **trims surrounding
       whitespace** (so the empty-request check matches the zsh widget's `${…## }` trim), calls
       `a.Ask`, writes `Command` to stdout (+`\n`), writes `Explanation` (when present) to stderr,
       returns `0`; on error writes `"clite: "+err` to stderr and returns `1`. Empty request (after
       trim) → usage line to stderr, return `2`.
   - `cmd/clite/main.go`: parse `os.Args`; for `ask`, call `cli.Run(context.Background(), args, os.Stdin, os.Stdout, os.Stderr, defaultAsker)`; unknown/empty subcommand → short usage to stderr, exit non-zero. Keep it dependency-free (stdlib only).
   - Verify `go build ./...` produces a buildable `clite` (`go build -o /tmp/clite ./cmd/clite`).

2. **Write the zsh integration (`shell/clite.zsh`).**
   - Header comment: what it does + `source /path/to/clite.zsh` in `~/.zshrc`.
   - Resolve the binary via `${CLITE_BIN:-clite}` (lets tests/users point at a build or stub).
   - **Testable core** — a plain function (no ZLE), so the bulk is unit-testable without a pty:
     ```zsh
     _clite_query() {
       # $1 = request. Prints command on stdout; returns clite's exit status.
       "${CLITE_BIN:-clite}" ask -- "$1"
     }
     ```
   - **ZLE accept widget** — the thin shell-specific wrapper:
     ```zsh
     _clite_accept_line() {
       if [[ $BUFFER == '?'* ]]; then
         local req=${${BUFFER#\?}## }            # strip leading '?' and spaces
         if [[ -z $req ]]; then
           zle -M -- "clite: type '? ' then what you want"
           return 0                               # do NOT accept-line
         fi
         local cmd err ret tmp=${TMPPREFIX:-/tmp/clite}.$$.$RANDOM
         cmd=$(_clite_query "$req" 2>"$tmp"); ret=$?
         err=$(<"$tmp"); command rm -f "$tmp"
         if (( ret == 0 )) && [[ -n $cmd ]]; then
           BUFFER=$cmd; CURSOR=${#BUFFER}         # insert for review — never run
           [[ -n $err ]] && zle -M -- "$err"
         else
           zle -M -- "clite: ${err:-no command returned}"
           # leave the original '? …' line so the user can retry/edit
         fi
         return 0                                  # critical: never accept-line on a '?' line
       fi
       zle .accept-line                            # normal lines behave normally
     }
     zle -N _clite_accept_line
     bindkey '^M' _clite_accept_line               # Enter
     bindkey '^J' _clite_accept_line               # Ctrl-J / numeric-keypad Enter
     ```
   - Guard against double-sourcing (e.g. `(( ${+functions[_clite_accept_line]} ))` early-return or a
     load-guard variable).
   - **Safety invariant in code:** the `?` branch always `return 0` and only ever assigns `$BUFFER`;
     it must never call `zle .accept-line`, `eval`, or execute `$cmd`.

3. **Go test for the CLI contract — `internal/cli/ask_test.go`** (runs in CI).
   - Fake `Asker` returning a fixed `Result{Command:"ls -la", Explanation:"list files"}`.
   - Success: `Run` with `args=["ls","files"]` → stdout == `"ls -la\n"`, stderr contains
     `"list files"`, return `0`.
   - Request via stdin (no args) → same.
   - `--` guard: `args=["--","-rf something"]` is treated as the request, not flags.
   - Failure: fake `Asker` returning an error → stdout empty, stderr has `"clite: "`, return `1`.
   - Empty request (no args, empty stdin) → return `2`, usage on stderr.
   - Optional: assert `notWiredAsker` returns a non-nil error mentioning the engine.

4. **zsh scripted check — `shell/clite_test.zsh`** (run locally/manually; self-skips if zsh absent).
   - Run with `zsh -f`. If `$ZSH_VERSION` unset / not zsh, print SKIP and exit 0.
   - Create a **stub `clite`** on a temp dir, pointed to by `CLITE_BIN`, whose `ask` prints a known
     command (`echo CLITE_RAN_<sentinel>`-style) to stdout. Source `shell/clite.zsh`.
   - **Transport test:** `out=$(_clite_query "show files")`; assert `out` equals the stub's command.
   - **Never-execute invariant (the safety-critical check):** make the stub's emitted command, *if it
     were ever executed*, create a sentinel file; after calling `_clite_query`, assert the sentinel
     does **not** exist — proving the command string was captured, not run.
   - **Failure path:** stub exits non-zero → `_clite_query` returns non-zero and prints nothing usable.
   - **(Optional, local-only) end-to-end ZLE smoke via `zsh/zpty`:** spawn interactive `zsh -f` in a
     pty, source the widget + stub, send `? show files` then Enter, and confirm the command sits in the
     editor and nothing executed. Marked best-effort (pty tests can be flaky); the function-level
     checks above are the required gate.
   - Exit non-zero on any failed assertion.

5. **Docs — `shell/README.md`.** Brief: build (`go build -o clite ./cmd/clite`, put on `PATH`),
   `source /path/to/shell/clite.zsh` in `~/.zshrc`, usage (`? what you want` → Enter → review →
   Enter), the `CLITE_BIN` override, and the current limitation (engine lands in S1; until then `clite
   ask` reports "not wired"). Optionally add one line to the top-level README "How it'll work"
   pointing here.

## Test strategy (high-value, TDD)

- **Automated (CI, Go):** `internal/cli/ask_test.go` is the enforceable gate — it pins the seam the
  shell relies on (stdout = command only, stderr = notes/errors, exit codes, stdin vs args, `--`
  guard). Write these first (red), then implement `ask` (green).
- **Manual/local (zsh):** `shell/clite_test.zsh` proves the two things that matter for the shell layer:
  the command is transported verbatim into the buffer, and it is **never executed** (sentinel check).
  This is the "scripted check" from the issue's done-when; the "manual smoke" is running it (and,
  optionally, the zpty end-to-end) in a real zsh.
- Not testing: model output quality, grounding, danger classification (other issues).

## Risks & rollback

- **CI cannot run the zsh test (zsh not installed; `.github/` is off-limits).** Mitigation: the
  enforceable contract is covered by the Go test, which *does* run in CI; the zsh check is a
  documented, self-skipping local script. Residual risk: the widget can regress without CI catching it
  — accepted for this issue and called out for a future CI change (outside this issue's `.github`
  boundary).
- **ZLE binding edge cases** (other plugins also rebinding `^M`, e.g. zsh-syntax-highlighting,
  fzf): we rebind `accept-line` via a named widget and fall through to `zle .accept-line` for non-`?`
  lines, minimizing interference. Documented; users source CLITE after such plugins.
- **Safety invariant (never execute).** Enforced structurally: the `?` branch only assigns `$BUFFER`
  and always `return 0`; the never-execute sentinel test guards against regressions. This is the one
  invariant we will not compromise.
- **Seam churn when S1 lands.** Low: S1 only swaps `defaultAsker` for the real engine; the contract,
  the widget, and both tests stay put.
- **Rollback:** all new files (`cmd/clite/`, `internal/cli/`, `shell/`) and additive — delete them /
  revert the commit; nothing existing is modified except (optionally) one README line.

## Dependencies

- **S1 (core engine):** required for real end-to-end `? …` output. Until it lands, `clite ask`
  returns "engine not wired yet" and the widget shows that via `zle -M`; the buffer mechanic and tests
  are exercised against the seam / a stub. This is the cleanest split that keeps #35 scoped to shell
  integration without prematurely building the engine.
