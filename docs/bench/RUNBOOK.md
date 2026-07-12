# TinyTalk benchmark runbook

Use this runbook to create a new `docs/bench/<YYYY-MM-DD>/` field run without touching personal
TinyTalk settings. The suite and scoring model are explained in [SUITE-V4.md](SUITE-V4.md) and
[AUTOMATION.md](AUTOMATION.md).

## Rules before the run

- Open a GitHub issue and post the run plan before generating data.
- Use `TT_CONFIG=docs/bench/bench.toml`; never edit or rely on personal config.
- Smoke one target before a full sweep.
- Run one local-GPU backend at a time.
- Keep every raw export, including failures.
- Never combine rows produced by different suite/scorer commits without recording that fact.
- Do not call a report reproducible unless the commit, machine, model/runtime, and run method are
  captured.

Set the run directory once:

```sh
cd <tinytalk-repo>
RUN_DATE=$(date +%F)
RUN_DIR="docs/bench/$RUN_DATE"
mkdir -p "$RUN_DIR"
```

## 1. Choose and record the roster

`docs/bench/bench.toml` contains historical and current backend definitions. An official run should
name its exact subset rather than silently running every table.

Example v4 roster:

```sh
BACKENDS='sonnet5-low local-gemma4-26b local-gemma4-12b-8bit local-gemma4-12b-qat'
```

Before generation, create `run_meta.json` in the run directory. Keep methodology prose factual and
specific:

```json
{
  "run_date": "YYYY-MM-DD",
  "machine": "OS, CPU/GPU, RAM",
  "pricing_notes": [
    "Suite v4: 25 targets, natural EN/KO pair per target, 50 prompts total.",
    "Repository commit: <sha>. Each backend ran separately at temperature 0.",
    "Local runtime and model/quantization details: <exact values>.",
    "Hosted backend, SDK/CLI, model, effort, and pricing source: <exact values>."
  ]
}
```

If a local server uses a draft model or speculative decoding, record it. If an Agent SDK injects
context or adds startup latency, record it. Prices are captured observations, not timeless config;
refresh them from primary provider sources before publishing a cost comparison.

## 2. Preflight each backend

### Local OpenAI-compatible servers

Check the exact endpoint declared in `bench.toml`:

```sh
curl -fsS http://localhost:3333/v1/models | python -m json.tool
curl -fsS http://localhost:18080/v1/models | python -m json.tool  # only when used
```

Confirm the intended model IDs are present. Do not start a second server on the same GPU unless the
run is explicitly testing multi-server contention.

### Claude Agent SDK

```sh
claude
TT_CONFIG=docs/bench/bench.toml uv run tt eval \
  --backends sonnet5-low --prompts count-lines-code
```

The CLI login must work in the same user environment as the run.

### Codex Agent SDK

```sh
codex login status
uv sync --extra codex
TT_CONFIG=docs/bench/bench.toml uv run tt eval \
  --backends gpt55-low --prompts count-lines-code
```

### API-backed endpoints

Set required environment variables only in the run environment. Never write secrets into
`bench.toml` or a committed artifact. Run a single-target smoke before the full suite.

## 3. Smoke the harness

`--prompts <target>` selects both language variants for that target. Smoke every backend separately:

```sh
for backend in $BACKENDS; do
  TT_CONFIG=docs/bench/bench.toml uv run tt eval \
    --backends "$backend" \
    --prompts count-lines-code \
    --export "$RUN_DIR/smoke-$backend.json"
done
```

Inspect the command, error, attempts, usage, model, and prompt ID in each JSON file. A process exit of
zero does not prove that every prompt passed.

Remove smoke exports from the publish glob or keep them under a `smoke/` subdirectory. Do not publish
them as full backend reports.

## 4. Run the full suite

The default method is serial by backend. It keeps local GPU contention and latency interpretation
simple:

```sh
for backend in $BACKENDS; do
  TT_CONFIG=docs/bench/bench.toml uv run tt eval \
    --backends "$backend" \
    --export "$RUN_DIR/$backend.json"
done
```

Each full export should contain one backend and 50 rows for suite v4. Check before publishing:

```sh
for file in "$RUN_DIR"/*.json; do
  jq -r 'if type == "array" and length == 1 then
    "\(input_filename): \(.[0].backend) \(.[0].results | length) rows"
  else empty end' "$file"
done
```

