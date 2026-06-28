# Plan — S5 caching (T0 exact-match cache) · #36

## Goal & scope

Add the **T0 exact-match cache**: the cheapest rung of CLITE's tiered-execution ladder
(PRD §4). Before any model call, the tier controller normalizes the request + context,
hashes it to a key, and on an exact hit returns the previously-stored good output — no
model call, < 50 ms (PRD §9, §12).

Deliver this as a **standalone, dependency-free Python module** `clite/cache.py` with a
small, stable surface (`make_key` + `Cache.get`/`Cache.put`) that the tier controller
(#31) will call when it lands. Defining that key/value contract now de-risks #31 rather
than adding churn.

**In scope**
- `clite/cache.py`: request/context **normalization**, a stable **`make_key(...)`** hash,
  and an on-disk **`Cache`** store (`get(key)` / `put(key, value)`) backed by stdlib
  `sqlite3` so the cache survives across CLITE's per-call invocations.
- A small `os_fingerprint()` helper so callers/tests have a canonical fingerprint string.
- `tests/test_cache.py`: unit-tested hit/miss + persistence + normalization + value
  round-trip (the issue's "done when").

**Explicitly out of scope** (named to prevent scope creep)
- **Tier controller wiring** (T0→T1→T2) — that is issue **#31**; this issue only ships the
  cache unit it will consume. No CLI/engine wiring here.
- **The structured-output contract type** — issue **#26**. The cache is **value-agnostic**:
  it stores/returns any JSON-serializable `dict`, so the contract `{command, explanation,
  danger, confidence, needs, alternatives}` (PRD §5) round-trips with **zero cache changes**
  when #26 lands.
- **Spec/doc cache** (parsed `--help`/man/tldr, keyed by tool+version, PRD §9 bullet 2) —
  a different cache tied to grounding (S2/#33). It belongs to the S5 epic but **not** to
  #36, whose "done when" is the T0 exact cache only. Tracked as an S5 follow-up.
- **Semantic/vector cache** — explicitly **deferred post-v1** (PRD §3 non-goals, §9, §13).
- **Eviction / TTL / size caps** — not required by the "done when"; `put` is last-good-wins
  upsert. Noted as a future hardening item (see Risks).

## Sequencing / branch base (load-bearing)

`main` is **still the Go codebase**; the Python package scaffold (`pyproject.toml`,
`clite/__init__.py`, `clite/cli.py`, `tests/`) exists only on the **open** pivot PR
`pivot/python-replatform` (#24), which is 2 commits ahead of `main` (merge-base = current
`main` HEAD). #36 is a **Python** issue under roadmap #25 (the re-expression of the
now-closed Go-era #6).

Consequence for **implement**: the cache module is pure-stdlib and adds **only new files**
(`clite/cache.py`, `tests/test_cache.py`) with **no edits to existing files and no
`pyproject.toml` changes** (pytest+ruff already in the scaffold's dev group). It therefore
composes cleanly on top of the Python scaffold and cannot conflict with #24.

- **Preferred order:** land #24 (Python scaffold) first, then #36 merges straight onto it.
- **If #24 has not merged when implement runs:** the implementer must bring the scaffold
  into the working tree before tests can run — rebase/merge `agentic/issue-36` onto
  `origin/pivot/python-replatform` (or onto `main` once #24 has merged) so `pytest`/`ruff`
  have a package to resolve. Because #36 only adds new files, this is a clean fast-forward
  of file set with no content conflicts.

This `plan` commit itself contains **only** `.agentic/plan.md` on `agentic/issue-36` (branched
from the default branch per the pipeline contract); the engine opens the draft PR from it.

## Definition of Done

Measurable acceptance criteria (smallest verification level that proves each):

1. **Repeated request served from cache without a model call.** A unit test routes a
   request through a "lookup-or-compute" path with a **call-counting stub model**; the
   first request misses and invokes the stub once, an identical repeated request hits the
   cache and invokes the stub **zero** additional times. (This is the issue's headline.)
2. **Hit/miss correctness.** `Cache.get(key)` returns the stored `dict` on a hit and
   `None` on a miss.
3. **Stable, normalized key.** `make_key` returns the **same** key for requests that differ
   only by surrounding/collapsible whitespace and letter-case in the NL request; returns a
   **different** key when `cwd`, `os_fingerprint`, or `posture` differ.
4. **On-disk persistence across invocations.** A value `put` via one `Cache(path)` instance
   is retrievable via a **freshly constructed** `Cache(path)` instance pointed at the same
   file — proving the cross-process persistence CLITE needs (it is invoked per call).
5. **Value integrity.** A full structured-output contract dict (PRD §5, incl. nested
   `needs`/`alternatives` lists and a float `confidence`) round-trips through `put`→`get`
   unchanged.
6. **Tooling green.** `pytest` passes and `ruff check` is clean (line-length 100, per the
   scaffold's `[tool.ruff]`).

Latency target (< 50 ms, PRD §12) is met by construction — a SQLite primary-key point
lookup is sub-millisecond; we assert correctness via tests, not a timing gate (timing
assertions are flaky in CI).

## Steps (ordered; each traces to the goal)

1. **Ensure the Python scaffold is present** (see Sequencing). No code change — a working-tree
   precondition for implement.

2. **`clite/cache.py` — normalization.** `_normalize_request(request: str) -> str`:
   conservative only — `strip()`, collapse internal whitespace runs to a single space, and
   lowercase the **NL request**. `cwd`, `os_fingerprint`, and `posture` are treated as exact
   identity (only stripped) — paths are case-sensitive on Linux and the other two are
   controlled tokens. Documented as a deliberate, easily-tunable choice to avoid
   over-normalizing (a small intent delta must not silently return a stale command).

3. **`clite/cache.py` — `make_key`.** `make_key(request, cwd, os_fingerprint, posture) -> str`:
   build a canonical structure `{"request": _normalize_request(request), "cwd": cwd.strip(),
   "os": os_fingerprint.strip(), "posture": posture.strip()}`, serialize with
   `json.dumps(..., sort_keys=True, separators=(",", ":"))`, and return
   `hashlib.sha256(...).hexdigest()`. JSON serialization (not string concatenation) avoids
   delimiter-collision ambiguity between fields. Matches PRD §9: `hash(normalized prompt +
   cwd + OS fingerprint)`, extended with `posture` per the issue scope ("context/posture").

4. **`clite/cache.py` — `os_fingerprint()` helper.** Return a coarse, stable fingerprint
   from stdlib `platform` (`platform.system()`, `platform.machine()`, `platform.release()`)
   joined into one string. Coarse on purpose: precise coreutils-vs-BSD flavor detection is
   grounding's concern (S2/#33); the cache only hashes whatever fingerprint string it is
   handed, so callers may pass a richer one without any cache change.

5. **`clite/cache.py` — `Cache` class (sqlite-backed).**
   - `__init__(self, path: str | Path | None = None)`: default path is
     `<base>/clite/cache.db`, where `base` honors `XDG_CACHE_HOME` only when it is a
     **non-empty** value, else falls back to `~/.cache` — i.e.
     `xdg = os.environ.get("XDG_CACHE_HOME"); base = Path(xdg) if xdg else Path("~/.cache").expanduser()`
     (the XDG spec treats a set-but-empty var as unset; a bare `os.environ.get(..., "~/.cache")`
     would yield a cwd-relative path when the var is `""`). This default lives under the user's
     home (`~/.cache/clite/`), outside the repo; tests pass an explicit `tmp_path` file — so
     nothing the cache writes ever lands in the working tree (no gitignore entry needed).
     `mkdir(parents=True, exist_ok=True)` the parent; open `sqlite3.connect(path, timeout=…)`;
     `CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT NOT NULL)`. Accept an
     explicit `path` (a `tmp_path` file in tests) and the literal `":memory:"`.
   - `get(self, key: str) -> dict | None`: `SELECT value` by key; `json.loads` on hit, else
     `None`.
   - `put(self, key: str, value: dict) -> None`: `INSERT … ON CONFLICT(key) DO UPDATE`
     (last-good-wins upsert) with `json.dumps(value)`; commit.
   - `close(self)` and context-manager (`__enter__`/`__exit__`) for clean handle release.
   - All-stdlib: `sqlite3`, `hashlib`, `json`, `platform`, `pathlib`, `os`. **No new deps.**
   - *(Nicety, not required by DoD)*: thin `lookup(request, cwd, os_fingerprint, posture)` /
     `store(...)` wrappers that fold `make_key` in, so #31 can call one method. Core surface
     stays `make_key` + `get`/`put` per triage.

6. **`tests/test_cache.py`** — see Test strategy.

7. **Verify locally:** `pytest -q` and `ruff check clite/cache.py tests/test_cache.py`.

8. **No wiring to `clite/cli.py`** — the cache is consumed by the tier controller (#31), not
   the CLI, in this issue.

## Test strategy (TDD — high-value, not exhaustive)

`tests/test_cache.py`, pytest, using `tmp_path` for on-disk cases:

- `test_repeated_request_served_without_model_call` — **DoD #1.** A small local
  `lookup_or_compute(cache, fields, model)` test helper: on miss calls a counting stub model
  and `put`s the result; on hit returns from cache. Assert stub call-count is 1 after two
  identical requests.
- `test_get_miss_returns_none` — **DoD #2.**
- `test_make_key_normalizes_whitespace_and_case` — **DoD #3a:** `"  List   Files "` and
  `"list files"` (same cwd/os/posture) → equal keys.
- `test_make_key_differs_on_context` — **DoD #3b:** changing each of `cwd`, `os_fingerprint`,
  `posture` independently changes the key.
- `test_persists_across_instances` — **DoD #4:** `put` via `Cache(path)`, `get` via a new
  `Cache(path)` on the same `tmp_path` file → hit.
- `test_contract_value_roundtrip` — **DoD #5:** store the PRD §5 sample object, assert
  `get` returns an equal dict (nested lists + float preserved).
- `test_put_overwrites_last_good` — upsert semantics: second `put` on the same key wins.

## Risks & rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Scaffold not merged (#24)** when implement runs | Med | Module adds only new files, no `pyproject` change → rebase/merge onto pivot or post-#24 `main` is conflict-free; documented in Sequencing. Prefer landing #24 first. |
| **Over-normalization** returns a stale command for a meaningfully different request | Low | Conservative normalize (whitespace+case on the NL request only; cwd/os/posture exact); isolated in `_normalize_request` and easy to tune; covered by key tests. |
| **Value contract undefined (#26)** | Low | Cache is value-agnostic JSON `dict`; contract is "just a dict" → no cache change when #26 lands; covered by the round-trip test. |
| **Cross-process SQLite contention** | Low | PK upsert + `connect(timeout=…)`; single-user, low write rate in v1. |
| **Stale "last good output"** (no TTL/version stamp) | Low (v1) | Out of scope per "done when"; note TTL / model-version-stamped keys as an S5 hardening follow-up. |

**Rollback:** the change is **two new files** (`clite/cache.py`, `tests/test_cache.py`) with
no edits to existing modules and no dependency changes — revert the single commit to fully
remove it. Nothing else references the cache yet (#31 consumes it later).

## Verification summary

Verified against the PRD/issue (requirements) and the codebase (feasibility) via a verifier
subagent loop — see the PR description for rounds used and residual risk.
