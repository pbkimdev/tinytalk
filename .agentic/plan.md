# Plan — #27 Provider: Claude Agent SDK adapter

> Phase: `plan` · Size: M · Risk: low · Route: `pending` (human `/approve`)
> Part of the **#25 Python re-platform** roadmap. Depends on **#24** (Go→Python
> pivot) and **#26** (provider seam + structured-output contract + strict parser +
> degradation chain). See "Dependencies & sequencing" before implementing.

## Goal & scope

Add a **first-class, in-process Claude backend** to clite's provider seam, implemented
over the **Claude Agent SDK for Python** (`claude-agent-sdk`, import `claude_agent_sdk`).
The adapter maps clite's `Request`/`Response` onto the SDK's one-shot `query()` call,
drives a single non-agentic turn, and returns the model's text so the seam's strict
parser (#26) can extract the structured-output contract. Model and reasoning **effort**
are config-driven; authentication rides the SDK's environment conventions
(`ANTHROPIC_API_KEY`).

**In scope**
- One new provider module implementing the #26 `Provider` interface over `claude_agent_sdk.query`.
- Map `Request` (system/user messages, `response_format`, reasoning effort) → `ClaudeAgentOptions` + prompt.
- Map the SDK's `AssistantMessage`/`ResultMessage` stream → clite `Response` (content, model, token usage, raw).
- Config-driven `model` + `effort`; env-based auth (no key handling in clite).
- Error mapping: SDK failures / `ResultMessage.is_error` → the seam's provider error type.
- Unit tests with the **SDK mocked** (inject the `query` callable; no `claude` CLI, no network, no key).
- Add `claude-agent-sdk` to `pyproject.toml` runtime dependencies (pinned).

**Out of scope** (explicit)
- The agent tool-loop beyond what v1 tiers T0–T2 need — **no agent tools** are exposed
  (`allowed_tools=[]`, single turn): no Read/Write/Bash/MCP access. T3 agentic is deferred
  (PRD §4, issue out-of-scope), and clite never runs commands.
- The seam's *native function-calling* structured-output tier. #26's (Go) plan gets the
  contract via an `emit_command` **function tool** on an OpenAI-compatible endpoint. The
  Claude Agent SDK has no equivalent OpenAI-style function-tool knob here, so this adapter
  serves the seam's **JSON-instruction / fenced-extraction** strategy instead (prompt for
  the contract JSON → return text → #26's strict parser). Native enforcement via the SDK's
  output schema (`ResultMessage.structured_output`) or an MCP `emit_command` tool is a
  documented future enhancement, not v1. (Note: the seam's "tools" = model function calls;
  the SDK's `allowed_tools` = agent file/shell tools — different concepts.)
- Multi-turn conversational sessions (`ClaudeSDKClient`); v1 is single-shot `query()`.
- Defining the seam types, the contract, the parser, or the degradation chain — those are #26.
- Temperature / explicit `max_tokens` knobs — the Agent SDK does not surface these the
  same way; documented as unsupported-via-this-backend rather than faked.

## Dependencies & sequencing (read first — there is a cross-issue blocker)

`main` is still the **Go-era** tree (`go.mod`, `internal/provider/**`). The Python tree
lands via **#24** (`origin/pivot/python-replatform`: flat `clite/` package — `clite/cli.py`,
`clite/__init__.py`, tests in top-level `tests/`, `pyproject.toml` with `uv` + hatchling,
pytest+ruff, `requires-python >=3.10`, empty runtime deps). The provider seam this adapter
implements lands via **#26**.

> ⚠️ **Blocker — #26's posted plan is Go, not Python.** As of now, `origin/agentic/issue-26`
> contains **no seam code** — only a plan, and that plan targets the **Go** tree:
> `internal/contract` + `internal/engine`, Go structs, and an explicitly *synchronous*
> `Complete(ctx, Request)` ("no new async API"). That contradicts #26's own issue body and
> the #24/#25 re-platform, which mandate **Python**. The #26 planner appears to have worked
> off Go-era `main` without the pivot in context.
>
> **#27 cannot be implemented in Go**: the Claude Agent SDK has *no Go binding* — providing
> first-class, in-process Claude support is the stated reason for the Python pivot (#24/#25).
> So the only feasible direction for #27 is Python, and it depends on a **Python** seam.
> **#26's plan must be re-done in Python (sync-vs-async settled there) before #27 is
> implemented.** This is surfaced for the human in `comment.md`; until #26 is corrected,
> #27's "Assumed seam contract" below is provisional.

Given that, this adapter **cannot build until #24 lands and #26 ships a Python seam.** The
plan and its review surface (`.agentic/plan.md`) are base-agnostic and committed now; the
*implement* phase must run on a branch based on the Python tree **with #26's Python seam
present**:

1. Base the implement branch on `main` **after** #24 + a corrected Python #26 have merged,
   **or** rebase onto #26's branch once it carries Python seam code.
2. The adapter **imports** the seam's `Request`, `Response`, `Message`, `Role`, `Usage`,
   `ResponseFormat`, `Provider`, and the provider error type from #26. It must **not**
   redefine them. #26 is the single source of truth for those names/shapes.
3. If a seam symbol's final name/signature/path/async-ness differs from what this plan
   assumes, **conform to #26** — adjust the adapter, not the seam.

Because the route is `pending`, a human approves and sequences this PR (and, first, the
#26 correction).

## Assumed seam contract (provisional — see the blocker above)

These are the shapes the adapter codes against. They are the **Go seam re-expressed in
Python** (#26's issue body says the seam is "unchanged in intent, re-expressed in Python"),
so they are a reasonable basis — but they are **provisional until #26 ships a Python seam**.
The module path and async-ness are explicitly TBD: the pivot package is **flat**
(`clite/cli.py`, no subpackages yet), so the seam may live at `clite/providers/…`
(proposed) *or* flat as `clite/provider.py` / `clite/providers.py`. Conform to #26's actual
choice; this plan uses `clite/providers/` as a placeholder only.

Assumed #26 surface:

- `Request`: `messages: list[Message]`, `response_format: ResponseFormat`,
  `reasoning_effort: str | None` (and possibly `tools`, `temperature`, `max_tokens`).
- `Message`: `role: Role`, `content: str`; `Role` ∈ {system, user, assistant, tool}.
- `Response`: `content: str`, `model: str`, `usage: Usage`, `raw: ...`
  (and possibly `tool_calls`, a cost field).
- `Usage`: `prompt_tokens`, `completion_tokens`, `total_tokens`.
- `ResponseFormat`: text vs `json_object` (mirrors the Go seam's `ResponseFormatJSONObject`).
- `Provider`: an async interface exposing `name` and `complete(request) -> Response`
  (async because both agent SDKs are async-first). If #26 makes `complete` **sync**, the
  adapter wraps the async `query()` via `asyncio.run`/a runner instead.
- A provider error type (e.g. `ProviderError`) for transport/decode/API failures
  (analogous to the Go `openai.APIError`).
- A strict contract parser (e.g. `parse_contract(text) -> Contract`) + degradation chain
  the adapter's output feeds into. In the Go seam this is `contract.Parse([]byte)` plus an
  `engine` that drives strategies (native tool-call → grammar → fenced); the Python seam is
  expected to expose an equivalent parser the adapter's text output feeds into.

These are derived from the Go seam (`internal/provider/provider.go`,
`internal/provider/openai/client.go`) re-expressed in Python. The Go seam is **not** merged
into the Python tree and #26 is re-platforming away from it, so treat these as provisional;
the implement phase confirms exact names/paths/async-ness against #26's Python seam.

## The Claude Agent SDK surface this adapter uses (verified against current docs)

- **Package** `claude-agent-sdk` · **import** `claude_agent_sdk`. Requires the Claude Code
  CLI (Node) at runtime — the SDK launches it as a subprocess. Tests avoid this by
  injecting a fake `query` callable.
- `async def query(*, prompt: str | AsyncIterable, options: ClaudeAgentOptions | None,
  transport: Transport | None) -> AsyncIterator[Message]` — one-shot, new session.
- `ClaudeAgentOptions` fields used:
  - `model: str | None` — full model ID or alias (`"sonnet"`/`"opus"`/`"haiku"`).
  - `system_prompt: str | None` — clite's system message (+ JSON-only instruction when needed).
  - `allowed_tools: list[str] = []` — **kept empty**: no *agent* tools (Read/Write/Bash/MCP).
    (Unrelated to the seam's model-function-calling tier, which this adapter does not use.)
  - `max_turns: int | None` — set to `1` (single model turn, no agentic loop).
  - `setting_sources: list | None` — set to `[]` so user/project `CLAUDE.md`/settings are
    **not** loaded (hermetic, reproducible — matters for the eval harness).
  - `effort: "low"|"medium"|"high"|"xhigh"|"max"` — maps 1:1 from clite's reasoning effort.
  - (`permission_mode` left default; irrelevant with no tools. `env`/`thinking` available
    if needed; `max_thinking_tokens` is deprecated in favour of `thinking`/`effort`.)
- Message stream types: `AssistantMessage(content: list[ContentBlock], model, usage, ...)`
  with `TextBlock(type, text)`; terminal `ResultMessage(is_error, result, total_cost_usd,
  usage={input_tokens, output_tokens, cache_*}, model_usage, stop_reason, errors, ...)`.
- **Auth**: env only, per the SDK's documented conventions — primarily `ANTHROPIC_API_KEY`
  (the SDK/CLI reads it; Bedrock/Vertex env flags also exist). clite never reads, stores, or
  logs the key, and tests assert *no key is required*, not specific env-var names.
- **Errors**: SDK raises `ClaudeSDKError` subclasses (`CLINotFoundError`,
  `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError`). Catch the base.

## Definition of Done

Working = the issue's done-when: *a sample prompt returns a parsed contract via the Claude
Agent SDK, unit-tested with the SDK mocked.* Concretely, all must hold:

1. A new adapter module (path per #26; placeholder `clite/providers/claude_agent.py`)
   defines a provider that satisfies #26's `Provider` interface (verified by a
   type/`isinstance`/protocol check in tests, mirroring the Go
   `var _ provider.Provider = (*Client)(nil)` assertion).
2. Given a `Request` whose expected model output is the PRD §5 contract JSON, `complete`
   returns a `Response` whose `content` **parses cleanly through #26's contract parser**
   into a `Contract` (`command`, `explanation`, `danger`, `confidence`, `needs`,
   `alternatives`). This is the headline acceptance check.
3. The adapter passes `model` and `effort` from its config into `ClaudeAgentOptions`, and
   sets `allowed_tools=[]`, `max_turns=1`, `setting_sources=[]` (asserted by capturing the
   options handed to the injected `query`).
4. SDK failure or `ResultMessage.is_error` surfaces as the seam's provider error, with
   **no partial `Response`** returned.
5. Auth is env-only: the adapter never accepts or logs an API key.
6. **Verification level**: unit tests with the SDK injected/mocked — `pytest` green,
   `ruff check` clean. No live API call, no `claude` CLI, no key required in CI. A live
   smoke test (real key + CLI) is documented as a manual/opt-in step, not a CI gate.

## Steps (ordered)

1. **Confirm the seam (no code) — and the blocker.** First verify #26 has shipped a
   **Python** seam (not the current Go plan). If it has not, **stop**: #27 is blocked (see
   "Dependencies & sequencing"). Once present, read the seam module (path TBD — likely
   `clite/providers/` or flat `clite/provider*.py`) and record the exact names/shapes of
   `Request`, `Response`, `Message`, `Role`, `Usage`, `ResponseFormat`, `Provider`, the
   error type, and the contract parser, **and whether `Provider.complete` is async or
   sync**. Everything below conforms to what is found.

2. **Add the dependency.** In `pyproject.toml`, add `claude-agent-sdk>=<pin>` to
   `[project].dependencies` (replace the empty list / append). Run `uv lock`. Pin to a
   known-good version and confirm the imported symbols (`query`, `ClaudeAgentOptions`,
   `AssistantMessage`, `ResultMessage`, `TextBlock`, `TextBlock.text`, `ClaudeSDKError`)
   exist in that version — the package was renamed from `claude-code-sdk`, so verify.

3. **Write the failing tests first (TDD).** `tests/test_claude_agent_provider.py` (or
   `tests/providers/…` to match #26). Inject a fake async `query` that yields real SDK
   dataclasses; drive `complete` with `asyncio.run`. (See Test strategy.)

4. **Implement the adapter.** `clite/providers/claude_agent.py`:
   - `class ClaudeAgentProvider` with constructor
     `(*, model: str, effort: str | None = None, system_prompt: str | None = None,
     query_fn=claude_agent_sdk.query, name="claude-agent")` — `query_fn` is the injection
     seam for tests (analogue of the Go `WithHTTPClient`).
   - `name` property returns the provider name.
   - `_build_options(request) -> ClaudeAgentOptions`: route `RoleSystem` messages (joined
     with the configured `system_prompt`) into `system_prompt`; set `model`,
     `effort` (normalized/validated against the SDK's accepted set), `allowed_tools=[]`,
     `max_turns=1`, `setting_sources=[]`. When `request.response_format == json_object`,
     append the contract's JSON-only instruction to the system prompt (degradation path —
     the SDK has no OpenAI-style `response_format`).
   - `_build_prompt(request) -> str`: concatenate user/assistant turns into the single
     `prompt` string (v1 requests are typically one system + one user message).
   - `async def complete(request) -> Response`: iterate `query_fn(prompt=…, options=…)`;
     accumulate `TextBlock.text` from `AssistantMessage`s; capture the terminal
     `ResultMessage`. On `is_error` (or a caught `ClaudeSDKError`) raise the seam's
     provider error. Otherwise build `Response(content=final_text, model=…,
     usage=Usage(input_tokens, output_tokens, total), raw=…)`; populate a cost field from
     `total_cost_usd` if the seam exposes one. Prefer `ResultMessage.result` for the final
     text, falling back to accumulated assistant text.
   - If #26's `Provider.complete` is **sync**, expose a sync `complete` that runs the async
     core via `asyncio.run`, keeping the async core private.
   - Module-level conformance assertion that `ClaudeAgentProvider` satisfies `Provider`.

5. **Tools / native tier handling.** This adapter does **not** implement the seam's native
   function-calling tier. If the seam hands a request carrying function `tools` (e.g. the
   `emit_command` schema), the adapter ignores them and relies on the JSON-instruction
   path; it never enables the SDK's *agent* tools (`allowed_tools` stays `[]`). Leave a
   comment pointing at the future `structured_output`/MCP enhancement and deferred T3.

6. **Green + lint.** `uv run pytest` green; `uv run ruff check` clean. Update the
   `pyproject.toml` comment that says deps are "added when those land" if helpful.

## Test strategy (high-value, TDD)

`tests/test_claude_agent_provider.py`. Build real SDK dataclasses (`AssistantMessage`,
`TextBlock`, `ResultMessage`) — importing the package is pure Python and needs no CLI/key —
and inject a fake async-generator `query_fn`. Drive `complete` via `asyncio.run` to avoid a
new async-test plugin (add `pytest-asyncio`/`anyio` only if #26 already uses it).

1. **Happy path → parsed contract** (headline DoD): fake `query` yields an
   `AssistantMessage` whose `TextBlock.text` is the PRD §5 contract JSON, then a
   `ResultMessage(is_error=False, result=<same JSON>, usage={input_tokens:10,
   output_tokens:5}, total_cost_usd:…)`. Assert `complete` returns a `Response` whose
   `content` parses through #26's parser into a `Contract` with the expected fields, and
   whose `usage` maps to prompt=10/completion=5/total=15.
2. **Options mapping**: capture the `ClaudeAgentOptions` passed to the fake `query`; assert
   `model` and `effort` came from config, and `allowed_tools==[]`, `max_turns==1`,
   `setting_sources==[]`. Assert system messages are routed to `system_prompt`.
3. **JSON-only degradation**: with `response_format=json_object`, assert the system prompt
   handed to the SDK contains the contract's JSON-only instruction.
4. **Error path**: fake `query` raises a `ClaudeSDKError` (and, separately, yields a
   `ResultMessage(is_error=True, errors=[…])`). Assert `complete` raises the seam's
   provider error and returns **no** `Response`.
5. **Conformance**: assert `ClaudeAgentProvider` satisfies the `Provider` interface and
   `name` is set (mirrors the Go compile-time assertion).
6. **No-key hermeticity** (light): construct and run the provider with no
   `ANTHROPIC_API_KEY` in the environment and the injected `query_fn`; assert it works and
   the adapter never reads/echoes a key.

This mirrors the five OpenAI client tests (`internal/provider/openai/client_test.go`:
happy path, HTTP/API error, malformed/decode, cancellation, no-key) adapted to the SDK.

## Risks & rollback

- **Cross-issue blocker (primary).** #26's only posted artifact is a **Go** plan, which
  contradicts the #24/#25 Python re-platform; #24 and a Python #26 are not yet on `main`.
  Implement is blocked until #26 ships a *Python* seam. #27 cannot fall back to Go (the
  Claude Agent SDK has no Go binding). Mitigation: the blocker is surfaced to the human in
  `comment.md`; `pending` route so a human re-plans #26 and sequences. *Residual risk:
  medium (process/dependency, not adapter code).*
- **Seam drift.** Even once #26 is Python, if its final names/signatures (esp.
  sync-vs-async `complete`, module path, the error type, the parser entry point) differ
  from the provisional contract, the adapter needs small edits. Mitigation: Step 1 confirms
  against #26; conform to #26.
- **SDK API/version drift.** `claude-agent-sdk` evolves and was renamed from
  `claude-code-sdk`; `max_thinking_tokens` is already deprecated. Mitigation: pin the
  version, verify symbols at implement time, prefer the stable `effort`/`thinking` fields.
- **Runtime CLI/Node requirement.** The SDK shells out to the Claude Code CLI at runtime.
  Out of scope for this unit (tests mock it), but note in docs that the Claude backend
  needs the CLI + a key present on the user's machine.
- **No native structured output enforced.** We rely on prompt instruction + #26's strict
  parser/degradation (PRD §5/§10), not the SDK's `structured_output`. Acceptable for v1;
  flagged as a future enhancement.
- **Rollback**: the change is additive and isolated (one module + tests + one dependency).
  Revert the PR; the seam and other providers are untouched.

## Verification summary

Filled in by the plan-phase verifier loop (rounds + residual risks) and reflected in
`.agentic/comment.md`.
