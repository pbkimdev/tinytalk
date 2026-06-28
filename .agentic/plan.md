# Plan — Issue #9: Anthropic adapter

## Goal & scope

Add a **native Anthropic Messages API adapter** as a new package
`internal/provider/anthropic`, implementing the existing `provider.Provider`
interface (`Name()`, `Complete()`) so CLITE can use Claude as a backend
alongside the OpenAI-compatible client from #8. This is the "+ Anthropic"
backend the PRD's v1 thin-slice requires (PRD §10, §13; epic #1 / S1).

The adapter is a self-contained translation layer: it converts a
`provider.Request` into an Anthropic `POST /v1/messages` call and the JSON
response back into a `provider.Response`. It deliberately mirrors the shape,
ergonomics, and test coverage of `internal/provider/openai/client.go`.

**In scope**
- New package `internal/provider/anthropic` with `client.go` + `client_test.go`.
- Request translation: model, system-prompt extraction, messages, tools
  (`input_schema`), `max_tokens`, temperature.
- Auth + protocol headers: `x-api-key`, `anthropic-version`, `content-type`.
- Response translation: flatten content blocks → `Content`; `tool_use` blocks →
  `[]ToolCall`; map `usage` (`input_tokens → PromptTokens`, `output_tokens →
  CompletionTokens`, `TotalTokens = input+output`); carry `Model` and `Raw`.
- Typed `*APIError` for non-2xx (mirrors `openai.APIError`) + `AsAPIError`.
- `ReasoningEffort` → Anthropic **extended thinking** mapping (see Decisions).
- Compile-time `var _ provider.Provider = (*Client)(nil)` assertion.
- Functional-options constructor mirroring the OpenAI client.

