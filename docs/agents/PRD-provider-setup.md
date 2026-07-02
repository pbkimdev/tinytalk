# CLITE — Provider Setup & Auth UX (PRD)

> Status: Draft v0.1 · Owner: Paul · Last updated: 2026-07-02
>
> Standalone feature PRD, additive to `docs/agents/PRD.md`. Cross-references below point to the
> current code, not to PRD.md section numbers.

## 1. Thesis

Today, using CLITE with anything other than a hand-written `~/.config/clite/config.toml` is not
possible — there is no onboarding. **`clite auth` is an interactive setup wizard** that picks one
provider kind, authenticates against it using that provider's own idiom (API key, OS credential
chain, or SDK-native login), lists the models actually available to that credential instead of a
guessed/hardcoded set, and writes a validated backend into config.toml. It also extends the
provider seam from two kinds to six.

Not in scope: a general multi-provider marketplace (opencode/pi-style). CLITE only ever needs a
handful of kinds; the wizard is a short linear flow, not a browsable catalog.

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Provider kinds | Six: `openai-compat`, `anthropic-compat` (new), `claude-agent-sdk`, `codex-agent-sdk` (new), `bedrock` (new), `azure-openai` (new) |
| Active backend model | One primary backend + one optional **fallback** backend. Reuses the existing `escalation_backend` config key/mechanism (renamed in wording only, not schema) — see §5 |
| Fallback trigger | Widened: today `TierController` only falls through to the second backend on a validation failure (`FormatError`/bad output). This adds provider-level failures (transport/auth/rate-limit errors) as a second trigger — see §5 |
| Secrets | OS keychain via the `keyring` package. `clite auth` never writes a raw secret into config.toml; it stores one and writes a lookup reference. Existing hand-written `api_key_env` continues to work unchanged |
| Model discovery | Live call where the provider has one; static curated list (bundled in clite, hand-refreshed per release) where it doesn't or the live call fails; free-type always available as an escape hatch |
| SDK packaging | `boto3` (Bedrock) and `openai-codex` (Codex Agent SDK) are optional extras (`clite[bedrock]`, `clite[codex]`). Azure needs no new dependency (plain HTTP via existing `httpx`). `claude-agent-sdk` stays a hard dependency, unchanged — out of scope to touch |
| Config writing | `tomlkit` (round-trip TOML) so `clite auth` can add/replace one `[backends.<name>]` table without clobbering hand-added tables, comments, or ordering elsewhere in the file |
| TUI | `questionary` (arrow-key select / text / password prompts) for a short linear wizard — not a full-screen framework. Imported lazily inside the `auth` subcommand only, same cold-start discipline as `clite eval`/`clite init` today |
| Trigger | Explicit `clite auth` only. A bare `clite "<request>"` with no config prints a one-line hint and exits 1 — never auto-launches the wizard (the zsh widget invokes clite via command substitution; an interactive prompt there would hang/garble the terminal) |
| `/model` | No separate subcommand. The wizard's model picker is the only model-selection surface; changing model later means re-running `clite auth`, which replaces just that one backend's table |
| Non-interactive path | Out of scope. Hand-editing config.toml remains the scripted/CI path; unchanged |

## 3. Goals / non-goals

**Goals**
- Turn "I have a key/credential for X" into a working, validated backend entry in under a minute,
  without hand-editing TOML.
- Never guess a model name the user's credential can't actually use, where discovery is possible.
- Extend the provider seam to six kinds without breaking the two that exist.
- Never store a raw secret in plaintext TOML.

**Non-goals (this PRD)**
- No general N-provider catalog/marketplace UX.
- No mid-session `/model` switching (no persistent session exists to switch within).
- No non-interactive/scripted `clite auth` (`--kind`/`--yes` flags, CI-friendly mode).
- No auto-launch of the wizard from the hot request path.
- Full model-access verification for Bedrock (the wizard lists the region's model *catalog* via
  `list_foundation_models`, not confirmed per-account entitlement — an unauthorized model choice
  surfaces as a runtime error on first real use, not at setup time).

## 4. Provider kinds

