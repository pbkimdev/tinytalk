# Plan — #68 widget: surface backend/transport failures instead of "no valid command (try rephrasing)"

## Goal & scope

When the configured backend is dead or misconfigured (HTTP 500, connection refused,
timeout, auth/rate-limit, or a broken config), the zsh widget currently prints the same
fixed line as a genuine model-couldn't-answer result:

> `clite: no valid command (try rephrasing; check \`clite\` on the CLI)`

That advice is wrong for a dead backend — no phrasing fixes it. The CLI already tells the
two apart on **stderr** (`clite: no valid command: endpoint returned HTTP 500; …`), but
`clite --widget` writes diagnostics to stderr (which the widget discards) and emits nothing
machine-readable on **stdout**, so the widget can't branch.

**In scope**

1. `clite --widget` classifies each failure and, on a **backend/config fault**, emits two
   shlex-quoted assignments on **stdout** the widget can branch on:
   `clite_error=<transport|config>` and `clite_message=<human-readable, backend-naming text>`.
   Exit code stays non-zero; stderr diagnostics stay exactly as today.
2. The `NoValidCommand` exception carries a `kind` (`"transport"` vs `"no_command"`) set by
   the tier controller, so the CLI can distinguish "every attempt died at the provider" from
   "a model responded but produced no usable command".
3. The zsh widget branches on `clite_error`: when set it shows `clite_message` (the
   backend-fault message); otherwise it keeps the existing rephrase message.

**Explicitly out of scope**

- No change to the CLI's human (non-`--widget`) output or exit codes.
- No change to `--json` output shape.
- No retry/reconnect logic, no new config keys, no changes to provider adapters.
- No change to the genuine no-command path's stdout: it still emits nothing (so "no output
  → rephrase" stays literally true).
- Nothing under `.github/`.

## Definition of Done

Measurable acceptance criteria, smallest verification that proves each:

- **A. Tier classification (unit — `tests/test_tiers.py`).** With no escalation and a provider
  that always raises `ProviderError`, `NoValidCommand.kind == "transport"`. With a validator
  that rejects every (well-formed) suggestion, `NoValidCommand.kind == "no_command"`.
- **B. CLI widget contract (unit — `tests/test_shell.py`).**
  - Transport fault: `main(["--config", cfg, "--widget", "…"])` returns `1`, and **stdout**
    contains `clite_error=transport` and a `clite_message=` value that (a) names the backend
    (`local`) and (b) is safely `eval`-able in `zsh` (round-trips through `shlex`/`zsh -c`).
  - Genuine no-command: same call returns `1` and **stdout** contains **no** `clite_error=`
    (rephrase stays owned by the widget).
- **C. Widget script stays valid (unit — `tests/test_shell.py::test_widget_script_is_valid_zsh`).**
  `zsh -n` accepts the updated `clite.zsh`.
- **D. End-to-end widget display (tmux harness — `.claude/skills/test-shell-ui`).** A stub that
  **exits 1 while printing** `clite_error=transport` + `clite_message=…` → widget shows the
  backend-fault message; a stub that **exits 1 with no output** → widget shows the existing
  rephrase message. (Run when a TTY/tmux is available; not part of the pytest gate.)
- **E. Live check.** Pointing `defaults.backend` at a dead port and pressing `?`→query→Enter in
  a real zsh yields the backend-fault message (naming the backend) in the widget.

The automated gate is **A + B + C** (`uv run pytest`, `zsh -n`); **D + E** are the
issue's end-to-end/live acceptance, run via the skill/manually.

## Design

Failure taxonomy at the CLI boundary (`clite/cli.py::_run`) and the widget message per kind:

| Python failure | `kind` | widget stdout | widget shows |
|---|---|---|---|
| `ConfigError` | `config` | `clite_error=config` + short message | `clite_message` |
| `NoValidCommand(kind="transport")` | `transport` | `clite_error=transport` + message naming backend + fault | `clite_message` |
| `NoValidCommand(kind="no_command")` | — | *(nothing on stdout)* | rephrase (unchanged) |
| any other `Exception` | `transport` | `clite_error=transport` + message naming backend | `clite_message` |

**Why classify in the tier controller (not by sniffing `__cause__` in the CLI):** the
controller already knows whether a tier produced a parseable suggestion. It catches
`(FormatError, ProviderError)` at both tiers; only it can say "every attempt died at the
provider, no model output came back." An explicit `kind` field is self-documenting and
unit-testable, and keeps `ProviderError` knowledge out of `cli.py`.

