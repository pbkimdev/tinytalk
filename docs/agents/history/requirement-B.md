# Requirement B — Retention & rotation

> **Binding source:** [`DECISIONS.md`](./DECISIONS.md) → *Retention (Scope B1)*. Node graph:
> [`tasks.json`](./tasks.json) → `req-B` / `spec-B1`. This doc restates those locked decisions in the
> repo's issue/spec shape (context → scope → done-when); it does not add behavior beyond them. Anything
> the decisions leave genuinely open is listed under **Open questions**, not guessed.

Scope B has exactly one leaf spec, **B1**, so this requirement and its spec are presented together:
the requirement frames the *why* and the boundary; the spec body is the one-commit unit of work.

---

## Requirement B (framing)

### Context
`tt history` (per [`DECISIONS.md`](./DECISIONS.md)) appends every prompt→command outcome to **dated
JSONL day-segments** at `<state>/history/YYYY-MM-DD.jsonl` (Scope A1). Left unbounded that store grows
forever. Retention keeps it small **without ever risking a lost append**: the store's whole safety
argument (Scope A1: append-only, `O_APPEND`-atomic, best-effort, no lock) holds only because appends
always target *today's* segment and nothing else contends for it. Retention must preserve that — it may
only delete **whole old segment files**, and must **never** open, rewrite, truncate, or line-edit the
active (today's) segment. That is why rotation is "prune-old-only" and not compaction.

### Scope
- Bound the on-disk history store by two rules, applied on the write path:
  - **Age:** delete whole day-segments older than **7 days**.
  - **Size:** a **15 MB** total-store cap as a *safety trim* — delete oldest whole segments until the
    store is back under the cap.
- Prune touches **old files only**, via whole-file `unlink`. The active segment is never a deletion or
  rewrite target.
- Best-effort, exactly like the rest of `history.py` / `cache.py`: any `OSError` is swallowed and never
  breaks a `tt` request.

### Out of scope / Deferred
- **Line-level compaction / dedup-on-disk** — store-all is preserved; dedup happens in the *view* only
  (Scope C), never by rewriting segments here.
- **Rewriting or truncating the active segment** — explicitly forbidden, even if it alone exceeds the
  cap (see B1 behavior rule 5).
- **Locking / cross-process coordination** — the prune-old-only design is what removes the need for it;
  do not add a lock.
- Configurable retention windows, compression, external rotation tools — not requested.

### Dependencies
- **Blocked by:** `spec-A1` (creates `tinytalk/history.py`, `default_state_dir()`, the dated-segment
  layout, and the append path this hooks into). B1 **extends** `history.py`; it does not duplicate it.
- **Blocks:** None. (Runs in build wave 2, file-disjoint from `spec-A3` which touches `cli.py`.)

### Definition of Done (requirement level)
- Scope B1 lands as **one self-contained commit** and its gate below passes.
- After a write, segments older than 7 days are gone, the store is bounded ≤ 15 MB except when the
  active segment alone exceeds it, and no append is ever lost or overwritten.

---

## Spec B1 — Retention sweep

> **One commit.** Touches: `tinytalk/history.py` (extend), `tests/test_history_retention.py` (new).

### Context
Extend the A1 store with a retention sweep invoked from the append path. It consults only file
**metadata** — the segment date encoded in each filename plus `stat()` size — so the common per-write
cost is a directory listing and a handful of `stat`s, with **no segment content reads** and **no writes
to the active segment**. This is the "safety trim" from `DECISIONS.md`, and it is deliberately
conservative: it would rather leave the store slightly over the cap than ever touch the file appends
are landing in.

### Scope — the changes
1. Add module-level constants to `tinytalk/history.py`, e.g. `_RETENTION_DAYS = 7` and
   `_MAX_TOTAL_BYTES = 15 * 1024 * 1024` (see Open questions on the MB/MiB choice).
2. Add a retention-sweep function to `history.py` (e.g. `_prune(directory: Path) -> None`) that a test
   can call directly on a temp directory. It:
   - Lists segment files in `directory` whose names match `YYYY-MM-DD.jsonl`; **ignores** every other
     name (stray files, sidecars, non-dated files) — they are neither aged out nor counted.
   - Identifies the **active segment** = today's dated file (reusing A1's date/timezone convention for
     "today" so B1 and A1 never disagree). The active segment is excluded from every deletion path.
   - **Age prune:** unlinks (`unlink(missing_ok=True)`) each non-active segment whose date is more than
     `_RETENTION_DAYS` days before today.
   - **Size cap (safety trim):** after age prune, sums `stat().st_size` over the remaining segment
     files (active included). While the total exceeds `_MAX_TOTAL_BYTES` **and** at least one non-active
     segment remains, unlinks the **oldest-dated non-active** segment and subtracts its size. Stops when
     the total is ≤ the cap or only the active segment is left.
   - **Best-effort:** wraps its filesystem work so any `OSError` is swallowed and the function returns
     normally — mirrors `cache.py:ExactCache.put`. A failed prune must never break the append.
