# The v1 work

The actual, living work lives in [GitHub issues](https://github.com/paulbkim-dev/clite/issues) — this
is just the map so you can see how the pieces fit. Each issue carries its own context, scope, and a
"done when…" you can check.

v1 is one thin slice end to end, plus a way to measure it. Build the engine and the benchmark first;
everything else hangs off the engine.

| Piece | What it does | Start after |
|---|---|---|
| [#1 Core engine](https://github.com/paulbkim-dev/clite/issues/1) | Talk to any model, get back one validated command in a fixed shape | — |
| [#2 Eval harness](https://github.com/paulbkim-dev/clite/issues/2) | Run 25 prompts, score them, track tokens/latency/cost per model | #1 |
| [#3 Grounding](https://github.com/paulbkim-dev/clite/issues/3) | Feed the model the tools you really have, so it stops inventing flags | #1 |
| [#4 Validation & safety](https://github.com/paulbkim-dev/clite/issues/4) | Check the command parses and is real; never hand over `rm -rf` quietly | #1, #3 |
| [#5 Shell integration](https://github.com/paulbkim-dev/clite/issues/5) | The `?` prompt in zsh; drop the command in your buffer, you press Enter | #1, #4 |
| [#6 Caching](https://github.com/paulbkim-dev/clite/issues/6) | Don't pay twice for the same question | #1, #3 |

New issues should match the same shape — the `to_issue` skill writes them for you. The reasoning
behind all of this is in [PRD.md](./PRD.md).
