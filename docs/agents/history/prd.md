# PRD — `tt history`: command history & usage tracking

**Node:** `prd-history` · **Date:** 2026-07-04 · **Status:** authored (plan-first)

**Binding sources — read these first, they win over this doc:**
- [`DECISIONS.md`](./DECISIONS.md) — the locked decisions (single source of truth; do not re-litigate).
- [`tasks.json`](./tasks.json) — the task DAG (`prd → requirement (per scope) → spec`) and file-disjoint waves.
- [`.claude/skills/to_issue/`](../../../.claude/skills/to_issue/SKILL.md) — the issue/spec shape (context → scope → checkable done-when) that each spec below matches.
- [`AGENTS.md`](../../../AGENTS.md) — one spec = one self-contained commit; squash-merge; review by comment.

This PRD is the overarching frame. It restates the decisions as **checkable requirements and
one-commit specs**, each cross-referenced to its `tasks.json` node id. It invents no behavior beyond
`DECISIONS.md`; genuine gaps are parked in [§9 Open questions](#9-open-questions), not guessed.

---

## 1. Problem

Every `tt "<request>"` today is fire-and-forget. The tier controller turns plain English into a
validated command, prints it, and forgets it. The user cannot:

- **Audit** what they asked and what TinyTalk actually ran — no record of prompt, command, backend,
  tier, danger, or spend.
- **Reuse** a command they already got — re-typing the English round-trips the model again (and pays
  again) for an answer TinyTalk already produced.

There is also **no substrate for evaluation**: the eval runner (`tinytalk/eval/`) scores a fixed
suite, but real usage — the prompts people actually type, the tiers they hit, the cost they pay — is
never captured, so we cannot measure or regression-test against live behavior.

The mental model is **Atuin / Bash history**: `↑` recalls the *command* verbatim, ready to run. We
extend that to TinyTalk's richer unit — a full prompt→command outcome with usage and cost.

## 2. Goals & non-goals

### Goals

1. **Audit** — persist every prompt→command outcome (success *and* failure) with all structured
   metadata, so the user can see what they asked, what was produced, on which backend/tier, at what
   danger and what cost.
2. **Reuse** — recall past commands verbatim: a `tt history` viewer (Scope C) and an in-shell
   prompt-mode `↑/↓` recall widget (Scope D), both Atuin-style — the recalled artifact is the
   **command**, ready to run, never a re-ask.
3. **Eval substrate** — the store is a faithful, machine-readable log of real usage (tokens, cost,
   tiers, attempts, outcomes) that future eval work can mine. This forces **usage fidelity** through
   failures and retries (Scope A2), which also fixes today's under-reporting of escalated spend.

### Non-goals (explicitly out; do not build)

- **Vector / semantic dedup — SHELVED.** Atuin and Bash use exact dedup + fuzzy find; that is the
  model. Dedup is **store-all, dedup-the-view only**, exact-normalized via the existing
  `cache.py:cache_key` normalization. No embeddings, no similarity search. (`DECISIONS.md` §Viewer,
  §Deferred/rejected.)
- **Local-model tokenizer estimation — DEFERRED.** Local OpenAI-compatible servers report real token
  counts (evidence: `docs/bench/`); we record what providers report and do not estimate. `cache_write`
  being `0` off Anthropic-family/Bedrock is a provider limitation, not a bug to paper over.
  (`DECISIONS.md` §Deferred/rejected.)
- **No plugin / sink framework.** The capture seam is exactly **one in-memory record + one thin sink
  call**. No registry, no hooks abstraction. (`DECISIONS.md` §The record.)
- **No auto-run.** Recall (viewer or widget) only ever *places* the command for the user to review and
  run; TinyTalk never executes it. (`AGENTS.md`; `DECISIONS.md` §Conventions.)

## 3. Users & primary flows

- **Audit flow** — `tt history` lists recent outcomes newest-first (dupes collapsed in the view);
  fzf-first with a full-record preview, plaintext numbered fallback when fzf is absent. Selecting an
  entry emits the command (print/copy). (Scope C: `req-C` → `spec-C1`, `spec-C2`.)
- **In-shell reuse flow** — inside prompt mode (`_TT_AI_MODE`, the state entered with `?`), `↑/↓`
  walk deduped past commands into `BUFFER`; accepting exits AI mode with the command ready to run.
  Leaving AI mode restores the default arrow bindings. (Scope D: `req-D` → `spec-D1`.)
- **Passive capture** — every invocation of `tt <request>` appends one record, best-effort, invisible
  to the user. (Scope A: `req-A` → `spec-A1`, `spec-A2`, `spec-A3`.)

## 4. The record model — *lean persistence*

**Capture-all in memory, persist-lean.** Persist **all structured metadata**; exclude **only** three
big/sensitive blobs. One in-memory record → one thin sink call. (`DECISIONS.md` §The record.)

**Excluded (never written to disk):**
1. the engineered prompt text (the assembled prompt surface),
2. the raw model response,
3. the shell-context *content* — keep only its length (`context_chars`).

The raw natural-language `prompt`, the verbatim `command`, and the `explanation` **are** kept — they
are what audit and reuse need.

**Fields** (exactly as decided; a spec may populate a field as best-effort — see §9):

| Field | Notes |
|---|---|
| `id` | monotonic integer across segments (seed from newest segment's last line) |
| `ts` | timestamp |
| `latency_ms` | end-to-end |
| `cwd` | working directory of the request |
| `mode` | `widget` \| `json` \| `plain` (from the CLI flags) |
| `backend`, `model`, `provider_kind` | resolved backend identity |
| `posture` | config posture (`local` \| `hybrid` \| `cloud`) |
| `os_fingerprint` | `platform`-derived, mirrors `cache.py:_os_fingerprint` |
| `language` | explanation language |
| `prompt_surface_hash` | hash of the assembled prompt surface (the surface itself is **not** stored) |
| `context_chars` | length of the (redacted) shell context — content excluded |
| `prompt` | raw NL request (kept) |
| `command` | verbatim generated command (kept) |
| `explanation` | model explanation (kept) |
| `danger_model` | the model's stated danger |
| `danger_final` | the validator's final classification |
| `confidence`, `needs`, `tier`, `attempts`, `escalated`, `cache_hit` | ladder/telemetry |
| `outcome` | `ok` \| `cache_hit` \| `no_command` \| `transport_error` |
| `billable` | `true` iff `outcome != cache_hit` **and** `usage.total_tokens > 0` **and** `config.price(model)` is non-zero — a fresh, priced model call that actually spent tokens; a cache hit, a zero-token transport fault, or a free/local (price-0) model is `false` (`DECISIONS.md §The record`) |
| `usage{prompt_tokens, completion_tokens, total_tokens, cached_prompt_tokens, cache_write_tokens}` | as reported |
| `cost_usd` | computed via `cost.py` |
| `cost_breakdown{fresh, cached, write, output}` | per-rate split; computed via `cost.py` (A2 producer, called in A3) — the four buckets sum to `cost_usd` |
| `attempts_detail[]{tier, backend, model, format_reached, usage, cost_usd, latency_ms, result}` | per-attempt log — **populated**: A2 emits the per-attempt ledger, A3 enriches each entry with `model` + `cost_usd` (`DECISIONS.md §The record` → Field derivations) |
| `error_kind?` | present on failures |
| `problems[]?` | validation/failure problems |

## 5. Storage (Scope A)

Per `DECISIONS.md` §Storage:

- New `tinytalk/history.py`. `default_state_dir()` → `XDG_STATE_HOME` or `~/.local/state`, then
  `/tinytalk` (mirrors `cache.py:default_cache_dir` and `config.py:default_config_path`).
- **Dated JSONL segments**: `<state>/history/YYYY-MM-DD.jsonl`, one JSON object per line.
- Append is **`O_APPEND`-atomic**; files are `0600`; **best-effort** — swallow `OSError`, never break
  a request (mirrors `cache.py:ExactCache.put`).
- **Monotonic integer `id`s** across segments (seed from the newest segment's last line).
- `read_recent(n)` — newest-first across segment files.

## 6. Retention & rotation (Scope B)

Per `DECISIONS.md` §Retention (built **last**, after A/C/D):

- Delete whole day-segments **older than 7 days** (`unlink`).
- **15 MB** total = safety trim.
- Prune only ever touches **old** files — **never rewrite the active segment** (no lock, no
  lost-append race). Extend `history.py`; do not duplicate it.

## 7. Scopes, requirements & specs (build order A → C → D → B)

Build priority is **A → C → D → B** (`DECISIONS.md`; `tasks.json.meta.scopes_build_priority`).
Requirements are authoring nodes; specs are the one-commit build units. `touches` and `done_when`
below are quoted/derived from `tasks.json`; nodes in the same build wave touch **disjoint files** and
run concurrently.

Critical path: `spec-A1 → spec-A3 → spec-C1 → spec-C2`. Max observed concurrency: 2.

### Scope A — Foundation: capture seam, store, faithful usage (`req-A`)

The in-memory capture record built at 3 write sites (→ 4 outcome states), one thin sink, dated-JSONL
storage, and usage plumbed faithfully through failures and retries. No sink framework.

| Spec | Deliverable | Touches | Done when (checkable, one commit) | Deps |
|---|---|---|---|---|
| **`spec-A1`** — History store & record model | `default_state_dir()`; dated JSONL segments; atomic best-effort append (`0600`, swallow `OSError`); record (de)serialize; `read_recent(n)`; exact-normalized **view-dedup** helper reusing `cache_key`; monotonic ids across segments | `tinytalk/history.py` (new), `tests/test_history.py` (new) | records round-trip through the store; `read_recent` returns newest-first; ids monotonic across a segment boundary | `req-A` |
| **`spec-A2`** — Faithful usage & cost plumbing | Lift `_cost` into new `tinytalk/cost.py` (shared by eval + history), re-import in `runner.py`; add `usage: Usage` to `NoValidCommand`, populate **all 4 raise sites** in `tiers.py`; accumulate per-attempt usage **and** latency across **all** attempts in `engine.generate`, carrying accumulated usage on the terminal `FormatError`; cost computed **per-attempt** then summed (`openai_compat` `total=0` → `total = prompt + completion`) | `tinytalk/cost.py` (new), `tinytalk/tiers.py`, `tinytalk/engine.py`, `tinytalk/eval/runner.py`, `tests/test_tiers.py`, `tests/test_engine.py` | a forced escalation/failure reports **non-zero tokens + cost**; eval runner still green after the `cost.py` extraction | `req-A` |
| **`spec-A3`** — Capture hooks + sink wiring + recall porcelain | Build the record at the **3 write sites** in `cli._run` (success block + `NoValidCommand` handler + generic `Exception` handler); derive cost via `cost.py`; write via the A1 sink on **every** outcome — the **4 outcome states** (`ok`/`cache_hit`/`no_command`/`transport_error`); add `tt history --porcelain` emitting recent **deduped** commands **NUL-delimited** for the widget (collapse via the A1 `dedup_key` helper, keep newest) | `tinytalk/cli.py`, `tests/test_cli_history.py` (new) | every `tt` invocation appends **exactly one** faithful record across the taxonomy states; porcelain emits recent **deduped** commands NUL-delimited | `req-A`, `spec-A1`, `spec-A2` |

### Scope C — `tt history` viewer (`req-C`)

Browse/audit records; fzf-first with full-record preview, plaintext numbered fallback; store-all,
dedup-the-view (exact-normalized); selecting yields the command (print/copy — a subprocess can't
inject into the parent shell).

| Spec | Deliverable | Touches | Done when (checkable, one commit) | Deps |
|---|---|---|---|---|
| **`spec-C1`** — `tt history` command + view-dedup + plaintext fallback | New `history` branch in `cli.main`; query via `read_recent`; collapse duplicates in the **view only** (store-all preserved); plaintext numbered listing (`id`, time, prompt→command, cost) when fzf absent | `tinytalk/cli.py`, `tests/test_cli_history.py` | `tt history` lists recent newest-first, dupes collapsed in view, plaintext path works with no fzf | `req-C`, `spec-A1`, `spec-A3` |
| **`spec-C2`** — fzf interactive picker + preview | fzf-first picker over records with a preview pane showing the full record (prompt, command, explanation, model, tokens, cost, time); selection prints the command (copy/stdout); falls back to C1's plaintext when fzf missing | `tinytalk/cli.py`, `tests/test_cli_history.py` | fzf present → interactive pick with preview; fzf absent → C1 fallback; selection emits the command | `req-C`, `spec-C1` |

### Scope D — Prompt-mode `↑/↓` command recall (`req-D`)

Inside AI mode, `↑/↓` recall past commands verbatim into the buffer (Atuin model); exits AI mode
ready to run; restores default arrows on leave.

| Spec | Deliverable | Touches | Done when (checkable, one commit) | Deps |
|---|---|---|---|---|
| **`spec-D1`** — Prompt-mode `↑/↓` recall widget | Bind `↑/↓` inside `_TT_AI_MODE` to a recall widget; on first press shell out **once** to the A3 porcelain and cache the array; `↑/↓` walk **deduped** past commands into `BUFFER`; leaving AI mode restores default arrow bindings | `tinytalk/shell/tt.zsh` | verified via `.claude/skills/test-shell-ui` (tmux + stubbed `tt`): `↑/↓` walks past commands; exit restores normal arrows | `req-D`, `spec-A1`, `spec-A3` |

### Scope B — Retention & rotation (`req-B`)

Delete day-segments >7 days old; 15 MB total safety trim; prune only touches old files (no
line-rewrite, no lock, no lost-append). Built **last**.

| Spec | Deliverable | Touches | Done when (checkable, one commit) | Deps |
|---|---|---|---|---|
| **`spec-B1`** — Retention sweep | On write, prune day-segments older than 7 days (`unlink`) and enforce a 15 MB total safety cap; cheap first-line/mtime checks; **never** rewrite the active segment | `tinytalk/history.py`, `tests/test_history_retention.py` (new) | segments >7d removed; total bounded ≤15 MB; concurrent appends never lost | `req-B`, `spec-A1` |

### Build waves (from `tasks.json.build_waves`)

1. **Wave 1:** `spec-A1` ‖ `spec-A2` — disjoint (`history.py` vs `cost.py`/`tiers`/`engine`).
2. **Wave 2:** `spec-A3` ‖ `spec-B1` — disjoint (`cli.py` vs `history.py`).
3. **Wave 3:** `spec-C1` ‖ `spec-D1` — disjoint (`cli.py` vs `tt.zsh`).
4. **Wave 4:** `spec-C2` — `cli.py`, serial after `spec-C1`.

> Note: build priority A → C → D → B is a **logical priority, not a strict sequence**. Per
> `DECISIONS.md`, `spec-B1` **may land as early as wave 2** — it only depends on `spec-A1` and is
> file-disjoint (`history.py` vs `cli.py`/`tt.zsh`) — so it is logically last but not sequenced last.
> Wave 2 above runs it alongside `spec-A3`.

## 8. Measurable acceptance criteria (PRD-level definition of done)

The feature is done when all of the following hold (verification level in brackets):

1. **Every invocation records once, best-effort** — `tt <request>` appends **exactly one** record for
   each of the four outcomes `ok` / `cache_hit` / `no_command` / `transport_error`; a store `OSError`
   never changes exit code, stdout, or stderr. [unit: `test_cli_history.py`] (`spec-A3`)
2. **Store integrity** — records round-trip; files are `0600` under `<XDG_STATE_HOME>/tinytalk/history/YYYY-MM-DD.jsonl`;
   `read_recent(n)` is newest-first; `id`s are monotonic across a segment boundary. [unit: `test_history.py`] (`spec-A1`)
3. **Usage fidelity** — a forced escalation/failure reports **non-zero** `usage` tokens **and**
   `cost_usd`; the eval runner remains green after `_cost` moves to `cost.py`. [unit: `test_tiers.py`,
   `test_engine.py`, `test_eval.py`] (`spec-A2`)
4. **Viewer** — `tt history` lists recent newest-first with duplicates collapsed **in the view only**
   (store still holds all); the plaintext numbered fallback works with **no fzf**; with fzf present,
   the interactive picker previews the full record and selection emits the command **verbatim**.
   [unit + manual] (`spec-C1`, `spec-C2`)
5. **Prompt-mode recall** — inside `_TT_AI_MODE`, `↑/↓` walk **deduped** past commands into `BUFFER`
   and exit AI mode ready to run; leaving AI mode restores the default arrow bindings; the porcelain is
   shelled out **once** and cached. [manual: tmux via `.claude/skills/test-shell-ui`] (`spec-D1`, `spec-A3`)
6. **Retention** — day-segments older than 7 days are removed; total store stays ≤ 15 MB; the sweep
   only unlinks non-active whole files, the active segment's bytes are unchanged across a sweep, and a
   record appended after the sweep is present in full. [unit: `test_history_retention.py`] (`spec-B1`)
7. **No auto-run** — no path in viewer or widget executes a generated command. [manual + code review]

## 9. Open questions

Genuinely underspecified in `DECISIONS.md` — flagged rather than guessed:

1. **`billable` derivation — RESOLVED (`DECISIONS.md §The record`).** Pinned rule:
   `billable = outcome != 'cache_hit' AND usage.total_tokens > 0 AND config.price(model) is non-zero`
   — `true` only for a fresh, priced model call that actually spent tokens; a cache hit, a zero-token
   transport fault, or a free/local (unpriced / price-0) model is `false`. `spec-A3` populates it and
   its done-when asserts it (cache hit → `false`; fresh priced T1 success → `true`).
2. **`prompt_surface_hash` capture seam — RESOLVED (owner: `spec-A2`).** The assembled prompt surface is
   built *inside* `TierController._messages`, but the record is assembled in `cli._run`, and `TierResult`
   did not expose the surface — so `cli._run` (A3) had no seam to hash it without re-assembling the
   surface, and no spec owned the field. **Resolved by assigning ownership to `spec-A2`:** `TierResult`
   gains a `prompt_surface_hash` field, computed where `_messages` assembles the surface, so A3 copies it
   straight off `result`. `context_chars` = `len(redacted session_context)` is derived directly in
   `cli._run` and was never in question.
3. **`attempts_detail[]` completeness in Scope A — RESOLVED: POPULATE per the 2026-07-04 review.**
   `spec-A2` emits a per-attempt ledger: `engine.generate` yields one entry per format-attempt
   (`format_reached`, `usage`, `latency_ms`, `result`) and `TierController` tags each with its `tier`
   and `backend`, carrying the ledger on `TierResult` **and** on the terminal
   `FormatError`/`NoValidCommand`. `spec-A3` enriches each entry with `model`
   (`config.backend(backend).model`) and per-attempt `cost_usd` (`cost(entry.usage, price(model))`),
   then persists the array. A2 stays one commit — its touched files (`cost.py`, `tiers.py`, `engine.py`)
   are unchanged, just a bigger commit. (`DECISIONS.md §The record` → Field derivations.)
4. **Capture on/off switch — RESOLVED: always-on** (no config opt-out) per the 2026-07-04 review.
   History capture is always-on and best-effort — never gated by a config flag. This was the assumed
   default and is now settled: unlike `cache.py` (gated by `cache_enabled`), history is deliberately
   not gated.
5. **Retention trigger cadence.** `spec-B1` prunes "on write" with cheap first-line/mtime checks — it
   is unspecified whether every single append triggers the stat/scan or it is throttled (e.g. once/day).

## References

- `DECISIONS.md` (binding) · `tasks.json` (DAG: `prd-history`, `req-A/C/D/B`, `spec-A1/A2/A3`,
  `spec-B1`, `spec-C1/C2`, `spec-D1`) · `START.md` (session bootstrap).
- Code seams: `tinytalk/cache.py` (`default_cache_dir`, `cache_key`, best-effort `put`),
  `tinytalk/config.py` (`default_config_path`, `posture`, `language`, `Price`),
  `tinytalk/cli.py` (`_run` hook points, `_emit_widget`),
  `tinytalk/tiers.py` (`NoValidCommand`, 4 raise sites, `TierResult`),
  `tinytalk/engine.py` (`generate` ladder, `Generation.usage`),
  `tinytalk/eval/runner.py` (`_cost` to be lifted), `tinytalk/provider/base.py` (`Usage`),
  `tinytalk/shell/tt.zsh` (`_TT_AI_MODE`, arrow bindings).
- `.claude/skills/to_issue/` (spec shape) · `.claude/skills/test-shell-ui/` (Scope D verification) ·
  `GLOSSARY.md` (prompt mode, badge, slot, backend, prompt surface).
</content>
</invoke>
