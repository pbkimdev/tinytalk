# CLITE (C-Lite) — Vision

> This is the canonical statement of Paul's original intent for CLITE, captured verbatim in spirit.
> It is the source of truth for *what we're trying to build and why*. The derived spec lives in
> [PRD.md](./PRD.md). When the PRD and this file disagree about intent, this file wins until the PRD
> is consciously updated.

## What it is

CLITE is a simple shell-level completion where you type in natural language what you want. Using the
tools available in the current environment, it matches the right CLI command combination for the
user's request.

## Examples

**1. Disk usage**

> "Hey, I want the current disk usage of my system:
> - where it's using
> - how much it's using
> - how much is remaining
> - what are some hotspots of high disk usage
> - I want to see the list of the items at the level of the immediate directory or software that's
>   taking up the size and the exact file locations as preformatted"

→ outputs `dh -...` / the appropriate CLI command(s) as needed.

**2. Sorted directory listing**

> "I want a list of items in the current directory sorted by file size, returning only the filename
> and the size"

→ outputs `ls ... | sort ... | transform ... | awk` or similar.

## Strengths the app should have

- Connectable via **Codex SDK / Agent SDK / OpenCode Go / any OpenAI-compatible endpoint** to get the
  right output.
- Default instructions and harnessing that ensure it **always returns working CLI** (dry-running would
  be a good idea).

## How it should be evaluated

With 25 different prompts:
1. How many CLI commands does it deliver **without outputting unformatted CLI output**?
2. Do the CLI outputs **actually work**?
3. Do the CLI outputs work **as intended**?
4. How well optimized is the output, and how well does it respect current system availability?
   (quality of output)
5. How many **tokens** does it use?
6. How **fast** does it get the job done?
7. How much does it **cost** in total, by model?

## How Paul imagines implementing it

1. Typing `?` as a prefix enables prompt mode, with a prompt sign showing it's in prompt mode
   (zsh supported).
2. You type in natural language; it outputs commands.
3. All the system's available tools are cached somewhere so the LLM can decide well.
4. Cache the outputs so the LLM saves tokens for similar requests (thinking a vector-based approach,
   open to other ideas).
5. Inside, it should look backward in the same terminal session for other previous commands by the
   user that are relevant.
6. For something it's 100% not sure about, it needs to look up official documentation.

## Models to test on

- Gemma 3/4 variants (QAT)
- Qwen
- Codex models (probably low reasoning)
- Anthropic models (probably Sonnet mid or low)
- DeepSeek v4 Flash

## Goal of this stage

Ideate this into concrete requirements → turn into a PRD → turn each scope into plans.
