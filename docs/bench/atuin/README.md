# Compare Atuin AI with TinyTalk

This harness captures commands from [Atuin AI](https://docs.atuin.sh/cli/ai/introduction/) and grades
them with TinyTalk's execution oracle. It is a behavioral comparison over local fixtures, not a
claim that the two products have identical context, models, or safety policy.

## Fairness boundary

TinyTalk's assertion DSL knows how TinyTalk usually phrases a command. Applying those text assertions
to another product would reward shared spelling rather than correct behavior.

The comparison therefore uses only fixture-backed targets from `tinytalk/eval/oracle.py`. Each command
runs in a disposable sandbox and is judged by normalized output or resulting filesystem state. The
harness reads `CASES` dynamically, so its target count follows the oracle instead of a number copied
into this document.

## What capture does

Atuin's interactive `?` key can conflict with TinyTalk's widget, so the script drives
`atuin ai inline --hook <request>` directly in a tmux pane. For every prompt it:

1. starts a separate Atuin process in a PTY;
2. waits for a suggestion;
3. sends Tab to choose **insert**, not run;
4. parses Atuin's `__atuin_ai_insert__:<command>` token;
5. writes the command to JSON.

Capture never executes the suggestion. A new process is used for each fixture, although Atuin's
server-side session behavior can still differ from TinyTalk's independent backend requests.

## Prerequisites

- zsh and tmux;
- an Atuin build with `atuin ai inline`;
- an authenticated Atuin Hub session;
- Python dependencies for the TinyTalk source checkout.

Confirm Atuin AI works interactively once before starting a field capture.

## Capture

From the repository root:

```sh
docs/bench/atuin/capture.zsh \
  -o docs/bench/atuin/atuin-commands.json
```

`ATUIN_GEN_WAIT` controls the maximum wait per prompt and defaults to 25 seconds. `COLS` and `ROWS`
control the tmux pane. If one result is blank, inspect the capture before increasing the timeout;
authentication or a changed UI token can look like a slow model.

Keep the captured JSON as raw evidence. Do not edit commands to make them executable.

## Report

Compare the capture with already-recorded TinyTalk rows:

```sh
python -m tinytalk.eval.atuin report \
  --captured docs/bench/atuin/atuin-commands.json \
  --results docs/bench/2026-07-05/results.json
```

`--results` does not call TinyTalk's model backends. It reuses their recorded commands and grades both
products with the same oracle cases. To inspect the current target IDs and English prompts:

```sh
python -m tinytalk.eval.atuin prompts
```

## Interpret the result carefully

- Atuin is Hub- and network-dependent; service or model changes can move results without a harness
  commit.
- UI capture is more fragile than an API. A blank or truncated command is a delivery failure and may
  reflect the capture seam.
- Atuin may use context or retrieval that TinyTalk does not, while TinyTalk grounds and validates
  against the local host. This report compares final commands on fixtures, not architecture.
- The oracle reflects the scoring host's shell and userland. Record the OS and commit with every
  comparison.
- A single field capture is directional evidence. Repeat it before claiming a stable difference.

## Offline harness self-test

`stubbin/atuin` emulates `atuin ai inline` with canned commands from `responses/`. It verifies tmux
launch, polling, Tab insertion, token parsing, JSON writing, and reporting without Atuin Hub or a
network call:

```sh
docs/bench/atuin/capture.zsh \
  --stub docs/bench/atuin/responses \
  -o /tmp/atuin-selftest.json

python -m tinytalk.eval.atuin report \
  --captured /tmp/atuin-selftest.json \
  --label atuin-stub \
  --results docs/bench/2026-07-05/results.json
```

The canned responses are plumbing fixtures, not benchmark results. Run this self-test after changing
the capture script, token parser, fixture order, or oracle case set.
