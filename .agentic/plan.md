# Plan — Issue #26: Core provider seam + structured-output contract + strict parser + degradation chain

> Epic S1 ("Core engine — the spine") of the v1 thin slice. This issue delivers the layer that turns a
> raw model completion into a *validated* CLITE command object, and the escalation chain that keeps weak
> or local models producing valid output. PRD references: §5 (structured output contract), §10 (provider
> abstraction + degradation), §12 (`format_ok` = 100%).

## 1. Goal & scope

**Goal.** On top of the existing `provider.Provider` seam, add (a) the structured-output **contract**
type, (b) a **strict parser** that rejects malformed completions (the `format_ok` gate), and (c) a
**degradation chain** that asks a provider for the contract through progressively more forgiving
strategies — native structured output / tool-calling → constrained grammar (GBNF) → fenced-block
extraction + strict parse — retrying/escalating on every format failure so malformed output is never
surfaced.

**In scope**
- `internal/contract` package: the `Command` contract struct, `Danger` enum, strict `Parse`, validation,
  and a tolerant JSON extractor (fenced/balanced-object) used by the lowest tier.
- `internal/engine` package: the degradation orchestrator (`Engine.Generate`) that drives a
  `provider.Provider` through the three strategies with bounded retry-then-escalate, returning a
  validated `contract.Command` plus a `Trace` (which strategy/tier won, attempt counts) for later eval
  telemetry.
- A small additive extension to the provider seam so the grammar tier is *real*, not cosmetic: a
  `Grammar string` field on `provider.Request`, forwarded by the OpenAI-compatible client (`omitempty`,
  so existing behavior/tests are unchanged).
- Settle **sync vs async**: keep the existing synchronous `Complete(ctx, …)` signature; `context.Context`
  carries cancellation. Documented, no signature change.
- TDD unit tests for the parser (each rejection path) and the degradation chain (each tier + escalation +
  retry + total-failure).

