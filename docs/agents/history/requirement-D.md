# Requirement D — Prompt-mode ↑/↓ command recall

> Binding source: [`DECISIONS.md`](./DECISIONS.md) → **Recall widget (Scope D)**; graph node `req-D`
> and its single leaf `spec-D1` in [`tasks.json`](./tasks.json). Terms (**prompt mode**, **badge**,
> **widget**, **buffer**) are used as defined in [`GLOSSARY.md`](../../../GLOSSARY.md). This doc is the
> requirement + the one spec body; it does not re-litigate the locked decisions.

## Context

TinyTalk's **prompt mode** (a.k.a. AI mode, `_TT_AI_MODE`) is entered by pressing `?` on an empty line:
the prompt gains the animated **badge**, what you type is sent to `tt` on Enter, and the validated
command replaces the editing **buffer** for review (`tinytalk/shell/tt.zsh`, PRD §8 shell integration,
#35). Today ↑/↓ inside prompt mode do whatever the user's default zsh bindings do (shell history) —
there is no way to reuse a command TinyTalk previously generated without regenerating it.

`tt history` (this feature) now persists every prompt→command outcome (Scope A). The recall widget is
the **prompt-mode consumer** of that store: it brings the **Atuin / Bash model** into AI mode — ↑
recalls a *past command verbatim*, ready to run — so a user who already asked for "list files by size"
last week can press ↑ and get the exact command back instead of paying another model round-trip.

The command surface it reads is the machine-readable porcelain added by **spec-A3**
(`tt history --porcelain`, NUL-delimited recent commands). This requirement wires that porcelain into
the zsh widget; it adds no new persistence and no new CLI subcommand.

## Scope

- Inside prompt mode, bind ↑/↓ to a recall widget that walks **past generated commands** (deduped)
  into `BUFFER`, verbatim.
- Load the command list **once per prompt-mode session** by shelling out to `tt history --porcelain`
  on the first arrow press; cache the parsed array for the rest of the walk.
- A recalled command is **command verbatim** and, once accepted, runs as a real command (it is not
  re-sent to the model) — "exits prompt mode ready to run".
- Leaving prompt mode by **any** path restores the default ↑/↓ behavior and drops the recall cache.
- Verified **empirically** via `.claude/skills/test-shell-ui` (tmux + a stubbed `tt`).

## Out of scope / Deferred

- The `tt history --porcelain` command itself and any persistence/retention — those are **spec-A3**
  and Scope A/B.
- The interactive `tt history` viewer / fzf picker (**Scope C**). Recall is the in-prompt-mode path;
  the viewer is the standalone-command path.
- Semantic / fuzzy search over history (Atuin-style filter-as-you-type). Recall is a linear walk only;
  vector/semantic dedup is **shelved** per DECISIONS.
- Changing history storage, record shape, or dedup normalization.

## Dependencies

- **Blocked by:** `spec-A1` (store) and `spec-A3` (`tt history --porcelain` porcelain contract) — the
  widget has nothing to read until both land.
- **Blocks:** None (Scope D is a leaf consumer).
- **Parallel with:** `spec-C1` (build wave 3) — disjoint files (`tt.zsh` vs `cli.py`).

## Definition of Done

- All of `spec-D1`'s acceptance checks pass.
- Verification level: **manual / empirical** via `test-shell-ui` (ZLE redraw + binding behavior does
  not exist outside a real terminal; the pytest suite cannot observe it), plus a static
  `zsh -n tinytalk/shell/tt.zsh` parse gate. Run the scenario under both the real `~/.zshrc` and
  `zsh -f`.

## Specs (one commit each)

- [ ] **D1 — Prompt-mode ↑/↓ recall widget** (below). Scope D has exactly one spec; this requirement
  is done when D1 is.

## References

- [`DECISIONS.md`](./DECISIONS.md) → *Recall widget (Scope D)*, *Deferred / rejected* (semantic dedup
  shelved).
- [`tasks.json`](./tasks.json) → `req-D`, `spec-D1` (touches `tinytalk/shell/tt.zsh`; depends on
  `req-D`, `spec-A1`, `spec-A3`).
- `tinytalk/shell/tt.zsh` — `_tt_ai_on`/`_tt_ai_off`, `_tt_question`, `_tt_backspace`,
  `_tt_accept_line`, `_tt_line_init` (the mode-toggle and delegate patterns to mirror).
- [`GLOSSARY.md`](../../../GLOSSARY.md) — prompt mode, badge, widget.
- `.claude/skills/test-shell-ui` — the tmux + stubbed-`tt` verification harness.

---

# Spec D1 — Prompt-mode ↑/↓ recall widget

**Scope:** D (prompt-mode recall). **Files (only):** `tinytalk/shell/tt.zsh`. **One commit.**

## Context

Extends `tinytalk/shell/tt.zsh`. The file already gates prompt-mode state through `_tt_ai_on` /
`_tt_ai_off`, and already uses the "bind globally, branch on `_TT_AI_MODE`, delegate to the builtin
otherwise" pattern for `?` (`_tt_question` → `.self-insert`) and Backspace (`_tt_backspace` →
`.backward-delete-char`). D1 adds the same shape for ↑/↓: recall while in prompt mode, default zsh
behavior otherwise. The command list comes from `tt history --porcelain` (spec-A3), which prints recent
commands **NUL-delimited** on stdout.

