# Plan — Issue #29: Provider: local / OpenAI-compatible adapter

## 0. Critical context (read first)

This issue is a sub-issue of **#25 — clite v1 (Python) re-platform**. The project is being moved
**from Go to Python** (#24, currently an *open* PR on branch `pivot/python-replatform`). The provider
seam in #25 is **SDK-first**: Claude Agent SDK + OpenAI Codex SDK as first-class in-process backends,
plus a lightweight OpenAI-compatible path for **local** models (Ollama / llama.cpp) — that lightweight
path is *this* issue.

**The existing Go code is not the deliverable.** `internal/provider/openai/client.go` + `provider.go`
are Go-era artifacts (PR #8/#21). #25 explicitly *supersedes* the Go-era issues (#1–#6, #8–#17). The
triage note that "the OpenAI client is already merged" refers to that superseded Go code; it does **not**
satisfy #29, which is a Python deliverable.

**Hard dependencies — #29 cannot be implemented until both land on `main`:**

| Dep | What it provides | Status |
|---|---|---|
| **#24** pivot to Python | removes all Go, scaffolds the `clite` Python package (uv/hatchling, `pyproject.toml`, console entry point) | open PR, **not merged** |
| **#26** provider seam | `Provider` Protocol (`name`, `complete(request) -> response`; **sync-vs-async decided there**), the structured-output **contract** model, the **strict parser**, and the **degradation-chain** orchestrator | open, **not started** |

Because `auto` is not set on this issue, this plan routes to **`pending`** for human approval. The
recommendation to the reviewer is: **schedule #29 to implement *after* #24 and #26 merge.** This plan is
written so it is ready to execute the moment those land, and so the design is reviewable now.

---

## 1. Goal & scope

**Goal.** A Python `OpenAICompatProvider` that implements the #26 provider seam over any
OpenAI-compatible `/chat/completions` endpoint, omits the auth header for keyless local endpoints
(Ollama / llama.cpp), and — driven by #26's degradation chain — returns a **parsed structured-output
contract**. Mirrors sibling #27 (Claude adapter) for the local transport.

**In scope**
- An `OpenAICompatProvider` class implementing the #26 `Provider` seam (`name` + `complete`).
- HTTP transport to `POST {base_url}/chat/completions` via `httpx`.
- **Auth header omitted when no API key** is configured (keyless local endpoints); `Authorization:
  Bearer <key>` sent when a key is present.
- **Participation in the degradation chain**: the adapter faithfully translates each rung's request
  hint to the wire so #26's chain can drive it —
  1. **native JSON mode** → `response_format={"type": "json_object"}`,
  2. **constrained grammar (GBNF)** → llama.cpp `grammar` field (best-effort; passed through),
  3. **fenced-block fallback** → plain completion; #26's parser extracts the fenced JSON.
- Clean mapping of clite `Request`/`Response` ↔ the OpenAI wire shapes (messages, tools, usage, model,
  raw bytes).
- Typed error surface for non-2xx responses, malformed envelopes, timeouts, and cancellation.
- Config-driven `base_url`, `model`, optional `api_key`, optional `reasoning_effort`, `timeout`,
  provider `name`.
- Unit tests against a **mock HTTP server** proving a sample prompt yields a parsed contract.

**Explicitly out of scope**
- The `Provider` Protocol, the contract dataclass/model, the strict parser, and the degradation-chain
  **orchestrator** — all owned by **#26**. #29 *consumes* them; it does not define or duplicate them.
- The Claude Agent SDK (#27) and OpenAI Codex SDK (#28) adapters.
- Config-file loading / `config.toml` parsing (#30) — the adapter takes already-resolved settings.
- Tier controller / T0–T2 escalation (#31), grounding (#33), validation & safety (#34).
- Retries / backoff and streaming responses (note as future; v1 keeps a single bounded request).
- Tool-calling as a structured-output rung: PRD §10 notes small local models have unreliable
  tool-calling, so the local chain relies on JSON-mode → grammar → fenced. The adapter still passes
  `tools` through if supplied, but tool-calling is not a required rung here.

---

## 2. Definition of Done (measurable)

The smallest verification level that proves "working" is **unit tests against a mock server** (the
issue's stated bar), plus `ruff`/`pytest` green. Specifically:

1. `OpenAICompatProvider` satisfies the #26 `Provider` seam (passes the seam's structural/`isinstance`
   check or Protocol conformance).
2. A sample prompt run through the adapter **+ #26's parser/chain** returns a **valid contract object**
   (`command`, `explanation`, `danger`, `confidence`, `needs`, `alternatives`) — `format_ok`.
3. Against a mock server with **no API key**, the request carries **no `Authorization` header**; with a
   key, it carries `Authorization: Bearer <key>`.
4. The request body for each rung is correct on the wire: JSON-mode rung sends
   `response_format={"type":"json_object"}`; grammar rung sends the `grammar` field; fenced rung sends
   neither (plain completion).
5. A non-2xx response raises a **typed provider error** carrying the status code (no partial
   `Response`); a malformed JSON **envelope** raises a decode error (no partial `Response`); a timeout /
   cancelled context surfaces as a typed error / propagates cancellation.
6. End-to-end **degradation**: when the JSON-mode rung returns content the parser rejects, the chain
   escalates and a later rung's valid content yields a parsed contract (drives #26's chain through the
   adapter against the mock server).
7. `pytest` passes; `ruff check` is clean.

---

## 3. Design

### 3.1 Seam contract consumed from #26 (assumptions to reconcile at implement time)

#29 imports from #26 rather than redefining. Expected surface (final names/shape come from #26 — the
implementer **reconciles against #26's merged code**, this is the working assumption):

```python
# owned by #26 — e.g. clite/providers/base.py
class Provider(Protocol):           # or ABC
    @property
    def name(self) -> str: ...
    def complete(self, request: "Request") -> "Response": ...   # sync vs async settled in #26

# owned by #26 — request/response carriers
@dataclass
class Request:
    messages: list[Message]
    structured: StructuredHint | None   # which degradation rung to use
    tools: list[Tool] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None

@dataclass
class Response:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    raw: bytes

# owned by #26 — structured-output contract + parser + chain
class Command: ...                       # command/explanation/danger/confidence/needs/alternatives
def parse_contract(text: str) -> Command # strict parser (format_ok gate)
# degradation chain orchestrator that calls Provider.complete per rung and parses
```

The **degradation rung** is expressed as a hint on the `Request` (e.g. `StructuredHint` =
`json_object | grammar(gbnf=...) | none`). #26's chain sets the hint and escalates on parse failure;
#29 only has to translate the hint to the wire and return raw `content`. If #26 instead models rungs
differently, the adapter's `_build_payload` mapping is the single place to adjust.

**Sync vs async:** #26 decides. Default assumption for #29 is **async** (`async def complete`) because
the sibling SDK backends (Claude Agent SDK, Codex SDK) are async-native, so an async seam is most
likely. The HTTP call is isolated in one helper, so flipping to sync is a localized change. **Flag for
the implementer:** match #26's actual signature exactly.

### 3.2 Adapter

```python
# clite/providers/openai_compat.py
class OpenAICompatProvider:                      # implements #26 Provider
    def __init__(self, base_url, model, *, api_key=None, name="local",
                 reasoning_effort=None, timeout=60.0, http_client=None): ...

    @property
    def name(self) -> str: return self._name

    async def complete(self, request: Request) -> Response:
        payload = self._build_payload(request)            # messages, model, params, rung mapping
        headers = {"Content-Type": "application/json"}
        if self._api_key:                                 # omit auth when keyless
            headers["Authorization"] = f"Bearer {self._api_key}"
        resp = await self._client.post(self._url, json=payload, headers=headers)
        self._raise_for_status(resp)                      # non-2xx -> ProviderHTTPError
        return self._parse_envelope(resp.content)         # decode -> Response (or decode error)
```

- `_build_payload(request)` — maps `messages`/`tools`/`temperature`/`max_tokens`/`reasoning_effort`,
  and the structured hint: `json_object` → `response_format={"type":"json_object"}`; `grammar(gbnf)` →
  `grammar=<gbnf>` (llama.cpp); `none` → neither.
- `_raise_for_status` — non-2xx → typed `ProviderHTTPError(status, message, type)` (best-effort decode
  of `{"error": {...}}`); reuse / subclass #26's base provider error if it defines one, else define a
  local error type to be folded into #26's hierarchy.
- `_parse_envelope` — decode the `/chat/completions` envelope → `Response`; raise on malformed JSON (no
  partial `Response`); empty `choices` → error.
- `http_client` injection lets tests pass an `httpx.AsyncClient` pointed at the mock server and avoids a
  per-call client when a caller wants to reuse one.

### 3.3 Where files live (per #24's `clite` package scaffold)

```
clite/providers/__init__.py            # export OpenAICompatProvider
clite/providers/openai_compat.py       # the adapter  (new — this issue)
tests/providers/test_openai_compat.py  # mock-server tests  (new — this issue)
pyproject.toml                         # add httpx (runtime) + pytest-httpserver (dev)  [+]
# consumed from #26: clite/providers/base.py, clite/<contract+parser+chain modules>
```

---

## 4. Steps (ordered)

> Precondition: #24 and #26 are merged to `main`. Reconcile every "#26-owned" name against the merged
> code before writing the adapter.

1. **Dependencies.** Add `httpx` to project runtime deps and `pytest-httpserver` to the dev group in
   `pyproject.toml`. (`pytest-httpserver` gives a *real* local server — the closest Python analog to the
   Go `httptest.NewServer` the superseded tests used, and it proves auth-header omission over the wire.)
2. **Tests first (TDD, red).** Write `tests/providers/test_openai_compat.py` against the mock server for
   the cases in §5 — they fail because the adapter doesn't exist yet.
3. **Adapter skeleton (green).** Create `clite/providers/openai_compat.py` with the constructor, `name`,
   `complete`, and helpers from §3.2; wire payload building, header logic, status handling, envelope
   decode. Export from `clite/providers/__init__.py`.
4. **Degradation wiring.** Confirm the structured-hint → wire mapping for all three rungs and that
   #26's chain, given this adapter, escalates JSON-mode → grammar → fenced and yields a parsed contract
   (the §5.6 end-to-end test).
5. **Errors & cancellation.** Implement the typed error surface; ensure timeouts/cancellation surface
   cleanly (async cancellation propagation; `httpx` timeout → typed error).
6. **Refactor & lint.** Tidy, ensure `ruff check` clean, docstrings on public API, type hints.
7. **Verify.** `pytest` + `ruff check` green; confirm DoD §2 items 1–7.

Each step traces to the goal; the adapter is a single self-contained commit (size:S).

---

## 5. Test strategy (high-value, TDD; mock server via `pytest-httpserver`)

1. **Happy path / JSON mode → parsed contract.** Mock server returns an envelope whose `content` is a
   valid contract JSON. Assert request body has `model`, `messages`, `response_format={"type":
   "json_object"}`; assert adapter `Response.content` round-trips and #26's `parse_contract` yields a
   `Command` with the six fields. *(covers DoD 1,2,4)*
2. **Keyless endpoint → no `Authorization`.** Construct with `api_key=None`; assert the server saw no
   `Authorization` header. *(DoD 3 — mirrors the Go `TestComplete_NoKey`)*
3. **Keyed endpoint → `Authorization: Bearer <key>`.** *(DoD 3)*
4. **HTTP error.** Server returns 500 with `{"error":{"message":...}}`; assert a typed
   `ProviderHTTPError` with `status_code == 500` and **no** `Response`. *(DoD 5)*
5. **Malformed envelope.** Server returns `{not valid json`; assert a decode error and no partial
   `Response`. *(DoD 5)*
6. **Degradation escalation end-to-end.** Mock server returns, in sequence: a JSON-mode reply the parser
   *rejects*, then a valid reply on the next rung. Drive #26's chain with the adapter; assert the final
   result is a parsed `Command` and that the escalation occurred. *(DoD 6)*
7. **Grammar-rung wire mapping.** With a grammar hint, assert the request body carries the `grammar`
   (GBNF) field. *(DoD 4)*
8. **Timeout / cancellation** (lightweight): a slow handler with a tight client timeout surfaces a typed
   error / cancellation, no partial `Response`. *(DoD 5)*

Not exhaustively permuting every param; these prove the seam conformance, auth behavior, error mapping,
and the degradation-to-parsed-contract path the issue's "done when" calls for.

---

## 6. Risks & rollback

**Risks**
- **R1 — sequencing (primary).** #24 (pivot) and #26 (seam/parser/chain) are not on `main`. Building #29
  first means there is no Python package and no seam to implement. *Mitigation:* route `pending`;
  recommend implementing after both merge. This plan defines the consumed contract so it's ready.
- **R2 — seam signature drift.** #26 settles sync-vs-async and the exact `Request`/`Response`/contract
  shapes; the plan assumes async + the shapes above. *Mitigation:* HTTP and mapping are isolated in
  `_build_payload`/`complete`; reconcile against #26's merged code at step 0. Low blast radius.
- **R3 — GBNF grammar is llama.cpp-specific.** Not every OpenAI-compatible server accepts `grammar`.
  *Mitigation:* treat the grammar rung as best-effort/pass-through; #26's chain always has the universal
  fenced-block fallback, so a server that ignores `grammar` still degrades safely.
- **R4 — new dependencies** (`httpx`, `pytest-httpserver`). Both are mainstream and small; `httpx` is
  already the transport under the Anthropic/OpenAI SDKs the project adopts. Low risk.

**Rollback.** The change is purely additive — a new module + tests + two dependency lines. Reverting the
PR removes the adapter with zero impact on other modules (nothing imports it until the tier controller /
config wiring does). No migration, no data, no destructive operations.

---

## 7. Verification summary

A verifier subagent checked this plan against **(1) requirements** (PRD §5, §10, §13; issue #29; parent
#25; seam #26) and **(2) feasibility** (referenced files/APIs and the dependency state). Rounds used and
residual risk are recorded in `.agentic/comment.md`. Residual risk is the sequencing dependency on #24
and #26 (R1), which is surfaced for the human approver rather than resolved here.
