# Atuin AI vs TinyTalk — behavioral head-to-head

Atuin AI ([docs](https://docs.atuin.sh/cli/ai/introduction/)) turns English into a
shell command. We capture its commands and grade them the same way TinyTalk grades
its own backends: with the **execution oracle** (`tinytalk/eval/oracle.py`), which
runs each command in an isolated sandbox and checks the result — stdout against a
golden, or the resulting **filesystem state** for side-effecting commands. That's
the only fair cross-tool grader; the suite's assertion DSL (`uses:awk` …) scores
*how TinyTalk phrased* a command, which a different tool shouldn't be judged by.

Both sides are scored on the oracle's behavioral fixtures — the `CASES` set
(18 at time of writing; the tooling reads it dynamically, so it tracks new
fixtures automatically).

## How the capture works

No TUI puppetry and no `?` key (so no conflict with TinyTalk's own `?` binding):
the `?` widget is just a wrapper around **`atuin ai inline`**, and we call that
directly. `atuin ai inline --hook "<request>"` seeds the request from its argument,
generates, and on **Tab** (insert) prints `__atuin_ai_insert__:<command>` to
stderr and exits. `capture.zsh` drives it in a tmux pane (it needs a PTY + one
keypress), polls stdout for the suggestion, presses Tab, and parses that token.
Each fixture is a **separate process**, so state can't bleed between prompts.
Nothing is ever executed by the capture.

## 1. Capture

Be logged in to Atuin Hub first (any one successful `?` / `atuin ai` call is
enough — AI is a Hub feature, separate from sync):

```sh
docs/bench/atuin/capture.zsh -o atuin-commands.json
```

Knobs: `ATUIN_GEN_WAIT` (max seconds to wait per prompt, default 25) · `COLS` /
`ROWS`. If a prompt comes back blank, bump `ATUIN_GEN_WAIT` and re-run.

## 2. Report

```sh
python -m tinytalk.eval.atuin report \
    --captured atuin-commands.json \
    --results docs/bench/2026-07-05/results.json
```

`--results` re-grades each TinyTalk backend's **already-recorded** commands with
the oracle (no models re-run), so `atuin-ai` sits next to `sonnet5-low`,
`gpt55-low`, etc. on identical footing. `prompts` prints the targets + English
prompts (TSV) if you'd rather capture by hand.

## What to read into it

- Atuin runs frontier models server-side, so its ceiling ≈ the frontier rows
  (`sonnet5-low` / `gpt55-low`). The signal is the **delta** those rows already
  set — whether Atuin's man-page/command-output retrieval layer adds anything.
- **Session continuity:** consecutive `atuin ai inline` calls share Atuin's
  short-lived AI session, so a prompt can see earlier ones as context. That's how
  the product behaves interactively, but it's a confound vs TinyTalk's
  independent-prompt eval — read the numbers as "Atuin as used," not context-free.
- **UI-scraped, Hub- and network-gated.** Directional head-to-head on *your*
  fixtures, not a stable CI metric.

## Offline self-test (no Hub, no network)

`stubbin/atuin` is a fake `atuin` that emulates `atuin ai inline` with canned
commands from `responses/` (Nth invocation → file N, in fixture order), so the
harness plumbing — launch, poll, Tab, token parse, JSON — is verifiable without
Atuin. `responses/` is rigged so `count-lines-code` (#1) and `awk-group-sum` (#6)
pass:

```sh
docs/bench/atuin/capture.zsh --stub docs/bench/atuin/responses -o /tmp/atuin-selftest.json
python -m tinytalk.eval.atuin report --captured /tmp/atuin-selftest.json \
    --label atuin-stub --results docs/bench/2026-07-05/results.json
```