**Out of scope (deferred / belongs to other issues)**
- Wiring the adapter into any registry/factory/config — that is the config
  loader (#12) and tier controller (#13). #9 ships only the package.
- Streaming (`stream: true`) — v1 is single-shot request/response.
- Full multi-turn tool-**result** round-tripping. The shared `provider.Message`
  (`{Role, Content string}`) cannot express a `tool_use_id`, so a proper
  `tool_result` turn is not representable today — same limitation the OpenAI
  client has. v1 tiers (T0/T1/T2) are single-shot or re-ask; the agentic
  tool-loop (T3) is deferred (PRD §4, §13). We send tools and parse `tool_use`
  back; feeding results in is a later `provider.Message` extension.
- Native JSON/`response_format` enforcement. Anthropic has no `json_object`
  mode; the structured-output **degradation chain** lives above the adapter
  (#11, PRD §10). `ResponseFormatJSONObject` is therefore a documented no-op
  at this layer.
- Prompt caching, batch API, files API, vision/image blocks.

## Key Messages-API differences from OpenAI (drives the translation)

| Concern | OpenAI (`/chat/completions`) | Anthropic (`/v1/messages`) |
|---|---|---|
| Auth header | `Authorization: Bearer <key>` | `x-api-key: <key>` |
| Version header | — | `anthropic-version: 2023-06-01` (required) |
| System prompt | message with `role:"system"` | top-level `system` string field |
| `max_tokens` | optional | **required** |
| Tool schema field | `function.parameters` | `input_schema` (sibling of `name`) |
| Response text | `choices[0].message.content` (string) | `content[]` blocks (`type:"text"`) |
| Tool calls | `message.tool_calls[].function` | `content[]` blocks (`type:"tool_use"`, `input` is an object) |
| Tool-call args | JSON **string** | JSON **object** (`input`) → we marshal to string |
| Usage | `prompt/completion/total_tokens` | `input_tokens`/`output_tokens` (no total → compute) |
| Stop field | `finish_reason` | `stop_reason` |
| Error shape | `{error:{message,type}}` | `{type:"error", error:{type,message}}` |
| Reasoning | `reasoning_effort` passthrough | `thinking:{type:"enabled",budget_tokens}` (+ temp must be unset/1) |

## Decisions (deliberate, with rollback)

1. **`ReasoningEffort` → extended thinking.** The OpenAI adapter honors
   `ReasoningEffort`; dropping it silently here would be a real parity gap.
   When effort is set (per-request `req.ReasoningEffort`, else client default
   from `WithReasoningEffort`), send `thinking:{type:"enabled",budget_tokens}`
   via a small table — `low:1024, medium:4096, high:8192` (min budget is 1024;
   unknown non-empty → `medium`). Anthropic requires `max_tokens` **strictly
   greater than** `budget_tokens` (equal is a 400) and rejects a non-1
   `temperature` while thinking is on, so when thinking is enabled we set
   `max_tokens = max(resolvedMaxTokens, budget + margin)` with a real non-zero
   output `margin` (the bump must always fire even when `defaultMaxTokens ≤
   budget`) and **omit `temperature`** (server defaults it to 1).
   On the response side, `thinking`/`redacted_thinking` blocks are skipped (only
   `text` feeds `Content`). *Rollback:* the mapping is isolated in one helper +
   one wire field; reducing it to a no-op (effort ignored) is a one-line change
   if a reviewer prefers to defer thinking.

2. **Default base URL.** `New("", ...)` defaults `baseURL` to
   `https://api.anthropic.com/v1`; a non-empty value (e.g. a proxy/gateway) is
   used verbatim. The endpoint path `"/messages"` is appended, mirroring the
   OpenAI client's `"/chat/completions"`.

3. **`anthropic-version` default** `2023-06-01`, overridable via `WithVersion`.

4. **`RoleTool` messages** map defensively to a `user` message (Anthropic has no
   `tool` role). Not exercised by v1 single-shot; documented in code.

## Definition of Done

Measurable acceptance — all verified at the **unit** level (the smallest level
that proves correctness; no live network, `httptest` only):

- [ ] `internal/provider/anthropic` compiles and the compile-time assertion
      `var _ provider.Provider = (*Client)(nil)` holds.
- [ ] `go build ./...` and `go vet ./...` are clean; `gofmt` reports no diff.
- [ ] `go test ./internal/provider/anthropic/...` passes.
- [ ] Happy-path test asserts the request carries `x-api-key`,
      `anthropic-version`, `POST /messages`, correct `model`, and a present
      `max_tokens`; and that a response with a `text` + a `tool_use` block
      yields `Content`, one `ToolCall` (with JSON-string `Arguments`), and
      `Usage` mapped to `PromptTokens`/`CompletionTokens` with
      `TotalTokens == input+output`.
- [ ] A `RoleSystem` message is extracted to the top-level `system` field and
      is **absent** from the `messages` array.
- [ ] A `provider.Tool` is serialized with `input_schema` (not `parameters`).
- [ ] When `ReasoningEffort` is set, the request carries a `thinking` block with
      a budget and **no** `temperature`; `thinking` response blocks are skipped.
- [ ] Non-2xx → `*anthropic.APIError` (with `StatusCode` + parsed message),
      `nil` response; malformed JSON → decode error, `nil` response; cancelled
      context → ctx error, `nil` response.
- [ ] `max_tokens` defaults are applied when `req.MaxTokens == 0`.

## Steps (ordered, TDD: red → green)

1. **Create the package skeleton** — `internal/provider/anthropic/client.go`:
   package decl, imports (std lib + `internal/provider`), defaults
   (`defaultBaseURL`, `defaultVersion`, `defaultMaxTokens`), and the
   compile-time assertion. No external deps (matches openai).

2. **Wire types** — request: `wireRequest{model, max_tokens, system,omitempty,
   messages, tools,omitempty, temperature,omitempty, thinking,omitempty}`,
   `wireMessage{role, content}`, `wireTool{name, description,omitempty,
   input_schema}`, `wireThinking{type, budget_tokens}`. Response:
   `wireResponse{model, content[], stop_reason, usage}`,
   `wireBlock{type, text, id, name, input json.RawMessage}`,
   `wireUsage{input_tokens, output_tokens}`, `wireError`.

3. **`APIError` + `AsAPIError`** — copy the openai pattern, `"anthropic:"`
   prefix; populate from `wireError.Error.{Message,Type}`.

4. **`Client` struct + options + `New`** — fields: `httpClient, baseURL, apiKey,
   model, version, reasoningEffort, maxTokens, name`. Options:
   `WithHTTPClient, WithName, WithReasoningEffort, WithMaxTokens, WithVersion`.
   `New(baseURL, apiKey, model, ...Option)` with `name:"anthropic"`,
   `version:defaultVersion`, default `httpClient`, empty `baseURL`→default.
   `Name()` returns `c.name`.

5. **`Complete`** —
   - `extractSystem(req.Messages)` → `(system string, msgs []wireMessage)`:
     join `RoleSystem` contents with `"\n\n"`; map the rest
     (`user`/`assistant`/`tool`→`user`).
   - resolve `max_tokens` (req → client → `defaultMaxTokens`). Unlike the OpenAI
     client (which omits `max_tokens` when 0), Anthropic **requires** the field,
     so we always send a value — add a one-line code comment so this divergence
     isn't flagged as inconsistency.
   - resolve effort (req → client); if set, `thinking` via `thinkingBudget`,
     ensure `max_tokens > budget` (bump if needed), drop `temperature`; else
     pass `req.Temperature` through.
   - tools via `toWireTools` (→ `input_schema`).
   - `ResponseFormatJSONObject`: comment noting it is a no-op here.
   - marshal; build `http.NewRequestWithContext(POST, baseURL+"/messages")`;
     set `content-type`, `x-api-key` (only if key non-empty),
     `anthropic-version`.
   - `Do`; read body; non-2xx → `*APIError`.
   - decode `wireResponse`; assemble `Response`: concat `text` blocks → `Content`,
     `tool_use` → `ToolCall{ID, Name, Arguments:string(input or "{}")}`, skip
     other block types; `Usage{PromptTokens:input_tokens,
     CompletionTokens:output_tokens, TotalTokens:input+output}` (note the
     `provider.Usage` field names — Anthropic has no total, so we compute it);
     `Model`, `Raw`.

6. **Helpers** — `extractSystem`, `toWireTools`, `thinkingBudget(effort) int`.

7. **Tests** — `internal/provider/anthropic/client_test.go` (see strategy).

8. **Quality gate** — `gofmt -l`, `go vet ./...`, `go build ./...`,
   `go test ./...`.

## Test strategy (high-value, `httptest`-based, mirrors openai_test)

A `happyResponse()` helper builds a Messages-shaped body (content blocks +
usage). External-network-free.

1. **Happy path** — captures request: asserts `x-api-key`+`anthropic-version`
   headers, `POST /messages`, `model`, `max_tokens > 0`; response text+tool_use
   → `Content`, one `ToolCall` (`Arguments` is the marshalled `input`), `Usage`
   with computed `TotalTokens`.
2. **System extraction** — a `RoleSystem` + `RoleUser` request: decoded body has
   top-level `system` set and `messages` containing only the user turn.
3. **Tool translation** — a `provider.Tool{Parameters:…}` serializes under
   `tools[].input_schema`; `name`/`description` carried.
4. **Extended thinking** — `WithReasoningEffort("high")` (and a per-request
   override case): body has `thinking.budget_tokens > 0`, no `temperature`, and
   `max_tokens > budget`; a response containing a `thinking` block + a `text`
   block yields `Content` == only the text.
5. **HTTP error** — 500 with Anthropic error body → `*APIError`, `StatusCode
   500`, message populated, `nil` response (via `AsAPIError`).
6. **Malformed JSON** — `nil` response + non-nil decode error.
7. **Context cancelled** — pre-cancelled ctx → error, `nil` response.
8. **max_tokens default** — `req.MaxTokens == 0` → body carries the default.
9. **No key** — empty `apiKey` → no `x-api-key` header sent (local proxy case).

## Risks & rollback

- **API contract drift** (thinking/temperature coupling, required fields). Mitigated
  by `anthropic-version` pinning and tests asserting the exact wire shape.
  *Rollback:* the package is additive and unreferenced by other code; deleting
  the directory fully reverts with zero blast radius.
- **Thinking scope creep.** If a reviewer wants the thinking mapping deferred,
  it collapses to a no-op (Decision 1) without touching the rest of the adapter.
- **`provider.Message` can't carry tool-result ids.** Acknowledged shared
  limitation (not introduced here); full tool-loop support is a separate change
  when T3/the harness needs it.

## Verification summary

Internal verifier checked the plan against PRD/issue intent and codebase
feasibility — **1 round, result PASS** (no blockers).

- **Requirements:** PASS — correctly scoped to a leaf `provider.Provider`
  adapter; out-of-scope boundaries (wiring #12, degradation #11, tier
  controller #13) match the triage note and PRD §13; the `ResponseFormat`
  no-op and the `provider.Message` tool-result limitation are accurately
  characterized.
- **Feasibility:** PASS — all referenced types/APIs exist; the Anthropic
  `/v1/messages` contract in the plan (headers, top-level `system`, required
  `max_tokens`, `input_schema`, `content[]` text/tool_use blocks with object
  `input`, `input_tokens`/`output_tokens`, error shape, extended-thinking
  constraints) is factually correct on every load-bearing point.

Two precision items raised and folded back in: (1) spell out the
`provider.Usage` field mapping (`input_tokens→PromptTokens`,
`output_tokens→CompletionTokens`, computed `TotalTokens`) so the shorthand
doesn't leak into code; (2) make the `max_tokens > budget_tokens` bump
unconditional with a real margin (equal is a 400). Nits (defaultMaxTokens
divergence comment, omit-temperature-under-thinking) also incorporated.

**Residual risk:** low — the package is additive and referenced by no other
code, so rollback is deleting the directory; the only API-contract sensitivity
is the thinking/temperature/max_tokens coupling, which the tests assert
directly.
