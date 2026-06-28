# Plan — #32 · S0 eval harness (prompt suite + assertion DSL + scoring + telemetry/cost + leaderboard)

## Goal & scope

Build CLITE's built-in benchmark: a deterministic harness that runs a fixed prompt suite
against ≥2 model backends and produces a scored, cost-annotated leaderboard, so a user can
pick the cheapest backend that's still good enough (PRD §11–§12, VISION "How I'll judge it").

**In scope**
- A ~25-prompt suite, each prompt `{ id, text, assertions, expected_danger }` spanning the
  PRD §11 categories (disk/fs, text, search, process/system, net, git, archive, permissions,
  package mgmt, monitoring), shipped as an embedded data file.
- An **assertion DSL** — a small, declarative vocabulary of deterministic checks evaluated
  against the generated command string.
- A **deterministic scoring engine**: `format_ok`, `parses` (`zsh -n`), `binaries_exist`,
  `assertions_pass`, `danger_correct` — a pure function over (prompt, model output, injected
  checkers), fully unit-testable without network/shell/real binaries.
- **Telemetry + cost**: per-run model, tier, prompt/completion/total tokens, wall latency,
  cost (tokens × price table from config), cache-hit flag.
- **Leaderboard + per-prompt matrix renderer** with CSV/JSON export.
- A thin `clite eval` CLI that wires a config (backends + price table) into the harness.
- The structured-output **contract** (PRD §5) parsed by the harness, in a small shared package
  so the S1 engine can reuse it.

**Explicitly out of scope** (other issues / PRD "Out"):
- The S1 core engine, S2 grounding (curated toolset / `--help` fetch / PATH spec), S3 full
  safety ladder + danger classifier, S5 caching tiers. The harness calls a provider behind a
  small `Translator` seam; the real engine slots in later.
- `intent_score` LLM judge (PRD defers post-v1), sandbox/command **execution** (validation-only),
  the global `~/.config/clite/config.toml` TOML loader (S1 owns it — S0 reads a minimal JSON
  config), additional providers, daemon.
- No live-model calls in CI; the end-to-end proof uses deterministic fake backends.
- No new third-party dependencies — stdlib only (`encoding/json`, `encoding/csv`,
  `text/tabwriter`, `os/exec`, `regexp`, `go:embed`, `time`, `context`).

## Definition of Done

Measurable acceptance criteria (smallest verification level in parentheses):

1. **Scored leaderboard over ≥2 backends** — a single end-to-end run across two backends
   produces a per-model leaderboard with correctness %, tokens, latency, and **cost** columns,
   ranked. (Go test `eval_test.go`: two fake backends, one strictly better → it ranks first;
   cost/tokens totals correct.)
2. **Unit-tested scoring** — `ScorePrompt` returns correct `format_ok / parses / binaries_exist /
   assertions_pass / danger_correct` across pass, format-fail, syntax-fail, missing-binary,
   danger-mismatch, partial-assertion cases. (`score_test.go`.)
3. **Suite + DSL validated** — embedded suite loads with ≥25 prompts, unique ids, every
   assertion uses a known op, every `expected_danger` is a valid enum. (`suite_test.go`.)
