# AGENTS.md — CLITE

CLITE turns plain English at the shell into a real, validated command. New here? Read
[README.md](./README.md); the intent lives in [VISION.md](./VISION.md) and the full spec in
[PRD.md](./PRD.md).

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

## The review → fix loop, concretely

The "an agent picks up my comments and fixes them" part runs on **Claude Code's GitHub integration**:

- Set it up once with `claude /install-github-app` — it installs the GitHub app, adds the
  `ANTHROPIC_API_KEY` repo secret, and drops in a workflow. The workflow listens on
  `pull_request_review_comment`, `pull_request_review`, `issue_comment`, and `issues`.
- It's **mention-triggered**: the agent acts only when a comment contains `@claude`. So leave the
  review note, mention `@claude`, and it pushes a fix commit to the PR branch.
- `@claude` on an *issue* makes it implement the work on a branch (you open the PR). `@claude` on an
  *inline review comment* makes it address that specific comment.
- Only people with write access can trigger it; keep `main` behind branch protection.

A fully hands-off review → fix → re-review loop isn't built in — that needs custom glue (a headless
`claude -p` script or the Agent SDK, with a turn/cost cap). We can add that later. For now the loop is
simply: **I comment with `@claude` → it fixes.**

## Conventions

- **Go** — one static binary, fast cold start.
- We never let CLITE auto-run the shell commands it generates; the product always hands control back to
  the user, and so do we when we script it.
- Keep each change scoped to its issue.