**Explicitly out of scope** (other issues / deferred)
- The actual T0 cache / T1 grounding / T2 on-demand help tiers (#3, #6) — this is only the *structured
  output* spine, not the execution tiers.
- Real GBNF enforcement against a live llama.cpp endpoint — the grammar tier *attaches* a grammar; tests
  use a stub backend and do not exercise a real grammar engine.
- Validation & safety ladder (`zsh -n`, binaries-exist, danger *classification logic*) — #4. We define
  the `danger` field and validate it is one of the allowed values, but we do **not** classify danger here.
- Eval harness, prompt suite, leaderboard — #2.
- Building the system/grounding prompt content — only the minimal format instruction needed to elicit the
  contract is added here.
- Additional providers; no change to the OpenAI client beyond forwarding `Grammar`.

## 2. Design overview

```
internal/
  provider/        (exists)  Provider seam: Name(), Complete(ctx, Request) -> Response
    provider.go    EDIT      + add Grammar string to Request
    openai/client.go EDIT    + forward Grammar (omitempty) on the wire request
  contract/        NEW       pure data + parsing, no provider dependency
    contract.go              Command, Danger enum, (Command).Validate()
    parse.go                 Parse([]byte) (Command, error); ExtractJSON(string) ([]byte, error); FormatOK
  engine/          NEW       degradation orchestrator, depends on provider + contract
    structured.go            Engine, strategies (native/grammar/fenced), Generate(), Trace
    grammar.go               the CLITE GBNF grammar constant + format-instruction text
```

### 2.1 The contract (`internal/contract/contract.go`)

Mirror PRD §5 exactly:

```go
type Danger string
const (
    DangerSafe        Danger = "safe"
    DangerCaution     Danger = "caution"
    DangerDestructive Danger = "destructive"
)
func (d Danger) Valid() bool // one of the three

type Command struct {
    Command      string   `json:"command"`
    Explanation  string   `json:"explanation"`
    Danger       Danger   `json:"danger"`
    Confidence   float64  `json:"confidence"`
    Needs        []string `json:"needs"`
    Alternatives []string `json:"alternatives"`
}
```

`(Command).Validate() error` enforces, after a clean unmarshal:
- `Command` non-empty (the whole point — only this is inserted into the buffer).
- `Explanation` non-empty.
- `Danger.Valid()` — exactly one of safe/caution/destructive.
- `0.0 <= Confidence <= 1.0`.
- `Needs`, `Alternatives` may be empty/nil (optional). Each entry, if present, non-empty.

Validation errors are typed/wrapped so the engine can distinguish a format failure from a transport
failure (e.g. a sentinel `ErrInvalidContract` that `Parse` wraps).

### 2.2 Strict parser (`internal/contract/parse.go`)

- `Parse(data []byte) (Command, error)`:
  1. Detect **presence** of required scalars (so `confidence: 0` is distinguishable from "confidence
     missing", and an absent `command`/`danger` is rejected). Decode into a presence-DTO with pointer
     fields for `command`, `explanation`, `danger`, `confidence`; nil → missing → reject.
  2. Use a `json.Decoder` with `DisallowUnknownFields()` so any extra/hallucinated key is a hard reject
     (strict contract, no pass-through of junk).
  3. Build the `Command`, then call `Validate()`. Any failure → wrap `ErrInvalidContract`.
- `ExtractJSON(s string) ([]byte, error)` — for the fenced tier: return the contents of the first
  ```` ```json ```` (or bare ```` ``` ````) fenced block; if none, return the first balanced top-level
  `{ … }` object found in the prose; if neither, error. This isolates candidate bytes; `Parse` still
  does the strict gate, so extraction never relaxes validation.
- `FormatOK(data []byte) bool` — convenience: `_, err := Parse(data); return err == nil`. This is the
  PRD's `format_ok` predicate.

### 2.3 Degradation chain (`internal/engine/structured.go`)

A `strategy` shapes the outgoing `provider.Request` for its tier and extracts candidate JSON bytes from
the `provider.Response`; the shared strict `contract.Parse` is the single gate for all tiers.

```go
type strategy struct {
    name    string
    shape   func(req provider.Request) provider.Request           // tier-specific request shaping
    extract func(resp *provider.Response) ([]byte, error)         // candidate JSON out of the response
}
```

Tiers, in order:
1. **native** — `shape` sets `ResponseFormat = ResponseFormatJSONObject`, registers an `emit_command`
   tool whose parameter schema is the contract, and includes the shared contract format instruction
   (mentioning "json" so json-object mode is satisfied). `extract` prefers
   `resp.ToolCalls[0].Arguments`, else falls back to `resp.Content`. (Covers "native structured
   output / tool-calling" in one tier; a provider that ignores tools but honors JSON mode still works.)
2. **grammar** — `shape` sets `req.Grammar = contract GBNF` (and `ResponseFormat = text`); `extract`
   reads `resp.Content`. This is the GBNF/constrained-decoding tier for unreliable local targets.
3. **fenced** — `shape` adds an instruction to return a single ```` ```json ```` block; `extract` runs
   `contract.ExtractJSON(resp.Content)`.

`Engine`:
```go
type Engine struct {
    p                 provider.Provider
    strategies        []strategy   // default: native, grammar, fenced
    maxAttempts       int          // per strategy; default 2 (one retry) before escalating
}
func New(p provider.Provider, opts ...Option) *Engine
func (e *Engine) Generate(ctx context.Context, msgs []provider.Message) (contract.Command, Trace, error)
```

`Generate` loop (the core guarantee):
```
for each strategy s:
    for attempt in 1..maxAttempts:
        resp, err := e.p.Complete(ctx, s.shape(base(msgs)))
        if err != nil:
            if ctx canceled -> return err            // transport/cancel: surface, do not treat as format fail
            record attempt; break to next strategy    // (transport error → escalate)
        raw, err := s.extract(resp)
        if err != nil { record; continue }            // malformed → retry within tier
        cmd, err := contract.Parse(raw)               // the format_ok gate
        if err != nil { record; continue }            // not format_ok → retry, never surface
        return cmd, trace(success: s.name, attempts), nil
return contract.Command{}, trace(failed), ErrFormatNotOK   // exhausted all tiers
```

Key invariants this enforces (map directly to "done when"):
- The **only** non-error return path is a value that passed `contract.Parse` → `format_ok` is enforced.
- A malformed completion is **retried within its tier, then the chain escalates**, and if everything
  fails the caller gets `ErrFormatNotOK` + a zero `Command` — malformed bytes are never returned as a
  `Command`.

`Trace` records: `Strategy` (winning tier name, "" on failure), `Attempts []AttemptRecord{Strategy,
Err}`, `FormatOK bool`. This is deliberately the hook the eval harness (#2) will read for "tier reached".

### 2.4 Provider seam touch-ups

- `provider.Request`: add `Grammar string`. Default empty; only the grammar strategy sets it.
- `openai/client.go`: add `Grammar string \`json:"grammar,omitempty"\`` to `wireRequest` and set it from
  `req.Grammar` when non-empty. `omitempty` keeps the existing OpenAI client tests unchanged and avoids
  sending an unknown field to strict cloud endpoints when the grammar tier never fires.
- **Sync vs async (settled):** the synchronous, `context`-cancellable `Complete` stays. No new async API;
  documented in the package doc comment of `internal/engine`.

## 3. Definition of Done

Measurable; smallest verification level noted.

1. **Seam → validated contract (stub backend).** A `Generate` call against an in-package stub
   `provider.Provider` that returns a well-formed completion yields a populated `contract.Command` with
   all fields correctly parsed, `Trace.FormatOK == true`, and `Trace.Strategy == "native"`. *Verified by:
   `go test ./internal/engine/` unit test.*
2. **Malformed is rejected + retried, never surfaced.** A stub that returns malformed output then a clean
   one succeeds via retry; a stub that is always malformed across all tiers returns `ErrFormatNotOK` and a
   zero `Command`. In neither case is malformed content returned as a `Command`. *Verified by: engine unit
   tests.*
3. **Degradation escalates.** With native + grammar always malformed and the fenced tier returning
   prose-wrapped fenced JSON, `Generate` succeeds at the fenced tier (`Trace.Strategy == "fenced"`), and
   the request delivered to the grammar tier carried a non-empty `Grammar`. *Verified by: engine unit
   test asserting on the stub's captured requests.*
4. **Strict parser gate (`format_ok`).** `contract.Parse` accepts a valid object and rejects: missing
   required field, unknown extra field, invalid `danger`, out-of-range `confidence`, truncated/invalid
   JSON. *Verified by: `go test ./internal/contract/` table tests.*
5. **No regressions / builds clean.** `go build ./...`, `go vet ./...`, and `go test ./...` all pass;
   existing `internal/provider/openai` tests still green.

## 4. Steps (ordered)

Each sub-step names the files/functions and traces to the goal. TDD: write the failing test first where
noted, then implement.

1. **Contract types** — add `internal/contract/contract.go`: `Danger` + constants + `Valid()`; `Command`
   struct with json tags; `(Command).Validate()`; `ErrInvalidContract` sentinel. *(DoD 4)*
2. **Parser (TDD)** — write `internal/contract/parse_test.go` table tests (valid + every reject path +
   `ExtractJSON` cases), then implement `internal/contract/parse.go`: `Parse`, `ExtractJSON`, `FormatOK`.
   *(DoD 4)*
3. **Provider seam extension** — `internal/provider/provider.go`: add `Grammar string` to `Request`.
   `internal/provider/openai/client.go`: add `grammar,omitempty` to `wireRequest` + set when non-empty.
   Re-run `go test ./internal/provider/...` to confirm no regression. *(DoD 5; enables tier 2)*
4. **Grammar + instructions** — `internal/engine/grammar.go`: the contract GBNF constant and the shared
   `contractInstruction` / fenced-instruction system-message text + the `emit_command` tool schema
   builder. *(supports DoD 1–3)*
5. **Engine (TDD)** — write `internal/engine/structured_test.go` first: a scriptable stub
   `provider.Provider` (a func field returning queued `(*Response, error)` and capturing requests), then
   the tests in §5. Implement `internal/engine/structured.go`: `strategy`, the three default strategies,
   `Engine`, `Option`s (`WithStrategies`, `WithMaxAttempts`), `Generate`, `Trace`, `ErrFormatNotOK`.
   *(DoD 1, 2, 3)*
6. **Whole-tree gate** — `go build ./... && go vet ./... && go test ./...`; fix anything red. *(DoD 5)*

One self-contained commit on `agentic/issue-26` (no sub-issues), squash-merged per AGENTS.md.

## 5. Test strategy (high-value, TDD)

**`internal/contract` (parser/contract):**
- Valid full object → exact field values; valid object with empty `needs`/`alternatives` accepted.
- Reject: missing `command`; missing `danger`; missing `confidence`; unknown extra key
  (`DisallowUnknownFields`); `danger:"nuke"`; `confidence:1.5` and `-0.1`; empty `command:""`; truncated
  JSON `{"command":`.
- `ExtractJSON`: ```` ```json ```` block amid prose; bare `{…}` amid prose; nested braces inside strings
  handled; no-JSON input → error. Then `Parse` over the extracted bytes still gates strictly.

**`internal/engine` (degradation chain) — scriptable stub provider:**
1. Native happy path (content JSON) → success, `Strategy=="native"`, fields populated.
2. Native happy path via **tool-call arguments** (no content) → success (proves tool-call extraction).
3. Native malformed once → retry → success within native; `len(Trace.Attempts)>1`.
4. Native always malformed → grammar clean → `Strategy=="grammar"`; assert captured grammar-tier request
   has non-empty `Grammar`.
5. Native + grammar malformed → fenced returns prose+```` ```json ```` → `Strategy=="fenced"`.
6. All tiers malformed → `ErrFormatNotOK`, zero `Command`, `Trace.FormatOK==false`.
7. Provider returns transport error / canceled `ctx` → error surfaced (not misreported as `format_ok`);
   canceled ctx returns promptly.

Deterministic, no network (mirrors the existing `httptest`-free stub style is unnecessary — a struct stub
is simpler). Assertions are exact-value, not snapshot.

## 6. Risks & rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| GBNF grammar not actually enforced (tests use a stub; no live llama.cpp) | High but **accepted** — out of scope | Grammar tier *attaches* a grammar and is structurally exercised; real enforcement validated when a local backend lands (#3/runtime). Documented as residual. |
| Forwarding `grammar` breaks a strict cloud endpoint that 400s on unknown fields | Low | `omitempty` + only set by the grammar tier, which only fires *after* native fails; for capable cloud models native succeeds, so `grammar` is never sent. |
| Required-field presence detection (`confidence:0` vs missing) done wrong | Low | Pointer-DTO presence pass before strict decode; covered by explicit reject tests. |
| `ExtractJSON` brace-matching mishandles braces inside strings | Low | String-aware scan; dedicated test with braces inside a JSON string value. |
| Native tier mixing `tools` + `response_format` confuses some providers | Low | Extractor tolerates either response shape; this issue is verified against a stub, and the tier ordering means a misbehaving provider simply escalates. |

**Rollback.** All changes are additive: two new packages plus one `omitempty` field on `Request`/the
OpenAI wire type. No existing behavior is altered. Revert the single commit to fully back out; the
existing provider/openai package and its tests are untouched in behavior.

---

## Verification summary

**Rounds used: 1 of 5 — PASS.** A verifier subagent checked the plan against (1) requirements (issue
scope + "done when" + PRD §5/§10/§12) and (2) feasibility (referenced files/types/Go APIs). It found
**no requirements defects and no feasibility defects**, confirming against the actual code that:
- every `provider.Request`/`Response`/`Tool`/`ToolCall`/`ResponseFormat` field the plan relies on exists;
- adding `Grammar` + `grammar,omitempty` is invisible to all five existing OpenAI client tests (none use
  `DisallowUnknownFields` or compare full request bodies) — they stay green;
- `json.Decoder.DisallowUnknownFields()` + pointer-DTO presence detection behaves exactly as claimed
  (valid accepted, `confidence:0` accepted, missing/unknown/truncated rejected) — reproduced standalone;
- the `internal/contract` + `internal/engine` layout is valid under module `github.com/paulbkim-dev/clite`.

**Residual risks (non-blocking, already in §6):**
- The contract format-instruction text must literally contain the word "json" or a real `json_object`-mode
  endpoint could 400 (stub tests won't catch this) — implement accordingly.
- `Grammar` is a llama.cpp-server extension, non-standard for OpenAI proper; mitigated by `omitempty` and
  the grammar tier only firing after native fails (capable cloud models succeed at native, so it is never
  sent). Real GBNF enforcement is out of scope and validated only when a local backend lands.
