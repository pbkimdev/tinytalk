# CLITE — issue source (epics)

Derived from [PRD.md](./PRD.md) §14. Six epics for v1, in dependency order. These are tracked as
**GitHub Issues** in this repo; new issues follow the format enforced by the `to_issue` skill
(`.claude/skills/to_issue/SKILL.md`). The sub-bullets below are candidate sub-tasks.

Build order: **S1 + S0 first** (everything hangs off the engine interface), then S2 → S3 → S4 → S5.

---

## S1 — Core engine
**Priority:** Urgent · **Depends on:** none · **Labels:** core, v1

The spine: provider abstraction + harness + structured output contract.

**Scope**
- OpenAI-compatible `/chat/completions` client with tool-calling.
- Adapter seam for Codex SDK / Agent SDK / OpenCode Go behind one interface.
- Two backends: a local endpoint (Ollama/llama.cpp for Gemma/Qwen) + Anthropic.
- Structured output contract (`command`, `explanation`, `danger`, `confidence`, `needs`, `alternatives`).
- Structured-output degradation: native JSON/tool-calling → GBNF constrained grammar → fenced-block extraction + strict parse.
- `~/.config/clite/config.toml` (model routing, posture, danger policy, cache, price table).
- Tier controller skeleton (T0→T1→T2 escalation hooks).

**DoD**
- Given a prompt + config, returns a valid contract object from ≥2 backends.
- `format_ok` parsing is enforced (malformed model output is rejected/retried, never passed through).

**Candidate sub-issues**
- Provider interface + OpenAI-compatible client
- Anthropic adapter
- Local (Ollama/llama.cpp) adapter
- Structured output contract + strict parser + degradation chain
- Config loader (`config.toml`)
- Tier controller skeleton

---

## S0 — Minimal eval harness
**Priority:** High · **Depends on:** S1 (interface) · **Labels:** eval, v1

Built alongside S1 so every change is measured.

**Scope**
- 25-prompt suite across: disk/fs, text processing, search, process/system, networking, git, archive, permissions, package mgmt, monitoring. Each `{id, text, assertions, expected_danger}`.
- Deterministic per-prompt scoring: `format_ok`, `parses` (`zsh -n`), `binaries_exist`, `assertions_pass`, `danger_correct`.
- Telemetry: model, tier reached, prompt/completion tokens, latency, cost (price table), cache hit.
- Leaderboard + per-prompt matrix; CSV/JSON export.
- Validation-only by default; sandbox execution opt-in.

**DoD**
- `clite eval` runs across ≥2 models and emits a leaderboard with all metrics.
- LLM judge for intent scoring is explicitly deferred (noted in output).

**Candidate sub-issues**
- 25-prompt suite + assertion DSL
- Scoring engine (deterministic checks)
- Telemetry + cost computation
- Leaderboard / matrix renderer + export

---

## S2 — Capability grounding
**Priority:** High · **Depends on:** S1 · **Labels:** grounding, v1

**Scope**
- Curated common-toolset catalog (purpose + key flags), seeded from `tldr`; injected at T1.
- OS facts in prompt: `uname`, shell, coreutils-vs-BSD flavor (macOS BSD flags differ).
- On-demand fetch (T2): `--help`/man/tldr for tools in `needs[]` outside the curated set; parse + cache.
- PATH binary-existence cache (hash set).

**DoD**
- Flag-hallucination measurably drops on the eval suite vs an ungrounded baseline.
- Correct GNU/BSD flavor is reflected in generated commands on macOS.

**Candidate sub-issues**
- PATH scan + binary cache
- Curated toolset catalog (tldr-seeded)
- OS/flavor fingerprint
- On-demand help fetcher + spec parser

---

## S3 — Validation & safety
**Priority:** High · **Depends on:** S1, S2 · **Labels:** safety, v1

**Scope**
- Validation ladder: parse (`zsh -n`) → binaries exist → flags exist (best-effort) → tiny native-dry-run allowlist.
- Danger classifier: safe / caution / destructive (rules in PRD §7).
- Safety invariants: destructive never auto-runs / never silently inserted as plain text; zero destructive false-negatives.

**DoD**
- Eval `danger_correct` passes; zero destructive false-negatives on the suite.
- Validation failure correctly triggers tier escalation.

**Candidate sub-issues**
- Parse + binary + flag validators
- Native dry-run allowlist
- Danger classifier + test fixtures (rm -rf, dd, fork bomb, force push, …)

---

## S4 — Shell integration (zsh)
**Priority:** Medium · **Depends on:** S1, S3 · **Labels:** shell, ux, v1

**Scope**
- `?` prompt mode: ZLE widget; line starting with `?` captures NL on Enter.
- Replace `BUFFER` with returned `command` (no `accept-line`); show `explanation` + `danger` via `zle -M`/`POSTDISPLAY`.
- Visible mode indicator (prompt sign).
- `preexec` hook: per-session, redacted command log used as T1 context.

**DoD**
- In a real zsh session, `?` + NL inserts a validated command for the 25-prompt set; user reviews → Enter to run.
- Destructive commands require explicit confirm; secrets redacted from session log.

**Candidate sub-issues**
- ZLE widget + `?` mode + indicator
- Buffer insertion + explanation display
- Session capture hook + redaction

---

## S5 — Caching
**Priority:** Medium · **Depends on:** S1, S2 · **Labels:** perf, v1

**Scope**
- T0 exact cache: key = hash(normalized prompt + cwd + OS fingerprint).
- Spec/doc cache: parsed `--help`/man/tldr keyed by tool + version.
- Semantic/vector command cache explicitly deferred (PRD §9).

**DoD**
- T0 cache-hit latency < 50 ms; repeated identical prompts consume ~0 model tokens (verified in eval telemetry).

**Candidate sub-issues**
- Exact prompt cache (normalize + key + store)
- Spec/doc cache layer
