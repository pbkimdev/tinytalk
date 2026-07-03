# TinyTalk

[한국어](README.ko.md)

TinyTalk turns plain English into a real shell command — right where you're already typing. Say
what you want, and it hands back a command that runs on *your* machine: checked against what's
actually installed, explained in one line, and dropped straight into your prompt so you can read
it before you hit Enter.

It never runs anything on its own. You always get the last look.

![TinyTalk demo](demo.gif)

```
? show me what's eating my disk, biggest first

du -h -d1 / 2>/dev/null | sort -hr | head -20
↳ top-level disk usage, largest first
```

Press Enter to run it, or edit it first. That's the whole idea.

## Quick start with a local agent

TinyTalk doesn't require a cloud API key — it runs just as well against a model on your own
machine through [Ollama](https://ollama.com).

```
brew install ollama
ollama pull qwen3:8b
./install.sh
```

The installer detects a keyless local server automatically and wires it up as your `local`
backend — no API key prompt, nothing sent off-device. From there:

1. Press `?` on an empty line to enter prompt mode (a small `TinyTalk` badge lights up).
2. Type what you want in plain English and hit Enter.
3. TinyTalk checks your request against grounding it collected from your shell — the binaries you
   actually have, your OS, your shell — and swaps the buffer for a real command plus a one-line
   explanation.
4. Read it, edit it if you want, then run it yourself.

Switch back to a cloud backend (Claude, GPT, etc.) any time by editing `defaults.backend` in
`~/.config/tinytalk/config.toml` — both a local and a cloud backend can live in the same config,
so you can fall back to one when the other is unavailable.

## Latest benchmark

TinyTalk ships with its own eval suite (`tt eval`) — 25 natural-language commands, run in both
English and Korean, graded on whether the output actually parses, references real binaries, and
passes its assertions. Local models are included alongside the hosted ones so you can see exactly
what you're trading off by going fully offline.

![TinyTalk CLI Bench — pass rate and score vs. cost](docs/bench/2026-07/assets/summary.png)

Full interactive report: [`docs/bench/2026-07/index.html`](docs/bench/2026-07/index.html).
