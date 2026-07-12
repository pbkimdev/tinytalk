# TinyTalk benchmark architecture

This document explains what the eval harness guarantees today, where reproducibility stops, and how
recorded runs become reports and analysis. Use [RUNBOOK.md](RUNBOOK.md) for commands and
[SUITE-V4.md](SUITE-V4.md) for the suite contract.

## The short version

An eval has two different jobs:

```text
request ──► model generation ──► recorded command ──► deterministic scoring
                    noisy                              repeatable
```

Generation depends on a model, runtime, login, host, and provider behavior. Even temperature 0 is not
a promise of identical text. Scoring should be repeatable from the recorded command.

TinyTalk therefore keeps model outputs and usage in JSON exports, supports re-rendering without new
model calls, and provides read-only analysis over those exports. The current harness does not yet
separate generation and scoring into independent top-level artifacts, so provenance must still be
checked before comparing runs.

## What exists today

| Capability | Command or module | Model calls? |
|---|---|---:|
| Run the suite | `tt eval` · `tinytalk/eval/runner.py` | Yes |
| Export JSON/CSV | `tt eval --export ...` | Yes |
| Render a saved export | `tt eval --report-from ... --report ...` | No |
| Publish a run directory | `tt eval publish` · `tinytalk/eval/publish.py` | No |
| Analyze failures and stability | `tt eval analyze` · `tinytalk/eval/analyze.py` | No |
| Build the analysis dashboard | `tt eval dashboard` · `tinytalk/eval/dashboard.py` | No |
| Execute fixture-backed commands | `tinytalk/eval/oracle.py` | No model call; isolated command execution |
| Compare Atuin AI | `tinytalk/eval/atuin.py` | Atuin capture only; TinyTalk rows are re-used |

`tt eval` records the prompt, command, provider attempts, usage, cost, latency, validation fields, and
oracle verdict when available. Keep the raw per-backend JSON files: they are the evidence from which
reports can be rebuilt.

## The two graders

### Strict pass

A row is a strict pass only when all of these hold:

1. the provider response satisfies TinyTalk's structured-output contract;
2. the command parses;
3. every command-position binary exists on the scoring host;
4. every deterministic assertion for the target passes.

Assertions describe command shape: tools used, required text, pipe structure, or a regular-expression
match. They are fast and explainable, but they approximate intent. A correct command expressed through
an unanticipated idiom can expose an assertion bug.

Strict pass does **not** execute the generated command.

### Execution oracle

The execution oracle answers the harder question: did the recorded command produce the required
behavior? It creates an isolated temporary fixture, runs the command there, and checks either stdout
or resulting filesystem state.

Suite v4 has oracle fixtures for 18 of 25 targets. Network, remote-host, and Kubernetes targets remain
text-only because a hermetic local fixture cannot honestly reproduce them.

`oracle_pass` is nullable and independent. Never fold it into strict pass or silently treat an
uncovered target as an oracle failure.

## Why the scores differ

The 2026-07-05 v4 run made the distinction visible:

| Backend | Strict pass | Oracle pass on covered results |
|---|---:|---:|
| Claude Sonnet 5, low effort | 92% | 81% |
| Gemma 4 26B A4B, local | 68% | 46% |
| Gemma 4 12B 8-bit, local | 68% | 56% |
| Gemma 4 12B QAT 4-bit, local | 58% | 44% |

A command can satisfy a text assertion and still use GNU-only behavior on macOS, mishandle quoting,
or produce the wrong file state. The oracle gap is not a reporting nuisance; it is the reason the
execution grader exists.

## Reproducibility boundaries

### Generation is variable

Record at least:

- repository commit;
- suite version and prompt subset;
- backend alias, model ID, effort, and endpoint;
- runtime or Agent SDK/CLI version when available;
- local model quantization and speculative-decoding setup;
- machine and OS;
- temperature and any other request options;
- whether the run was fresh, resumed, or merged from prior exports.

Hosted models can change behind a stable name. Agent SDKs may add their own context and startup cost.
Local speculative decoding can alter output. Repeat important comparisons and report spread instead
of presenting one run as a permanent property of a model.

### Scoring is deterministic only with fixed inputs

The assertion DSL is deterministic, but two host inputs can still move a verdict:

- binary discovery reads the scoring machine's `PATH`;
- parse and execution behavior depend on the host shell and userland;
- oracle fixtures intentionally expose platform differences;
- scorer and extractor code evolve.

Compare two runs only when the suite and scorer commit match and the host difference is understood.
If they do not match, describe the comparison as directional.

## Analysis artifacts

`tt eval analyze` writes `analysis.json` with four views:

- **delivery** — usable structured responses and failure taxonomy;
- **layers** — delivered → parses/runs → intent, with first-failing stage;
- **slices** — language, category, and selected hypothesis groups;
- **stability** — command and verdict flips across repeated exports.

`tt eval dashboard` combines recorded results, analysis, and stability exports into a standalone HTML
dashboard. Both commands are read-only with respect to model providers.

Treat analysis as diagnosis, not a leaderboard decoration. A malformed JSON envelope calls for parser
or model-output work; a correct alternative rejected by an assertion calls for scorer work; a command
that passes text checks but fails a fixture is a product-quality signal.

## Parallelism

Local backends on one GPU must run serially. Cloud or Agent SDK backends do not share that GPU, but
running them concurrently changes observed latency and can trigger provider rate limits. The current
runbook chooses one backend at a time for interpretable timing.

Future scheduling should model resources explicitly:

| Resource class | Safe policy |
|---|---|
| One local GPU | One backend at a time |
| Independent remote endpoint | Separate bounded worker per endpoint |
| Hosted provider | Bounded concurrency with 429 backoff |
| Latency measurement | Dedicated single-flight pass |

Score throughput and latency fidelity are different experiments. Do not speed up a score sweep and
then compare its latency column with a serialized run.

## Artifact contract

A publishable directory under `docs/bench/<YYYY-MM-DD>/` should retain:

```text
<backend>.json          raw per-backend exports
results.json            consolidated report input
run_meta.json           machine and methodology notes
analysis.json           read-only analysis output
index.html              published benchmark report
dashboard.html          analysis dashboard
stability/*.json        optional repeated exports
oracle/*.json           optional oracle-focused exports
```

Do not hand-edit measured JSON to improve a chart. Fix the scorer, re-score recorded commands when the
path supports it, and describe the correction in `run_meta.json` or the issue that owns the run.

## Safe execution boundary

The product and the eval harness have different execution policies:

- The **product** never executes a generated command. It returns a validated command to the user.
- The **execution oracle** may run a recorded command only inside its disposable fixture sandbox.
- A network, host, or cluster command without a safe fixture remains unexecuted and oracle-uncovered.

Any new oracle target must prove containment, fixture cleanup, timeout behavior, and platform
expectations in tests before it can join the suite.

## Remaining automation work

The following ideas are useful but are not implemented as top-level `tt bench` commands:

1. capture a frozen grounding snapshot with every run;
2. split generation and scoring into separately addressable phases;
3. stamp a suite/scorer hash into the run manifest;
4. add a provenance-gated cross-run diff;
5. schedule independent resource classes while preserving a separate latency pass;
6. resume partially completed generations without overwriting recorded rows.

Until those land, the runbook and committed artifacts are the operational contract. Do not document
proposed `tt bench` verbs as if users can run them today.