## Behavior contract (the checkable spec)

1. **Bindings.** ↑ and ↓ invoke a recall widget while in prompt mode; outside prompt mode they behave
   exactly as before. Cover the real terminal keysyms for cursor up/down in **both** cursor-key and
   application-keypad modes — CSI `^[[A` / `^[[B` **and** SS3 `^[OA` / `^[OB` (or the `terminfo`
   `kcuu1`/`kcud1` equivalents) — so recall works regardless of keypad state.
2. **Load once per session.** On the **first** ↑ press after entering prompt mode, shell out **once**
   to `command tt history --porcelain`, split its stdout on NUL into an array, and cache it. Subsequent
   ↑/↓ presses in the same prompt-mode session **must not** re-invoke `tt`.
3. **Walk.** With the list cached (newest-first), ↑ moves toward **older** commands and ↓ toward
   **newer**, replacing `BUFFER` with the selected command **verbatim** and putting `CURSOR` at end of
   buffer. ↓ past the newest entry restores the pre-recall buffer (the empty prompt-mode line).
4. **Deduped.** The walked sequence contains no exact-duplicate commands. `tt history --porcelain`
   (`spec-A3`) returns **pre-deduped** commands (collapsed via the A1 `dedup_key` helper), so the widget
   just walks them — it does no dedup of its own.
5. **Verbatim, ready to run.** A recalled command, when accepted, runs **as-is** — it is NOT sent back
   to the model (no thinking spinner, no `tt --widget` call). This is "exits prompt mode ready to run".
6. **Restore on leave.** Leaving prompt mode by any path — `?` toggle, Backspace on empty line,
   submitting, or the `line-init` reset (`_tt_ai_off`) — restores the default ↑/↓ behavior and drops
   the recall cache. Re-entering prompt mode reloads on the next first press.
7. **Graceful degradation.** If `tt history --porcelain` prints nothing (no history yet) or fails
   (non-zero / missing binary), ↑ leaves `BUFFER` unchanged and the widget does not error out. (Store
   is best-effort by design.)

## Out of scope / Deferred

- Any change to `cli.py`, `history.py`, or the porcelain format (owned by A3).
- Fuzzy/semantic recall, a preview pane, or scoping recall to `cwd` (recall is a plain linear walk over
  what porcelain returns).

## Dependencies

- **Blocked by:** `spec-A1`, `spec-A3`.
- **Blocks:** None.

## Definition of Done

- Verification level: **manual / empirical** via `.claude/skills/test-shell-ui`, plus
  `zsh -n tinytalk/shell/tt.zsh`. The test-shell-ui scenario must stub `tt history --porcelain`
  (NUL-delimited canned commands, e.g. via `TT_STUB_RESPONSES`-style fixture) in addition to the
  existing `tt --widget` stub, and drive real ↑/↓ keystrokes in a tmux pane. Run under both the user's
  `~/.zshrc` and `zsh -f`.

## Acceptance checks

- [ ] `zsh -n tinytalk/shell/tt.zsh` parses clean.
- [ ] In prompt mode, first ↑ populates `BUFFER` with the newest past command; a stubbed
      `tt history --porcelain` is invoked **exactly once** across an entire ↑↑↓↓ walk (assert via an
      invocation counter in the stub).
