# Plan — Issue #26: Core provider seam + structured-output contract + strict parser + degradation chain

> **Re-plan note.** This issue was first triaged against the Go codebase. `main` has since been
> re-platformed to Python (commit `7ece95a`, PR #24), and the earlier plan (#39) was rejected for
> exactly this reason. This plan targets the **current Python tree** and supersedes #39. The proven
> Go seam from #8 (`Name()`/`Complete(ctx, req)` with `Request`/`Response`/`Usage`/`Message`/`Tool`/
> `ToolCall`/`ResponseFormat`) is mirrored in Python idiom; the parts #8 never built — contract,
> strict parser, degradation chain — are added on top.

## Goal & scope

Build the **spine** of CLITE (part of #25): a transport-agnostic `Provider` seam, the
structured-output **contract** every backend must satisfy, a **strict parser** that gates on
`format_ok` (malformed output is rejected/retried, never surfaced), and a **degradation chain** so
weaker/local models still yield a valid contract object.

This is grounded in PRD §5 (structured-output contract), §10 (provider abstraction +
structured-output degradation), and §12/§11 (`format_ok` is the #1 enforceable eval metric → 100%).

### In scope
- `Provider` seam (`typing.Protocol`) with `name` and an **async** `complete(request) -> Completion`.
- Transport value types mirrored from the Go #8 design: `Message`/`Role`, `Tool`, `ToolCall`,
  `Usage`, `CompletionRequest`, `Completion`, `ResponseFormat`, `Capabilities`.
- Structured-output **contract** dataclass `Suggestion` (`command`, `explanation`, `danger`,
  `confidence`, `needs`, `alternatives`) + `Danger` enum + a JSON Schema describing it.
- **Strict parser**: `parse_completion()` / `parse_payload()` that validate to a `Suggestion` or
  raise `FormatError`. Includes a robust fenced-block / balanced-brace JSON extractor.
- **Degradation chain** orchestrator `generate()`: prefer native tool-calling/JSON → GBNF grammar →
  fenced-block extraction; reject + **retry** on malformed; never surface a malformed result.
- A reusable **stub backend** for tests (`tests/stubs.py`) — the issue's "stub backend".
- Unit tests for the parser and each degradation level (TDD).

### Explicitly out of scope (named, so `implement` doesn't drift)
- Real provider implementations (Claude Agent SDK, OpenAI Codex SDK, OpenAI-compatible/local) — these
  are sibling issues. Here we validate only against a **stub**.
- Actual **GBNF grammar compilation** from the schema. We define the *seam* (a `GRAMMAR` response
  format + an optional `grammar` field on the request + the contract JSON Schema the grammar will be
  derived from); generating/compiling real GBNF lands with the llama.cpp provider.
- **Danger classification** logic (PRD §7) — issue #4. Here we only *parse/validate* the model's
  stated `danger`; the real classifier overrides it later.
- Prompt assembly / grounding (PRD §6) — issue #3. `CompletionRequest.messages` is passed through.
- Caching (PRD §9), eval harness (PRD §11), CLI wiring. `clite/cli.py` is **untouched**; its tests
  stay green.
- Config file loading (`config.toml`).

## Definition of Done

Maps directly to the issue's "done when":

1. **Seam → validated contract.** A request run through `generate()` against the **stub backend**
   returns a validated `Suggestion` (a `Generation` carrying it with `format_ok=True`). Proven by a
   unit test.
2. **Malformed rejected + retried, not surfaced.** A stub that emits a deliberately malformed
   completion is rejected; `generate()` retries and either returns a *valid* `Suggestion` (when a
   later attempt is good) or raises `FormatError` (when all attempts fail). It **never** returns a
   malformed/garbage result. Proven by unit tests on both branches.
3. **Each degradation level is parseable.** Unit tests cover: native tool-call args, native JSON
   object / grammar output (clean JSON), and fenced-block extraction from prose — plus rejection of
   every malformed shape (bad JSON, missing field, bad enum, out-of-range confidence, wrong types,
   empty command).
4. **Verification level:** unit tests via `pytest` (`uv run pytest`), plus `ruff check`. No
   network, no real model — the stub is deterministic. Existing `tests/test_cli.py` still passes.

## Design decisions (settled here, per the issue)

- **Sync vs async → async-native seam.** `complete` is `async def`. Both first-class backends (Claude
  Agent SDK, OpenAI Codex SDK) are async-native (PRD §10), and the eval harness (#2) benefits from
  concurrency. Locking the seam async now avoids a painful signature migration when those land.
  - The parser and all extraction/validation helpers are **pure sync functions** — only the provider
    call and `generate()` are async, so most tests stay sync and need no async test plugin.
  - A thin sync convenience `generate_sync()` wraps `asyncio.run()` for simple/CLI callers.
  - Reversibility: the seam is tiny; if async proves wrong it is a localized change.
- **No runtime dependencies.** Use stdlib `dataclasses` + `enum` + `json` + hand-rolled validation
  rather than pydantic. Keeps the hot path light (PRD §15 cold-start concern) and avoids committing to
  a parsing library prematurely. `pyproject.toml` `dependencies` stays `[]`.
- **Chat-message request shape** (`messages: list[Message]`), mirroring #8 — not a single prompt
  string — because it fits OpenAI-compatible and the agent SDKs and lets #3 inject system grounding.
- **Capabilities drive the ladder.** A provider advertises `Capabilities(supports_tool_calling,
  supports_native_json, supports_grammar)`; `generate()` builds the ordered ladder from them, always
  appending the universal **text/fenced-block** fallback.

## Module layout

New files under `clite/` (additive; nothing existing is moved):

```
clite/provider/__init__.py   # public re-exports of the seam types
clite/provider/base.py       # Provider Protocol; Role/Message/Tool/ToolCall; Usage;
                             #   ResponseFormat; Capabilities; CompletionRequest; Completion
clite/contract.py            # Danger enum; Suggestion dataclass; contract_json_schema(); to/from dict
clite/parsing.py             # FormatError; extract_json_block(); parse_payload(); parse_completion()
clite/engine.py              # Generation dataclass; build_ladder(); generate(); generate_sync()
tests/stubs.py               # StubProvider / ScriptedProvider test double (the "stub backend")
tests/test_contract.py
tests/test_parsing.py
tests/test_engine.py
```

## Steps (ordered; TDD — test then code)

### 1. Seam value types — `clite/provider/base.py` + `__init__.py`
Mirror the #8 Go transport, in Python idiom:
- `class Role(str, Enum)`: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL`.
- `@dataclass(frozen=True) Message(role: Role, content: str)`.
- `@dataclass(frozen=True) Tool(name: str, description: str = "", parameters: dict | None = None)`.
- `@dataclass(frozen=True) ToolCall(id: str, name: str, arguments: str)` (`arguments` = JSON string).
- `class ResponseFormat(str, Enum)`: `TEXT`, `JSON_OBJECT`, `TOOL_CALL`, `GRAMMAR`.
- `@dataclass(frozen=True) Usage(prompt_tokens: int = 0, completion_tokens: int = 0,
  total_tokens: int = 0)` — telemetry for the eval harness (#2).
- `@dataclass Capabilities(supports_tool_calling=False, supports_native_json=False,
  supports_grammar=False)`.
- `@dataclass CompletionRequest(messages: list[Message], tools: list[Tool] = [],
  response_format: ResponseFormat = TEXT, grammar: str | None = None,
  reasoning_effort: str | None = None, temperature: float | None = None, max_tokens: int | None = None)`.
- `@dataclass Completion(text: str = "", tool_calls: list[ToolCall] = [], usage: Usage = Usage(),
  model: str = "", raw: object | None = None)`.
- `class Provider(Protocol)` (`@runtime_checkable`): `name: str`;
  `capabilities: Capabilities`; `async def complete(self, request: CompletionRequest) -> Completion`.
- `__init__.py` re-exports all of the above for `from clite.provider import ...`.

### 2. Contract — `clite/contract.py`
- `class Danger(str, Enum)`: `SAFE="safe"`, `CAUTION="caution"`, `DESTRUCTIVE="destructive"`.
- `@dataclass(frozen=True) Suggestion`: `command: str`, `explanation: str`, `danger: Danger`,
  `confidence: float`, `needs: tuple[str, ...]`, `alternatives: tuple[str, ...] = ()`.
  - `to_dict()` → JSON-serializable dict (danger as its `.value`, tuples → lists).
- `contract_json_schema() -> dict`: the JSON Schema for the object (used later by native
  structured-output providers and as the basis for GBNF). Required:
  `command, explanation, danger, confidence, needs`; `alternatives` optional.

### 3. Strict parser — `clite/parsing.py`
- `class FormatError(ValueError)` — raised on any non-conforming payload (the `format_ok=False` gate).
- `extract_json_block(text: str) -> str`: returns the JSON substring from a model's free-text reply:
  1. prefer a ```json … ``` fenced block, then a generic ``` … ``` fence;
  2. else scan for the first **balanced** `{ … }` object using a brace counter that is *string-aware*
     (ignores braces inside JSON string literals, respects `\"` escapes);
  3. raise `FormatError` if none found.
- `parse_payload(data: dict) -> Suggestion`: strict validation → `Suggestion` or `FormatError`:
  - reject if not a dict / missing any required key;
  - `command`: non-empty string after strip (VISION: always a real, runnable command);
  - `explanation`: string;
  - `danger`: must coerce to a `Danger` member (else reject);
  - `confidence`: real number in `[0.0, 1.0]` (bool rejected);
  - `needs`: list of strings (may be empty) → tuple;
  - `alternatives`: optional list of strings → tuple (default `()`).
- `parse_completion(completion: Completion, response_format: ResponseFormat) -> Suggestion`:
  dispatch on the format the answer was *requested* in:
  - `TOOL_CALL` → `json.loads(first tool_call.arguments)` → `parse_payload` (reject if no tool call);
  - `JSON_OBJECT` / `GRAMMAR` → `json.loads(completion.text.strip())` → `parse_payload`;
  - `TEXT` → `parse_payload(json.loads(extract_json_block(completion.text)))`.
  Any `json.JSONDecodeError` / `KeyError` / `TypeError` is converted to `FormatError`.

### 4. Degradation engine — `clite/engine.py`
- `@dataclass(frozen=True) Generation`: `suggestion: Suggestion`, `response_format: ResponseFormat`,
  `attempts: int`, `usage: Usage` (last completion's usage, for eval telemetry).
- `build_ladder(caps: Capabilities) -> list[ResponseFormat]`: ordered per PRD §10 —
  native (`TOOL_CALL` if tool-calling else `JSON_OBJECT` if native JSON) → `GRAMMAR` (if supported) →
  always-present `TEXT` fallback.
- `async def generate(provider, messages, *, tools=(), grammar=None, retries_per_tier=2,
  **req_opts) -> Generation`:
  - for each `fmt` in `build_ladder(provider.capabilities)`:
    - up to `retries_per_tier` times: build a `CompletionRequest` for `fmt` (set `response_format`,
      attach contract `tools`/`grammar`/schema as appropriate), `await provider.complete(req)`,
      `parse_completion(...)`; on success return `Generation(...)`; on `FormatError`, count the
      attempt and continue;
  - if the whole ladder is exhausted, raise `FormatError` — **malformed is never returned.**
  - This satisfies both DoD branches: a stub that returns `[garbage, valid]` within a tier succeeds on
    the retry (`attempts==2`); an all-garbage stub raises.
- `def generate_sync(...) -> Generation`: `asyncio.run(generate(...))` for non-async callers.

### 5. Stub backend — `tests/stubs.py`
- `class StubProvider`: constructed with `Capabilities` and a list/queue of canned `Completion`s (or a
  callable mapping `(request, attempt) -> Completion`); `complete()` pops the next scripted reply and
  records the requests it saw. Lets each test drive a specific tier and the garbage→valid retry path.

### 6. Tests (high-value, TDD)
- `test_contract.py`: `Suggestion` construction; `Danger` values; `to_dict()` round-trips through
  `parse_payload`; `contract_json_schema()` lists the required keys.
- `test_parsing.py`:
  - valid clean JSON → `Suggestion`;
  - fenced ```json``` block surrounded by prose → extracted + parsed;
  - generic ``` fence; no-fence balanced-brace object with trailing prose; **braces inside a string
    value** don't break extraction;
  - rejects (each `FormatError`): invalid JSON, missing required key, unknown `danger`, `confidence`
    out of `[0,1]` and non-numeric, `needs` not a list-of-str, empty/whitespace `command`.
- `test_engine.py` (async exercised via `asyncio.run`):
  - native tool-call stub → `generate` returns `format_ok` result, `attempts==1`;
  - native JSON-object stub → success;
  - **retry**: stub returns garbage then valid in the same tier → success, `attempts==2`, returned
    command equals the *valid* one (malformed never surfaced);
  - text-only capabilities → ladder falls to `TEXT`, extraction path used;
  - all-garbage across every tier → `generate` raises `FormatError`;
  - `build_ladder` ordering for representative capability sets.
- Keep `tests/test_cli.py` passing (no CLI change).

### 6a. Implementation caveats (do not transcribe pseudo-signatures verbatim)
The dataclass signatures above are illustrative. When implementing:
- **Mutable defaults must use `field(default_factory=...)`.** `tools: list[Tool] = []`,
  `tool_calls: list[ToolCall] = []`, and any `{}` default raise `ValueError` at class definition.
  Use `field(default_factory=list)` / `field(default_factory=dict)`. (`usage: Usage = Usage()` is fine
  *because* `Usage` is a frozen dataclass — keep it frozen.)
- **`confidence` bool guard.** `isinstance(True, (int, float))` is `True` in Python, so reject bools
  explicitly: check `not isinstance(value, bool)` before the numeric/range check.
- `@runtime_checkable` Protocol `isinstance` only checks attribute presence (not signatures/async) —
  adequate for the stub; don't rely on it for stronger guarantees.

### 7. Quality gate
- `uv run ruff check .` clean (line-length 100 per `pyproject.toml`); `uv run pytest` green.
- `pyproject.toml`: leave runtime `dependencies = []`. (No `pytest-asyncio` — tests use `asyncio.run`.)

## Test strategy (summary)
TDD, stdlib `pytest`. The parser/extractor get the densest coverage (it is the `format_ok` gate, the
#1 metric). The engine is tested against a deterministic `StubProvider` exercising each ladder tier
and the reject-then-retry contract. No network, no real models — fully reproducible.

## Risks & rollback
- **Async seam contested.** Mitigation: justified by SDK-first posture; sync wrapper provided; the
  seam is small and reversible. Residual: low.
- **GBNF deferred.** Only the seam exists now; `GRAMMAR` output is parsed as clean JSON, and the
  universal `TEXT` tier still catches anything non-JSON. The real grammar lands with the llama.cpp
  provider. Residual: low — no false guarantee is made.
- **Extractor edge cases** (nested/stringy braces). Mitigation: string-aware scanner + targeted tests;
  worst case the `TEXT` tier raises `FormatError` (safe — never surfaces malformed). Residual: low.
- **Validated only against a stub.** This is exactly the issue's stated done-when; real providers are
  sibling issues. Residual: low.
- **Rollback:** the change is purely additive (new modules + tests; `cli.py` untouched). Revert the
  single commit to return to the skeleton; no migration.
