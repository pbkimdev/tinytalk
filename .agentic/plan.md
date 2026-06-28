# Plan — README pre-release / install-from-source caveat (#51)

## Goal & scope

Add **one short caveat line near the README install instructions** making clear that CLITE is
pre-release (not yet on PyPI) and should be installed from source for now.

The install instruction lives in the "How it'll work" section:

> `README.md:48` — *"Under the hood it's a Python CLI (install with `uv tool install clite` or
> `pipx`), local-first, …"*

That phrasing reads as if `clite` is published. We add an adjacent caveat so readers don't try to
`uv tool install clite` / `pipx install clite` and fail.

**In scope**
- A single caveat line placed immediately next to the install reference in `README.md` (the
  paragraph at lines 48–50).

**Out of scope (explicitly does not change)**
- No changes to code, `pyproject.toml`, packaging, or CI.
- No rewrite of the existing "Status" section (`README.md:34–40`); we only avoid contradicting it.
- No new release/publishing process — this is doc-only.
- No PyPI publication.

## Definition of Done

- `README.md` contains a one-line pre-release / install-from-source caveat positioned near the
  install instructions (the paragraph at lines 48–50), not buried elsewhere.
- The caveat names both facts: **(a)** not yet on PyPI / pre-release, **(b)** install from source
  for now.
- The caveat is consistent with the existing "Status" section and the `pyproject.toml` reality
  (`uv tool install .` already works from a clone).
- Doc-only diff: `git diff --stat` shows `README.md` as the only changed file.

**Smallest verification that proves it:** read the rendered/raw `README.md` and confirm the caveat
line is present adjacent to the install paragraph. No automated test applies (repo has no markdown
lint or doc tests; CI dev tooling is `pytest`/`ruff` over Python only).

## Steps

1. **Edit `README.md`** — add the caveat to the install paragraph (lines 48–50). Keep it to one
   sentence, in the README's existing plain-spoken voice. Suggested wording (implement may refine
   tone, must keep both facts):

   > *Not on PyPI yet — CLITE is pre-release. For now, install from source: clone this repo and run
   > `uv tool install .` (or `pipx install .`).*

   Placement: append as a short trailing sentence/line to the existing install paragraph, or as an
   italic note directly beneath it, so it sits right next to `uv tool install clite`.

2. **Self-check** — `git diff README.md` to confirm only the caveat was added and the surrounding
   text still reads cleanly; `git diff --stat` to confirm `README.md` is the only changed file.

## Test strategy

Doc-only change; no unit tests are warranted and none exist for the README. Verification is a manual
read of `README.md` confirming:
- the caveat appears adjacent to the install instructions, and
- it states both pre-release/not-on-PyPI and install-from-source.

Adding a test harness for prose would be disproportionate to an S-sized doc edit, so we deliberately
do not.

## Risks & rollback

- **Risk:** wording drifts from / contradicts the existing "Status" section. *Mitigation:* keep the
  caveat aligned with `README.md:36` ("no working release yet").
- **Risk:** caveat placed too far from the install command to be useful. *Mitigation:* DoD pins it to
  the lines 48–50 install paragraph.
- **Rollback:** trivial — revert the single-file commit; no code, deps, or CI are touched.

> _Note:_ the issue flags this as a throwaway exercise of the pipeline — the resulting PR is not meant
> to be merged. The plan is scoped accordingly (minimal, reversible).