4. **Contract parsing** — `contract.Parse` accepts strict JSON and fenced ```json blocks with
   surrounding prose; rejects malformed/invalid-danger. (`contract_test.go`.)
5. **Cost computation** — `PriceTable.Cost` = (prompt·input + completion·output)/1e6, unknown
   model → 0 (not a panic). (`cost_test.go`.)
6. **Export** — CSV and JSON exports of the report parse back to the same records.
   (`report_test.go`.)
7. **Runnable command** — `go build ./...` succeeds and `clite eval -config <file>` exists and
   runs the suite, rendering a leaderboard (+ `-matrix`, `-format text|json|csv`, `-out`).
   (Build + `clite eval -h`; config parsing unit-tested.)
8. **Safety** — the harness never executes a generated command: only `zsh -n` (parse-only) and
   `command -v`/`LookPath` (existence) touch the shell. (Code review + no `exec` of `cmd.Command`.)

`go test ./...` and `go vet ./...` are green.

## Design

### Package layout
```
internal/contract/contract.go        Command{}, Danger, Parse()         (+ _test)
internal/eval/suite.json             25 prompts (embedded)
internal/eval/suite.go               Prompt, Assertion, ParseAssertion, LoadSuite (+ _test)
internal/eval/assert.go              assertion ops registry + Eval(cmd)  (+ in suite_test/own _test)
internal/eval/score.go               Checks, SyntaxChecker, BinaryResolver, Result, ScorePrompt (+ _test)
internal/eval/cost.go                Price, PriceTable, Cost             (+ _test)
internal/eval/prompt.go              baseline SystemPrompt constant
internal/eval/runner.go              Translator, providerTranslator, Backend, RunRecord, Run (+ _test)
internal/eval/report.go              Report, leaderboard, RenderLeaderboard/Matrix, ExportJSON/CSV (+ _test)
internal/eval/config.go              eval Config (backends + prices) JSON loader (+ _test)
internal/eval/eval_test.go           END-TO-END: 2 fake backends → ranked leaderboard (DoD #1)
cmd/clite/main.go                    `clite eval` subcommand wiring
```

### Structured-output contract (`internal/contract`)
Mirrors PRD §5. Shared so the S1 engine reuses it.
```go
type Danger string // "safe" | "caution" | "destructive"
type Command struct {
    Command      string   `json:"command"`
    Explanation  string   `json:"explanation"`
    Danger       Danger   `json:"danger"`
    Confidence   float64  `json:"confidence"`
    Needs        []string `json:"needs"`
    Alternatives []string `json:"alternatives"`
}
func Parse(raw string) (Command, error)
```
`Parse`: try strict `json.Unmarshal`; on failure, extract the first ```json … ``` (or first
`{…}`) block and re-parse; validate `Danger` ∈ enum and `command` non-empty; else error.
This single function backs `format_ok`.

### Assertion DSL (`internal/eval/suite.go` + `assert.go`)
Assertions are written as compact strings (`"op arg"`) in the suite file — the DSL. Ops:
| op | meaning |
|---|---|
| `uses <tool>` | `<tool>` appears as a command word (token boundary) |
| `contains <substr>` | literal substring present |
| `absent <substr>` | literal substring **not** present (e.g. safety: `absent rm`) |
| `pipes_to <tool>` | a `\| <tool>` segment exists |
| `flag <flag>` | a flag token (`-h`, `--human-readable`) present |
| `regex <pattern>` | command matches the regex |

`ParseAssertion(s) (Assertion, error)` splits on first space → `{Op, Arg}`; unknown op or
empty arg → error (so the suite is self-checking at load). `Assertion.Eval(cmd string) bool`
dispatches on op. `assertions_pass` for a prompt = all assertions pass; the matrix also keeps
passed/total for partial reporting.

### Suite (`suite.json`, embedded via `//go:embed`)
Array of `{ id, text, expected_danger, assertions:[...] }`. `LoadSuite()` unmarshals, parses
each assertion via the DSL, and validates (unique ids, ≥25 entries, valid danger, valid ops),
returning `[]Prompt` or an error. The data file is guarded by `suite_test.go`.

### Scoring (`internal/eval/score.go`) — the pure core
```go
type SyntaxChecker interface { Check(command string) error } // default: zsh -n via stdin
type BinaryResolver interface { Exists(name string) bool }     // default: exec.LookPath
type Checks struct { Syntax SyntaxChecker; Binary BinaryResolver }

type Result struct {
    PromptID         string
    FormatOK         bool
    Parses           bool
    BinariesExist    bool
    DangerCorrect    bool
    AssertionsPassed int
    AssertionsTotal  int
    Assertions       []AssertionResult // {expr, pass}
}
func ScorePrompt(p Prompt, raw string, ch Checks) Result
```
Logic: `cmd, err := contract.Parse(raw)`. `FormatOK = err==nil`. If not OK, command-dependent
fields stay false (no reliable command). Else: `Parses = ch.Syntax.Check(cmd.Command)==nil`;
`BinariesExist = ∀ w ∈ commandWords(cmd.Command): ch.Binary.Exists(w)` (extract leading words of
each pipeline segment; fall back to `cmd.Needs` if extraction yields nothing);
`DangerCorrect = cmd.Danger == p.ExpectedDanger`; assertions evaluated against `cmd.Command`.
Pure & deterministic — tests inject fake `Checks`. Default impls live here but are never used in
the scorer's unit tests; the zsh checker has its own test guarded by `LookPath("zsh")`.

> S0 simplification (documented): `danger_correct` compares the model's **self-reported**
> `danger` field to `expected_danger`. When S3's classifier lands, the harness swaps in the
> real classification. This keeps the safety invariant (validation-only) intact.

### Telemetry, cost, runner (`runner.go`, `cost.go`)
```go
type Price struct { InputPerMTok, OutputPerMTok float64 }
type PriceTable map[string]Price
func (pt PriceTable) Cost(model string, u provider.Usage) float64

type Translator interface {
    Translate(ctx context.Context, nl string) (raw string, usage provider.Usage, model string, tier int, err error)
}
type providerTranslator struct { p provider.Provider; system, model string } // S0 baseline
type Backend struct { Name string; T Translator }

type RunRecord struct {
    Backend  string
    Result   Result
    Usage    provider.Usage
    Model    string
    Tier     int
    CacheHit bool   // PRD §11 telemetry column; hardwired false in S0 (caching is S5)
    LatencyMS int64
    CostUSD  float64
    Err      string
}
func Run(ctx context.Context, suite []Prompt, backends []Backend, ch Checks, prices PriceTable) Report
```
`providerTranslator.Translate` builds `[system, user]` messages with
`ResponseFormat: json_object`, calls `provider.Complete`, returns `resp.Content` (or first
`ToolCall.Arguments` if content is empty) as `raw`, plus `Usage`/`Model`. `Run` times each call
(`time.Now()` deltas → `LatencyMS`), scores via `ScorePrompt`, computes `CostUSD` from `prices`.
`Tier` is recorded as `1` and `CacheHit` as `false` in S0 (single grounded-lite call, no cache);
T0/T2 wiring attaches with S5/S2 without reshaping `RunRecord`.
The `Translator` seam lets the runner be tested with a fake (no HTTP) and lets the S1 engine slot
in unchanged.

### Report / renderer / export (`report.go`)
```go
type Report struct { Records []RunRecord }
type BackendSummary struct {
    Backend string
    N int
    FormatOKPct, ParsesPct, BinariesPct, AssertionsPct, DangerPct float64
    TotalPromptTokens, TotalCompletionTokens int
    TotalCostUSD float64
    MeanLatencyMS float64
}
func (r Report) Leaderboard() []BackendSummary // ranked: assertions_pass desc, then cost asc
func RenderLeaderboard(w io.Writer, r Report)  // text/tabwriter table
func RenderMatrix(w io.Writer, r Report)       // prompt × backend pass/fail grid
func ExportJSON(w io.Writer, r Report) error
func ExportCSV(w io.Writer, r Report) error    // encoding/csv, one row per RunRecord
```

### CLI (`cmd/clite/main.go`)
`clite eval` flags: `-config <path>` (JSON: backends `[{name, base_url, api_key_env, model,
reasoning_effort?}]` + `prices {model:{input_per_mtok, output_per_mtok}}`), `-format
text|json|csv` (default text), `-matrix`, `-out <file>` (default stdout). It loads config, builds
`openai.Client` backends (api key read from the named env var; `reasoning_effort`, when set, is
applied at construction via `openai.WithReasoningEffort` — not per request), runs `eval.Run`, and
renders.
Thin wiring; the tested core is `eval.Run` + renderers. `eval.Config` + `buildBackends` are
unit-tested. Unknown subcommand / `-h` prints usage.

## Steps (ordered, TDD: test first where noted)

1. **`internal/contract`** — write `contract_test.go` (strict, fenced, prose-wrapped, malformed,
   bad danger), then `contract.go` `Command`/`Danger`/`Parse`. *(traces: DoD #4, format_ok)*
2. **DSL + suite types** — `assert_test.go` for `ParseAssertion` + each op's `Eval`; then
   `assert.go` (op registry) and the `Prompt`/`Assertion` types + `LoadSuite` in `suite.go`.
   *(DoD #3)*
3. **`suite.json`** — author 25 prompts across PRD §11 categories with assertions +
   `expected_danger`; `suite_test.go` guards count/uniqueness/valid ops/valid danger. *(DoD #3)*
4. **Scoring** — `score_test.go` (fake `Checks`, all branches), then `score.go` `ScorePrompt` +
   default `zsh -n` / `LookPath` impls; command-word extraction helper. *(DoD #2, #8)*
5. **Cost** — `cost_test.go`, then `cost.go` `Price/PriceTable/Cost`. *(DoD #5)*
6. **Runner + telemetry** — `runner_test.go` with a fake `Translator` (canned raw + usage);
   assert telemetry captured and records scored; then `runner.go` (`Translator`,
   `providerTranslator`, `Run`) + `prompt.go` baseline system prompt. *(DoD #1 plumbing)*
7. **Report/render/export** — `report_test.go` (aggregation %, ranking, CSV/JSON round-trip,
   matrix dims), then `report.go`. *(DoD #1, #6)*
8. **End-to-end** — `eval_test.go`: two fake backends (B-good returns clean contracts, B-poor
   returns malformed/wrong) over the real suite → `Leaderboard()[0]` is B-good; cost/token totals
   correct; render to a buffer is non-empty. *(DoD #1 — the headline proof)*
9. **Config + CLI** — `config_test.go` (JSON parse, env-var key resolution, missing fields),
   then `eval.Config`/`buildBackends` and `cmd/clite/main.go` `eval` subcommand. *(DoD #7)*
10. `go vet ./...`, `go test ./...`, `go build ./...` green; tidy.

## Test strategy (high-value, not exhaustive)
- **Contract parse**: strict / fenced-with-prose / first-`{}`-fallback / malformed / invalid
  danger / empty command. (Realistic messy model outputs — the main field robustness risk.)
- **DSL**: every op parses + evaluates true/false on crafted commands; unknown op & empty arg
  error.
- **Suite data guard**: ≥25, unique ids, valid ops, valid danger (catches data-file rot).
- **ScorePrompt**: pass; format-fail short-circuits dependents; syntax-fail; one missing binary;
  danger mismatch; partial assertions — all via injected fakes (no shell, no real binaries).
- **Cost**: normal, zero usage, unknown model → 0.
- **Runner**: fake `Translator` → latency ≥ 0, usage/cost recorded, error surfaced into
  `RunRecord.Err` without aborting the run.
- **Report**: percentage math, ranking order, CSV & JSON export re-parse to identical records,
  matrix dimensions = prompts × backends.
- **End-to-end** (`eval_test.go`): the DoD scenario — deterministic, no network.
- **zsh checker**: integration test skipped when `zsh` absent.
- Out: exhaustive per-op permutations, live-model tests, CLI golden-output snapshots.

## Risks & rollback
- **No live model in CI** → end-to-end proof uses fake backends; real `clite eval` is verified
  by build + manual local run. *Residual:* provider-specific output quirks (JSON-in-markdown,
  refusals) aren't exercised live — mitigated by `contract.Parse` fallback tests over messy
  inputs. (Low.)
- **`zsh` not installed in CI** → `SyntaxChecker` is injectable; scorer tests never need zsh;
  the zsh-backed test self-skips. (Low.)
- **`danger_correct` is model-self-reported in S0** → documented seam; real classifier arrives
  with S3. (Low — does not affect the safety invariant; nothing is executed.)
- **Config format (JSON now vs PRD TOML later)** → isolated to `internal/eval` + the CLI; S1's
  global `config.toml` supersedes it without touching the harness core. (Low.)
- **Scope creep into S1/S2/S3/S5** → the `Translator` seam and explicit non-goals keep the
  harness independent; no existing files change.
- **Rollback**: fully additive (`internal/contract`, `internal/eval`, `cmd/clite`, `go.mod`
  unchanged except possibly nothing). Revert the PR; existing `provider`/`openai` tests are
  untouched and stay green.

## Verification summary
**Rounds: 1 of 5 → PASS.** A verifier subagent checked the plan on two axes:
- **Requirements** — all issue scope (25-prompt suite + assertion DSL, deterministic scoring,
  telemetry+cost from a price table, leaderboard/matrix + CSV/JSON export) and the "done when"
  (≥2-backend cost-annotated leaderboard, unit-tested scoring, one e2e run) are covered, and map
  cleanly to PRD §11/§12 (the five deterministic scores + telemetry columns + export +
  validation-only safety). No out-of-scope leakage into S1/S2/S3/S5 — those are deferred behind
  the `Translator` seam.
- **Feasibility** — every referenced API exists and matches the source:
  `provider.Provider.Complete(ctx, Request) (*Response, error)`, `Request.Messages` /
  `ResponseFormat=json_object`, `Response.Content` / `ToolCalls[].Arguments`,
  `Usage.{Prompt,Completion,Total}Tokens`, and `openai.New(baseURL, apiKey, model, opts...)` with
  `WithReasoningEffort`/`WithName`. Stdlib-only suffices; `go.mod` (go 1.24) needs no new deps.

Two minor, non-blocking notes were raised and **incorporated**: (1) added a `CacheHit bool`
telemetry field to `RunRecord` (hardwired `false` in S0 so the report schema is S5-ready);
(2) made explicit that `reasoning_effort` is applied at client construction via
`WithReasoningEffort`, not per request.

**Residual risk (low):** real-model output quirks and the live `clite eval` path are not exercised
in CI (no network/keys) — mitigated by `contract.Parse` fallback tests over messy inputs and the
deterministic fake-backend end-to-end test; verified for real by a local build/run.
