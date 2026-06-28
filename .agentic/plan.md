# Plan — Issue #48: add a pre-release note near the top of `README.md`

> **Note:** This is a throwaway end-to-end co-test issue driving the engine context-digest
> feature against clite's pipeline. The resulting PR is **not to be merged** — it will be closed
> and its branch deleted once the e2e check is done. The change below is intentionally trivial so
> the diff stays minimal.

> **Revision (`/revise` #2, 2026-06-28):** per a second human clarification, the blockquote must be a
> **single physical line with no internal line breaks** — i.e. one `>` line, not a `>`-continuation
> across two lines. (Folds in the earlier `/revise` #1, which required the note to also state clite
> is **not yet published to PyPI**.) It remains a single sentence, a single blockquote, directly
> under the `# CLITE` H1.

## Goal & scope

**Goal.** Add a single sentence near the top of `README.md` stating that clite is pre-release,
under active development, and not yet published to PyPI — rendered as one blockquote on one
physical line.

**In scope**
- Insert one short pre-release note as a blockquote immediately under the `# CLITE` H1 in
  `README.md` (repo root). The single sentence must convey three things — pre-release / under
  active development / not yet published to PyPI — and must occupy **exactly one physical line**
  (`> …`), with **no internal line breaks** and no `>` continuation line.

**Out of scope (explicitly no change)**
- No edits to the existing `## Status` section (lines 34–40) — it already covers project maturity;
  we only add the brief top-of-file note the issue asks for, nothing else.
- No edits to the install line (currently line 48, `uv tool install clite` / `pipx`). Although it
  references a package not yet on PyPI, fixing it is **out of scope** for this throwaway doc-only
  issue; flagged under Risks for awareness only.
- No code, no other docs, no config, no tests, no `.github/` changes.

## Definition of Done

- [ ] `README.md` contains a one-line pre-release note within the first ~5 lines (immediately after
      the `# CLITE` title) that states clite is pre-release / under active development **and** not
      yet published to PyPI.
- [ ] The note is a single sentence rendered as a single Markdown blockquote (`>`) on **one
      physical line** — no internal line breaks, no second `>` continuation line.
- [ ] `git diff` shows only an insertion in `README.md` near the top (no deletions, no other files).

**Smallest verification that proves it works:** visual / `grep` check that the inserted line sits
near the top of `README.md`, reads as a pre-release note, and mentions PyPI; that it is exactly one
`>` line (the blockquote occupies a single physical line with no wrapped `>` continuation
immediately after it); plus `git diff --stat` showing `README.md` as the only changed file with an
insertion-only diff. No automated tests apply (doc-only).

## Steps

1. **Edit `README.md`** — insert a blank line + a single-line blockquote between the H1 (line 1,
   `# CLITE`) and the opening tagline paragraph (currently line 3). The blockquote text is one
   physical line (shown here on one line; it will be longer than the surrounding ~110-char prose
   wrap, which is intentional and required by this revision):

   ```markdown
   # CLITE

   > **Pre-release:** clite is under active development, not yet published to PyPI, and not yet ready for general use.

   CLITE turns plain English at your shell into a real command. ...
   ```

   Exact wording may be adjusted slightly during implementation, but it must remain **one sentence
   on one physical line** conveying "pre-release" + "under active development" + "not yet published
   to PyPI." Do **not** wrap it onto a second `>` line.

2. **Verify the diff** — run `git diff --stat` and `git diff README.md`; confirm a single
   insertion-only change to `README.md` near the top (the new blank line + one `>` line) and
   nothing else.

## Test strategy

Doc-only change — no unit/integration tests are warranted. Verification is the DoD check above:
the new note is present near the top of `README.md`, is a single sentence on a single `>` physical
line, mentions PyPI, and the diff touches only `README.md` (insertion only). This is the
appropriate, smallest level of proof for a one-line documentation edit.

## Risks & rollback

- **Risk:** The single-line blockquote is longer than the repo's usual ~110-char prose wrap.
  *Mitigation:* this is explicitly required by the `/revise` ("no internal line breaks"); it is a
  blockquote banner, not body prose, so the longer line is acceptable and intended.
- **Risk:** Redundancy with the existing `## Status` section. *Mitigation:* keep the new note to a
  single short sentence with a distinct framing (top-of-file at-a-glance banner vs. the fuller
  Status section); do not modify `## Status`.
- **Risk:** Inconsistency with the install line (`uv tool install clite` / `pipx`, ~line 48), which
  implies the package is already on PyPI. *Mitigation:* out of scope for this issue — left
  unchanged deliberately; noted here so a future issue can reconcile it.
- **Risk:** Accidental scope creep beyond the one line. *Mitigation:* DoD requires an
  insertion-only, single-file diff; review `git diff` before committing.
- **Rollback:** Trivial — revert the single-line insertion in `README.md` (or close the PR and
  delete the branch, which is the planned end state for this throwaway issue anyway).
