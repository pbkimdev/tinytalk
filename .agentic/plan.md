# Plan — Issue #31: Tier controller (T0→T1→T2)

## Goal & scope

Build the **escalation spine** that drives CLITE's tiered execution (PRD §4): try the
cheapest tier first and escalate only when a tier's validation gate fails.

```
T0  Cache         exact-match hit on normalize(prompt+cwd+os) → return (no model call)
T1  Grounded-lite curated context → single structured model call → gate
T2  On-demand     fetch --help/man for flagged tools → re-ask stronger model → gate
```

This issue delivers the **controller + the escalation hook *seams*** and proves the
T0→T1→T2 flow end to end with fakes. The real cache (#6), grounding (#3), validation
ladder (#4), and structured-output decoding (#1) plug into these seams in their own
issues. The controller is **wired to the existing provider interface**
(`internal/provider`) and to an in-memory tier **`Config`**.

**In scope**
- New package `internal/tier`: tier model, `Controller`, escalation logic, in-memory
  `Config`, and the hook interfaces (`Cache`, `Grounder`, `Decoder`, `Gate`, `Solver`).
- A default `providerSolver` that wires `provider.Provider` + `Grounder` + `Decoder`
  into a tier (this is the concrete "wired to providers").
- Thin stand-in defaults so escalation is exercisable without the other subsystems:
  `JSONDecoder` (native-JSON happy path), `AcceptAllGate`, and `ConfidenceGate{Floor}`.
- Deterministic cache-key normalization (`sha256(normalized prompt + cwd + os)`).
- Unit tests proving the escalation ladder, hint propagation, and provider wiring.

**Explicitly out of scope** (owned by other issues — we only define the seam)
- **T3 agentic loop** — deferred per the issue and PRD §13. `MaxTier` caps at T2; T3
  is never defined or attempted.
- Real **cache** store/eviction (#6), real **grounding** (curated toolset, on-demand
  `--help`/man/tldr fetch, PATH cache) (#3), real **validation/safety ladder** (`zsh
  -n`, binaries/flags exist, danger classification) (#4), full **structured-output
  decoding** (GBNF / fenced-block degradation) (#1).
- **TOML config loader** (`~/.config/clite/config.toml`) — only the in-memory `Config`
  struct the loader will later populate.
- CLI command / binary wiring — there is no `cmd/` yet; the controller ships as a
  library a later issue wires into the entrypoint.

## Definition of Done

Measurable acceptance criteria:

1. `go build ./...`, `go vet ./...`, `go test ./...` all pass.
2. `tier.Controller.Run` escalates **T0 → T1 → T2** driven by the hooks:
   - a cache hit at T0 short-circuits (no solver invoked);
   - a passing gate at T1 returns without invoking T2;
   - a failing gate at T1 escalates to T2, **forwarding hints** (e.g. unknown tools)
     to the T2 solver;
   - exhausting the ladder returns the last candidate with `Accepted=false` and a
     full escalation `Trail` — **T3 is never attempted**.
3. The controller is **wired to providers** (`providerSolver` calls
   `provider.Provider.Complete`) and to **config** (`Config{CacheEnabled, MaxTier}`
   governs behavior).
4. The above is covered by unit tests (table/fake-driven, no network).

**Smallest verification level:** unit tests over `internal/tier` with fake hooks and a
fake provider (`httptest`-free; the provider is stubbed at the interface). This is
sufficient because the controller is pure orchestration over interfaces — no I/O of its
own.

## Design

New package `internal/tier`. Files:

- `tier.go` — tier enum, domain types, hook interfaces, `Config`.
- `controller.go` — `Controller`, `New`, options, `Run`, `normalizeKey`.
- `solver.go` — `providerSolver` (provider wiring) + `JSONDecoder` + gate stand-ins.
- `controller_test.go`, `solver_test.go` — unit tests with fakes.

### Domain types (`tier.go`)

```go
type Tier int
const (
    T0Cache    Tier = iota // exact-match cache
    T1Grounded             // grounded-lite single structured call
    T2OnDemand             // on-demand --help/man fetch + re-ask
)
// T3 intentionally undefined — deferred post-v1.
func (t Tier) String() string

// Input: the NL request plus the environment used to ground and key it.
type Input struct {
    Prompt         string
    CWD            string
    OSFingerprint  string   // uname / shell / coreutils flavor
    SessionHistory []string // recent commands (T1+ context); consumed already-redacted —
}                           // this package does NOT redact (redaction is #4/#5, PRD §7/§8)

// Output: the structured contract (PRD §5). Only Command is inserted into the buffer;
// the rest feeds validation/escalation.
type Output struct {
    Command      string   `json:"command"`
    Explanation  string   `json:"explanation"`
    Danger       string   `json:"danger"`     // safe | caution | destructive
    Confidence   float64  `json:"confidence"` // 0..1
    Needs        []string `json:"needs"`      // tools required
    Alternatives []string `json:"alternatives,omitempty"`
}

// Hints carry escalation context forward (why the prior tier failed, which tools T2
// must fetch docs for).
type Hints struct {
    UnknownTools []string
    LastReason   string
    FromTier     Tier
}

// Verdict: the gate's decision about a candidate Output.
type Verdict struct {
    Accept bool
    Reason string
    Hints  Hints
}

// Result: what Run returns — chosen output, the tier that produced it, acceptance, the
// escalation trail (proof of the T0→T1→T2 path), and summed usage.
type Result struct {
    Output   Output
    Tier     Tier
    Accepted bool
    Trail    []TrailEntry
    Usage    provider.Usage
}
type TrailEntry struct{ Tier Tier; Reason string } // Reason "" on the accepted tier
```

### Hook interfaces — the escalation seams (`tier.go`)

```go
// Cache is the T0 hook: exact-match lookup keyed by normalizeKey(Input).
type Cache interface {
    Get(ctx context.Context, key string) (Output, bool)
    Put(ctx context.Context, key string, out Output)
}

// Grounder builds the prompt context for a tier (T1 curated toolset; T2 on-demand help
// for hints.UnknownTools).
type Grounder interface {
    Ground(ctx context.Context, in Input, t Tier, hints Hints) (Grounding, error)
}
type Grounding struct{ System string } // minimal v1; extension point

// Decoder turns raw model content into a structured Output (native JSON → GBNF →
// fenced-block degradation lives behind this; real impl from #1).
type Decoder interface{ Decode(raw string) (Output, error) }

// Gate is the validation gate run between tiers (#4 supplies the real ladder).
type Gate interface {
    Check(ctx context.Context, in Input, out Output) Verdict
}

// Solver produces a candidate Output for one model tier. Default impl wires a provider.
type Solver interface {
    Solve(ctx context.Context, in Input, hints Hints) (Output, provider.Usage, error)
}
```

### Config (`tier.go`)

```go
// Config is the in-memory tier policy (the TOML loader maps onto this later).
type Config struct {
    CacheEnabled bool
    MaxTier      Tier // escalation ceiling; v1 default T2OnDemand (never T3)
}
func DefaultConfig() Config { return Config{CacheEnabled: true, MaxTier: T2OnDemand} }
```

### Controller (`controller.go`)

```go
type Controller struct {
    cfg     Config
    cache   Cache             // nil → always-miss
    gate    Gate              // nil → AcceptAllGate
    solvers map[Tier]Solver   // T1, T2 (extensible)
    keyFn   func(Input) string
}
type Option func(*Controller)
func WithCache(c Cache) Option
func WithGate(g Gate) Option
func WithSolver(t Tier, s Solver) Option
func WithKeyFunc(fn func(Input) string) Option
func New(cfg Config, opts ...Option) *Controller
func (c *Controller) Run(ctx context.Context, in Input) (Result, error)
```

`Run` algorithm:
1. `trail := nil`.
2. **T0** — if `cfg.CacheEnabled && cache != nil`: `key := keyFn(in)`; on `Get` hit →
   `Result{Output, T0Cache, Accepted:true, Trail:[{T0,""}]}`. On miss → record and fall
   through (remember `key` for the eventual `Put`).
3. `hints := Hints{}`; track `last Output`, `lastTier`, summed `usage`.
4. For `t := T1Grounded; t <= cfg.MaxTier; t++`:
   - `s := solvers[t]`; if nil → append `{t,"no solver"}`; continue.
   - `out, u, err := s.Solve(ctx, in, hints)`; add `u` to usage; `last, lastTier = out, t`.
   - on `err` → append `{t, "solver error: "+err}`; set `hints.LastReason/FromTier`; if
     `t == cfg.MaxTier` → return `Result{Accepted:false, Trail, Usage}, err`; else escalate.
   - `v := c.check(ctx, in, out)` (uses `gate` or AcceptAll).
   - if `v.Accept` → if cache enabled `cache.Put(key,out)`; return
     `Result{out, t, Accepted:true, Trail+{t,""}, usage}, nil`.
   - else append `{t, v.Reason}`; `hints = v.Hints; hints.FromTier = t`.
5. Ladder exhausted → `Result{Output:last, Tier:lastTier, Accepted:false, Trail, usage}, nil`.
   **No T3.**

`normalizeKey(in Input) string`: lower-case + whitespace-collapse the prompt, join with
`CWD` and `OSFingerprint`, return `sha256` hex (stdlib `crypto/sha256`). Deterministic.

### Provider wiring + stand-ins (`solver.go`)

```go
// providerSolver wires provider.Provider + Grounder + Decoder into one tier. This is
// the concrete "escalation hooks wired to providers": T1 gets a cheap provider, T2 a
// stronger one (caller chooses at construction from config routing).
type providerSolver struct {
    tier            Tier
    p               provider.Provider
    g               Grounder
    d               Decoder
    responseFormat  provider.ResponseFormat // default JSONObject
    reasoningEffort string                  // per-tier (e.g. higher at T2)
}
func NewProviderSolver(t Tier, p provider.Provider, g Grounder, d Decoder, opts ...PSOption) Solver
func (s *providerSolver) Solve(ctx, in, hints) (Output, provider.Usage, error):
    gr, err := s.g.Ground(ctx, in, s.tier, hints)        // T2 uses hints.UnknownTools
    if err != nil { return Output{}, provider.Usage{}, err }
    req := provider.Request{Messages: buildMessages(gr.System, in, hints),
                            ResponseFormat: s.responseFormat, ReasoningEffort: s.reasoningEffort}
    resp, err := s.p.Complete(ctx, req)
    if err != nil { return Output{}, provider.Usage{}, err } // resp is nil on error — guard before deref
    out, err := s.d.Decode(resp.Content)
    return out, resp.Usage, err

// JSONDecoder: native-JSON happy path (json.Unmarshal into Output). #1 adds GBNF /
// fenced-block degradation behind the same Decoder seam.
type JSONDecoder struct{}

// Gate stand-ins (replaced by #4's real ladder):
type AcceptAllGate struct{}                 // Accept everything (controller default when gate==nil)
type ConfidenceGate struct{ Floor float64 } // Accept iff out.Confidence >= Floor
```

`ConfidenceGate` reads `out.Confidence`; with `JSONDecoder` a model that omits the
`confidence` field decodes to `0.0` and therefore always escalates — documented on the
type so callers don't mistake it for a bug. The controller's default gate is
`AcceptAllGate`; escalation tests inject a scripted `fakeGate`, and `ConfidenceGate` is
covered directly in `solver_test.go`.

`buildMessages` assembles a system message (`gr.System`) + a user message (the prompt,
optionally appended with redacted `SessionHistory`); kept deliberately small in v1.

## Steps (ordered, TDD)

1. **Types & seams** — add `internal/tier/tier.go`: `Tier`/`String`, `Input`, `Output`,
   `Hints`, `Verdict`, `Result`/`TrailEntry`, `Config`/`DefaultConfig`, and the five hook
   interfaces. Compiles, no logic.
2. **Controller tests first** — write `controller_test.go` with fakes (`fakeCache`,
   `fakeGate`, `fakeSolver` recording received hints) covering the escalation matrix
   (see Test strategy). Red.
3. **Controller** — implement `controller.go` (`New`, options, `Run`, `normalizeKey`,
   `check`). Green.
4. **Solver tests first** — `solver_test.go`: `providerSolver` calls grounder→provider→
   decoder and forwards hints; `JSONDecoder` parses the contract / errors on garbage;
   `ConfidenceGate` accept/escalate boundary. Red.
5. **Solver + stand-ins** — implement `solver.go` (`providerSolver`, `JSONDecoder`,
   `AcceptAllGate`, `ConfidenceGate`, `buildMessages`). Green.
6. **Verify** — `go build ./... && go vet ./... && go test ./...`; tidy doc comments to
   match the existing provider package's style.

## Test strategy (high-value, not exhaustive)

Fakes only — no network. The provider is stubbed at the `provider.Provider` interface.

Controller (`controller_test.go`):
- **CacheHit_ShortCircuits** — `Get` hit ⇒ `Tier==T0Cache`, no solver called.
- **CacheMiss_T1Accept** — gate accepts at T1 ⇒ `Tier==T1`, `cache.Put` called, T2
  solver **not** called.
- **Escalate_T1_to_T2** — T1 gate fails with `UnknownTools` ⇒ T2 solver receives those
  hints (assert recorded), `Tier==T2`, `Trail` has the T1 reason.
- **Exhausted_NoT3** — both gates fail ⇒ `Accepted==false`, `Trail` has T1+T2, no tier
  beyond `MaxTier` attempted.
- **MaxTier_T1_StopsEarly** — `MaxTier=T1` + T1 fails ⇒ T2 never called; abstain.
- **CacheDisabled** — `CacheEnabled=false` ⇒ cache never `Get`/`Put`.
- **SolverError_AtMaxTier_ReturnsErr** — T1 solver errors → escalates; T2 errors at
  MaxTier → `Run` returns the error with `Accepted==false`.
- **NormalizeKey_StableAndDistinct** — same input ⇒ same key; case/whitespace-only
  differences collapse to the same key; differing prompt/cwd/os ⇒ different keys.

Solver (`solver_test.go`):
- **ProviderSolver_WiresProvider** — fake grounder + fake provider + `JSONDecoder`:
  asserts `Complete` is called with the grounded system prompt and chosen
  `ResponseFormat`, hints reach the grounder, and `Output`+`Usage` are returned.
- **JSONDecoder_ParsesContract / RejectsGarbage**.
- **ConfidenceGate_Boundary** — confidence `< Floor` ⇒ escalate; `>= Floor` ⇒ accept.

## Risks & rollback

- **Scope creep into #1/#3/#4/#6.** Mitigation: this issue ships *interfaces* + thin
  stand-ins only; each real subsystem lands behind its named seam later. The stand-ins
  (`AcceptAllGate`, `ConfidenceGate`, `JSONDecoder`) are explicitly labeled as
  placeholders in doc comments.
- **`Output` contract may overlap with #1's structured-output work.** Mitigation: keep
  `Output` minimal and matching PRD §5 exactly; it lives in `internal/tier` and #1 can
  converge/relocate it (no external dependents yet). Noted as a follow-up.
- **`Config` shape may diverge from the eventual TOML config.** Mitigation: `Config` is
  pure in-memory policy; the future loader maps onto it — no file-format coupling here.
- **Solver-error semantics.** Decision recorded above (escalate; return error only when
  the *last* tier errors) and pinned by a test so behavior is intentional, not accidental.
- **Rollback:** delete `internal/tier/` — nothing else imports it yet, so removal is
  clean and isolated.

## Verification summary

**Rounds used: 1** (both verifiers PASS). Dispatched a requirements verifier (issue/PRD
intent) and a feasibility verifier (against `internal/provider` + `go.mod`) in parallel.

- **Requirements: PASS.** Escalation T0→T1→T2 via hooks + unit tests, "wired to
  providers + config", T3 correctly excluded with clean seams for #1/#3/#4/#6; `Output`
  mirrors PRD §5 and the cache key matches PRD §9. No missing/over-built requirements.
- **Feasibility: PASS.** All referenced provider types/signatures exist as assumed
  (`Complete(ctx, Request) (*Response, error)`, `Request.{Messages,ResponseFormat,
  ReasoningEffort}`, `ResponseFormatJSONObject`, `Usage`, `Message`/`Role`); import path
  and `crypto/sha256`/`encoding/json` are correct for Go 1.24; fakes-at-interface tests
  work; no name collisions.

**Corrections folded in:** nil-guard the `Complete` error before dereferencing `resp`
(it is nil on every error path); documented that `SessionHistory` arrives pre-redacted
(this package does not redact); documented `ConfidenceGate`'s missing-field→escalate
behavior.

**Residual risks (low):** `Output` may later be relocated/owned by #1's structured-output
work (no external dependents yet, so cheap to converge); `Config` will be superseded by
the TOML loader (kept file-format-agnostic on purpose).
