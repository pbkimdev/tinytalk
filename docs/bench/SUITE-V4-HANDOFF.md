# Handoff prompt — golden suite v3 → v4 (harder, more discriminating)

*Paste this to start a fresh session. Written 2026-07-05 after the delivery/layers/slices/stability
analysis layer landed and the field re-saturated at the top.*

---

You are refreshing TinyTalk's eval golden suite from **v3 (25 targets) to v4 (still 25 targets)**, made
more discriminating. Work per `AGENTS.md`: **plan on a GitHub issue first** (use the `to_issue` skill),
then implement — one commit per sub-issue, squash-merge.

**Read first:** `AGENTS.md` · `docs/bench/AUTOMATION.md` (scoring philosophy "What 'pass' means" + the
`tt eval analyze` tooling) · `docs/bench/RUNBOOK.md` (how a sweep runs) · `tinytalk/eval/suite.py` (the
25 targets + assertion DSL) · `tinytalk/eval/analyze.py` (the vetting instrument).

## Goal

- **REMOVE 10 saturated targets** — every benchmarked model strict-passes both EN+KO on them, so they
  carry zero discriminative power.
- **ADD 10 new HARD targets**, calibrated so **Sonnet 5 (low effort) strict-passes ≈ 80%** of v4 (down
  from ~98). That headroom is what separates models.
- Keep the shape: 25 targets, each = one **EN** + one **KO** prompt sharing one assertion set (50 prompts).

## Why now (baseline)

The field re-bunched at the top: **Sonnet 98 / GPT 98 / Qwen 96 / 26B 94 / 12B-QAT 88 / E4B 80** — the
top three are ~saturated. Sonnet's real band is **[96,100]** (3 runs; hosted serving is non-deterministic
at temp 0). To make v4 discriminate the top, Sonnet must land ~80% — i.e. **miss ≈ 10 of 50 prompts**.

## The calibration math (the rigor bar)

v4 has 50 prompts. Sonnet ≈ 80% = **~10 prompt-misses**. After removing 10 saturated targets (which
Sonnet passes) and keeping the discriminating + anchor targets (Sonnet passes almost all of those too),
**almost all of Sonnet's misses must come from the 10 NEW targets (20 prompts)** — so the new set should
**stump Sonnet on ~40–50% of its prompts**. If Sonnet passes the new targets easily, they're not hard
enough. That is the target difficulty; do not settle for "somewhat harder than v3".

## Removal pool (re-derive under current code, then pick 10)

Preliminary saturated set — all 6 models pass both langs (07-03 field + 07-05), **16 candidates**:
`extract-columns, replace-in-files, unique-frequency, watch-log, grep-recursive-ext, find-large-files,
archive-create, git-delete-branch, delete-node-modules, log-top-errors, csv-columns-transform,
k8s-crashloop, git-find-deleted, cert-expiry, ssh-stream-copy, json-extract`.

**CAVEAT — re-derive first.** The 07-03 field predates the `{}`-extractor and count-lines assertion
fixes, so it under-scores parallel-compress/count-lines. Before locking the saturated set, **re-score
every committed raw export under CURRENT code** (`tinytalk.eval.publish.rescore_row` over each
`results.json`), then recompute "all models pass both langs". The true saturated set is likely *larger*
than 16. Remove the **10 most trivially saturated** (plain listings, single-flag lookups); **keep all 9
discriminating targets** (`loop-backup-copies, k8s-restart-count, ini-section, extract-ips,
parallel-compress, count-lines-code, dns-trace, awk-group-sum, diff-sorted`) + a few easy anchors.

## What the 10 NEW targets must be

1. **HARD-BUT-STABLE.** Each needs a crisp right/wrong. After drafting, **run each candidate N≥3 times and
   drop any with `flip_rate > 0` on a deterministic model** (the local 12B-QAT is bit-stable at temp 0 —
   use it as the stability oracle via `tt eval analyze --runs`). A coin-flip target adds noise, not
   discrimination — that's exactly why Sonnet's 3 verdict-flips were on hard targets.
2. **AXIS-TARGETED, not just "hard".** Probe dimensions where models actually diverge:
   - **structured-parsing / escape-heavy** (regex/sed/awk with capture groups + backslash escapes) — the
     local models' weakest axis (12B `escape_heavy` delivery 37.5%).
   - **multi-stage composition** (3+ stages: filter → transform → join/dedup → aggregate).
   - **tools beyond coreutils / tool-choice under ambiguity** (jq nested extraction, `comm`/`join`, `xargs`
     quoting edges, date arithmetic, `tar | ssh` streaming variants, `find` pruning with `-prune`).
   - **quoting / whitespace / filename edge cases.**
3. **Score by INTENT, not spelling** (the v3 lesson — `contains:.py` wrongly rejected `fd -e py`).
   Assertions must accept **every** correct spelling and reject wrong ones: use `uses_any` / `regex`
   alternations covering idiomatic variants (`fd`/`find`/`rg`, `jq`/`python`, …). A brittle assertion on a
   hard target = grader noise, not model signal.
   - **Prefer execution-based grading where feasible.** For targets whose output is deterministic and
     fixture-able (e.g. "count lines over this fixed tree" → one integer), grade by **running the candidate
     in a disposable sandbox against a golden output** instead of regex-matching the command text. This is
     the real discrimination lever (Terminal-Bench / InterCode paradigm; see AUTOMATION.md §"What pass
     means" and the execution-oracle discussion). It does NOT violate "never auto-run" — that constrains the
     *product's user shell*, not an isolated eval sandbox. Scope this as its own sub-issue if you take it on.
4. Each target: a realistic dev shell task, `expected_danger` set correctly, and **natural EN + KO** phrasings
   (idiomatic in each language, not translations of each other).

## Definition of done

1. v4 suite in `tinytalk/eval/suite.py` (25 targets; update the module docstring's v4 rationale).
   `test_suite_shape` / `test_suite_is_parallel_en_ko_pairs` still green.
2. Run the full v4 sweep (RUNBOOK) for **Sonnet-low** (ideally the whole field): **Sonnet strict-pass
   ≈ 80% (±5)**. Too high → replace the easiest new targets; far too low → the hardest is impossible, not
   discriminating — ease it.
3. `tt eval analyze` on the v4 result confirms the new targets are **stable** (flip_rate ~0 on 12B) and
   **spread the field** (each new target passed by some models, missed by others — not all-pass, not all-fail).
4. Publish v4 under `docs/bench/<new-date>/`; update the RUNBOOK roster/run-log and `analyze.py`'s
   `CATEGORY` map + `ESCAPE_HEAVY` set for any new targets (the `test_category_map_complete` guard will
   fail until you do).

## Guardrails

- Never let the eval execute a *product* command; execution-based grading, if added, runs candidates in an
  **isolated eval sandbox against fixtures** — a separate context from the product.
- Cross-run comparison is only valid under matching scoring code (the v3→v4 numbers are not comparable to
  the raw v3 numbers). State that when you publish.
