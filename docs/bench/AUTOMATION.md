# tt bench — deterministic automation (research)

Companion to [RUNBOOK.md](./RUNBOOK.md). The runbook is the *manual* procedure; this doc is the
*design* for making that procedure maximally deterministic, parallel, and self-analyzing — so a
sweep is one idempotent command whose output we can trust, diff, and learn from.

Status: research / proposal. Nothing here is built yet. It is grounded in the current harness
(`tinytalk/eval/`) and in concrete findings from the 2026-07-05 two-model run.

---

## 1. Thesis

An eval has two layers, and they have opposite determinism budgets:

| Layer | What it does | Determinism |
|---|---|---|
| **Generation** | prompt → model → command | *Irreducibly noisy.* An LLM at temperature 0 is only *approximately* reproducible. |
| **Scoring** | command → pass/fail | *Should be a pure function.* Same command + same suite → same verdict, forever, on any machine. |

Almost all of our reproducibility pain comes from **mixing the two** — re-running generation every
time we want a score, so scorer changes and model noise are entangled. The design goal:

> **Record once, score many.** Generation is quarantined behind a provenance-stamped record.
> Scoring is a pure re-score of that record. Insight is mined from the record, not re-generated.

### What "pass" means

A command passes when — and only when — all three hold. The assertion DSL is a cheap, deterministic
proxy for these; where a proxy and the principle disagree, **the principle wins and the proxy is a bug
to fix** (this is what moved `count-lines-code`, §7.2):

1. **It runs.** Parses as shell, every binary/flag is real → the validation ladder (`validate.py`).
2. **It's a sensible command.** A reasonable approach to the actual request, not gibberish that
   happens to type-check → `uses:` / `pipes_to:` (right tools in the right place).
3. **The result is exactly what was asked for.** The command's effect matches the request's intent —
   *by intent, not by spelling*. `fd -e py` and `find -name '*.py'` both satisfy "the Python files";
   an assertion that accepts only one spelling is under-specified → `contains:` / `regex:`.

`publish.py:rescore_row` already does the pure re-score — it re-derives parse/binaries/assertions
from a *recorded* command without calling the model. That function is the seed of the whole
architecture; the rest of this doc is "make that the main path, not a merge special-case."

---

## 2. Where determinism leaks today (grounded)

### 2a. Scoring layer — nearly pure, but leaking in three places

The scorer is rule-based and never executes anything (`runner.py`: `run_dry_run=False`), and the
assertion DSL is deterministic string logic (`suite.py:check_assertion`). Good. But:

1. **Host-dependent `binaries_exist`.** The validation ladder checks each command word against the
   *live* `$PATH` (`grounding.py:installed_binaries`, `validate.py` `shutil.which`). The same command
   scores differently on a machine with vs. without `kubectl`, `rg`, `fd`. Scores are silently
   host-bound.
2. **Host- and time-dependent T2 enrichment.** Retries fetch live `--help`/`man` text keyed per tool
   *version* (`grounding.py`). Upgrade a tool → different enrichment → different retry output. The
   prompt itself (T1 host facts, curated-tool catalog filtered to installed binaries) is also
   host-specific — so even generation isn't prompt-stable across machines.
3. **Extractor / assertion bugs that reject correct commands.** Two live false negatives from
   2026-07-05 (both models, so clearly the scorer, not the model):

   | Target | Model output (correct) | Why it wrongly failed |
   |---|---|---|
   | `parallel-compress` | `… \| xargs -P 4 -I {} gzip {}` | `binaries_exist` treated the xargs placeholder `{}` as a command name → "`{}`: not installed". The #95 extractor fix covered `-exec … {}` and process substitution but not `-I {}` in a **piped** xargs stage. **✅ Fixed 2026-07-05** (`validate.py:_segments` now skips `{}` in command position); a re-score flipped both models' parallel-compress EN/KO to pass (Sonnet 90→94, 12B QAT 82→86) with no new model calls — the record-once-score-many payoff in action. |
   | `count-lines-code` | `fd -e py -x wc -l …` | assertion `contains:.py` wanted the literal `.py`; `fd -e py` selects Python files idiomatically without that substring — the assertion encoded one spelling, not the intent. **✅ Fixed 2026-07-05** (broadened to `regex:\.py\b\|\bpy\b`, accepting `.py` globs, `fd -e py`, `rg --type py`); re-score flipped both models (Sonnet 94→98, 12B QAT 86→88). |

   `parallel-compress` was called "the hardest overall" in the 07-03 runbook. It was in fact partly a
   **scorer artifact** — the model was right. This is the single strongest argument for the insight
   layer (§5): we only found it by reading the outputs, and fixing it moved the leaderboard.

