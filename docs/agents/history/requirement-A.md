# Requirement A — Foundation: capture seam, store, faithful usage

> Binding sources: [`DECISIONS.md`](./DECISIONS.md) (§Storage, §The record, §Usage fidelity, §Write
> site) and [`tasks.json`](./tasks.json) (`req-A`, `spec-A1/A2/A3`). The decisions are **fixed** — do
> not re-litigate. This doc turns them into three commit-sized specs in the repo's
> [`to_issue`](../../../.claude/skills/to_issue/SKILL.md) shape (Context → Scope → Files → Done-when).

## Context

`tt history` persists every prompt→command outcome so a user can **audit and reuse** past commands
(Atuin/Bash model), and gives future eval a substrate. Scope A is the foundation the other scopes
(C viewer, D recall widget, B retention) build on: a **store** to write to, a **record** to write,
and **faithful usage/cost** so the persisted numbers are true even when a request retried or failed.

Three principles from `DECISIONS.md` drive every spec below:

1. **Lean persistence.** Capture *all* structured metadata in memory; persist all of it **except**
   three big/sensitive blobs — the engineered prompt text, the raw model response, and the
   shell-context *content* (keep only its length). The seam is **one in-memory record + one thin sink
   call**. No plugin/sink framework.
2. **Best-effort, never break a request.** Persistence mirrors `cache.py:ExactCache.put`: append is
   atomic and swallows `OSError`; a history failure must never change what `tt` prints or its exit code.
3. **Faithful spend.** A retried success or an outright failure must still report the tokens and cost
   it actually burned — not zero, and not just the winning attempt.

### How the three specs line up (from `tasks.json`)

| Spec | Title | Build wave | Touches (disjoint within a wave) |
|---|---|---|---|
| **A1** | History store & record model | 1 | `tinytalk/history.py` (new), `tests/test_history.py` (new) |
| **A2** | Faithful usage & cost plumbing | 1 | `tinytalk/cost.py` (new), `tinytalk/tiers.py`, `tinytalk/engine.py`, `tinytalk/eval/runner.py`, `tests/test_tiers.py`, `tests/test_engine.py` |
| **A3** | Capture hooks + sink wiring + recall porcelain | 2 | `tinytalk/cli.py`, `tests/test_cli_history.py` (new) |

A1 ‖ A2 run concurrently (disjoint files). A3 is wave 2 because it consumes **both**: it builds the
A1 record from the A2 usage/cost signals inside `cli._run`.

## Dependencies