**Classification rule:** `kind = "transport"` **iff** the final failing tier raised a
`ProviderError` **and** no tier ever produced a parseable suggestion (`last is None`);
otherwise `"no_command"`. This makes `"transport"` mean "we never got a usable model
response — the backend itself failed," which is exactly the rephrase-won't-help case. A
`FormatError` (degradation ladder exhausted — the model *did* answer, just unparseably) and
any validation rejection both stay `"no_command"`.

**Security — `eval` safety:** the widget already `eval`s `clite --widget` stdout. Every
`clite_*` value the CLI emits — including `clite_error` and `clite_message`, whose text is
derived from exception/HTTP-body strings — MUST go through `shlex.quote`, so the eval is
inert regardless of the fault text. This is a hard requirement of the implementation.

## Steps

### 1 — `clite/tiers.py`: give `NoValidCommand` a `kind`
- `NoValidCommand.__init__(self, problems, last=None, *, kind="no_command")`; store
  `self.kind = kind`. (Keyword-only with default → existing positional callers and
  `eval/runner.py`'s `exc.last`/`str(exc)` usage are unaffected.)
- Line ~169 (T2 raised `FormatError|ProviderError`):
  `raise NoValidCommand(problems + (str(exc),), last, kind=("transport" if isinstance(exc, ProviderError) and last is None else "no_command")) from exc`.
- Line ~182 (T2 produced a suggestion that failed validation): leave as-is → `kind`
  defaults to `"no_command"`.

### 2 — `clite/cli.py`: emit a machine-readable error kind in `--widget` mode
- Add a small helper near `_run`:
  ```python
  def _emit_widget_error(kind: str, message: str) -> None:
      import shlex
      print("\n".join((
          f"clite_error={shlex.quote(kind)}",
          f"clite_message={shlex.quote(message)}",
      )))
  ```
- In `_run`, track a best-effort backend label so error messages can name it:
  initialise `backend_label = args.backend or "default"` before the `try`, and set
  `backend_label = backend_cfg.name` right after `backend_cfg = config.backend(args.backend)`.
- Rework the three `except` arms (keep every existing stderr print and the `--json` branch
  byte-for-byte; only *add* the widget stdout emission):
  - `except ConfigError as exc:` → after the existing stderr print, if `args.widget`:
    `_emit_widget_error("config", "clite: config problem — check your clite config (run `clite …` for details)")`
    (a short one-liner; do **not** dump the multi-line `ConfigError` text into `zle -M`).
    Note: unlike transport, this message does **not** name the backend — a `ConfigError` is
    often not backend-scoped (missing file, bad TOML, unknown backend) and its full text is
    multi-line; the widget points the user at the CLI for detail. Acceptable deviation from the
    issue's literal "names the backend" wording; the primary repro is transport, which does.
  - `except NoValidCommand as exc:` → keep the existing stderr print and `--json` block. Then,
    if `args.widget and exc.kind == "transport"`:
    `detail = exc.problems[-1] if exc.problems else str(exc)` and
    `_emit_widget_error("transport", f"clite: backend {backend_label!r} unreachable — {detail} (check the server or defaults.backend)")`.
    For `kind == "no_command"` emit nothing on stdout (unchanged).
  - `except Exception as exc:` → keep the existing stderr print; if `args.widget`:
    `_emit_widget_error("transport", f"clite: backend {backend_label!r} failed — {type(exc).__name__} (check the server or defaults.backend)")`.
- Success path (`if args.widget:` block) is unchanged.

### 3 — `clite/shell/clite.zsh`: branch on `clite_error`
Replace the failure check + eval in `_clite_accept_line` (currently lines ~82–87). New order:
```zsh
    local clite_command clite_danger clite_explanation clite_error clite_message
    [[ -n "$out" ]] && eval "$out"   # shlex-quoted assignments emitted by `clite --widget`
    if [[ -n "$clite_error" ]]; then
      # Backend/config fault: rephrasing can't help — show the named fault, stay in AI mode.
      zle -M "$clite_message"
      return 0
    fi
    if [[ $rc -ne 0 || -z "$clite_command" ]]; then
      zle -M "clite: no valid command (try rephrasing; check \`clite\` on the CLI)"
      return 0
    fi
    _clite_ai_off
    # …rest of the success path (histchars swap, BUFFER set, danger comment, final zle -M)…
```
Notes: declare all five locals up front so nothing leaks between invocations; success is now
keyed on `clite_command` being set (not on non-empty `out`), since error output is now also
non-empty. Both error branches end with `zle -M …; return 0` at end-of-widget — same safe
`zle -M` placement the file already documents; AI mode is deliberately left on so the user
can edit and retry.

### 4 — Tests (TDD: write first, watch them fail, then implement 1–3)
- `tests/test_tiers.py`:
  - Extend `test_both_tiers_provider_error_raises_no_valid_command` to assert
    `exc.value.kind == "transport"`.
  - Extend `test_both_tiers_failing_raises_with_history` to assert `exc.value.kind == "no_command"`.
- `tests/test_shell.py` (mirror the `stubbed_cli` fixture pattern):
  - `test_widget_transport_error_names_backend`: a `StubProvider(Capabilities(), boom)` whose
    `complete` raises `ProviderError("endpoint returned HTTP 500")`; assert `main([... "--widget" ...]) == 1`,
    stdout contains `clite_error=transport` and a `clite_message=` naming `local` and `HTTP 500`.
    When `zsh` is present, `eval` the stdout in `zsh -c` and assert `$clite_error`/`$clite_message`
    round-trip (proves shlex safety).
  - `test_widget_no_command_stays_rephrase`: a provider that returns un-parseable text on
    **every** call — use the **callable** `StubProvider` form (`lambda request, i: Completion(text="not json")`),
    NOT a fixed list. T1+T2 make 4 `complete()` calls; a short list pops `IndexError`, which
    escapes as a generic `Exception` → mis-emitted as `transport` and silently breaks the
    assertion. Every rung then fails with `FormatError` (`last is None`, not a `ProviderError`)
    → `kind="no_command"`; assert exit `1` and stdout contains **no** `clite_error`.

### 5 — Make the tmux harness able to model "exit 1 with output" (for DoD D)
The current `clite-stub` can only `exit 0`-with-output or `exit 1`-with-no-output; the
transport case needs `exit 1`-**with**-output. Add a backward-compatible sidecar to
`.claude/skills/test-shell-ui/scripts/clite-stub`: after printing `$CLITE_STUB_RESPONSES/$n`,
if a sibling `$CLITE_STUB_RESPONSES/$n.rc` exists, `exit "$(cat …)"`; otherwise `exit 0` (today's
behaviour). Then document the two-file scenario (a response file with
`clite_error=transport`/`clite_message='…'` plus a `.rc` of `1`; and a missing file for the
no-output case) and the `drive.zsh`-style keystrokes to run it. This step supports the
manual/skill verification of D+E; it is not part of the pytest gate. (Allowed: it's under
`.claude/`, not `.github/`.)

## Test strategy (high-value, not exhaustive)

- **Unit, tier layer:** the two `kind` assertions (A) — cheapest proof the taxonomy is right.
- **Unit, CLI boundary:** the two widget-contract tests (B) — the real seam the widget reads;
  the `zsh -c` round-trip doubles as the shlex-safety proof.
- **Static:** `test_widget_script_is_valid_zsh` already guards `zsh -n` (C) — no new test needed.
- **E2E/live (manual/skill):** the tmux scenario (D) and the dead-port live check (E) prove the
  ZLE display branch, which has no unit-test surface.

Not adding: exhaustive per-exception permutations, or a committed tmux pytest (needs a TTY).

## Risks & rollback

- **`eval` of error text.** Mitigated by `shlex.quote` on every emitted value and the `zsh -c`
  round-trip test. This is the one security-relevant line; the widget already trusts CLI stdout.
- **Mis-tagging `kind`.** The `and last is None` guard keeps `"transport"` narrow (only when no
  model output ever came back). Residual: the rare combo "T1 produced a command that failed
  validation **and** T2's backend then died" is tagged `no_command` — acceptable (a command *was*
  produced, so "rephrase" is not actively misleading). The primary repro (dead backend, no
  escalation) is unambiguously `transport`.
- **Widget branch regression.** The success path is now keyed on `clite_command` rather than
  non-empty `out`; `test_widget_output_is_shell_evalable` + the tmux harness guard against a
  broken happy path and against overdraw/redraw regressions.
- **Rollback.** Three small, self-contained edits (`tiers.py`, `cli.py`, `clite.zsh`) plus tests
  and the stub tweak; revert the commit to restore prior behaviour with no migration.
