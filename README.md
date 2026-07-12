<div align="center">

# TinyTalk

**[English](README.md)** · [한국어](README.ko.md)

![TinyTalk turns a request into a command you can review](demo.gif)

**Say what you want. Get one command back. You decide whether it runs.**

TinyTalk turns a plain-language request into a shell command that fits the machine in front of you.
It validates the suggestion, puts it in your command line, and stops. Read it, edit it, run it, or
throw it away.

</div>

```text
? show me the five biggest folders here

du -sh ./* 2>/dev/null | sort -hr | head -n 5
          [safe] Shows the five largest entries in the current directory.
```

TinyTalk is deliberately smaller than a terminal agent:

- **It suggests; it does not execute.** The generated command always comes back to you.
- **It checks your machine.** Shell syntax, installed binaries, known long flags, and selected native
  dry-runs are checked before a command reaches the prompt.
- **It treats danger as UI, not fine print.** Destructive suggestions arrive commented out and must be
  deliberately uncommented.
- **It works with your model.** Use a Claude or Codex login, AWS Bedrock, Azure OpenAI, another
  OpenAI-compatible endpoint, or a local model.

## Start in a minute

### 1. Install

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/install.sh | sh
```

The installer selects the release for macOS or Linux on arm64 or x86_64, verifies the checksum when
the release provides one, and installs `tt` under `~/.local`. It asks before changing a shell rc file
and never overwrites an existing TinyTalk config.

In an interactive terminal, the installer then opens `tt setup`, a three-step wizard:

1. choose the command-explanation language; when a matching UI translation exists, the rest of the
   wizard switches to it too;
2. add the zsh widget, with your approval;
3. connect a model provider.

You can leave any step for later and run `tt setup` again at any time.

> The `?` interaction is a zsh widget. The regular `tt "..."` command works from bash and other
> shells too.

### 2. Open a new shell

On an empty command line, press `?`. The TinyTalk badge means you are in **prompt mode**. Type a
request and press Enter. The validated command replaces your request; TinyTalk does not press Enter
again for you.

No widget yet? The CLI follows the same contract:

```sh
tt "list files larger than 100 MB under this directory"
```

### 3. Review the result

Every result has a final danger level:

| Level | What TinyTalk does |
|---|---|
| `safe` | Inserts a read-only command for review. |
| `caution` | Inserts a command that may change state and labels it clearly. |
| `destructive` | Inserts the command commented out, with a warning. You must remove the comment yourself. |

TinyTalk can still be wrong. The label and validation ladder reduce easy mistakes; they do not turn
generated shell into trusted code. Read the command before running it.

## Choose a model

Run `tt auth` whenever you want to add, replace, or remove a provider. The wizard owns two named
**slots**: `primary` and an optional `fallback`. If the primary backend fails or produces a command
that does not pass validation, TinyTalk can retry with richer grounding and the fallback.

| Provider path | Best when | Authentication |
|---|---|---|
| Claude Agent SDK | You already use Claude Code | Existing `claude` login or `ANTHROPIC_API_KEY` |
| OpenAI Codex Agent SDK | You already use Codex | Existing local Codex CLI login |
| AWS Bedrock | Your organization uses AWS | Standard AWS credential chain or named profile |
| OpenAI-compatible HTTP | You have a hosted API or local server | API key, or none for a keyless local server |
| Anthropic-compatible HTTP | You call an Anthropic-compatible endpoint directly | API key |
| Azure OpenAI | Your deployment lives in Azure | Endpoint, API version, deployment name, and API key |

The wizard tests credentials or model discovery before it writes the backend. API secrets collected
by TinyTalk go to the operating-system keyring, not `config.toml`. Bedrock keeps using AWS's own
credential chain; the Agent SDK paths keep using their own CLI login.

### Claude

Install and sign in to [Claude Code](https://docs.anthropic.com/en/docs/claude-code/getting-started),
then choose **Claude Agent SDK** in `tt auth`. TinyTalk's release build downloads the matching Claude
add-on the first time you choose it.

```sh
claude
tt auth
```

### Codex

Install [Codex CLI](https://github.com/openai/codex), sign in with ChatGPT, then choose
**OpenAI Codex Agent SDK**.

```sh
codex login
tt auth
```

### AWS Bedrock

Make sure the normal AWS credential chain works, then choose **AWS Bedrock**. TinyTalk asks for a
region and optional profile, discovers available model IDs, and also lets you enter a model ID when
discovery is unavailable. Bedrock support is a version-matched add-on downloaded on first setup.

```sh
aws sso login --profile my-profile   # when your profile uses SSO
tt auth
```

Bedrock access and inference-profile availability vary by model and region. Use the ID discovered by
the wizard and consult the [Bedrock model-access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
and [inference-profile](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html)
documentation when AWS rejects a bare model ID.

### A local model

Choose **OpenAI-compatible HTTP API** in `tt auth`. TinyTalk can attempt a managed Gemma setup on a
supported Mac or Linux machine; if the runtime or model cannot be provisioned safely, it falls back
to asking for an existing server URL.

Two straightforward servers are:

- [oMLX](https://github.com/jundot/omlx) on Apple Silicon, normally at
  `http://localhost:8000/v1`;