- **Blocked by:** `prd-history` (the PRD/requirement author wave).
- **A1, A2 block A3.** A3 imports the A1 store + record and the A2 `cost.py`/usage plumbing.
- **A1 blocks B1** (retention extends `history.py`) and **A3 blocks C1/D1** (viewer reads the store;
  the widget shells out to A3's porcelain). Those live in later waves — out of scope here.

## Out of scope / Deferred (whole of Scope A)

- **Retention/rotation** (delete >7d segments, 15 MB trim) — Scope B (`spec-B1`), extends `history.py`.
- **`tt history` viewer, fzf picker, view-dedup rendering** — Scope C (`spec-C1/C2`).
- **↑/↓ recall widget in `tt.zsh`** — Scope D (`spec-D1`).
- **Local-model tokenizer estimation** and **vector/semantic dedup** — deferred/shelved per
  `DECISIONS.md §Deferred`.

## Open questions (whole of Scope A)

Four fields in the spec-A1 record table (below) have a **locked shape** but an **unresolved
derivation** — parked here rather than guessed, and resolved before the spec that populates them. The
first three restate items already tracked in [`prd.md` §9](./prd.md#9-open-questions); the fourth
(`cost_breakdown`) is **not** tracked there and is raised here.

1. **`billable` derivation — RESOLVED (`DECISIONS.md §The record`).** Pinned rule:
   `billable = outcome != 'cache_hit' AND usage.total_tokens > 0 AND config.price(model) is non-zero`
   — `true` only for a fresh, priced model call that actually spent tokens; a cache hit, a zero-token
   transport fault, or a free/local (price-0) model is `false`. `spec-A3` populates it; its done-when
   asserts it (cache hit → `false`; fresh priced T1 success → `true`). (`prd.md` §9, item 1.)
2. **`prompt_surface_hash` capture seam — RESOLVED (owner: `spec-A2`).** The assembled prompt surface is
   built inside `TierController._messages`, but the record is assembled in `cli._run`, and `TierResult`
   did not expose the surface — so `cli._run` had no seam to hash it and no spec owned the field. Pinned:
   `spec-A2` adds `prompt_surface_hash` to `TierResult` (computed where `_messages` assembles the
   surface); `spec-A3` copies it off `result`, never re-assembling the surface. (`prd.md` §9, item 2.)
3. **`attempts_detail[]` completeness — RESOLVED: POPULATED in v1 per the 2026-07-04 review** (Paul's
   ruling; *not* deferred). `spec-A2` emits a per-attempt ledger: `engine.generate` yields one entry per
   format-attempt (`format_reached`, `usage`, `latency_ms`, `result`) and `TierController` tags each with
   its `tier` and `backend`, carrying the ledger on `TierResult` **and** on the terminal
   `FormatError`/`NoValidCommand`. `spec-A3` enriches each entry with `model`
   (`config.backend(backend).model`) and per-attempt `cost_usd` (`cost(entry.usage, price(model))`), then
   persists the array. (`DECISIONS.md §The record` → Field derivations; `prd.md` §9, item 3.)
4. **`cost_breakdown{fresh,cached,write,output}` derivation — RESOLVED.** The per-rate split is exactly
   the decomposition of `cost_usd`: `_cost` already sums four buckets (fresh, cached-read, cache-write,
   output), so `spec-A2` adds a `cost_breakdown(usage, price)` producer beside `_cost` in `cost.py`
   returning those four buckets, and `spec-A3` calls it over the same accumulated `usage`. The
   record-level split is **not** computed per-attempt (the `attempts_detail[]` ledger carries a per-attempt
   `cost_usd` scalar, item 3, but **not** a per-attempt four-bucket split). Acceptance: `spec-A2`'s
   done-when asserts the four buckets sum to `cost_usd`. **Not tracked in `prd.md` §9.**

---

## spec-A1 — History store & record model

### Context

The store is the sink every capture writes to and the source every reader (C viewer, D widget) reads
from. It mirrors the shapes already proven in `cache.py`/`config.py`: an XDG-rooted state dir, atomic
best-effort writes, and a strict-parse-on-read discipline. This spec delivers the **store + the record
dataclass** only; it does not wire capture into `cli.py` (that is A3) and does not prune (that is B1).

### Scope

**State dir + segment layout**

- New module `tinytalk/history.py`.
- `default_state_dir() -> Path`: `XDG_STATE_HOME` or `~/.local/state`, `/tinytalk` — mirroring
  `cache.py:default_cache_dir` and `config.py:default_config_path` exactly (same env-override → expand
  → `/tinytalk` shape).
- **Dated JSONL segments**: `<state>/history/YYYY-MM-DD.jsonl`, one JSON object per line, keyed by the
  record's date.

**Append (write path)**

- Append-only, `O_APPEND`-atomic (`os.open(..., O_WRONLY|O_APPEND|O_CREAT, 0o600)` then a single
  `write` of `json.dumps(record)+"\n"`); files created `0600`; parent dirs `mkdir(parents=True)`.
- **Best-effort**: swallow `OSError` and return without raising — never break a request (mirror
  `ExactCache.put`). No lock (single atomic append; the active segment is never rewritten — the B1
  contract).

**Monotonic ids**

- Integer `id`, **monotonic across segments**. On open, seed the next id from the **newest segment's
  last line** (highest existing `id + 1`); empty store starts at 1. Ids never reset at a day boundary.

**Record model**

- A `dataclass` (`HistoryRecord` or equivalent) carrying every field in `DECISIONS.md §The record`,
  with `to_dict`/`from_dict` (or `asdict` + a strict loader) so records **round-trip** through JSONL.
- Persist all fields **except** the three excluded blobs (never add fields for the engineered prompt
  text, the raw model response, or the shell-context content — only `context_chars`).
- The full field set and each field's source (populated by A3) is fixed as:

  | Field | Type | Source (populated in A3) |
  |---|---|---|
  | `id` | int | store-assigned, monotonic |
  | `ts` | str (ISO-8601 UTC) | write time |
  | `latency_ms` | int | wall time around `controller.suggest` |
  | `cwd` | str | `request.cwd` |
  | `mode` | str `widget\|json\|plain` | which output flag was set |
  | `backend` | str | winning/last backend name (`TierResult.backend` / `exc.backend`) |
  | `model` | str | model of the winning backend — `config.backend(result.backend).model`; A3 resolves it (**not** carried on `TierResult`) |
  | `provider_kind` | str | `backend_cfg.kind` |
  | `posture` | str | `config.posture` |
  | `os_fingerprint` | str | same value as `cache._os_fingerprint()` |
  | `language` | str | `config.language` |
  | `prompt_surface_hash` | str | `TierResult.prompt_surface_hash` — computed in A2 where `_messages` assembles the surface; A3 copies it off `result` |
  | `context_chars` | int | `len(redacted session_context)` |
  | `prompt` | str | raw NL request (kept) |
  | `command` | str \| null | `suggestion.command` (kept verbatim) |
  | `explanation` | str \| null | `suggestion.explanation` |
  | `danger_model` | str \| null | model's stated `suggestion.danger` |
  | `danger_final` | str \| null | `validation.danger` |
  | `confidence` | float \| null | `suggestion.confidence` |
  | `needs` | list[str] | `suggestion.needs` |
  | `tier` | int \| null | `TierResult.tier` (0/1/2) |
  | `attempts` | int | `TierResult.attempts` |
  | `escalated` | bool | tier reached T2 |
  | `cache_hit` | bool | tier == 0 |
  | `outcome` | str `ok\|cache_hit\|no_command\|transport_error` | outcome taxonomy |
  | `billable` | bool | pinned rule (`DECISIONS.md §The record`): `outcome != 'cache_hit'` **and** `usage.total_tokens > 0` **and** `config.price(model)` non-zero (A3) |
  | `usage` | obj `{prompt_tokens,completion_tokens,total_tokens,cached_prompt_tokens,cache_write_tokens}` | accumulated `Usage` (A2) |
  | `cost_usd` | float | `cost.py` (A2) via `config.price(model)` (A3) |
  | `cost_breakdown` | obj `{fresh,cached,write,output}` | `cost.py:cost_breakdown(usage, price)` (A2), called by A3 via `config.price(model)`; its four buckets sum to `cost_usd` |
  | `attempts_detail` | list[obj `{tier,backend,model,format_reached,usage,cost_usd,latency_ms,result}`] | populated — A2 emits the per-attempt ledger (`tier`/`backend`/`format_reached`/`usage`/`latency_ms`/`result`), A3 enriches each entry with `model` + `cost_usd`; shape unchanged |
  | `error_kind` | str \| null (optional) | `NoValidCommand.kind` on failure |
  | `problems` | list[str] (optional) | failure problems |

  > A1 fixes the **shape** (all fields present, typed, round-trippable); A3 fixes **population**. The
  > fields pointing to [Open questions](#open-questions-whole-of-scope-a) have a locked shape but an
  > unresolved derivation — resolve each per that section before the spec that populates it builds.

**Readers**

- `read_recent(n: int) -> list[HistoryRecord]`: newest-first across segment files (walk dated segments
  newest→oldest, lines within a segment bottom→top), tolerant of a corrupt/partial trailing line (skip
  it, never raise — a half-written line must not break a read). Stop once `n` collected.
- `dedup_key(command: str) -> str`: the exact-normalized key C/D use to collapse duplicates **in the
  view only** — same normalization as `cache.py:cache_key` (`strip` → `lower` → collapse runs of
  whitespace to one space). Implemented locally in `history.py` (cache.py is outside this spec's
  touched files; do not refactor it).

### Files

- `tinytalk/history.py` (new)
- `tests/test_history.py` (new)

### Definition of Done — unit

- [ ] A record with every field round-trips: `from_dict(to_dict(r)) == r`, and none of the three
      excluded blobs appear in the serialized JSON.
- [ ] `default_state_dir()` honors `XDG_STATE_HOME` and falls back to `~/.local/state/tinytalk`
      (parametrized like the `cache.py`/`config.py` tests).
- [ ] Appended segment files are mode `0600`; two appends on the same day land in one
      `YYYY-MM-DD.jsonl`; a different date opens a second segment.
- [ ] `read_recent(n)` returns newest-first and respects `n`, spanning a segment boundary.
- [ ] **Monotonic ids across a segment boundary**: writing on day N then day N+1 yields strictly
      increasing ids with no reset (seed = newest segment's last `id + 1`).
- [ ] A forced `OSError` on append (e.g. unwritable dir) is swallowed — the call returns, raises
      nothing.
- [ ] A corrupt trailing line in the newest segment is skipped by both `read_recent` and id-seeding.
- [ ] `dedup_key` collapses case/whitespace variants of the same command to one key.

---

## spec-A2 — Faithful usage & cost plumbing

### Context

Today usage/cost is faithful only on the eval happy path. Two gaps make persisted spend lie:

1. `NoValidCommand` carries no usage, so a **failed** request (or a success reached only after
   escalation whose failing attempts spent tokens) reports **zero** tokens/cost.
2. `engine.generate` reports `usage` from the **winning parse only** (`Generation.usage =
   completion.usage`); tokens burned by earlier format-retries in the degradation ladder are dropped.

This spec makes spend faithful through failures and retries, and lifts the cost function into a shared
module so history and eval compute cost identically. No behavior change to what `tt` prints.

### Scope

**Lift `_cost` → `tinytalk/cost.py`**

- Move the `_cost(usage, price) -> float` body (currently `eval/runner.py:34`) verbatim into new
  `tinytalk/cost.py` (shared by eval + history).
- **Keep eval green** by re-importing in `runner.py` under the name it already exports:
  `from tinytalk.cost import cost as _cost` (or keep the name `_cost` in `cost.py`). `eval/report.py`
  does `from tinytalk.eval.runner import ... _cost` — the re-export must preserve `runner._cost` so
  `report.py` and `test_report.py` stay green **without editing them**.

**`cost_breakdown` producer → `tinytalk/cost.py` (resolves OQ#4)**

- Add a small `cost_breakdown(usage: Usage, price) -> dict` beside `_cost`, returning the four per-rate
  USD buckets `{fresh, cached, write, output}` — the **same four addends `_cost` already sums**:
  `fresh = max(prompt_tokens - cached_prompt_tokens - cache_write_tokens, 0)` at `input_per_mtok`;
  `cached` at `cached_input_per_mtok` (→ `input_per_mtok` when unset); `write` at `cache_write_per_mtok`
  (→ `input_per_mtok` when unset); `output` at `output_per_mtok`; each `/1e6`.
- **Invariant:** the four buckets sum to `cost(usage, price)` — `cost_breakdown` is exactly the
  decomposition of `cost_usd`, so the split and the scalar can never drift. This is the producer for the
  record's `cost_breakdown` field (A1 fixed only its shape); `spec-A3` calls it over the accumulated
  `usage`. It lives beside `_cost` so both share the cached/cache-write fallback and stay in one commit.

**`usage` on `NoValidCommand`, populated at all 4 raise sites**

- Add `usage: Usage = Usage()` to `NoValidCommand.__init__` (store on the instance).
- Populate at **all four** `raise NoValidCommand` sites in `tiers.py` (currently lines 175, 183, 187,
  202) with the **accumulated** `usage` at that point — carrying spend from every tier attempted so
  far, including the failed ones.
- Fold failed-attempt spend into the running total: the T1/T2 `except FormatError` branches must read
  the FormatError's carried usage — `getattr(exc, "usage", Usage())` (see engine change) — and add it
  into `usage` **before** constructing `NoValidCommand`, so failure records are non-zero. **Not** via
  `_accumulate`: that helper takes a `Generation` (it reads `gen.usage` *and* `gen.attempts`) and
  cannot fold a bare `Usage` carried on an exception. Fold with a direct field-wise `Usage`+`Usage`
  add — either a small helper (e.g. `_fold_usage(a, b)`) that `_accumulate` can reuse for its usage
  half, or an inline per-field sum. `ProviderError` carries no usage (transport failed before tokens)
  — those sites report the usage accumulated so far.

**Accumulate across ALL attempts in `engine.generate`**

- Sum `usage` across **every** attempt in the degradation ladder (not just the winning parse) and
  report the accumulated `Usage` on `Generation`. Track **per-attempt latency** alongside (needed by
  the record's per-attempt ledger).
- **Emit a per-attempt ledger.** Alongside the accumulation, `engine.generate` yields **one entry per
  format-attempt** — `format_reached`, `usage`, `latency_ms`, `result` — and the controller tags each
  entry with its `tier` and `backend`, carrying the ledger on `TierResult` **and** on the terminal
  `FormatError`/`NoValidCommand`. `spec-A3` later enriches each entry with `model` + per-attempt
  `cost_usd`. (`DECISIONS.md §The record` → Field derivations.)
- On ladder exhaustion, construct the terminal `FormatError` in `engine.generate` and **attach the
  accumulated usage as a dynamic attribute on the instance** — `err = FormatError(...); err.usage =
  accumulated; raise err` (attach the accumulated attempts/latency the same way). `FormatError` is a
  plain `ValueError` subclass with no `__slots__`, so this needs **no change to `parsing.py`** (which
  is outside this spec's touched files — do **not** add a `usage` field there, and do not add it to
  the touches). `tiers.py` reads it back with `getattr(exc, "usage", Usage())` (above), so a
  `FormatError` raised without usage — or a `Usage`-less transport failure — safely defaults to
  `Usage()` and stays as-is.
- **Total-token coalescing**: when folding each attempt's usage, set
  `total_tokens = total_tokens or (prompt_tokens + completion_tokens)` per attempt before summing —
  `openai_compat` can report `total=0` with `prompt/completion>0`. This lives in the A2-owned
  accumulation code (`engine.py`/`tiers.py`), **not** in the provider (provider files are out of scope).

**Surface hash on `TierResult` (the record's capture seam)**

- Add `prompt_surface_hash: str = ""` to `TierResult` (`tiers.py:43`). Compute the hash where
  `_messages` assembles the prompt surface — hash the assembled surface (system + user messages) — and
  carry it on every `TierResult` the controller returns (empty for a pure cache hit, which assembles no
  surface). This gives `cli._run` (A3) a seam to copy the hash straight off `result` instead of
  re-assembling the surface; the surface text itself is **never** stored (lean persistence — only its
  hash rides on the result). Resolves PRD §9 OQ#2 by making `spec-A2` the owner of the field.

### Files

- `tinytalk/cost.py` (new)
- `tinytalk/tiers.py`
- `tinytalk/engine.py`
- `tinytalk/eval/runner.py` (re-import only)
- `tests/test_tiers.py`
- `tests/test_engine.py`

### Out of scope / Deferred

- **Per-attempt** `cost_breakdown` decomposition — the **record-level** `cost_breakdown` **is in scope**
  (the `cost.py` producer above resolves OQ#4); the `attempts_detail[]` ledger carries a per-attempt
  `cost_usd` scalar (added by A3) but **not** a per-attempt four-bucket split. Do not compute a
  per-attempt breakdown here.
- The **`attempts_detail[]` ledger IS in scope for A2** (Paul's 2026-07-04 review — no longer deferred):
  `engine.generate` emits one entry per format-attempt (`format_reached`, `usage`, `latency_ms`,
  `result`) and the controller tags each with `tier`/`backend`, carried on `TierResult` and the terminal
  `FormatError`/`NoValidCommand` (see the accumulation scope above). `spec-A3` enriches each entry with
  `model` + per-attempt `cost_usd` and persists the array. This keeps A2 one commit (a bigger one) within
  its already-listed touched files (`cost.py`, `tiers.py`, `engine.py`).
- Any change to provider adapters (`provider/openai_compat.py` etc.).

### Definition of Done — unit + eval regression

- [ ] `NoValidCommand.usage` exists and is set at **all 4** raise sites; a forced escalation that ends
      in `NoValidCommand` reports **non-zero** `usage` and non-zero `cost` (stub providers returning
      usage on failing attempts).
- [ ] `engine.generate` with a first-format failure then a later-format success reports **summed**
      usage (both attempts), not just the winner's.
- [ ] Ladder exhaustion raises a `FormatError` that **carries** the accumulated usage; `tiers.py`
      folds it so the resulting `NoValidCommand` is non-zero.
- [ ] `engine.generate` emits **one ledger entry per attempt** (`format_reached`, `usage`, `latency_ms`,
      `result`); the controller tags each entry with `tier`+`backend`, and the ledger is carried on
      **both** `TierResult` and the terminal `FormatError`/`NoValidCommand`.
- [ ] `total_tokens` coalesces to `prompt+completion` when an attempt reports `total=0`.
- [ ] `cost_breakdown(usage, price)` returns `{fresh, cached, write, output}` whose four buckets
      **sum to `cost(usage, price)` (== `cost_usd`)** for a usage exercising all four rates (fresh +
      cached-read + cache-write prompt tokens and completion tokens).
- [ ] `cost.py` reproduces `_cost` bit-for-bit; **existing eval + report tests pass unchanged**
      (`uv run python -m pytest tests/test_eval.py tests/test_report.py tests/test_tiers.py
      tests/test_engine.py`).
- [ ] `TierResult.prompt_surface_hash` is a stable hash of the assembled surface for grounded/escalated
      results (empty for a pure cache hit); A3 can read it off `result` without re-assembling the surface.

---

## spec-A3 — Capture hooks + sink wiring + recall porcelain

### Context

With the A1 store/record and A2 faithful usage in place, this spec makes **every** `tt` request emit
exactly one faithful record, and adds the machine-readable recall feed the D widget consumes. All of
it lives in `cli._run`, plus the `history` subcommand skeleton (dispatch branch, `build_history_parser`,
`_history`) that serves the porcelain path — Scope C extends that same skeleton. Capture is
best-effort and must never change what `tt` prints or its exit code.

### Scope

**Build + write the record on every outcome**

- Time the request: wrap `controller.suggest` with a `perf_counter` for `latency_ms`.
- Build the A1 record and write it via the A1 sink at the **3 write sites** covering the **four outcome
  states** (`DECISIONS.md §Write site`):
  - **success block** → `ok` (tier > 0) **and** `cache_hit` (tier == 0), distinguished by
    `result.tier`;
  - **`NoValidCommand` handler** → `no_command` or `transport_error`, by `exc.kind` (map `kind`:
    `"transport"` → `transport_error`, else `no_command`), carrying `exc.usage`, `exc.problems`,
    `exc.kind`, `exc.backend`, and `exc.last` (for `command`/`danger_model` when present);
  - **generic `Exception` handler** → `transport_error`.
- **Resolve `backend`/`model` for the record.** The backend actually used is the *name* on the
  result (`result.backend`) — the escalation provider under tier 2 — or `exc.backend` on the failure
  paths. `model` is **not** carried on `TierResult`, and `cli._run` retains only the default
  `backend_cfg` and `escalation_name` (the escalation cfg and its `.model` are transient — cli.py:337).
  So map the winning name back to its config for the model:
  `model = config.backend(result.backend).model` (`config.backend(exc.backend).model` on the failure
  paths). Use that resolved `model` both in the record and for cost.
- Derive `cost_usd` **and** `cost_breakdown` via `cost.py` (A2) using `config.price(model)`:
  `cost(usage, price)` for the scalar `cost_usd`, and `cost_breakdown(usage, price)` for the record's
  four-bucket `{fresh, cached, write, output}` split, both over the same accumulated `usage`.
- Derive `billable` (pinned rule, `DECISIONS.md §The record`): `outcome != 'cache_hit'` **and** `usage.total_tokens > 0` **and** `config.price(model)` is non-zero — `true` only for a fresh, priced model call that actually spent tokens.
- **`attempts_detail[]` — POPULATED.** A3 populates `attempts_detail`: for each ledger entry carried on
  the result/exception (emitted by A2), enrich it with `model` (`config.backend(entry.backend).model`)
  and per-attempt `cost_usd` (`cost(entry.usage, price(model))`), then persist the array. (`DECISIONS.md
  §The record` → Field derivations.)
- **Never break the request**: the sink already swallows `OSError`; additionally, wrap record-build +
  write so any capture-side error is swallowed and the original stdout/stderr/exit code is untouched.
- **`ConfigError` writes no record.** A config-missing/invalid failure happens before a request
  outcome exists and is **not** in the `ok|cache_hit|no_command|transport_error` taxonomy — leave that
  handler as-is. "Exactly one record per invocation" means per invocation that reached the request
  path. Prompt (`request_text`) and `cwd` (`os.getcwd()`) are always available for the other paths,
  so even an early transport fault yields a record with those fields set.

**`history` subcommand skeleton + recall porcelain for the widget**

- **A3 introduces the `history` subcommand skeleton** that Scope C later extends. Add a `history`
  dispatch branch in `main()` (mirroring the existing `if argv[:1] == ["eval"]` pattern at
  `cli.py:127`), a `build_history_parser()` carrying the **`--porcelain`** flag, a `history` entry in
  the parser `epilog` command list, and a `_history` handler. Keep heavy imports inside the handler
  (cold-start budget, per the `cli.py` module docstring).
- `_history` here handles the **`--porcelain`** path only: read recent records via A1 `read_recent`
  and emit recent **commands NUL-delimited** (`\0`-separated) to stdout for the zsh widget to slurp in
  one shot. **Porcelain emits DEDUPED commands** — collapse duplicates via the A1 `dedup_key` helper
  (exact-normalized, keep newest) so the Scope D widget walks pre-deduped commands and does **no** dedup
  of its own.
- This is only the porcelain feed D needs. The full interactive `tt history` viewer is Scope C
  (`spec-C1`), which **extends** this same branch/parser/`_history` — it does not re-create them.

### Files

- `tinytalk/cli.py`
- `tests/test_cli_history.py` (new)

### Out of scope / Deferred

- The interactive `tt history` viewer / fzf picker / view-side dedup rendering — Scope C.
- Retention on write — Scope B.

### Definition of Done — integration (stubbed providers/store)

- [ ] Every `tt` invocation that reaches the request path appends **exactly one** record; a successful
      run, a cache hit, a `NoValidCommand`, and a generic transport fault each produce a record with
      the correct `outcome` and non-empty `prompt`/`cwd`.
- [ ] A failure record carries the A2 non-zero `usage`/`cost_usd` (and its `cost_breakdown` split, which
      sums to `cost_usd`) plus `error_kind` and `problems`.
- [ ] `attempts_detail` is **populated** — one entry per attempt carrying `model` and per-attempt
      `cost_usd` (enriched from the A2 ledger).
- [ ] `billable` follows the pinned rule (`DECISIONS.md §The record`): a `cache_hit` record has `billable == false`; a fresh T1 success on a priced model has `billable == true`; a zero-token transport fault or a price-0/unpriced model is `false`.
- [ ] A forced capture-side failure (e.g. unwritable state dir) does **not** change stdout, stderr, or
      the exit code of the underlying request.
- [ ] `tt history --porcelain` emits recent commands NUL-delimited, newest-first, and prints nothing
      (exit 0) on an empty store.
- [ ] `tt history --porcelain` output is **deduped** — no duplicate command under the A1 `dedup_key`
      normalization (keep newest).
- [ ] `history` appears in the top-level `--help` epilog command list.
- [ ] A `ConfigError` invocation writes **no** record.