| Kind | Adapter | Auth | Model discovery | Effort |
|---|---|---|---|---|
| `openai-compat` | `provider/openai_compat.py` (existing) | Paste API key → keyring (skippable for keyless local servers) | `GET {base_url}/models` (OpenAI-compatible; works for Ollama/llama.cpp too); free-type fallback | Static low/medium/high, passthrough as `reasoning_effort` (existing, unvalidated by the server) |
| `anthropic-compat` | `provider/anthropic_compat.py` (new) | Paste API key → keyring | `GET {base_url}/v1/models` (`x-api-key` + `anthropic-version` headers); response includes `capabilities.effort` per model | Read directly from that model's `capabilities.effort` list — no guessed mapping |
| `claude-agent-sdk` | `provider/claude_agent.py` (existing) | SDK's own convention (Claude Code login or `ANTHROPIC_API_KEY`) — wizard tests a trivial call and reports pass/fail with guidance; clite manages no secret here | No list-models call on the SDK; static curated list (aliases: sonnet/opus/haiku + full IDs) | Existing `_EFFORT_LEVELS` (low/medium/high/xhigh/max) |
| `codex-agent-sdk` | `provider/codex_agent.py` (new) | SDK-managed, stateful: reuse an existing local Codex CLI login, or `codex.login_api_key(key)` once (persists into the SDK's own local storage — clite doesn't store this secret) | `codex.models(include_hidden=True)` | `none/minimal/low/medium/high/xhigh` via `model_reasoning_effort` |
| `bedrock` | `provider/bedrock.py` (new) | Region (+ optional named profile) using boto3's default credential chain; explicit access-key-pair entry is a fallback, stored in keyring as one JSON blob if the default chain fails | `bedrock.list_foundation_models()` | Only for Claude-on-Bedrock, via `additionalModelRequestFields.thinking.budget_tokens` (low=2048/medium=8192/high=24576); skipped for other model families |
| `azure-openai` | `provider/azure_openai.py` (new) | Endpoint + API version + paste API key → keyring | None — Azure retired the key-only deployment-list API; deployment name is always typed | Same as `openai-compat` (identical wire shape) |

`openai_codex`'s exact package name/API shape is based on the current `openai/codex` GitHub repo
(`sdk/python`) and is pre-1.0 (`0.1.0b3`) — re-verify against the installed version before
finalizing the adapter; a mismatch here is a pinned-version bump, not a design change.

## 5. Config schema changes

`BackendConfig` (clite/config.py) gains:

```
keyring_account: str | None = None   # keyring lookup (service "clite"); tried when api_key_env unset
effort: str | None = None            # passed through as reasoning_effort; validated loosely (§ enum below)
aws_region: str | None = None        # bedrock only
aws_profile: str | None = None       # bedrock only
azure_api_version: str | None = None # azure-openai only
```

No new fields for Azure's endpoint/deployment — the existing `base_url` and `model` fields already
cover them, keeping the schema additive rather than kind-specific everywhere.

`VALID_KINDS` grows from `("openai-compat", "claude-agent-sdk")` to include
`"anthropic-compat"`, `"codex-agent-sdk"`, `"bedrock"`, `"azure-openai"`.

`VALID_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")` — the config
validator only checks membership in this superset; the wizard is what narrows the offered choices
to what a given kind/model actually supports. An adapter ignores effort values it doesn't
understand rather than erroring (unsupported combos degrade silently, matching the existing
degradation-chain philosophy).

`api_key` resolution order (unchanged precedence, just widened): if `api_key_env` is set and the
env var has a value, use it (backward compatible with hand-written configs); else if
`keyring_account` is set, look it up via `keyring.get_password("clite", account)`; else `None`.

