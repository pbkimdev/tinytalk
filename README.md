# CLITE (C-Lite)

Type natural language at your shell; get a **grounded, validated CLI command** inserted into your zsh
editing buffer for review. Model-agnostic, with a built-in benchmark that finds the *cheapest model
good enough* on your machine.

> Status: pre-implementation (PRD v0.1). Not yet runnable.

## Docs
- [VISION.md](./VISION.md) — what we're building and why (source of truth for intent).
- [PRD.md](./PRD.md) — v0.1 spec: tiered execution, structured output, validation/safety, eval harness.
- [ISSUES.md](./ISSUES.md) — the six v1 epics (S0–S5), tracked as GitHub Issues.

## v1 at a glance
- **Go**, single static binary per call. Hybrid runtime, **local-first** default.
- `?` zsh prompt mode → command inserted into the editing buffer (**never auto-run**).
- Grounding: curated toolset + on-demand `--help`/man fetch (anti-hallucination).
- Built-in eval harness: 25 prompts scored on correctness, tokens, latency, and cost across models.

## Filing issues
Issues in this repo follow a fixed, review-friendly format (Context / Scope / Out-of-scope /
Dependencies / measurable DoD / Acceptance / Sub-tasks / References). Use the repo-scoped **`to_issue`**
skill (`.claude/skills/to_issue/SKILL.md`) to turn a requirement into a properly-contextualized issue.
