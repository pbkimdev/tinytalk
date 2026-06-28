# CLITE (C-Lite) — Product Requirements Document

> Status: Draft v0.1 · Owner: Paul · Last updated: 2026-06-28

## 1. Thesis

**CLITE is a grounded, validated, model-agnostic natural-language → shell translator, with a
built-in benchmark that tells you the *cheapest model that's good enough* on your machine.**

You type natural language at the shell; CLITE emits a single working command or pipeline,
grounded in the tools that actually exist on this system, validated before it's handed to you,
and inserted into your editing buffer for review. A built-in eval harness scores any
OpenAI-compatible model across correctness, cost, latency, and token use so you can pick the
right backend.

### Differentiation vs prior art
Existing NL→shell tools (`sgpt`/shell_gpt, `ai-shell`, `gh copilot suggest`, `aichat`, Warp AI,
butterfish) are single-model, **ungrounded** (hallucinate flags), and unvalidated. CLITE's wedge
is the combination that none of them have: **local tool-spec grounding + a validation/safety
ladder + a multi-model cost/latency/correctness benchmark.** We do not try to out-engineer the
raw NL→command step; we make it *grounded, safe, and measurable*.

## 2. Locked decisions (v1)

| Decision | Choice | Implication |
|---|---|---|
| v1 target | Thin vertical slice + minimal eval harness | Measured from day one |
| Delivery | Insert command into zsh editing buffer; user presses Enter | No auto-run; ZLE widget |
| Runtime posture | Hybrid, **local-first default** | Pluggable providers; cloud/web opt-in |
| Grounding | Curated common-toolset prompt + on-demand `--help`/man fetch | Anti-hallucination without indexing all of `$PATH` |
| Stack | **Go**, single static binary invoked per call | Fast cold start; daemon optimization deferred |
| Tracker | **GitHub** (private repo `clite`: Issues + Project board) | Overrides the global Taskman default for CLITE |

## 3. Goals / Non-goals

**Goals**
- Turn NL into a correct, runnable command grounded in this system's real tools.
- Guarantee *syntactic validity & safety*; *measure* intent correctness.
- Be model-agnostic via an OpenAI-compatible provider abstraction.
- Ship a benchmark that ranks models on correctness, tokens, latency, cost.

**Non-goals (v1)**
- Not a full agent that executes multi-step workflows on your behalf.
- Not a replacement for the shell or a TUI; it augments the prompt line.
- No semantic/vector command cache, no web doc lookup, no full `$PATH` spec indexing yet (deferred).

## 4. Core principle: tiered execution

The eval criteria conflict — correctness/quality pull toward a rich agentic loop; tokens/speed/cost
pull toward a single cheap shot. Resolution: **escalate only when a cheaper tier fails its gate.**

```
T0  Cache         normalize(prompt + cwd + os-fingerprint) → exact-match hit → return  (~free, <50ms)
T1  Grounded-lite curated toolset + OS facts + redacted recent session cmds → structured output
T2  On-demand     model names tools not in curated set OR validation fails on a flag →
                  fetch real --help/man/tldr for those tools → re-ask
T3  Agentic       (DEFERRED post-v1) low confidence/failure → tool-call loop: get_help,
                  search_docs(web), native dry-run, retry (bounded steps)
```

A **validation gate** runs between tiers. Pass → return. Fail → escalate. Destructive → confirm
regardless of tier.

> Reframe: "always returns a working CLI" is split into *syntactic validity & grounding* (guaranteed,
> cheap) and *intent correctness* (measured, not guaranteed). Promise the first; benchmark the second.

## 5. Structured output contract

The model must return only this object (enforced by native JSON/tool-calling where available,
else constrained grammar, else fenced-block extraction + strict parse). Only `command` is inserted
into the buffer.

```json
{
  "command": "du -h -d1 / 2>/dev/null | sort -hr | head -20",
  "explanation": "Top-level disk usage, human-readable, sorted by size, top 20",
  "danger": "safe",            // safe | caution | destructive
  "confidence": 0.86,           // 0.0–1.0, drives escalation/abstain
  "needs": ["du", "sort", "head"],  // tools required → feeds validation/grounding
  "alternatives": []            // optional
}
```

