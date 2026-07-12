# Suite v4 contract

Suite v4 is TinyTalk's current golden command-generation suite. The source of truth is
`tinytalk/eval/suite.py`; this document explains the design so a reviewer can judge changes without
reverse-engineering the tuple data.

## Shape

- 25 targets.
- One natural English prompt and one natural Korean prompt per target.
- 50 prompts total.
- Both languages share one assertion set and expected danger level.
- Each backend runs at temperature 0 in the standard eval path.

The paired design makes the EN/KO delta meaningful. Korean prompts should describe the same task
naturally, not mirror English word order or leak assertion keywords.

## What v4 measures

The suite keeps a few anchors and emphasizes tasks that separate plausible shell text from reliable
shell behavior:

- multi-stage pipelines;
- structured JSON, JSONL, CSV, YAML, and INI parsing;
- quoting, whitespace, and filename edge cases;
- regular expressions and escape-heavy commands;
- process substitution, parallel work, joins, and aggregation;
- Git and Kubernetes inspection;
- dry-run and overwrite policy;
- side effects whose filesystem result can be checked safely.

The target list is:

```text
count-lines-code          delete-node-modules       log-top-errors
csv-columns-transform     loop-backup-copies         cert-expiry
dns-trace                 ssh-stream-copy            k8s-restart-count
json-extract              extract-ips                ini-section
awk-group-sum             diff-sorted                parallel-compress
jsonl-p95-routes          csv-quoted-join            yaml-image-policy
env-missing-keys          git-conflict-markers       rsync-delete-dry-run
k8s-old-tls-secrets       log-window-uniq-ip         find-hardlink-groups
tar-safe-extract-logs
```

## Assertion DSL

Assertions are deterministic `kind:value` strings:

| Kind | Meaning |
|---|---|
| `uses:tool` | `tool` appears in command position. |
| `uses_any:a\|b` | At least one listed tool appears in command position. |
| `pipes_to:tool` | `tool` appears in a command position after the first pipeline stage. |
| `contains:text` | The literal text is present. |
| `not_contains:text` | The literal text is absent. |
| `regex:pattern` | Python `re.search` matches the command. |

The DSL grades command shape, not shell output. An assertion should encode the target's intent while
accepting materially equivalent idioms. For example, Python-file selection may be expressed by a
`*.py` glob, `fd -e py`, or `rg --type py`; an assertion that accepts only one spelling is a grader
bug.

When a correct alternative is rejected:

1. add the alternative to a focused scorer test;
2. broaden the assertion without admitting a known wrong answer;
3. re-score recorded commands where the publication path supports it;
4. document any leaderboard correction.

Do not tune an assertion solely to make a preferred model pass.

## Strict pass

A prompt earns strict pass only when:

- its structured response is valid;
- the command parses;
- command-position binaries exist on the scoring host;
- every target assertion passes.

Danger accuracy is reported separately. Strict pass does not mean the command was executed, and it
does not prove the requested effect occurred.

## Execution-oracle coverage

Eighteen targets have fixture-backed behavioral grading in `tinytalk/eval/oracle.py`:

```text
jsonl-p95-routes          csv-quoted-join            env-missing-keys
find-hardlink-groups      count-lines-code           extract-ips
ini-section               awk-group-sum              diff-sorted
csv-columns-transform     json-extract               log-window-uniq-ip
yaml-image-policy         k8s-old-tls-secrets        delete-node-modules
loop-backup-copies        parallel-compress          tar-safe-extract-logs
```

Most compare normalized stdout. Four side-effecting targets compare resulting filesystem state:
`delete-node-modules`, `loop-backup-copies`, `parallel-compress`, and `tar-safe-extract-logs`.

Network, remote-host, and live-cluster tasks remain oracle-uncovered. Do not fake those dependencies
or count `null` oracle results as failures.

## Danger anchors

Every target declares `safe`, `caution`, or `destructive`. The suite intentionally includes all three:

- inspection and pure transformation are usually `safe`;
- copies, archives, remote transfers, and dry-run mutation previews may be `caution`;
- deleting `node_modules` is the explicit `destructive` anchor.

Expected danger should follow the product validator's policy, not intuition in isolation. A target
change that alters danger must update validator tests or explain why the suite intentionally probes a
disagreement.

## Adding or replacing a target

A candidate belongs in v4 only when it has:

1. a realistic developer or operator task;
2. one crisp requested outcome;
3. natural, independently written EN and KO prompts;
4. assertions that accept common correct implementations and reject known wrong ones;
5. the correct danger level;
6. deterministic fixtures when safe local execution can judge it;
7. evidence that it adds signal rather than noise.

Before replacing a target, measure the current field. A target that every backend passes may be a
useful easy anchor, but too many saturated anchors compress the leaderboard. A target that every
backend fails may be impossible, under-specified, or poorly graded rather than discriminating.

For a new oracle case, add correct and adversarial commands. Verify timeout, cleanup, containment,
allowed return codes, and platform assumptions. The command may run only inside the disposable eval
fixture.

## Stability gate

Run candidate prompts at least three times on a deterministic local backend and analyze them with:

```sh
uv run tt eval analyze <run-dir> --runs '<candidate-runs>/*.json'
```

Inspect both `command_flip_rate` and verdict `flip_rate`. Command wording may vary while the verdict
stays stable; a verdict that flips repeatedly adds measurement noise. Hosted model variation should
be reported as a distribution rather than “fixed” by weakening assertions after each run.

## Required tests

Suite changes should keep these invariants green:

- exactly 25 targets and 50 prompt rows;
- every target has one EN and one KO prompt;
- prompt IDs are unique and ordered predictably;
- every assertion kind parses;
- analysis category mappings cover every target;
- every oracle case has a matching suite target;
- correct and adversarial oracle fixtures behave as expected.

Run the focused suite, analysis, and oracle tests listed in [RUNBOOK.md](RUNBOOK.md), then perform a
full field calibration before publishing new headline numbers.

## Version boundary

Suite v3 and v4 scores are not directly comparable. V4 replaced ten saturated targets with harder
structured and composition-heavy work, and added broader execution-oracle coverage. When citing a
score, always name the suite version, date, backend/model, scorer commit, and whether the number is
strict or oracle-based.