3. Invoke the sweep from A1's append routine so retention runs **on write**. Ordering relative to the
   append does not matter (the sweep never touches the active segment); keep it best-effort so it can
   never turn a successful append into a failed request.

### Behavior rules (load-bearing, checkable)
1. Only whole-file `unlink` — the sweep never opens a segment for writing, never truncates, never
   rewrites a line.
2. The active (today's) segment is never deleted and never opened for writing, in either the age or the
   size path.
3. Age and size are independent: age prune runs first; the size cap is a secondary safety trim over
   what age prune left.
4. The size cap deletes **oldest-dated first** among non-active segments.
5. If, after removing every non-active segment, the store is still over the cap (the active segment
   alone exceeds 15 MB), the sweep **stops and leaves the active segment untouched** — the cap is
   intentionally exceeded rather than sacrificing the file appends are landing in.
6. Concurrency: because the sweep only unlinks old whole files and never touches today's segment, an
   append racing a sweep cannot be lost or corrupted (no lock needed).

### Out of scope / Deferred
- Everything in the requirement's Out-of-scope list (compaction, active-segment rewrite, locking,
  configurability).
- Throttling the sweep to less than once-per-write — see Open questions; not decided, so B1 runs it on
  every write with metadata-only checks.

### Dependencies
- **Blocked by:** `spec-A1`.
- **Blocks:** None.

### Definition of Done
- **Verification level: unit.** New `tests/test_history_retention.py` drives the sweep on a temp
  directory of fabricated dated segments and asserts every acceptance check below.
- `uv run python -m pytest` green, including A1's existing history tests (proves the active segment is
  never rewritten — no regression to append/read behavior).

### Acceptance checks
- [ ] A segment dated 10 days before today is unlinked; a segment dated today and one dated yesterday
      survive. (Age prune.)
- [ ] Given several old segments plus today's whose sizes sum to > 15 MB, the sweep unlinks
      **oldest-first** among the non-active segments until the total is ≤ 15 MB; today's segment is
      never unlinked.
- [ ] When only the active (today's) segment exists and it alone exceeds 15 MB, the sweep leaves it
      **byte-identical** (rule 5).
- [ ] The sweep never opens the active segment for writing: its existing bytes are unchanged across a
      sweep, and a record appended after the sweep is present in full. (Rules 1–2, 6.)
- [ ] A failing `unlink`/`stat` (e.g. a permission error or a file that vanished mid-sweep) is
      swallowed; the sweep returns normally and the surrounding append still succeeds. (Best-effort,
      mirrors `cache.py:ExactCache.put`.)
- [ ] Files in the directory that do not match `YYYY-MM-DD.jsonl` are left untouched by both prune
      paths.
- [ ] Integration: after appends whose segments span dates older than 7 days, a subsequent write leaves
      no >7-day-old segment on disk (proves the sweep is wired into A1's append path).

### Sub-tasks
- [ ] Add `_RETENTION_DAYS` / `_MAX_TOTAL_BYTES` and the sweep function to `tinytalk/history.py`.
- [ ] Wire the sweep into A1's append routine (best-effort).
- [ ] Add `tests/test_history_retention.py` covering the acceptance checks.

### References
- [`DECISIONS.md`](./DECISIONS.md) — *Retention (Scope B1)*, *Storage (Scope A1)*, *Conventions*.
- [`tasks.json`](./tasks.json) — `req-B`, `spec-B1` (wave 2; disjoint from `spec-A3`).
- `tinytalk/cache.py` — `ExactCache.put` (best-effort `OSError`-swallow pattern), `default_cache_dir`.
- `tinytalk/history.py` (from A1) — `default_state_dir()`, dated-segment layout, append path.
- `AGENTS.md` — one spec = one commit; `uv run python -m pytest`.

---

## Open questions

Genuinely underspecified in `DECISIONS.md`; flag rather than guess. None blocks starting B1 — the
acceptance checks above use clearly-old (≥ 10 days) and clearly-recent (≤ 1 day) fixtures so they do not
hinge on these edges.

1. **7-day boundary edge.** "Older than 7 days" does not pin the inclusive/exclusive edge: is a segment
   dated exactly 7 days ago kept or deleted, and is age counted in whole calendar days
   (`(today - date).days`) or as a 7×24h span? B1's stated rule is `(today - date).days > _RETENTION_DAYS`
   (keep today through today-7); confirm this is the intended edge.
2. **15 MB = MiB or MB.** B1 uses `15 * 1024 * 1024` (15 MiB, 15,728,640 bytes). Confirm, or switch to
   decimal `15_000_000`.
3. **Sweep cadence.** `DECISIONS.md`/`tasks.json` say "on write"; B1 runs the (metadata-only) sweep on
   every append. Is per-append acceptable, or should it throttle (once per process, or once per day)?
   If throttling is wanted it is a small follow-up, not a change to the sweep's behavior.
4. **Stray / non-dated files.** B1 ignores files not matching `YYYY-MM-DD.jsonl` (never deletes them,
   never counts them toward the size cap). Confirm the store never legitimately contains such files
   (e.g. A1 does not leave `.tmp` sidecars from a partial write); if it can, decide whether they should
   count toward the cap.