**Fix direction:** freeze scoring inputs. Pin (a) the suite + extractor by version/hash, and (b) a
**captured grounding snapshot** (the PATH set + per-tool `--help` corpus) so `binaries_exist` and
enrichment are replayed from a frozen host, not the live one. A sweep records which snapshot it used;
a re-score replays it byte-for-byte on any machine.

### 2b. Generation layer — accept the noise, pin what you can

- **No `seed` is ever sent.** No provider (`openai_compat`, `anthropic_compat`, `bedrock`, …) puts a
  `seed` in the payload — only `temperature`/`max_tokens`. Temperature 0 is greedy, but ties and
  server-side batched float reductions can still flip a token.
- **Speculative decoding is not guaranteed lossless here.** MTPLX "flips a few prompts between
  temperature-0 runs" (known). The new Gemma-4-12B-QAT rides an *assistant MTP drafter*
  (`gemma-4-12B-it-assistant-4bit`) — same risk class. Verify vs. label: run the target model with
  the drafter **off** once and diff; if outputs differ, MTP is lossy and must be a labeled axis, not
  a silent default.
- **SDK/model/CLI drift.** `sonnet5-low`/`gpt55-low` ride the Claude Code / Codex logins. The model
  version *and* the CLI's injected system context (~20-28k tokens) move underneath us with no pin. A
  score from July is not comparable to one from September unless we stamp the CLI + model build.
- **Methodology drift makes cross-run numbers lie.** 07-03 Sonnet = 98 was a *merge* of 15 re-scored
  saturated targets + 10 fresh hard ones. 07-05 Sonnet = 90 is a *fully fresh* run over all 25. The
  8-point gap is mostly methodology, not the model. **Only compare runs produced the same way** — the
  provenance manifest (§3) is what makes "the same way" checkable.

---

## 3. Target architecture: record once, score many

Three phases, each an addressable artifact under `docs/bench/<date>/`:

```
generate ─► generations/<backend>.jsonl   (raw model outputs + usage + provenance)
   │
score ───► results.json                   (pure re-score under pinned suite + grounding snapshot)
   │
render ──► index.html + analysis.json     (report + mined insights)
```

**Phase A · generate** (the only non-deterministic step). For each (backend, prompt): call the model,
write one JSONL line = `{prompt_id, lang, target, command, error, tier, usage, latency_s,
server_time_s, raw_completion}`. `raw_completion` preserves the pre-parse text so envelope failures
(§5) are debuggable. This is a superset of what `runner.PromptResult` already captures — we just
persist it *before* scoring and never overwrite it.

**Phase B · score** (pure function). `results.json = score(generations, suite@hash,
grounding_snapshot@hash)`. Re-runnable offline, on CI, on any laptop, producing byte-identical output.
This is `rescore_row` generalized from "kept rows" to "all rows."

**Phase C · render** — `report.py` unchanged; `analysis.json` is new (§5).

**Provenance manifest** (`run_meta.json`, extended). Every field that can move the numbers, captured
at generate time:

```jsonc
{
  "run_date": "2026-07-05",
  "git_sha": "…",                    // suite + scorer + prompts code
  "suite_hash": "…",                 // hash of SUITE targets+assertions
  "extractor_version": "…",          // command_words / ladder version
  "grounding_snapshot": "snap-…",    // PATH set + --help corpus used for scoring
  "backends": {
    "local-gemma4-12b-qat": {
      "model": "gemma-4-12B-it-qat-4bit",
      "server": "oMLX <build>", "endpoint_model_list_sha": "…",
      "mtp": {"drafter": "gemma-4-12B-it-assistant-4bit", "verified_lossless": false}
    },
    "sonnet5-low": { "model": "claude-sonnet-5", "sdk": "claude-code <ver>", "effort": "low" }
  }
}
```

Two runs are comparable **iff** `suite_hash`, `extractor_version`, and `grounding_snapshot` match.
The differ (§5) refuses (or loudly warns) otherwise — that alone would have caught the 98-vs-90
apples-to-oranges above.

---

## 4. Parallel benching (cloud ∥ local)