Format compliance (clean parse of this object) is the #1 eval metric and is **enforceable** → target 100%.

## 6. Grounding (curated + on-demand)

- **Curated toolset**: a seeded catalog of common tools with one-line purpose + key flags
  (seed from `tldr` pages). Injected into the T1 system prompt. Includes OS facts: `uname`,
  shell, coreutils-vs-BSD flavor (macOS BSD `du`/`sed` differ from GNU — material for flag correctness).
- **On-demand fetch**: when `needs[]` references a tool outside the curated set, or validation flags an
  unknown option, CLITE runs `tool --help` / `man` / `tldr` for *those tools only*, caches the parsed
  spec, and re-asks (T2). Keeps context small for tiny local models.
- **PATH cache**: a hash set of installed binaries (from `$PATH`) for fast existence checks; not full specs.

## 7. Validation & safety ladder

Run in order; cheapest first.

1. **Parse** — `zsh -n` on the command string (and on any here-doc/subshell).
2. **Binaries exist** — every command word resolves via PATH cache / `command -v`.
3. **Flags exist** — best-effort: extract options, check against cached help text.
4. **Native dry-run** — only for an allowlist that supports it and only if posture permits
   (`rsync -n`, `git ... --dry-run`, `rm -i` preview, etc.). (Minimal allowlist in v1.)
5. **Danger classification** —
   - `safe`: read-only (`ls`, `du`, `cat`, `grep`, `find` without `-delete`).
   - `caution`: mutates state (`mv`, `cp` overwrite, `brew/pip install`, `chmod`).
   - `destructive`: `rm -rf`, `dd`, `mkfs`, `truncate`, history rewrite / force push, fork bomb,
     output redirection over existing files, anything under `sudo` that writes.

**Safety invariant:** destructive commands are never auto-run and are never silently inserted as
plain text — they require an explicit confirm keystroke (or are inserted commented). False-negatives
on destructive classification must be **zero** (better to over-warn).
Session history read for context is **redacted** for secrets before use.

## 8. Shell integration (zsh)

- **Session context capture**: a `preexec` hook appends executed commands to a per-session,
  redacted log used as T1 context ("look backward in the same terminal session").
- **`?` prompt mode**: a ZLE widget; when the line begins with `?`, the rest is captured as NL on
  Enter. CLITE replaces `BUFFER` with the returned `command` (does *not* `accept-line`), and shows
  `explanation` + `danger` via `zle -M` / `POSTDISPLAY`. User reviews → Enter to run.
- A visible mode indicator (prompt sign) signals prompt mode is active.

## 9. Caching (v1)

- **T0 exact cache**: key = hash(normalized prompt + cwd + OS fingerprint). Value = last good output.
- **Spec/doc cache**: parsed `--help`/man/tldr results, keyed by tool + version. Stable across similar
  prompts — this is the biggest cheap token win.
- **Deferred**: semantic/vector command cache (risky — small intent deltas flip commands; needs
  embedding model + high threshold + verify step). Revisit post-v1.

## 10. Provider abstraction

- Interface: OpenAI-compatible `/chat/completions` with tool-calling. Adapters for Codex SDK /
  Agent SDK / OpenCode Go behind the same interface.
- **Structured-output degradation**: prefer native JSON/tool-calling → fall back to constrained
  grammar (GBNF via llama.cpp) → fall back to fenced-block extraction + strict parse. Required because
  small local targets (Gemma QAT) have unreliable tool-calling.
- Config: `~/.config/clite/config.toml` — model routing, posture (local/cloud), danger policy, cache
  settings, per-model price table.

## 11. Eval harness

- **Prompt suite (25)** spanning: disk/filesystem, text processing, search (find/rg/fd),
  process/system, networking, git, archive/compress, permissions, package mgmt, monitoring.
  Each prompt: `{ id, text, assertions, expected_danger }`.