- [ ] Repeated ↑ walks to older commands; ↓ walks back toward newer; ↓ past the newest restores the
      empty prompt-mode buffer.
- [ ] The walk shows no exact-duplicate command twice (the `spec-A3` porcelain returns pre-deduped
      commands, so the widget need not dedup).
- [ ] Accepting a recalled command runs it verbatim: the `tt --widget` stub is **not** called and no
      thinking spinner appears (distinguishes recall from a normal prompt-mode submit).
- [ ] After leaving prompt mode (`?`, Backspace-on-empty, submit, or fresh prompt), ↑ triggers the
      **default** shell-history behavior, not recall; entering prompt mode again reloads the list.
- [ ] Outside prompt mode, ↑/↓ are unchanged.
- [ ] Empty / failing porcelain: ↑ in prompt mode is a no-op on `BUFFER` and raises no widget error.
- [ ] Scenario passes under both real `~/.zshrc` and `zsh -f` per `test-shell-ui`.

## Sub-tasks

- [ ] Add recall state globals (cached command array, walk index, per-session "loaded" flag) alongside
      the existing `_TT_*` state.
- [ ] Add `_tt_recall_up` / `_tt_recall_down` ZLE widgets; bind the ↑/↓ keysyms (CSI + SS3 / terminfo).
- [ ] Load-once: parse NUL-delimited `tt history --porcelain` on first press; degrade on empty/failure.
- [ ] Make accepting a recalled command run verbatim (see open question #2) and reset recall state.
- [ ] Reset/restore in `_tt_ai_off` (and confirm `_tt_line_init` path) so leaving prompt mode drops the
      cache and returns default arrows.
- [ ] Author the `test-shell-ui` recall scenario (stub porcelain + `--widget`); capture mid-walk and
      final screens.

## Implementation notes (non-binding)

- The `_tt_question` / `_tt_backspace` widgets are the model to copy: one globally-bound widget that
  branches on `(( _TT_AI_MODE ))` and delegates to the builtin (`zle .up-line-or-history` /
  `zle .down-line-or-history`, or the previously-bound widget) when not in prompt mode. This satisfies
  "restore default arrow bindings" by behavior without literally rebinding on every toggle; a literal
  `bindkey` save-at-init / restore-on-leave is the alternative if preferred.
- Setting `BUFFER`/`CURSOR` and letting normal redisplay run is enough; recall does not interact with
  the wave/spinner machinery.

## References

- `DECISIONS.md` → *Recall widget (Scope D)*; `tasks.json` → `spec-D1`.
- A3 porcelain contract: `tt history --porcelain`, NUL-delimited (see `req-A` / `spec-A3`).
- `tinytalk/shell/tt.zsh`; `.claude/skills/test-shell-ui`.

---

## Open questions (underspecified in DECISIONS — do not guess)

1. **Badge/exit timing.** Does prompt mode (the badge) clear the moment a command is recalled into
   `BUFFER`, or only when the recalled line is accepted? Both readings satisfy "exits prompt mode ready
   to run," but they differ in whether ↑/↓ can keep walking after the first recall. This spec assumes
   **walking continues while in prompt mode and mode exits on accept**; confirm.
2. **accept-line integration.** For a recalled command to run *verbatim*, `_tt_accept_line` must not
   send it to the model. Does D1 teach `_tt_accept_line` to detect a recalled buffer and run it
   verbatim (a small edit to that widget, still within `tt.zsh`), or does recall exit prompt mode so
   `_tt_accept_line` already falls through to `.accept-line`? Ties directly to #1.
3. **Where dedup happens — RESOLVED: porcelain pre-dedups (`spec-A3`).** Per the 2026-07-04 review,
   `tt history --porcelain` emits **pre-deduped** commands (collapsed via the A1 `dedup_key` helper —
   exact-normalized, keep newest), so the widget walks pre-deduped commands and does **no** dedup of its
   own. D1 (which touches only `tt.zsh` and cannot call `cache_key`) needs no in-widget dedup.
4. **Recall ordering/scope.** Is `tt history --porcelain` guaranteed newest-first, and is recall global
   or `cwd`-scoped? This spec assumes **global, newest-first**. Confirm (A3 concern that D1 consumes).
5. **List size.** Is there a cap on how many entries porcelain emits / the widget caches? None is
   specified; the widget currently assumes "whatever porcelain returns."
