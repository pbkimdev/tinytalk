---
name: to_issue
description: Turn a requirement into a properly-contextualized GitHub issue for the CLITE repo, in this repo's mandated review-friendly format. Use when the user wants to create, draft, file, or open an issue here, or convert a requirement / feature / bug / spike into a clite GitHub issue.
---

# to_issue — CLITE issue authoring

Turn a requirement (feature, bug, chore, spike) into a GitHub issue in this repo that a reviewer can
fully judge **without chasing external context**. Every issue MUST follow the template below.

## When to use
The user gives a requirement and wants it filed as a GitHub issue in the `clite` repo, or asks to
draft / create / file / open an issue here.

## Workflow
1. **Clarify intent** only if genuinely ambiguous (one or two questions max). Classify the issue as
   feature / bug / chore / spike.
2. **Gather context — do not skip:**
   - Read the relevant part of `docs/agents/PRD.md` and `VISION.md`; identify the section(s) this maps to.
   - Skim related code/files and existing issues (`gh issue list`).
   - Identify dependencies (what must land first / what this unblocks) and what is explicitly out of scope.
3. **Draft** the issue using the mandatory template. Fill every section. If a section is genuinely
   N/A write `None` — never delete a heading.
4. **Show the full draft to the user and wait for approval.** Do not create the issue first.
5. **Create** with `gh issue create --title "<title>" --body-file <file> --label <l> [--label <l>]`.
   If a Project board exists, add it afterward (`gh project item-add`).
6. Report the created issue URL.

## Title rule
`<type/area>: <imperative summary>` — e.g. `feat(grounding): cache parsed --help output`,
`fix(shell): ? widget drops multiline buffer`. For epics use the `Sxx — title` form.

## Mandatory body template
```markdown
## Context
Why this exists and where it fits. Link the relevant PRD section(s) and VISION. State the problem, the
user-visible behavior, and the relevant architecture (which execution tier, which step of the
validation ladder, which provider path). Enough that a cold reader understands it from the issue alone.

## Scope
- What this issue delivers (concrete, bounded).

## Out of scope / Deferred
- What this issue explicitly does NOT cover.

## Dependencies
- Blocked by: #<n> (or None)
- Blocks: #<n> (or None)

## Definition of Done
- Measurable success criteria. Name the verification level: unit | integration | eval | manual.

## Acceptance checks
- [ ] Concrete, checkable conditions a reviewer can verify.

## Sub-tasks
- [ ] Optional breakdown.

## References
- PRD §<x> · VISION · key files/paths · related issues.
```

## Rules
- **Context first.** A thin Context section means the issue is not done. The reviewer must not need to
  open the PRD to understand why this matters — summarize *and* link.
- **Measurable DoD.** No "works correctly". State what proves it and at which verification level,
  mirroring the project's Definition-of-Done discipline.
- **Honest scope.** Always fill "Out of scope / Deferred" — it prevents scope creep and speeds review.
- **Labels** from the repo set: `core, eval, grounding, safety, shell, ux, perf, v1` plus a type label
  `type:feature | type:bug | type:chore | type:spike`. Add only what applies.
- Keep it tight; link rather than restate large chunks of the PRD.