For a focused data experiment, use `--prompts` and label the export as a subset. Never pass it to the
full-report publisher as though it contained the suite.

## 5. Publish recorded exports

For a fresh full-suite run, use the no-recompute export path and name each backend file explicitly:

```sh
TT_CONFIG=docs/bench/bench.toml uv run tt eval publish "$RUN_DIR" --exports \
  "$RUN_DIR/sonnet5-low.json" \
  "$RUN_DIR/local-gemma4-26b.json" \
  "$RUN_DIR/local-gemma4-12b-8bit.json" \
  "$RUN_DIR/local-gemma4-12b-qat.json"
```

This preserves recorded rows, including `oracle_pass`, writes `results.json`, and renders
`index.html`. Adjust the explicit list to the run's roster.

The publisher's legacy merge mode (`tt eval publish --run-date ...` without `--exports`) exists for
older v3 kept/new export layouts. Do not use it for a new full v4 run.

To rebuild a generic HTML report directly from an existing consolidated JSON:

```sh
TT_CONFIG=docs/bench/bench.toml uv run tt eval \
  --report-from "$RUN_DIR/results.json" \
  --report "$RUN_DIR/index.html"
```

## 6. Analyze and render the dashboard

Both commands below use recorded data and make no model calls:

```sh
uv run tt eval analyze "$RUN_DIR"
uv run tt eval dashboard "$RUN_DIR"
```

If repeated exports live somewhere other than `$RUN_DIR/stability/*.json`, pass `--runs GLOB`.
Stability analysis requires at least three matching repeats by default.

Read the outputs before publishing:

- delivery faults should name concrete response failures;
- category and EN/KO slices should have the expected denominator;
- `flip_rate` must never exceed `command_flip_rate`;
- oracle coverage must exclude unsupported network/remote/cluster targets rather than count them as
  failures;
- the HTML report and dashboard must state the host and methodology caveats.

## 7. Validate artifacts

Run the relevant test and hygiene gates:

```sh
uv run pytest tests/test_eval.py tests/test_eval_analysis.py tests/test_eval_dashboard.py \
  tests/test_eval_publish.py tests/test_oracle.py
uv run ruff check tinytalk/eval tests
git diff --check
```

Then verify the directory itself:

```sh
test -s "$RUN_DIR/results.json"
test -s "$RUN_DIR/index.html"
test -s "$RUN_DIR/analysis.json"
test -s "$RUN_DIR/dashboard.html"
jq empty "$RUN_DIR"/*.json
```

Open both HTML files locally and inspect narrow and wide layouts. Do not publish a report solely from
passing JSON/schema checks.

## 8. Write the run log

Record the run in this document only after artifacts are committed. Include:

- date and suite version;
- roster and exact model IDs;
- strict and oracle scores with denominators;
- machine/runtime and request protocol;
- whether rows were fresh, reused, or re-scored;
- stability sample count when cited;
- major harness defects found and how recorded data was corrected;
- links to the committed report and analysis.

## Committed runs

### 2026-07-05 — suite v4

- **Shape:** 25 targets × natural EN/KO = 50 prompts.
- **Roster:** Claude Sonnet 5 low effort through the Claude Agent SDK; local Gemma 4 26B A4B
  MLX-8bit, 12B 8-bit, and 12B QAT 4-bit through oMLX.
- **Machine:** Apple M5 Max, 128 GB unified memory.
- **Strict pass:** Sonnet 92%; 26B 68%; 12B 8-bit 68%; 12B QAT 58%.
- **Execution oracle:** Sonnet 81% of 36 covered results; 26B 46% of 35; 12B 8-bit 56% of 32;
  12B QAT 44% of 32. Coverage denominators differ because missing/unusable model responses have no
  command to execute.
- **Finding:** text-only scoring materially overstated executable correctness, especially for local
  models. The oracle exposed userland, quoting, and state-result failures that shape assertions
  accepted.
- **Artifacts:** [report](2026-07-05/index.html), [dashboard](2026-07-05/dashboard.html),
  [`results.json`](2026-07-05/results.json), and [`analysis.json`](2026-07-05/analysis.json).

### 2026-07-03 — suite v3

This run is historical and not directly comparable with v4. It used a partly merged methodology: 15
recorded targets were re-scored under a fixed extractor and 10 hard targets were generated fresh.
The artifacts remain useful for scorer history and per-command inspection, not as the current product
headline. See the [v3 report](2026-07-03/index.html).