`[defaults].escalation_backend` is **unchanged as a config key** (renaming it would break existing
hand-written configs and the tests/wiring already built around it in #31). The wizard's own
copy/prompts call it "fallback" — the user-facing framing shifts, the TOML key doesn't.

## 6. Fallback trigger widening (`clite/tiers.py`)

`TierController.suggest()`'s T1 step currently only escalates on `FormatError`. This adds a shared
`ProviderError` base exception in `clite/provider/base.py`; every adapter's own error type
(`OpenAICompatError`, `ClaudeAgentError`, and the four new ones) subclasses it. T1's `except`
clause widens to `except (FormatError, ProviderError)`, so a dead/misconfigured primary backend
(timeout, 401, rate limit) falls through to the fallback backend instead of failing the whole
request. `tiers.py` only ever imports `clite.provider.base`, so this doesn't violate the
lazy-import/cold-start discipline — no new adapter imports are added to the hot path.

## 7. `clite auth` wizard flow

1. If `config.toml` exists, show configured backends and offer: add a new backend, replace an
   existing one by name, set which is primary/fallback, or exit. Otherwise go straight to step 2.
2. Pick a provider kind (`questionary.select`, one-line description each).
3. Kind-specific auth step (per §4) — test the credential with one real call (list-models call
   where one exists, else a minimal completion) before proceeding; on failure, show the actual
   error and let the user retry or abort.
4. Model picker, populated per §4; always offers "type a different model id".
5. Effort picker, scoped to what that kind/model supports; step is skipped entirely when nothing
   applies (e.g. Bedrock non-Claude; Azure keeps the static low/medium/high picker per §4).
6. Capabilities are set automatically per kind (not prompted) — e.g. a known public
   `openai-compat` endpoint defaults to `tool_calling`+`native_json`; an unrecognized `base_url`
   (self-hosted/local) keeps today's conservative empty default.
7. Confirm, then read-modify-write `config.toml` via `tomlkit`: add/replace exactly the one
   `[backends.<name>]` table (plus `[defaults]` pointers), leaving every other table untouched.
   Print the resulting default/fallback line and a "try it: `clite \"...\"`" hint.

## 8. Definition of Done

- `config.py`: schema tests cover all six kinds, the new fields, `VALID_EFFORTS` membership, and
  the `api_key_env`-before-`keyring_account` precedence (extend `tests/test_config.py`).
- Each new adapter (`anthropic_compat.py`, `codex_agent.py`, `bedrock.py`, `azure_openai.py`) ships
  with a test file mirroring the existing `test_openai_compat.py`/`test_claude_agent.py` pattern —
  fake HTTP client / fake boto3 client / fake Codex object injected at the constructor, same shape
  as today's `query_fn`/`client` injection points. No live network/AWS/Azure calls in the suite.
- `tiers.py`: extend `tests/test_tiers.py` with a case where the primary backend raises a
  `ProviderError` and the fallback backend is used — proves §6 without needing real credentials.
- The wizard's decision logic (kind → config mapping, model/effort resolution, TOML merge-write)
  is unit-tested with fake I/O in place of `questionary` prompts. The interactive prompt flow
  itself is **not** unit-tested (impractical to script real keypresses against `questionary`) —
  required manual proof: one full run-through of `clite auth` per kind against a real or
  locally-stubbed endpoint, confirming the written config.toml matches what was picked and that
  `clite "<request>"` then works against it.
- `clite auth` with no existing config, and again with one already present (add + replace paths),
  both leave the file valid per `load_config`.

## 9. Build order

| # | Piece | Depends on |
|---|---|---|
| 1 | `ProviderError` base + widen `TierController` fallback trigger | — |
| 2 | Config schema: new `BackendConfig` fields, `VALID_KINDS`/`VALID_EFFORTS`, keyring-based `api_key` resolution | 1 |
| 3 | New adapters: `anthropic_compat.py`, `azure_openai.py` (no new deps) | 2 |
| 4 | New adapters: `codex_agent.py`, `bedrock.py` (optional-extra deps) | 2 |
| 5 | `clite auth` wizard (questionary + tomlkit), wired into `cli.py` | 3, 4 |
| 6 | Manual run-through per kind (DoD §8) | 5 |

## 10. Open items to verify during implementation, not before

- Exact raw-Messages-API field name for effort on `anthropic-compat` (top-level `effort` vs.
  nested under `thinking`) — confirm against current Anthropic API docs when writing
  `anthropic_compat.py`; the model-list `capabilities.effort` field is confirmed, the request-side
  field name to set it is not yet.
- `openai-codex` package/API surface is pre-1.0 and may shift; pin the exact version once
  `codex_agent.py` is written and re-check the `Codex()`/`thread_start()`/`turn()` shapes then.
