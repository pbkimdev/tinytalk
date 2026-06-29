# Plan — #29 Provider: local / OpenAI-compatible adapter

> **For the reviewer:** This is the **re-plan from current `main`**. The prior plan (#41) was written
> when its two hard dependencies were unmerged and so routed to `pending`; both have since landed on
> `main`:
> - **#24** — Go→Python pivot (`7ece95a`).
> - **#53 / #26** — provider seam + structured-output contract + strict parser + degradation chain
>   (`87b7585`): `clite/provider/base.py`, `clite/contract.py`, `clite/parsing.py`, `clite/engine.py`.
>
> The earlier triage note that "the OpenAI client is already merged" refers to **dead Go-era code**
> (`internal/provider/openai/client.go`), which no longer exists in the tree. The deliverable here is a
> **new Python adapter**, nothing more.

## Goal & scope

Add a Python `OpenAICompatProvider` that implements the existing `Provider` seam
(`clite/provider/base.py`) over any OpenAI-compatible `POST {base_url}/chat/completions` endpoint, so
the **already-built degradation chain** (`clite/engine.py:generate`) can drive a local backend
(Ollama / llama.cpp) end-to-end and the **already-built strict parser** (`clite/parsing.py`) can turn
its reply into a validated `Suggestion` contract.

**In scope (what #29 owns — the adapter):**
- New module `clite/provider/openai_compat.py` exposing `OpenAICompatProvider` with the seam's shape:
  attributes `name: str` and `capabilities: Capabilities`, and `async def complete(request: CompletionRequest) -> Completion`.
- HTTP transport via `httpx.AsyncClient` (POST to `/chat/completions`).
- **Auth header logic:** `Authorization: Bearer <key>` only when an API key is configured; **omitted entirely** for keyless local endpoints.
- **Per-rung request mapping** driven by `request.response_format`, so the engine's ladder rungs land correctly on the wire:
  - `TOOL_CALL` → `tools: [{type:"function", function:{name,description,parameters}}]` + `tool_choice` forcing the contract tool.
  - `JSON_OBJECT` → `response_format: {"type": "json_object"}`.
  - `GRAMMAR` → llama.cpp top-level `grammar` field (best-effort pass-through of `request.grammar`).
  - `TEXT` → plain completion (no special fields).
  - Always: `model`, `messages` (role+content); pass through `temperature`, `max_tokens`, `reasoning_effort` when set.
- **Response mapping** → `Completion`: `text` from `choices[0].message.content`; `tool_calls` from `choices[0].message.tool_calls` (→ `ToolCall(id, name, arguments)`); `usage` (missing → `Usage()`); `model`; `raw` = parsed envelope.
- **Typed error surface** for transport/envelope faults: non-2xx → typed HTTP error carrying status (no partial `Completion`); malformed/missing envelope → typed decode error; `asyncio.CancelledError` propagates untouched; timeouts surface as a typed error.
- Add `httpx` as the first runtime dependency in `pyproject.toml`; refresh `uv.lock`.

**Explicitly out of scope (owned elsewhere, consumed not duplicated):**
- The `Provider` Protocol, `CompletionRequest`/`Completion`/`Capabilities` types, the contract model,
  the strict parser, and the degradation-chain orchestrator — all already on `main` (#53/#26). This
  adapter is a pure leaf that plugs into them.
- **GBNF generation** (JSON-Schema→GBNF). The adapter only puts an already-built `request.grammar`
  string on the wire; producing it belongs to the engine/caller.
- Config-file wiring (`config.toml`) — that's #30. The adapter takes plain constructor args; #30 will
  construct it later.
- The Claude Agent SDK (#27) and Codex SDK (#28) adapters — sibling issues.
- Capability **auto-detection** (probing a server for what it supports). Capabilities are supplied
  explicitly; default is conservative (see below).

## Key design decisions

1. **Module path is `clite/provider/openai_compat.py`** (singular `provider/`, matching the existing
   package). *(The #41 draft said `clite/providers/…` — that path does not exist.)*
2. **Lazy import.** Do **not** add the adapter to `clite/provider/__init__.py`'s eager re-exports —
   that would import `httpx` on every `import clite.provider`. Callers import
   `from clite.provider.openai_compat import OpenAICompatProvider` on demand (PRD §15 hot-path /
   lazy-import posture). `httpx` is imported at the top of the adapter module only.
3. **Capabilities are explicit and conservative by default.** The issue says "no native JSON mode
   assumed," so the constructor's default is `Capabilities()` (all `False`) → the engine's ladder is
   the universal `[TEXT]` fenced-extraction path, which works on any server. A caller (or #30 config)
   passes `Capabilities(supports_tool_calling=…, supports_native_json=…, supports_grammar=…)` to opt
   into richer rungs per endpoint. The adapter never silently assumes a capability the server may lack.
4. **Adapter raises only on transport/envelope faults, never on valid-HTTP-but-unparseable *content*.**
   A clean HTTP 200 whose content is prose returns a normal `Completion`; the strict parser then
   rejects it (`FormatError`) and the engine degrades to the next rung. This division is essential:
   `engine.generate()` catches only `FormatError` for in-ladder retry/degradation, and lets transport
   errors abort — which is the intended behavior for a dead/erroring endpoint.
5. **Client lifecycle.** `complete()` uses an injected `httpx.AsyncClient` if one was supplied
   (constructor arg `client=…`, used by tests and connection reuse), else creates and closes a
   transient `AsyncClient` for that call (`async with`). No global client, no leak warnings.
6. **Request mapping keys off `request.response_format`** (the authoritative ladder signal), using
   `request.tools` / `request.grammar` as payload content. `tool_choice` forces the single contract
   tool by name to maximize `format_ok`; servers that ignore it yield empty `tool_calls` → the
   degradation chain covers them.

## Definition of Done

Measurable, all proven at the **unit level against a mocked transport** (the issue's "mock server"):

1. `OpenAICompatProvider` satisfies the seam: `isinstance(provider, Provider)` is `True`
   (`Provider` is `@runtime_checkable`), and it carries `name` + `capabilities`.
2. **End-to-end parsed contract:** `engine.generate(provider, messages)` against a mock endpoint
   returning a valid contract yields a `Generation` whose `.suggestion` is a fully-populated
   `Suggestion` (`command`/`explanation`/`danger`/`confidence`/`needs`/`alternatives`). This is the
   issue's literal "done when."
3. **Auth:** keyless (no/empty key) → outgoing request has **no** `Authorization` header; keyed →
   header is exactly `Bearer <key>`.
4. **Per-rung wire correctness:** each rung's POST body carries the right fields and nothing extra —
   `JSON_OBJECT`→`response_format`; `TOOL_CALL`→`tools`+`tool_choice` (schema == `contract_json_schema()`);
   `GRAMMAR`→`grammar`; `TEXT`→none of those. All carry `model` + mapped `messages`.
5. **Response mapping:** `usage` and `model` are carried into `Completion`; tool-call replies populate
   `Completion.tool_calls`; missing `usage` → `Usage()` default.
6. **Error surface:** non-2xx → typed HTTP error with `.status_code`, no `Completion` returned;
   malformed/missing envelope (e.g. `{}` / non-JSON body) → typed decode error;
   `asyncio.CancelledError` propagates; a timeout surfaces as a typed error.
7. **End-to-end degradation:** with `Capabilities(supports_native_json=True)`, a mock that fails the
   JSON-mode rung (both in-tier retries) then serves a fenced-block reply on the `TEXT` rung yields a
   parsed contract — proving the adapter is correctly driven across rungs.
8. `uv run pytest` and `uv run ruff check` are green; `uv.lock` is updated for the new `httpx` dep.

Smallest verification level that proves it: **unit tests over `httpx.MockTransport`** (deterministic,
async-native, lets us assert the outgoing `httpx.Request` URL/headers/JSON body and return canned
responses — a true mock of the server with no extra dependency and no real sockets). Tests run with
`asyncio.run(...)`, matching `tests/test_engine.py` (the repo has no `pytest-asyncio`).

## Steps (ordered, TDD)

1. **Add the runtime dependency.** In `pyproject.toml`, set `dependencies = ["httpx>=0.27"]`; run
   `uv lock` to refresh `uv.lock`. (First runtime dep — expected per PRD §10/§13.)
2. **Write failing tests first** in `tests/test_openai_compat.py` (red) — see Test strategy. Add a
   small local helper for building a `MockTransport` handler that records the request and returns a
   canned OpenAI-shaped envelope.
3. **Implement `clite/provider/openai_compat.py`** (green):
   - `class OpenAICompatProvider` — `__init__(self, base_url, model, *, api_key=None,
     capabilities=None, timeout=60.0, client=None)`; set `name = "openai-compat"` (or include the
     model), `capabilities = capabilities or Capabilities()`.
   - `async def complete(self, request)` — build payload via `_build_payload(request)`, build headers
     via `_headers()`, POST to `f"{base_url.rstrip('/')}/chat/completions"`, check status, decode
     envelope via `_parse_response(data)`.
   - Helpers: `_build_payload` (rung mapping + messages + optional params), `_headers` (auth logic),
     `_parse_response` (envelope → `Completion`, incl. `tool_calls`/`usage`/`model`/`raw`).
   - **Auth guard (verifier note):** emit `Authorization` only when the key is truthy *and* non-blank
     (`if api_key and api_key.strip():`) — never send a bare `Bearer ` for `api_key=""`.
   - **Tool-call arg normalization (verifier note):** the strict parser does
     `json.loads(tool_calls[0].arguments)` (`parsing.py:134`), so `ToolCall.arguments` MUST be a JSON
     *string*. OpenAI returns `function.arguments` as a string, but some local servers return an
     object — normalize: `arguments = tc if isinstance(tc, str) else json.dumps(tc)`.
   - Errors: define `OpenAICompatError(Exception)` base, `ProviderHTTPError(OpenAICompatError)` with
     `status_code`/`body`, `ProviderResponseError(OpenAICompatError)` for envelope decode/shape faults.
     Let `asyncio.CancelledError` propagate; wrap `httpx.TimeoutException` (and connect errors) in a
     typed `OpenAICompatError` (or a `ProviderTransportError` subclass).
4. **Run tests green; lint.** `uv run pytest -q`, `uv run ruff check`. Refactor for clarity (match the
   `from __future__ import annotations` + dataclass/typed style of the existing modules; line-length 100).
5. **Verify the end-to-end paths** (DoD #2 and #7) actually route through `engine.generate` with the
   mock transport, not just `complete()` in isolation.

## Test strategy (high-value, not exhaustive)

All in `tests/test_openai_compat.py`, over `httpx.MockTransport`:

- **`complete()` happy path (JSON-mode):** returns a `Completion` with mapped `text`, `usage`, `model`.
- **Seam conformance:** `isinstance(provider, Provider)`.
- **Auth — keyless:** no `Authorization` header on the captured request.
- **Auth — keyed:** header == `Bearer test-key`.
- **Auth — empty-string key** (`api_key=""`): no `Authorization` header (no bare `Bearer `).
- **Tool-call args returned as an object** (non-spec server): normalized to a JSON string so the
  parser's `json.loads` succeeds.
- **Per-rung wire mapping** (parametrized over the four `ResponseFormat`s via `complete()` directly):
  assert presence/absence of `response_format`, `tools`+`tool_choice` (with `parameters == contract_json_schema()`), `grammar`; assert `messages`/`model` always present.
- **Response → tool_calls mapping:** a tool-call envelope yields `Completion.tool_calls[0]` with id/name/arguments.
- **Missing `usage`** → `Usage()` default.
- **HTTP 500** → `ProviderHTTPError` raised, `.status_code == 500`, no `Completion`.
- **Malformed envelope** (`{}` / non-JSON) → `ProviderResponseError`.
- **Timeout / cancellation** surfaces cleanly (typed error / propagated `CancelledError`).
- **End-to-end via `engine.generate`:** valid contract → parsed `Suggestion` (DoD #2).
- **End-to-end degradation via `engine.generate`:** `Capabilities(supports_native_json=True)`; mock
  fails the JSON rung twice then serves a fenced block on `TEXT` → parsed `Suggestion`, and the
  captured requests show the rung switch (DoD #7).

## Risks & rollback

- **R1 — Seam-signature drift.** Mitigated: the seam is already merged and read directly; the adapter
  targets the exact dataclasses/Protocol in `clite/provider/base.py`. Low.
- **R2 — `tool_choice`/`response_format` dialect differences across local servers.** Best-effort wire
  shape; the degradation chain (and the universal `TEXT` rung) absorbs servers that ignore a field, so
  format compliance is preserved. Low.
- **R3 — GBNF is llama.cpp-specific.** The `grammar` rung is pass-through only; if a server ignores it,
  parsing fails that rung and the chain degrades. No correctness loss. Low.
- **R4 — New runtime dependency (`httpx`).** First runtime dep; standard, already in the agent-SDK
  ecosystem. Considered-and-rejected zero-dep alternative: stdlib `urllib` via `asyncio.to_thread`
  (loses real async I/O + clean cancellation, clunky). Low.
- **Rollback:** purely additive — one new module, one new test file, one dependency line + lockfile.
  Revert the PR; blast radius is zero (nothing else imports the adapter yet).
