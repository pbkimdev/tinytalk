# B2 — Cached-token capture, cache-aware pricing, effort plumbing, warmup

Spec for the bench PRD sub-issue B2. Parent: #90.

## Goal

Make the eval measure everything the benchmark charts need and nothing the product doesn't already
do: cached-read tokens per response, cache-aware cost, `effort=low` actually reaching Sonnet 5 /
GPT-5.5, deterministic sampling, and cold-start-free latency.

## Design

### 1. `Usage` gains cache fields (`tinytalk/provider/base.py`)

```python
@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0        # TOTAL prompt tokens, cached included (OpenAI convention)
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0 # subset of prompt_tokens read from provider cache
    cache_write_tokens: int = 0   # tokens billed at cache-write rate (Anthropic only today)
```

**Normalization convention** (documented on the dataclass): `prompt_tokens` always includes cached
and cache-written tokens; `cached_prompt_tokens` / `cache_write_tokens` are subsets. Adapters that
report exclusive counts must normalize.

Adapter mapping (each in its `_parse_usage`, with fixture-payload unit tests):

| Adapter | cached_prompt_tokens | cache_write_tokens | normalize |
|---|---|---|---|
| `openai_compat` | `usage.prompt_tokens_details.cached_tokens` | — | none (already inclusive) |
| `anthropic_compat` | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` | `prompt_tokens = input_tokens + cache_read + cache_creation` |
| `claude_agent` | same keys off SDK result usage | same | same as anthropic |
| `codex_agent` | `cached_input_tokens` if the SDK reports it, else 0 | — | none |
| `bedrock` / `azure_openai` | provider-equivalent field if present, else 0 | — | per provider |

`TierController._accumulate` (`tinytalk/tiers.py`) sums the two new fields alongside the existing
three.

### 2. Cache-aware pricing (`tinytalk/config.py`, `tinytalk/eval/runner.py`)

```python
@dataclass(frozen=True)
class Price:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_input_per_mtok: float = 0.0   # 0 ⇒ cached tokens billed at input rate
    cache_write_per_mtok: float = 0.0    # 0 ⇒ billed at input rate
```

Cost per prompt becomes:

```
fresh   = prompt_tokens - cached_prompt_tokens - cache_write_tokens
cost    = fresh * input + cached * (cached_input or input)
        + cache_write * (cache_write or input) + completion * output      # all /1e6
```

`[prices.<model>]` TOML tables accept the two new optional keys. `PromptResult` gains
`cached_prompt_tokens` / `cache_write_tokens`; JSON/CSV export picks them up automatically via
`asdict`/`__dataclass_fields__`.

### 3. Effort plumbing

`CompletionRequest.reasoning_effort` already exists. This issue guarantees the path end to end:

- `BackendConfig.effort` → request: verify the engine sets `reasoning_effort` from config (add if
  missing).
- `openai_compat`: send `reasoning_effort` on chat completions when set.
- `anthropic_compat` / `claude_agent`: map to the provider's effort control (`effort` request field
  on the Messages API; SDK option on the agent SDK). Unit-test the serialized request body/options.
- Adapters for providers with no effort control ignore it silently (current behavior, kept).

### 4. Runner protocol (`tinytalk/eval/runner.py`)

- **Warmup**: before scoring, `_run_backend` issues one throwaway request (fixed prompt
  `"echo warmup"`-class, not from the suite), untimed, un-scored, errors logged but non-fatal.
  Removes model-load/cold-start from the first scored latency. Flag: `run_eval(..., warmup=True)`.
- **Determinism**: the runner pins `temperature=0.0` on its tier requests (plumb through
  `TierRequest` → engine → `CompletionRequest.temperature`, which already exists).
- Latency, as today, is per-prompt wall clock around `controller.suggest` — end-to-end including
  grounding/validation, which is what a user feels.

## Out of scope
- Adding `cache_control`/prompt-caching behavior to the product — we *capture*, never *induce*.
- Suite changes (B1), HTML report (B3), bench configs/pricing values (B4).

## Done when
- **unit**: per-adapter `_parse_usage` fixtures incl. the Anthropic normalization; cost math with
  and without cache rates; effort appears in serialized openai/anthropic request fixtures;
  warmup issues exactly one extra provider call and contributes zero rows/tokens to the report.
- **integration (manual)**: one live `tt eval --backends sonnet5-low --prompts disk-usage-top`
  shows nonzero `cached_prompt_tokens` on the second prompt (Anthropic auto/explicit cache) or a
  clean zero with cost falling back to input rate.
