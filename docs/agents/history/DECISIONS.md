# `tt history` — Locked Design Decisions

Binding context for the build/author workflows and any fresh session. These were settled in a
`/grill-me` session (2026-07-04). **Do not re-litigate them.** The task graph and wave ordering
live in [`tasks.json`](./tasks.json).

## What we're building

`tt history`: persist every prompt→command outcome so the user can **audit and reuse** past
commands (Atuin / Bash model — ↑ recalls the *command* verbatim, ready to run). Also the substrate
for future eval. Build order **A → C → D → B** — a logical priority, not a strict sequence: **B may
land as early as wave 2** (it only depends on A1, and is file-disjoint from A3/C/D in `history.py`); it
is logically last but not sequenced last.

## Storage (Scope A1)

- New `tinytalk/history.py`. `default_state_dir()` → `XDG_STATE_HOME` or `~/.local/state`, `/tinytalk`
  (mirror `cache.py:default_cache_dir` / `config.py:default_config_path`).
- **Dated JSONL segments**: `<state>/history/YYYY-MM-DD.jsonl`, one JSON object per line.
- Append-only, `O_APPEND`-atomic; files `0600`; **best-effort** (swallow `OSError`, never break a
  request — mirror `cache.py:ExactCache.put`).
- **Monotonic integer `id`s** across segments (seed from the newest segment's last line).
- `read_recent(n)`: newest-first across segment files.

## Retention (Scope B1)

- Delete whole day-segments **older than 7 days** (`unlink`); **15 MB** total = safety trim.
- Prune only ever touches **old** files — **never rewrite the active segment** (no lock, no
  lost-append race). Extend `history.py`; do not duplicate it.

## The record — *lean persistence*

Persist **all structured metadata**; exclude **only** three big/sensitive blobs: the engineered
prompt text, the raw model response, and the shell-context *content* (keep only its length).
Capture-all in-memory, persist-lean. The capture seam is **one in-memory record + one thin sink
call** — **no plugin framework**.

Fields: `id`, `ts`, `latency_ms`, `cwd`, `mode` (widget/json/plain), `backend`, `model`,
`provider_kind`, `posture`, `os_fingerprint`, `language`, `prompt_surface_hash`, `context_chars`,
`prompt` (raw NL), `command` (verbatim), `explanation`, `danger_model`, `danger_final`,
`confidence`, `needs`, `tier`, `attempts`, `escalated`, `cache_hit`,
`outcome` (`ok|cache_hit|no_command|transport_error`), `billable`,
`usage{prompt_tokens,completion_tokens,total_tokens,cached_prompt_tokens,cache_write_tokens}`,
`cost_usd`, `cost_breakdown{fresh,cached,write,output}`,
`attempts_detail[]{tier,backend,model,format_reached,usage,cost_usd,latency_ms,result}`,
`error_kind?`, `problems[]?`.

**`billable` rule (pinned):** `billable = outcome != 'cache_hit' AND usage.total_tokens > 0 AND
config.price(model) is non-zero` — `true` only for a fresh, priced model call that actually spent
tokens; a cache hit, a zero-token transport fault, or a free/local (unpriced / price-0) model is
`false`. Populated in `spec-A3`.

**Field derivations (pinned at the 2026-07-04 review — settle genuine gaps DECISIONS left open; do
not re-litigate):**
- **`prompt_surface_hash`** — `spec-A2` adds `prompt_surface_hash: str` to `TierResult`, computed where
  `TierController._messages` assembles the surface (empty for a pure cache hit, which assembles none).
  `spec-A3` copies it off `result`; the surface *text* itself is never stored (lean persistence).
- **`cost_breakdown{fresh,cached,write,output}`** — `spec-A2` adds a `cost_breakdown(usage, price)`
  producer beside `_cost` in `cost.py`, returning the four per-rate USD buckets `_cost` already sums.
  `spec-A3` prices **each attempt at its own backend's rate** and sums the breakdowns element-wise, so
  the headline `cost_usd` stays **exact under a mixed-price escalation** (a free local T1 and a priced
  cloud T2 are each billed at their own rate — §Usage fidelity: "cost computed **per-attempt, then
  summed**"). For a single backend this equals pricing the accumulated usage once. **Invariant:** the
  four buckets sum to `cost_usd`.
- **`attempts_detail[]` — POPULATED in v1** (Paul's review ruling; *not* deferred): `spec-A2` emits a
  per-attempt ledger — `engine.generate` yields one entry per format-attempt (`format_reached`, `usage`,
  `latency_ms`, `result`) and `TierController` tags each with its `tier` and `backend`, carrying the
  ledger on `TierResult` **and** on the terminal `FormatError`/`NoValidCommand`. `spec-A3` enriches each
  entry with `model` (`config.backend(backend).model`) and per-attempt `cost_usd`
  (`cost(entry.usage, price(model))`), then persists the array. This is the "cost computed per-attempt,
  then summed" of §Usage fidelity made concrete.

## Usage fidelity — do BOTH (Scope A2)

- Lift `_cost` from `eval/runner.py` into new **`tinytalk/cost.py`** (shared by eval + history);
  re-import in `runner.py` (keep eval green).
- Add `usage: Usage` to `NoValidCommand`; populate at **all 4 raise sites** in `tiers.py` with the
  accumulated usage.
- In `engine.generate`, **accumulate usage + per-attempt latency across ALL attempts** (not just the
  winning parse); carry the accumulated usage on the terminal `FormatError`.
- Cost computed **per-attempt** (exact under escalation), then summed. `openai_compat` can report
  `total=0` with `prompt/completion>0` → compute `total = prompt + completion`.
- A2 also delivers (see **Field derivations** above): `cost_breakdown(usage, price)` in `cost.py`;
  `prompt_surface_hash` on `TierResult`; and the **per-attempt `attempts_detail` ledger** emitted by
  `engine.generate` and tagged (`tier`, `backend`) by the controller, carried on `TierResult` and the
  terminal `FormatError`/`NoValidCommand`. These fit within A2's already-listed touched files
  (`cost.py`, `tiers.py`, `engine.py`).

## Write site + recall porcelain (Scope A3)

- `cli._run`: build the record at the **3 write sites** and write via the A1 sink on **every** outcome —
  the **4 outcome states** (success block → `ok`/`cache_hit` by tier; `NoValidCommand` handler →
  `no_command`/`transport_error` by `exc.kind`; generic `Exception` handler → `transport_error`);
  derive cost via `cost.py`.
- Add a machine-readable recall command — `tt history --porcelain` — emitting recent commands
  **NUL-delimited** for the zsh widget. **Porcelain emits DEDUPED commands** (Paul's review ruling):
  collapse duplicates with the A1 `dedup_key` helper (exact-normalized, keep newest) so the Scope D
  widget walks pre-deduped commands and does **no** dedup of its own.

## Viewer (Scope C)

- New `history` branch in `cli.main`. **fzf-first** picker with a full-record preview; **plaintext
  numbered fallback** (`id`, time, prompt→command, cost) when fzf is absent.
- **Dedup = store-all, dedup-the-VIEW only**, exact-normalized via the normalization in
  `cache.py:cache_key`. **No vector/embedding dedup.**
- Selecting an entry yields the **command** (print/copy — a subprocess can't inject into the parent
  shell; that's the widget's job).

## Recall widget (Scope D)

- `tinytalk/shell/tt.zsh`: bind ↑/↓ inside `_TT_AI_MODE` to a recall widget; on first press shell out
  **once** to `tt history --porcelain` and cache the array; ↑/↓ walk **deduped past commands** into
  `BUFFER`; leaving AI mode **restores default arrow bindings**. Recall = command verbatim, exits AI
  mode ready to run.
- Verify empirically via `.claude/skills/test-shell-ui` (tmux + stubbed `tt`).

## Deferred / rejected

- **Local-model tokenizer estimation — DEFER.** Local OpenAI-compat servers report real token counts
  (evidence: `docs/bench/`). `cache_write` only exists on Anthropic-family + Bedrock; `0` elsewhere is
  a provider limitation, not a bug.
- **Vector/semantic dedup — SHELVED.** Atuin/Bash use exact dedup + fuzzy; that's the model.

## Conventions

- Python via `uv`; run tests `uv run python -m pytest` (bare `pytest` has stale shebangs).
- One spec = one self-contained commit; squash-merge the PR; review by comment; `main` behind branch
  protection.
- Never let `tt` auto-run generated shell commands. Match existing style; touch only what the spec needs.
