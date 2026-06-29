# CLITE

CLITE turns plain English at your shell into a real command. You say what you want; it hands you a
command that actually runs on *your* machine — checked, explained, and dropped right into your prompt
so you can read it before you hit Enter.

It never runs anything on its own. You always get the last look.

```
? show me what's eating my disk, biggest first

du -h -d1 / 2>/dev/null | sort -hr | head -20
↳ top-level disk usage, largest first
```

Press Enter to run it, or edit it first. That's the whole idea.

## Why another one of these?

There are plenty of "English to shell" tools. Most share two problems: they invent flags that don't
exist on your system, and they'll hand you `rm -rf` with a straight face.

CLITE is built around fixing exactly those:

- **It knows what's actually installed.** Before writing a command it looks at the tools you really
  have and their real options — so you get `du` flags that exist on *your* OS, not GNU flags on a Mac.
- **It checks the command before you ever see it.** Parses it, confirms the binaries and flags are
  real, and flags anything destructive. Dangerous commands never run by themselves and never slip in
  quietly.
- **It's model-agnostic, and it'll tell you which model to use.** Point it at a local model (Gemma,
  Qwen) or a hosted one (Claude, DeepSeek). A built-in benchmark scores them on your own machine so
  you can pick the cheapest one that's still good enough.

## Status

Early. There's no working release yet — right now this repo is the plan. The full picture lives in:

- **[VISION.md](./VISION.md)** — what we're building and why.
- **[PRD.md](./docs/agents/PRD.md)** — the spec: how it decides, how it stays safe, how it's measured.
- **[The issues](https://github.com/paulbkim-dev/clite/issues)** — v1, in progress.

## How it'll work

You type `?` and then what you want. CLITE tries the cheapest path first — a cached answer or a single
quick model call — and only does more (reads a man page, retries) when it has to. Whatever it settles
on gets validated, then placed in your editing buffer. You decide whether to run it.

Under the hood it's a Python CLI (install with `uv tool install clite` or `pipx`), local-first,
with the model and the surrounding pieces swappable — the **Claude Agent SDK** and **OpenAI Codex
SDK** are first-class in-process backends, alongside an OpenAI-compatible path for local models.

## Configuring it

CLITE reads `config.toml` — which backend (local or hosted) to use and the runtime posture.
You don't create it by hand: on first run CLITE writes a starter `config.toml` to
`$XDG_CONFIG_HOME/clite/config.toml` (else `~/.config/clite/config.toml`), prints where it
put it, and you just edit that file.

The lookup order is `$CLITE_CONFIG` → `$XDG_CONFIG_HOME/clite/config.toml` →
`~/.config/clite/config.toml`. See [`config.toml.example`](./config.toml.example) for the
full schema — it's the committed copy of that starter file.

## Working in this repo

Issues here follow a fixed shape so they're easy to pick up and review: context first, then scope,
then how you'll know it's done. The `to_issue` skill (`.claude/skills/to_issue/`) writes them in that
shape for you.
