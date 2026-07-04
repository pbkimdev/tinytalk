# `tt history` — fresh-session start

Everything to build the TinyTalk command-history feature is scaffolded. Open a fresh session
(ultracode on) and use the prompt at the bottom.

## Read first (binding context)
- Project memory auto-loads the locked decisions (`history-feature-plan`).
- [`DECISIONS.md`](./DECISIONS.md) — the binding decisions (single source of truth).
- [`tasks.json`](./tasks.json) — the task DAG (PRD → requirement/scope → specs; file-disjoint waves).
- Workflows: `.claude/workflows/history-author.js`, `.claude/workflows/history-build.js`.

## Recommended sequence
1. **Plan-first** (repo-idiomatic): `Workflow({ name: 'history-author' })` → review the PRD /
   requirement / spec docs it writes under `docs/agents/history/`; edit anything off.
2. **Foundation, with a review gate:** `Workflow({ name: 'history-build', args: { only: ['A'] } })`
   → review the working-tree diff → on a feature branch, commit **per spec** (A1, A2, A3 — files per
   `tasks.json`) → open a PR for human review.
3. **Consumers** (parallelize; all depend on A being merged):
   `Workflow({ name: 'history-build', args: { only: ['C', 'D', 'B'] } })` → review → commit per spec →
   PR. *Or* run the whole parallel DAG at once: `Workflow({ name: 'history-build' })`.
4. Verify D empirically via `.claude/skills/test-shell-ui`; final `uv run python -m pytest`.

## What the workflows already do
implement → adversarial verify (correctness / decision-conformance / test-adequacy) → fix →
per-spec test gate → integration gate. **You still own:** branching, per-spec commits, the PR, and
human review (`main` is behind branch protection).

## Conventions (non-negotiable)
- Python via `uv`; tests `uv run python -m pytest`.
- One spec = one self-contained commit; squash-merge the PR.
- Never let `tt` auto-run generated shell commands.

## Paste-in starting prompt
> Build the TinyTalk `tt history` feature. First Read `docs/agents/history/DECISIONS.md` and
> `docs/agents/history/tasks.json` and treat the decisions as fixed. Then run the `history-author`
> workflow and let me review the drafted PRD/specs. After I approve, run `history-build` with
> `args.only=["A"]`, show me the diff, and commit per spec on a feature branch. Do not execute shell
> commands `tt` generates, and don't merge — open a PR for review.