The runbook's "one backend at a time" rule exists **only** because local models share one GPU. It
over-serializes: a cloud/SDK backend and a local backend contend for *nothing*. The 2026-07-05 run
paid this tax — `local-gemma4-12b-qat` (GPU) and `sonnet5-low` (network) ran back-to-back when they
could have fully overlapped.

**Model it as resource classes, schedule one worker per class:**

| Class | Members | Contention | Policy |
|---|---|---|---|
| `local-gpu` | 26B, E4B, Qwen, 12B-QAT | one Metal device | **serialize** within the class |
| `cloud-anthropic` | sonnet5-low | remote + rate limit | parallel vs. everything else |
| `cloud-openai` | gpt55-low | remote + rate limit | parallel vs. everything else |
| `remote-http` | future off-box endpoints | that box's GPU | serialize per host |

Scheduler = one async worker per class, classes run concurrently; the `local-gpu` worker drains its
queue one backend at a time. For 2026-07-05 that means 12B-QAT and Sonnet run **at the same time**;
wall-clock ≈ max(local pass, cloud pass) instead of the sum.

**Prompt-level concurrency** is a second, independent knob: within a *cloud* backend, fire N prompts
concurrently (bounded semaphore + backoff on 429). 50 Sonnet prompts × ~5.5s each drops from ~5 min
to ~1 min at concurrency 8. Local backends stay serial (one GPU).

**The one caveat — latency fidelity.** `p50 latency` is a *reported metric*. Concurrency inflates
wall-clock latency (queuing) and batching deflates per-request latency. Two clean escapes:

1. **Record server-reported time.** oMLX already returns `usage.total_time` (e.g. `0.35`) — decoupled
   from client queuing. Persist it as `server_time_s` and report *that* for local backends.
2. **Dedicated latency pass.** Throughput mode for scores (parallel), a separate serialized single-
   flight pass for the latency column. Scores don't care about concurrency (each request is
   independent at temp 0), so the score sweep can go fast and wide; only latency needs quiet.

Parallelism changes *speed and latency numbers*, never *scores*. Keep those two concerns separate and
parallel benching is free.

---

## 5. Output & insight collection

