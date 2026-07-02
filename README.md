# TinyTalk

TinyTalk turns plain English at your shell into a real command. You say what you want; it hands you a
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

TinyTalk is built around fixing exactly those:

- **It knows what's actually installed.** Before writing a command it looks at the tools you really
  have and their real options — so you get `du` flags that exist on *your* OS, not GNU flags on a Mac.
- **It checks the command before you ever see it.** Parses it, confirms the binaries and flags are
  real, and flags anything destructive. Dangerous commands never run by themselves and never slip in
  quietly.
- **It's model-agnostic, and it'll tell you which model to use.** Point it at a local model (Gemma,
  Qwen) or a hosted one (Claude, DeepSeek). A built-in benchmark scores them on your own machine so
  you can pick the cheapest one that's still good enough.

## Status

v1 works end-to-end: `tt "what you want"` (or `?` in zsh) runs config → provider seam
(Claude Agent SDK, or any OpenAI-compatible endpoint — Ollama, llama.cpp, MLX servers) → tier
controller (T0 exact cache → T1 → T2) → capability grounding → validation & safety → a command
in your editing buffer, never auto-run. `tt eval` benchmarks your configured backends on your
own machine — format, assertions, danger calls, tokens, latency, cost over a 25-prompt suite
(see the [v1 epic's closing evidence](https://github.com/paulbkim-dev/tinytalk/issues/25)).

- **[VISION.md](./VISION.md)** — what we're building and why.
- **[The issues](https://github.com/paulbkim-dev/tinytalk/issues)** — what's landed and what's next.

## How it'll work

You type `?` and then what you want. TinyTalk tries the cheapest path first — a cached answer or a single
quick model call — and only does more (reads a man page, retries) when it has to. Whatever it settles
on gets validated, then placed in your editing buffer. You decide whether to run it.

Under the hood it's a Python CLI (install with `uv tool install tinytalk` or `pipx`), local-first,
with the model and the surrounding pieces swappable — the **Claude Agent SDK** and **OpenAI Codex
SDK** are first-class in-process backends, alongside an OpenAI-compatible path for local models.

## Working in this repo

Issues here follow a fixed shape so they're easy to pick up and review: context first, then scope,
then how you'll know it's done. The `to_issue` skill (`.claude/skills/to_issue/`) writes them in that
shape for you.