- [llama.cpp](https://github.com/ggml-org/llama.cpp) on macOS or Linux, normally at
  `http://localhost:8080/v1`.

For example, an existing `llama-server` can be connected in three steps:

```sh
llama-server -hf <owner>/<gguf-repo>:<quant> --port 8080
curl -s http://localhost:8080/v1/models
tt auth
```

In the wizard, choose the manual server path, enter `http://localhost:8080/v1`, leave the API key
blank, and select the model reported by `/v1/models`.

## Everyday use

### Prompt mode

- `?` on an empty line enters prompt mode.
- `?` or Backspace on an empty prompt leaves it.
- Enter sends the request and returns a command to the same editing buffer.
- Up and Down recall earlier TinyTalk commands while prompt mode is active.

### CLI commands

```sh
tt "show the processes listening on ports"  # generate one command
tt --json "show the five newest files"      # structured output
tt history                                   # browse recorded outcomes
tt ground                                    # inspect the grounding snapshot
tt ground --refresh                          # rebuild it now
tt prompt "find duplicate filenames"        # print the model prompt; no model call
tt config explanation off                    # hide the one-line explanation
tt setup                                     # revisit language, widget, and provider
tt auth                                      # manage primary and fallback slots
tt upgrade                                   # install the latest release
tt uninstall                                 # remove app data and keyring entries
```

`tt history` uses an `fzf` picker when both `fzf` and a terminal are available; otherwise it prints a
plain list. History is stored as dated JSONL under `XDG_STATE_HOME` and keeps outcomes for diagnosis,
including failed requests.

### What is sent to the model

TinyTalk sends your request, current working directory, and a grounding summary built from the
current OS, shell, installed commands, and cached tool versions. If `TT_SESSION_CONTEXT` is set, that
redacted text is included as session context. Product requests do not read arbitrary files or run a
generated command to gather context.

Inspect the exact assembled prompt without making a model call:

```sh
tt prompt "find the biggest log files"
```

## How validation works

A model response must pass the ladder before TinyTalk returns it:

1. **Parse** the command with `zsh -n` (or the available POSIX shell).
2. **Resolve binaries** in command position against the grounded machine.
3. **Check long flags** against real help text when TinyTalk has it; missing documentation never
   causes a rejection by itself.
4. **Use native dry-run flags** for a small allowlist of single commands such as selected `rsync`,
   `git`, `npm`, and `kubectl` operations.
5. **Classify danger** with command- and redirect-aware rules. The final level can only be as safe as
   the model claimed or more severe.

If a command fails, TinyTalk retries once with the validation problem and relevant tool help. A
configured fallback backend is used for that second tier. If no suggestion passes, TinyTalk returns
an error and leaves the shell buffer untouched.

## Configuration

The default file is `~/.config/tinytalk/config.toml`. Override it with `TT_CONFIG` or `--config PATH`.
`tt auth` is the safest way to write provider entries, but the file is ordinary TOML:

```toml
[defaults]
backend = "primary"
escalation_backend = "fallback"  # optional
posture = "hybrid"               # local | hybrid | cloud
language = "en"
explanation = true

[backends.primary]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "your-model-id"

[backends.fallback]
kind = "claude-agent-sdk"
model = "your-claude-model-id"
effort = "low"

[cache]
enabled = true
```

Supported backend kinds are `openai-compat`, `anthropic-compat`, `claude-agent-sdk`,
`codex-agent-sdk`, `bedrock`, and `azure-openai`. Names such as `primary` and `fallback` are config
aliases; `kind` selects the protocol.

Useful environment variables:

| Variable | Purpose |
|---|---|
| `TT_CONFIG` | Use a different config file. |
| `TT_SESSION_CONTEXT` | Add caller-supplied, redacted session context. |
| `XDG_CONFIG_HOME` | Change the config root. |
| `XDG_CACHE_HOME` | Change the grounding and exact-match cache root. |
| `XDG_STATE_HOME` | Change the history root. |

## Install, update, and remove

Install a particular release when reproducibility matters:

```sh
TT_VERSION=v0.2.0rc9 curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/install.sh | sh
```

Installer options include `--yes`, `--no-rc`, `--bin-dir DIR`, and `--version TAG`. When piping a
flag to `sh`, use `sh -s -- --version TAG`; the `TT_VERSION` form above is easier.

```sh
tt upgrade
tt uninstall                    # keeps shell rc blocks for you to remove manually

# Remove the binary, data, keyring entries, and installer-owned rc blocks:
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/uninstall.sh | sh
```

Release builds download Bedrock and Claude add-ons only when those providers are selected. Add-ons
are checksum-verified, version-matched, and stored under
`${XDG_DATA_HOME:-~/.local/share}/tinytalk/addons/`. Air-gapped installs can download the matching
release archives and `.sha256` files on another machine and unpack them into that directory.

To run from source:

```sh
git clone https://github.com/pbkimdev/tinytalk.git
cd tinytalk
uv tool install .
```

## Benchmark

TinyTalk's suite asks each backend the same 25 tasks in natural English and Korean. It reports two
different questions:

- **Strict pass:** did the response satisfy TinyTalk's structured contract, parse, use installed
  binaries, and meet deterministic command-shape assertions?
- **Execution oracle:** on the 18 fixture-backed targets, did the command produce the correct output
  or filesystem state inside an isolated eval sandbox?

The most recent committed field run is suite v4 from **2026-07-05**. Sonnet 5 scored 92% strict pass
and 81% on oracle-covered results. The three local Gemma 4 variants scored 58–68% strict pass and
44–56% on their oracle-covered results. That gap is the useful result: a command can look plausible
and still fail when executed against a fixture.

See the [interactive report](docs/bench/2026-07-05/index.html),
[analysis dashboard](docs/bench/2026-07-05/dashboard.html), [suite contract](docs/bench/SUITE-V4.md),
and [reproduction runbook](docs/bench/RUNBOOK.md). Benchmark commands run only inside the explicit
eval harness; TinyTalk's product path still never executes generated commands.

## Troubleshooting

- **`?` types a literal question mark:** run `tt setup`, then open a new zsh. Check that `tt` is on
  `PATH` and `eval "$(tt init zsh)"` loads without an error.
- **No config or backend:** run `tt setup` or `tt auth`. The starter config intentionally has no fake
  provider.
- **A local server has no models:** check `curl -s <base-url>/models` before re-running `tt auth`.
- **Bedrock discovery fails:** refresh the AWS login, check the selected region/profile, or use the
  wizard's manual model-ID path.
- **A command is rejected:** run `tt ground --refresh` after installing or upgrading tools. Use
  `tt prompt "..."` to inspect the prompt and `tt history` to inspect the recorded outcome.
- **The explanation is noisy:** use `tt config explanation off`; the danger label remains.

## Contributing

TinyTalk is a Python 3.11+ project managed with `uv`:

```sh
uv sync
uv run pytest
uv run ruff check .
uv run tt --help
```

Read [AGENTS.md](AGENTS.md) before starting. Every change begins with a GitHub issue and an approved
plan; TinyTalk never auto-runs generated commands, including in contributor tooling outside the
explicit isolated eval harness.