> **✅ Built 2026-07-05** — `tt eval analyze` (`tinytalk/eval/analyze.py`, read-only, no model calls)
> now computes four metric families and writes `analysis.json`:
> - **delivery** (renamed from "envelope health") — `delivery_rate` + a fault taxonomy
>   (`unescaped_backslash` / `malformed_json` / `transport` / …). 12B-QAT: 90% delivered; **5 of its 6
>   real misses are dropped answers**, 4 of them unescaped backslashes in regex/sed — the JSON envelope,
>   not shell reasoning, is its ceiling.
> - **layers** — strict-pass decomposed into delivered → runs → intent (② sensible + ③ result fused,
>   honestly: assertion *kind* doesn't map to layer) with a first-failing histogram + exact per-assertion
>   breakdown.
> - **slices** — EN/KO, per-category, and an `escape_heavy` hypothesis: 12B delivers **37.5% on
>   escape-heavy targets vs 100% elsewhere**.
> - **stability** — flip_rate / command_flip_rate over N runs. Measured: **12B-QAT [86,86], flip 0%**
>   (Gemma assistant-MTP is bit-deterministic at temp 0); **Sonnet [96,100], flip 6% but command_flip
>   52%** — hosted batched-MoE serving is non-deterministic at temp 0 (not a temperature issue), so its
>   single-run score swings ±2-4pts and needs N-sample medians. Invariant `flip_rate ≤ command_flip_rate`.
>
> Next: `bench diff` (provenance-gated cross-run), and the v3→v4 suite refresh (retire ~10 saturated
> targets, add 10 hard-but-stable ones calibrated to Sonnet ~80%; vet candidates with `analyze --runs`).

We already store every model command in `results.json`; we just don't *mine* it. The point of keeping
generations (§3, Phase A) is to turn each sweep into a dataset we learn from. Two artifacts:

**`analysis.json` — a failure taxonomy**, auto-derived by bucketing every non-passing row:

| Bucket | Signature | 2026-07-05 examples | Action it drives |
|---|---|---|---|
| `model-json-envelope` | `error` contains `could not decode completion` | 12B: `k8s-restart`, `extract-ips-ko`, `ini-section` en+ko (4 of its 9 misses) | Salvage the `command` field from malformed JSON (unescaped `\` in regex/sed) → recovers real answers. Also a model-quality signal: 12B's shell reasoning is better than 82% implies; the envelope is the bottleneck. |
| `scorer-false-negative` | correct command, scorer rejects | `parallel-compress` (`{}` placeholder) and `count-lines-code` (`fd -e py` vs `contains:.py`) — **both fixed 2026-07-05** | **Fix the harness**, not the model — extractor + assertion bugs. |
| `binary-missing` | `binaries_exist` false, tool genuinely absent | (host-dependent) | Needs the grounding snapshot (§2a) to be reproducible. |
| `assertion-too-strict` | idiomatic alternative not matched | `k8s-restart-count-ko` (filters the RESTARTS column positionally, never says "restart") | Broaden assertions to intent, or accept as a real miss — decide per target. |
| `genuine-model-error` | wrong approach, correctly rejected | — | The only bucket that actually measures the model. |

The taxonomy is the feedback loop the user asked for: buckets 2 & 4 are **harness bugs we fix**,
bucket 1 is a **harness hardening + model signal**, bucket 5 is the **true model score**. On
2026-07-05 both scorer-false-negatives (bucket 2) were fixed on the spot and re-scored with zero model
calls: Sonnet 90→**98** (its two "misses" were the harness), 12B QAT 82→**88**. What's left is real —
Sonnet's one remaining miss (`k8s-restart-count-ko`, bucket 4/5) and 12B's six, of which four are the
JSON envelope (bucket 1). The leaderboard now measures the models, not the extractor.

**`tt bench diff <dateA> <dateB>`** — per-target flip report (pass→fail / fail→pass), gated on a
matching provenance manifest (§3). Surfaces regressions, EN↔KO deltas, tier/token/cost drift. This is
also the CI gate: fail the build if a known-good target regresses under an unchanged suite+snapshot.

**Standing signals to track per run:** EN vs KO strict gap, tier distribution (how often T2 retry
fires), tokens/latency/cost trend, and envelope-error rate per backend (a structured-output health
metric that's independent of shell skill).

---

## 6. CLI surface (proposed)

Collapse the runbook's manual loop into idempotent, resumable verbs:

```sh
tt bench run   --date 2026-07-05 --backends sonnet5-low,local-gemma4-12b-qat
#   preflight (endpoints/logins/model-list) → capture provenance → schedule by resource class
#   → generate (parallel across classes) → score → render.  Re-run = resume; skips done work.
tt bench score --date 2026-07-05           # pure re-score of recorded generations (offline, CI)
tt bench analyze --date 2026-07-05         # write analysis.json (failure taxonomy)
tt bench diff  2026-07-03 2026-07-05       # flip report, provenance-gated
```

`run` is `generate → score → render`; the other verbs are the phases in isolation so a scorer fix is
a cheap `score`+`analyze` with **zero** model calls.

---

## 7. Next steps (small, filable per AGENTS.md)

Ordered by leverage — the first two are pure harness-correctness fixes surfaced by today's outputs:

1. ~~**Extractor: stop treating `-I {}` / piped `{}` as a binary**~~ — **✅ done 2026-07-05**
   (`validate.py:_segments` skips `{}` in command position; regression tests in `test_validate.py`).
2. ~~**Assertion quality: `count-lines-code`**~~ — **✅ done 2026-07-05** (assertion broadened to
   `regex:\.py\b|\bpy\b`; scoring by intent, not spelling — see "What 'pass' means" in §1).
3. **Grounding snapshot: capture + replay** for `binaries_exist`/enrichment → host-independent scoring.
4. **JSON-envelope salvage** in the parser + an `envelope_error_rate` metric (12B-QAT motivates it).
5. **Resource-class scheduler + prompt-level concurrency** (`runner.run_eval` runs backends serially
   today) — parallel benching from §4, with `server_time_s` for latency.
6. **Provenance manifest + `bench diff`** — make cross-run comparison honest and CI-gateable.
7. **MTP lossless check** — one drafter-off run diffed against drafter-on; label the axis.

## 8. What stays non-deterministic (honest scope)

Generation is irreducibly noisy: temperature-0 is not a promise, MTP can flip tokens, hosted model
versions move. The design does **not** claim bit-identical generations. It claims: **scoring is
pure** (record once, score many), **generation is reproducible-if-cached and fully provenance-
stamped**, and **run-to-run comparison is gated on matching provenance**. Where residual generation
noise matters, the answer is repetition (N samples → median + spread), not a false promise of
determinism.
```
