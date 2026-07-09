# AGENTS.md — TinyTalk

TinyTalk turns plain English at the shell into a real, validated command. New here? Read
[README.md](./README.md); the intent lives in [VISION.md](./VISION.md). Terms (prompt mode,
badge, slot, …) are defined in [GLOSSARY.md](./GLOSSARY.md) — use them as written there.

## How we work

**Every piece of work is a [GitHub issue](https://github.com/pbkimdev/tinytalk/issues).** Some issues
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

Wired up in [`.github/workflows/claude.yml`](./.github/workflows/claude.yml) (plus
`claude-code-review.yml`, which auto-reviews each PR). The GitHub app is installed and authenticated
via the `CLAUDE_CODE_OAUTH_TOKEN` secret.

- **Plan first:** add the `plan` label. An agent posts an implementation plan as a comment and stops —
  no code. Review it (edit the issue if you want changes).
- **Build an issue:** add the `claude` label. It implements *per the posted plan* if there is one. If
  the issue has sub-issues, one agent resolves them in the order they're listed — one commit each —
  then opens a PR. A leaf issue is resolved directly. The ordering lives in the workflow prompt, not in
  this file, so it runs the same way every time.
- **Heavy issues:** add the `ultra` label *first*, then the `claude` label. That routes to a
  multi-agent `ultracode` run (Opus, decompose + self-review + verify per unit) instead of the lean
  single-agent lane.
- **Review → fix:** review the code and leave comments that mention `@claude`; it pushes fixes to the
  PR branch. Repeat until clean.

Only people with write access can trigger any of this; keep `main` behind branch protection.

## Conventions

- **Python** — managed with `uv`; installed as a CLI (`uv tool install` / `pipx`). The two agent
  SDKs (Claude Agent SDK, OpenAI Codex SDK) are first-class in-process backends, which is why this is
  Python and not Go.
- We never let TinyTalk auto-run the shell commands it generates; the product always hands control back to
  the user, and so do we when we script it.
- Keep each change scoped to its issue.

## Cursor Cloud specific instructions

Notes for the **Cursor Cloud Agent VM** (the ephemeral dev environment). The startup
update script already runs `uv sync --group dev`, so `uv` and the `.venv` (Python 3.14,
per `.python-version`) are in place — you do **not** need to install them again.

- **Run everything through uv:** `uv run tt …`, `uv run pytest -q`, `uv run ruff check .`.
  The pinned dev commands live in `pyproject.toml` and the `.github/workflows/*.yml` CI files
  (`uv sync --frozen --group dev`, `uv run pytest`). `uv tool install .` also works to put a
  real `tt` on `$PATH` for interactive tries.
- **The product needs a model backend to translate.** There are no cloud/local model
  credentials in this VM by default. `tt "<request>"` fails at the provider unless a backend is
  reachable. Two credential-free ways to exercise things end to end:
  - `tt ground` (builds/inspects the real host grounding snapshot) and
    `tt prompt "<request>"` (assembles the exact system+user prompt, **no** model call) — both
    run the real grounding/prompt pipeline with zero backend.
  - Point an `openai-compat` backend (`~/.config/tinytalk/config.toml`, `base_url` →
    `/v1/chat/completions`) at any local OpenAI-compatible server to drive the whole
    pipeline (grounding → HTTP → strict parse → validate → output). A tiny stub server that
    returns a fenced-JSON contract body is enough to prove the flow without a real LLM.
- **The `?` prompt widget is zsh-only and `zsh` is not installed here.** The plain
  `tt "<request>"` / `tt --json` / `tt --widget` paths work in `bash`. To exercise the
  interactive widget, `apt install zsh` and follow `.claude/skills/test-shell-ui/`
  (tmux + a stubbed `tt`).
- **Two host-sensitive test groups are expected to fail on this minimized Ubuntu image**
  (they pass on a fuller/macOS-flavored box, and are *not* a setup problem): the grounding
  "no-help" tests (`man <x>` prints an "unminimized" banner here, so it looks like docs) and
  several `test_oracle.py` fixtures whose "correct" commands need `fd`/`kubectl` (absent) or
  BSD-only flags like `stat -f` / `date -v` (this VM is GNU coreutils). The other ~719 tests
  pass. `apt install fd-find kubectl` + `unminimize` narrows the gap but never closes the
  BSD-flag ones — don't chase them.
