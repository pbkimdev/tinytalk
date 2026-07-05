# tt bench — runbook

How the published run under `docs/bench/<YYYY-MM-DD>/` was produced, and how to reproduce it.
Linked from [README.md](../../README.md) and [README.ko.md](../../README.ko.md).
Config: [`bench.toml`](./bench.toml) (used via `TT_CONFIG`; personal config untouched).
Parent: #90 · spec: `docs/specs/bench-04-run.md` · sub-issue: #101.

Set the run date once per sweep — folder names are the ISO date (`YYYY-MM-DD`), not year-month:

```sh
RUN_DATE=2026-07-03
```

## Roster

| Backend | Model | Runtime | Notes |
|---|---|---|---|
| `local-gemma4-26b` | gemma-4-26B-A4B-it-MLX-8bit | oMLX, managed server on :3333 | MoE 25.2B total / ~3.8B active; no MTP drafter in the default roster |
| `local-gemma4-12b-8bit` | gemma-4-12B-it-8bit | oMLX, managed server on :3333 | 12B 8-bit with matching assistant drafter downloaded (`gemma-4-12B-it-assistant-8bit`); record MTP-on/off provenance when used |
| `local-gemma4-e4b` | lmstudio-community--gemma-4-E4B-it-MLX-4bit | oMLX, managed server on :3333 | on-device class, 4.5B effective |
| `local-qwen36-35b` | mtplx-qwen36-35b-a3b-optimized-balance | `mtplx quickstart --model Youssofal/Qwen3.6-35B-A3B-MTPLX-Optimized-Balance --port 18080 --yes` | MTP speculative decoding. Roster note: the PRD named Qwen 3.6 **27B**; the mtplx-installed build is the **35B-A3B** MoE — swapped by decision on #90. |
| `sonnet5-low` | claude-sonnet-5 | Claude Agent SDK (`effort = "low"`) | Auth = local Claude Code login, **no API key**. Latency includes SDK/CLI startup per request — footnoted in the report. |
| `gpt55-low` | gpt-5.5 | OpenAI Codex SDK (`effort = "low"`) | Auth = local Codex CLI ChatGPT login, **no API key** (the env `OPENAI_API_KEY` 401'd). Latency includes SDK/CLI startup — footnoted like Sonnet. Needs `uv sync --extra codex`. |

Machine: Apple M5 Max, 128 GB unified memory. Protocol: single backend per run (no
cross-backend escalation; same-backend T2 retry is as-shipped behavior), temperature 0,
one scored run per prompt, one discarded warmup request per backend.

## Pricing quotes (captured 2026-07-03)

| Model | in / out $ per MTok | cache | Source |
|---|---|---|---|
| Sonnet 5 | 2.00 / 10.00 | read 0.20, 5m-write 2.50 | Anthropic pricing docs — intro pricing through 2026-08-31; its tokenizer counts ~30% more tokens for the same text |
| GPT-5.5 | 5.00 / 30.00 | cached input 0.50 | OpenAI pricing docs |
| Gemma 4 26B A4B | 0.06 / 0.33 | — | openrouter.ai/google/gemma-4-26b-a4b-it (proxy†) |
| Gemma 4 E4B | 0.20 / 0.20 | — | Fireworks quote via pricepertoken.com — E4B is not hosted on OpenRouter (proxy†) |
| Qwen 3.6 35B A3B | 0.14 / 1.00 | — | openrouter.ai/qwen/qwen3.6-35b-a3b (proxy†) |

† Local models run at $0 marginal cost on this machine; hosted quotes put all five models on
one comparable cost axis. The report's fine print repeats this.

## Preflight

1. `curl -s localhost:3333/v1/models` lists `gemma-4-26B-A4B-it-MLX-8bit`,
   `gemma-4-12B-it-8bit`, `gemma-4-12B-it-assistant-8bit`, and
   `lmstudio-community--gemma-4-E4B-it-MLX-4bit` (managed oMLX).
2. Do not start a second oMLX server on `3334`; the bench roster uses the managed `3333` server.
3. `mtplx quickstart --model Youssofal/Qwen3.6-35B-A3B-MTPLX-Optimized-Balance --port 18080 --yes`;
   `curl -s localhost:18080/v1/models` answers.
4. `claude` CLI logged in (Sonnet rides it); `codex login status` says logged in (GPT-5.5 rides
   it); `uv sync --extra codex` done.
5. Suite binaries present (the validator checks anyway): standard macOS + git, curl, tar…

## The sweep

One backend at a time — local models share the GPU, so never two sweeps concurrently:

```sh
cd <repo>
RUN_DATE=2026-07-03
mkdir -p "docs/bench/$RUN_DATE"
for b in local-gemma4-26b local-gemma4-12b-8bit local-gemma4-e4b local-qwen36-35b sonnet5-low gpt55-low; do
  TT_CONFIG=docs/bench/bench.toml uv run tt eval --backends "$b" \
    --export "docs/bench/$RUN_DATE/$b.json"
done
```

Smoke first (`--prompts disk-usage-top` = 2 prompts) before committing to a full 60.

## Merge + render

The published page is built by `tt eval publish` (from the repo root):

```sh
RUN_DATE=2026-07-03
uv run tt eval publish --run-date "$RUN_DATE"
# equivalent: uv run tt eval publish "docs/bench/$RUN_DATE"
```

(`python -m tinytalk.eval.publish` is equivalent.) It re-scores the 15 kept targets from their
recorded commands under the fixed command extractor, merges the fresh v3 sweeps (`v3new-*.json`),
writes `results.json`, and renders `index.html`. Report metadata (machine string, pricing
footnotes) comes from `run_meta.json` in the run directory when present.

To re-render HTML from an existing `results.json` without re-merging:

```sh
RUN_DATE=2026-07-03
uv run tt eval --report-from "docs/bench/$RUN_DATE/results.json" \
  --report "docs/bench/$RUN_DATE/index.html"
```

## Run log

- 2026-07-03 (`docs/bench/2026-07-03/`, suite v3, 50 prompts) — 25 targets after retiring the 15 the whole
  field saturated (#95) and adding 10 hard ones (TLS cert expiry, dig +trace, tar|ssh
  streaming, kubectl restart counts, jq, IPv4-regex extraction, INI range addressing, awk
  group-sums, process substitution, xargs -P). The 15 kept targets are re-scored from the
  recorded 60-prompt-run commands under the fixed command extractor (#95: find -exec descent,
  xargs flag args, process substitution — previously these idioms were falsely rejected);
  the 10 new targets ran fresh the same day (~12 min). Strict pass: **Sonnet 5 98 (EN 100 /
  KO 96) · GPT-5.5 98 (96/100) · Qwen 3.6 35B-A3B 96 (96/96) · 26B A4B 94 (96/92) ·
  E4B 80 (80/80)**. p50 latency: 26B 1.6s, Sonnet 5.3s, E4B 8.0s, Qwen 8.8s, GPT-5.5 11.3s.
  Sweep cost (list-rate): locals $0.010–0.061 (proxy†), Sonnet $1.17, GPT-5.5 $4.21.
  Findings: the hard set inverted the v2 story — E4B, which "tied Sonnet" on the saturated
  suite, drops to 80 (missed k8s-restart-count, ini-section, and one language on four more);
  Qwen swept all 10 hard targets in both languages; frontier models sit on top, exactly the
  spread v2 failed to show. parallel-compress is the hardest overall (three models dropped a
  language). Earlier same-day runs (50-prompt v2, 60-prompt v2+rigor) are superseded; their
  raw exports remain committed as publish inputs. Standing caveats: MTPLX flips a few
  prompts between temperature-0 runs (± a few points); MTPLX reports no cached tokens; rows
  can carry an `error` yet strict-pass (strict pass re-scores the returned command on
  parse/binaries/assertions only).
