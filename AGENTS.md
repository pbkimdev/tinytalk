# AGENTS.md — CLITE

CLITE turns plain English at the shell into a real, validated command. New here? Read
[README.md](./README.md); the intent lives in [VISION.md](./VISION.md) and the full spec in
[PRD.md](./docs/agents/PRD.md).

## How we work

**Every piece of work is a [GitHub issue](https://github.com/paulbkim-dev/clite/issues).** Some issues
stand alone; others break into sub-issues. Either is fine — split only when it helps.

For each issue:

1. **Plan it first.** Before touching code, write a short plan on the issue: what changes, in what
   order, and how we'll know it works. If the issue has sub-issues, the plan is how they line up.
2. **Resolve it.** Work through the issue (and its sub-issues) until the issue's "done when…" holds.
3. **One sub-issue → one commit.** Each sub-issue lands as a single, self-contained commit; an issue
   with no sub-issues is itself one commit. Keep history clean by squashing — do the work on a branch
   and **squash-merge the PR** so each sub-issue collapses to one commit on `main`.
4. **Review by comment.** I review the code and leave comments on it. An agent picks those up, makes
   the fixes, and we go again until it's clean.

## Writing issues

Issues follow a fixed shape so they're quick to pick up and fair to review: context first, then scope,
then a checkable "done when…". The `to_issue` skill (`.claude/skills/to_issue/`) writes them that way —
new issues should match it.

## How work is triggered

Wired up in [`.github/workflows/claude.yml`](./.github/workflows/claude.yml). One-time setup: run
`claude /install-github-app`, add the `ANTHROPIC_API_KEY` repo secret, and make a `claude` user
assignable (or switch the triggers to a label).

- **Build an issue:** assign it to `claude`. If the issue has sub-issues, one agent resolves them in
  the order they're listed — one commit each — then opens a PR. A leaf issue is resolved directly.
  The ordering lives in the workflow prompt, not in this file, so it runs the same way every time.
- **Heavy issues:** add the `ultra` label *before* assigning. That routes to a multi-agent `ultracode`
  run (Opus, decompose + self-review + verify per unit) instead of the lean single-agent lane.
- **Review → fix:** review the code and leave comments that mention `@claude`; it pushes fixes to the
  PR branch. Repeat until clean.

Only people with write access can trigger any of this; keep `main` behind branch protection.

## Conventions

- **Go** — one static binary, fast cold start.
- We never let CLITE auto-run the shell commands it generates; the product always hands control back to
  the user, and so do we when we script it.
- Keep each change scoped to its issue.
