# B4 — Bench configs, pricing quotes, the sweep, and publishing

Spec for the bench PRD sub-issue B4. Parent: #90. Depends on B1–B3.

## Goal

Pin the five backends and their prices in a reproducible bench config, run the full 5 × 50 sweep on
this machine, and publish the results (JSON + HTML) under `docs/bench/`.

## Roster & backend config

A dedicated bench config (`docs/bench/bench.toml`, used via `TT_CONFIG`) — the personal
`~/.config/tinytalk/config.toml` is never touched:

| Backend name | kind | model | endpoint / notes |
|---|---|---|---|
| `local-gemma4-26b` | openai-compat | `gemma-4-26B-A4B-it-MLX-8bit` | oMLX `http://localhost:3333/v1` (already loaded) |
| `local-gemma4-e4b` | openai-compat | `gemma-4-E4B-it-MLX-4bit` | oMLX `http://localhost:3333/v1` (weights in HF cache; load via oMLX) |
| `local-qwen36-27b` | openai-compat | Qwen3.6-27B oQ8 MTP | MTPLX `mtplx quickstart --port 18080` → `http://localhost:18080/v1` |
| `sonnet5-low` | anthropic-compat | `claude-sonnet-5` | `effort = "low"`, key from env/keyring |
| `gpt55-low` | openai-compat | `gpt-5.5` | `reasoning_effort = "low"` via `effort = "low"`, api.openai.com |

Local backends: `capabilities` left empty (universal TEXT rung) unless a smoke request shows the
server handles `native_json` reliably — decided once during setup, recorded in the runbook.

## Pricing table (`[prices]` in bench.toml)

| Model | in $/MTok | out $/MTok | cached-in $/MTok | basis |
|---|---|---|---|---|
| Sonnet 5 | 2.00 | 10.00 | 0.20 | Anthropic list (intro thru 2026-08-31); tokenizer counts ~30% more tokens — noted in report fine print |
| GPT-5.5 | 5.00 | 30.00 | 0.50 | OpenAI list |
| Gemma 4 26B A4B | 0.06 | 0.33 | — | OpenRouter quote (proxy†) |
| Gemma 4 E4B | (quote at run time) | (quote) | — | OpenRouter quote (proxy†) |
| Qwen 3.6 27B | (quote at run time) | (quote) | — | OpenRouter/cheapest-host quote (proxy†) |

† Local models run at $0 marginal cost on this machine; hosted quotes put them on the same axis.
Quotes are captured (value + URL + date) in the runbook the day of the run.

## Runbook (`docs/bench/RUNBOOK.md`)

1. Preflight: `omlx` serving 26B (`curl :3333/v1/models`), E4B loadable; `mtplx quickstart` up;
   `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` resolvable; `zsh`, suite binaries present.
2. Per-backend smoke: 1 prompt (`--prompts disk-usage-top`) before committing to the sweep.
3. The sweep, one backend at a time (local models are memory-heavy — never two MLX models loaded
   at once; order: 26B → swap → E4B → mtplx → cloud):
   `TT_CONFIG=docs/bench/bench.toml uv run tt eval --backends <name> --export docs/bench/<date>/<name>.json`
4. Merge + render: `tt eval --report-from docs/bench/<date>/results.json --report docs/bench/<date>/index.html`
   (merge step = concatenate backend JSONs; add a tiny `--merge` helper only if genuinely needed).
5. Commit JSON + HTML together; link from README ("Benchmarks" section, one line + screenshot-free).

## Published layout

```
docs/bench/
  bench.toml          # pinned backends + prices (no secrets — keys stay in env/keyring)
  RUNBOOK.md          # steps above + captured quotes + run log
  2026-07/            # one dir per run date
    results.json
    index.html
```

## Out of scope
- Automating oMLX model swaps or mtplx lifecycle from Python — the runbook does it by hand.
- CI/scheduled reruns; publishing beyond the repo.

## Done when
- **eval**: all 5 backends complete 50/50 prompts with zero harness (non-model) errors; results
  committed under `docs/bench/2026-07/`.
- **manual**: report sanity — every model has 50 rows, latency medians plausible (local ≫ warmup
  effect absent, cloud sub-~5s), cloud backends show cached-token counts if the APIs reported them.
- README links to the report.
