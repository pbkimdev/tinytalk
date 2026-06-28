# Plan — #25 · clite v1 (Python): re-platform roadmap

> **Type:** roadmap / tracking epic. This issue does not land a single feature commit;
> its deliverable is the *agreed build order* across sub-issues **#26–#36**, the
> integration milestones that prove the epic's "Done when," and the per-piece scope/DoD
> so each sub-issue can be planned and built independently (one PR, one squash-merged
> commit each — per `AGENTS.md`). Per-piece implementation detail lives in each
> sub-issue's own `plan` artifact, not here.

## 1. Goal & scope

**Goal.** Re-platform clite from Go to **Python** with an **SDK-first provider seam**, and
deliver the v1 thin vertical slice: plain English → a **validated** shell command, grounded
in the host's real tools, never auto-run, with a built-in eval that scores backends on the
user's machine. Intent is unchanged from VISION.md / PRD.md — only the language and the
provider seam shape change (SDK-first instead of OpenAI-compatible-first).

**In scope (this epic = sequencing + integration of):**
- Core engine (spine): provider seam + structured-output contract + strict parser +
  degradation chain (#26); Claude Agent SDK adapter (#27); OpenAI Codex SDK adapter (#28);
  local / OpenAI-compatible adapter (#29); config loader (#30); tier controller T0→T1→T2 (#31).
- Surrounding epics: S0 eval harness (#32); S2 capability grounding (#33); S3 validation &
  safety (#34); S4 zsh shell integration (#35); S5 caching (#36).
- The Python re-platform groundwork (PR **#24**) as the **Step 0 prerequisite**.

**Explicitly NOT in scope (deferred — PRD §13 "Out"):**
- T3 agentic tool-loop controller; web/doc lookup; semantic/vector command cache; full
  `$PATH` spec indexing; sandbox execution; LLM-judge intent scoring; daemon optimization;
  providers beyond the three backend kinds.
- Re-doing the #24 scaffold here — it lands via its own PR; this epic only depends on it.
- Re-planning each sub-issue in depth — each carries its own `plan`/`explore` label and
  flows through the pipeline separately.

## 2. Definition of Done (epic-level)

Mirrors the issue body's "Done when," made measurable against PRD §12 targets:

1. Given an English request + `config.toml`, clite returns a **validated** command from
   **≥2 backends** — a local model (#29) **and** a hosted Agent/Codex SDK backend (#27 or #28).
2. The command is **never auto-run**; destructive commands are flagged and require an
   explicit keystroke (S3 #34). **Destructive false-negatives = 0** on the safety fixture set.
3. The built-in eval (`clite eval`, #32) scores **≥2 backends** and produces a leaderboard
   with correctness + tokens + latency + cost.
4. `format_ok = 100%` on the eval suite (enforced by the strict parser, #26).
5. `? <request>` in a real zsh session inserts the validated command into the editing
   buffer without executing it (S4 #35).

**Smallest verification that proves the epic:** the M4 + M5 integration checks below
(one end-to-end eval run over ≥2 backends, plus a scripted zsh buffer-insert check). Each
sub-issue proves itself with its own unit tests at merge time.

## 3. Target package layout (proposed; sub-issue plans firm it up)

Extends the #24 scaffold (`clite/` package, `clite.cli:main` entry point):

```
clite/
  cli.py                # entry point (exists) — grows `generate` + `eval` subcommands
  contract.py           # CommandResult + JSON schema (command/explanation/danger/         [#26]
                        #   confidence/needs/alternatives)
  parser.py             # strict parser + degradation chain                                 [#26]
  provider/
    base.py             # Provider Protocol/ABC: name, complete(Request)->Response          [#26]
    openai_compat.py    # local / OpenAI-compatible adapter (port of the Go client)         [#29]
    claude_agent.py     # Claude Agent SDK adapter                                          [#27]
    codex.py            # OpenAI Codex SDK adapter                                          [#28]
  config.py             # config.toml loader + validation (tomllib)                         [#30]
  tiers.py              # tier controller T0→T1→T2                                          [#31]
  grounding.py          # installed-binary + flag detection, feeds generation              [#33]
  safety.py             # parse + binary/flag check + danger classification                [#34]
  cache.py              # T0 exact cache + spec/doc cache                                   [#36]
  shell/clite.zsh       # `?` ZLE widget + preexec session capture                         [#35]
  eval/                 # 25-prompt suite, assertion DSL, scoring, telemetry, leaderboard   [#32]
tests/                  # mirrors the above; one mock backend reused across seam tests
```

## 4. Build order (ordered steps, each tracing to the goal)

**Step 0 — Prerequisite: land #24.** The Python scaffold (Go removed; `pyproject.toml` via
uv/hatchling; `clite` package + entry point; `tests/`). *Blocks everything* — no sub-issue
can build against a Go tree. Merge #24 before any of #26–#36 implement.

Then, by dependency (PRD §14 + sub-issue "depends on"). The numbering is a *recommended*
order, not a strict chain: once the spine (#26) plus a backend (#29) and config (#30) exist,
**#27/#28 can proceed in parallel**, and **#33 and #36 can start alongside #31** — only the
listed "needs" are hard dependencies.

1. **#26 — provider seam + contract + parser + degradation chain.** The spine; every
   backend and tier plugs into it. → enables all downstream.
2. **#29 — local / OpenAI-compatible adapter** (size:S; direct port of the existing Go
   client). First concrete backend → makes the seam testable end-to-end. *(needs explore/plan
   label.)*
3. **#30 — config loader.** Selects backend + posture from `config.toml`; needed to wire
   adapters and the tier controller.
4. **#27 — Claude Agent SDK adapter.** First-class hosted backend → with #29 gives the
   "≥2 backends" the epic DoD requires.
5. **#28 — OpenAI Codex SDK adapter** (currently `pending` — gated on Codex SDK
   availability/API confirmation). Second hosted backend; parallel to #27 once #26 is stable.
6. **#31 — tier controller T0→T1→T2.** Orchestrates providers + escalation; needs ≥1
   provider + config.
7. **#33 — S2 capability grounding.** Feeds the T1/T2 generation prompt; integrated by #31.
8. **#34 — S3 validation & safety** (risk:high; `explore` first). The gate between tiers;
   destructive classification. Needs #26 + #33.
9. **#36 — S5 caching** (`explore` first). Becomes the T0 layer inside the tier controller.
10. **#32 — S0 eval harness** (size:L). Scores backends; can start against the seam once #26
    is stable, finalized once tiers/grounding/safety land.
11. **#35 — S4 shell integration (zsh).** The `?` widget; only inserts **validated** output,
    so it lands after #34.

### Integration milestones (prove the epic incrementally)
- **M1 — Seam alive:** #24 + #26 + #29 → a request runs through the seam against a local
  backend and yields a validated contract object; malformed output rejected (`format_ok`).
- **M2 — Two backends + config:** + #30 + (#27 and/or #28) → validated command from ≥2
  config-selected backends. *(satisfies epic DoD #1.)*
- **M3 — Grounded, safe, tiered:** + #31 + #33 + #34 + #36 → escalation works; commands are
  grounded + danger-classified; destructive never auto-runs. *(epic DoD #2.)*
- **M4 — Measured:** + #32 → `clite eval` scores ≥2 backends with a cost/latency/correctness
  leaderboard. *(epic DoD #3, #4.)*
- **M5 — In the shell:** + #35 → `? <request>` inserts a validated command into the zsh
  buffer. *(epic DoD #5.)*

## 5. Test strategy (high-value, TDD per sub-issue)

Each sub-issue lands red→green with its own tests; this epic only defines the gates:
- **#26** — parser: a valid contract parses; a malformed completion is **rejected and
  retried, never surfaced**; one test per degradation rung (native JSON → GBNF → fenced
  extraction). Seam against a stub backend yields a validated contract.
- **#29** — port the 5 existing Go client tests to a Python mock-HTTP server: happy path,
  HTTP error → typed error, malformed JSON → decode error, context/timeout cancel, no-key
  local endpoint → no `Authorization` header.
- **#27 / #28** — SDK mocked; a sample prompt returns a parsed contract.
- **#30** — config selects backend/posture; missing/invalid config → clear, actionable error.
- **#31** — a request escalates T0→T1→T2 per the hooks (mock providers).
- **#33** — grounding lookups; a generated command only uses flags present on the host.
- **#34** — destructive command flagged + never auto-run; invalid command rejected;
  **zero destructive false-negatives** on a curated fixture set (the highest-value gate).
- **#36** — cache hit/miss; a repeated request is served without a model call.
- **#32** — scoring-engine unit tests + **one end-to-end eval run** over ≥2 backends.
- **#35** — scripted zsh check: `? <request>` puts a (mocked) validated command in `BUFFER`
  and does **not** `accept-line`.
- **Integration** — M1–M5 each get one end-to-end check; M4 + M5 together are the epic's
  acceptance proof.

## 6. Risks & rollback

| Risk | Mitigation |
|---|---|
| **#24 not merged** → blocks the whole roadmap | Step 0: land #24 first; it is the single hard prerequisite. |
| **SDK availability / API drift** — Claude Agent SDK (Python) and OpenAI Codex SDK (`openai-codex`); #28 is `pending` on this | Confirm package + API at adapter-build time and pin versions; keep each adapter behind the seam so a surprise is contained to one module; #29 (local) guarantees ≥1 backend even if a hosted SDK slips. |
| **Small-model structured output** (Gemma QAT) unreliable tool-calling | Degradation chain in #26: native JSON → constrained grammar (GBNF) → fenced-block extraction + strict parse. |
| **Safety false-negatives** (#34, risk:high) | Over-warn by default; explicit dry-run allowlist; destructive false-negatives gated to **0**; #34 routes through `explore` before build. |
| **Scope creep** into deferred work | PRD §13 "Out" list is binding; T3/web/semantic-cache/sandbox/LLM-judge stay out of v1. |
| **Epic PR closes #25 on merge** (engine appends `Closes #25`) | Acceptable — the roadmap doc *is* this epic's deliverable; #26–#36 remain open and are built/merged individually. |

**Rollback.** Each sub-issue is its own squash-merged PR → revert is one commit on `main`.
The Go→Python pivot (#24) is itself a single revertable PR. This roadmap doc reverts trivially.

## 7. Verification summary

Verified internally against (1) requirements — PRD + issue #25 body + sub-issues #26–#36 —
and (2) feasibility — referenced files (#24 scaffold, the Go client being ported) and SDK
assumptions. Rounds used and residual risks are recorded in the PR description.