- **Per-prompt scoring**:
  - `format_ok` — output parses to the contract.
  - `parses` — `zsh -n` clean.
  - `binaries_exist` — all referenced binaries present.
  - `assertions_pass` — **deterministic** checks (e.g. "contains `du`", "piped to `sort`", "sorts by
    size", "limited to N"). Preferred over an LLM judge: cheaper, reproducible.
  - `intent_score` — optional LLM judge (0–1) for open-ended prompts (deferred to post-v1).
  - `danger_correct` — classification matches expectation.
- **Per-run telemetry**: model, tier reached, prompt/completion tokens, wall latency, cost
  (tokens × price table), cache hit y/n.
- **Output**: per-model leaderboard + per-prompt matrix; export CSV/JSON.
- **Safety**: eval runs in validation-only mode by default (no execution); sandbox execution is opt-in.

## 12. Metric targets (non-functional / DoD signals)

| Metric | Target |
|---|---|
| `format_ok` | 100% (enforceable) |
| `parses` + `binaries_exist` | ≥ 95% |
| `assertions_pass` (a capable model) | ≥ 80% |
| Destructive false-negatives | 0 |
| T0 cache-hit latency | < 50 ms |
| T1 latency | reported per model, not gated |
| Tokens / cost per request | reported per model |

## 13. v1 thin-slice scope

**In**
- Provider abstraction with two backends: one local OpenAI-compatible endpoint (Ollama/llama.cpp for
  Gemma/Qwen) + Anthropic.
- Tiers T0 (exact cache) + T1 (grounded-lite) + T2 (on-demand help). [T3 deferred]
- Structured output contract + strict enforcement.
- Validation ladder steps 1–3 + danger classification + a tiny native-dry-run allowlist.
- zsh `?` widget, insert-into-buffer, redacted session-history capture.
- Minimal eval runner: 25 prompts, deterministic assertions, token/latency/cost capture, leaderboard.

**Out (later)**
- T3 agentic loop, web doc lookup, semantic/vector cache, full `$PATH` spec indexing, sandbox
  execution, LLM judge, additional providers, daemon optimization.

**v1 Definition of Done**
- `clite eval` runs across ≥ 2 models and produces a leaderboard (correctness + tokens + latency + cost).
- `?` mode works in a real zsh session, inserting a validated command for the 25-prompt set.
- Destructive commands never auto-run; `format_ok` = 100% on the suite.

## 14. Scope → plan breakdown (build order)

| # | Epic | Depends on | Notes |
|---|---|---|---|
| S1 | Core engine: provider abstraction, prompt/harness, structured output | — | The spine |
| S0 | Minimal eval harness: 25 prompts, assertions, telemetry, leaderboard | S1 interface | Build alongside S1 |
| S2 | Grounding: curated toolset, on-demand help fetch, PATH cache | S1 | Anti-hallucination |
| S3 | Validation + safety ladder + danger classifier | S1, S2 | Trust |
| S4 | Shell integration: `?` ZLE widget, buffer insert, session capture | S1, S3 | UX |
| S5 | Caching: T0 exact + spec/doc cache | S1, S2 | Token/cost |
| S6 | (Deferred) doc lookup (web), T3 agentic controller, semantic cache | S1–S5 | Post-v1 |

## 15. Open questions / risks

- **Process model (decided: Go single binary per call).** Shell hooks demand fast startup; a Go static
  binary gives fast cold start. A **persistent local daemon + thin zsh client** (to amortize warm caches
  / model connections) is a known future optimization, deferred until per-call latency proves
  insufficient against the latency target.
- **Small-model structured output** (Gemma QAT) reliability → mitigate with constrained decoding (GBNF).
- **Curated toolset scope & upkeep** — how many tools, seeded from tldr; refresh strategy.
- **Price-table maintenance** per model for cost reporting.
- **Secret redaction completeness** in session-history capture.
- **BSD vs GNU** flag divergence on macOS — grounding must encode the active flavor.
