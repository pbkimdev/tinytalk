# What CLITE is

> This is the source of truth for _what we're building and why_, and it stands on its own. The spec
> and the issues point back here — not the other way around. If anything ever disagrees about intent,
> this file wins until we deliberately change it.

I want to type what I mean at the shell and get the command for it.

Not a chatbot, not a web search — just this: I describe the outcome in plain English, CLITE works out
the right combination of tools I already have, and it hands me a command that runs. I read it, maybe
tweak it, and hit Enter.

## Two examples

**Disk usage.** I type something like:

> "Show me my disk usage — where it's going, how much is used and free, the biggest hotspots, down to
> the directories or apps taking up space, with exact paths, nicely formatted."

and I get back the right `du`/`df` incantation, piped and sorted, instead of me trying to remember the
flags.

**Sorted listing.** Or:

> "List everything in this folder by size — just the name and the size."

and I get `ls … | sort … | awk …` already wired together.

## What matters to me

A few things make or break this:

- **It plugs into any model.** The OpenAI Codex SDK, the Claude Agent SDK, or any OpenAI-compatible
  endpoint (a local model like Ollama/llama.cpp) — I want to swap the brain out freely.
- **The output is always a real, runnable command** — not an explanation, not a "you could try."
  The harness should be strict about that, and dry-run when it can to be sure.

## How I picture it working

1. I hit `?` to enter prompt mode, and the prompt changes so I can see I'm in it. (zsh.)
2. I type plain English; it gives me the command.
3. It keeps a picture of the tools available on my system so the model chooses well.
4. It caches results so similar requests don't burn tokens twice. I'm imagining something
   vector-based, but I'm open to better ideas.
5. It can glance back at earlier commands in the same terminal session for context.
6. When it's genuinely unsure, it looks up the real docs.

## How I'll judge it

Run 25 different prompts and ask:

1. How many give a clean command, with no junk around it?
2. Do the commands actually run?
3. Do they do what I meant?
4. Are they well-chosen, and do they respect what's actually on the system?
5. How many tokens did it burn?
6. How fast was it?
7. What did it cost, per model?

## Models I want to try

Gemma 3/4 (QAT), Qwen, Codex (probably low reasoning), Claude (Sonnet, low-to-mid), and
DeepSeek v4 Flash.
