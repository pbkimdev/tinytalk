# B3 — Self-contained HTML benchmark report (`tt eval --report`)

Spec for the bench PRD sub-issue B3. Parent: #90.

## Goal

One command turns eval results into a single, beautiful, self-contained HTML page — the public face
of the benchmark. artificialanalysis-grade readability, Anthropic-blog restraint. A stranger finds
the best model in ten seconds.

## Design

### CLI & module

- New module `tinytalk/eval/report.py`: `render_report(reports: list[BackendReport], meta: RunMeta) -> str`.
- `RunMeta`: run date, machine descriptor, protocol line, per-model pricing notes (from config
  `[prices]` + a free-text footnote), suite version.
- CLI: `tt eval ... --report bench.html` (composable with `--export`). Also
  `tt eval --report-from results.json --report bench.html` to re-render without re-running
  (reads the B2-extended export schema).

### Hard constraints

- **Fully self-contained**: inline CSS, charts as server-side-generated inline SVG. Zero external
  requests — no CDN, no webfonts, no JS chart libraries. (JS optional and minimal; SVG `<title>`
  gives native hover tooltips for free.)
- Responsive: charts scale via `viewBox`; wide tables scroll in their own container.
- Deterministic output for a given input (stable ordering, fixed precision) so re-renders diff
  cleanly in git.

### Visual language ("lab" aesthetic)

- Paper background `#FAF9F5`, ink `#141413`, hairline rules `#E8E6DC`.
- Accent: terracotta `#D97757` (highlight, frontier line); locals in warm neutrals, cloud models in
  a cool slate — the local-vs-frontier contrast is a *color story*, consistent across all charts.
- Type: serif display stack (`ui-serif, Georgia`) for the title and section heads; system sans for
  data; tabular numerals for metric columns.
- Layout: single column, max-width ~880px, generous whitespace; each chart gets a one-sentence
  plain-language caption stating what to conclude, artificialanalysis-style.

### Page structure

1. **Header** — title, run date, machine ("Apple M5 Max, 128 GB"), protocol line (T1 only ·
   temp 0 · 1 run/prompt · warmup excluded), suite version (25 EN/KO pairs).
2. **Headline bars** — grouped horizontal bars per model: strict pass % overall, with EN and KO
   bars side by side. Sorted best-first. Value labels on bars; no legend hunting.
3. **Score vs cost** — scatter: y = strict pass %, x = cost of one full 50-prompt sweep (USD,
   log scale). Point per model, labeled directly; Pareto frontier drawn as a step line in accent;
   footnote marker (†) on proxy-priced local models.
4. **Speed vs cost** — scatter: y = median end-to-end latency per prompt (s, lower is better),
   x = cost per sweep (log). Bubble area = strict pass %, so "fast, cheap, and actually right"
   reads at a glance.
5. **Full table** — per model: strict pass (overall/EN/KO), assertion %, format %, parse %,
   danger accuracy, tokens (in / out / cached), p50 latency, sweep cost.
6. **Fine print** — pricing table with sources and proxy footnote; assertion-DSL one-liner; link
   to the repo + results JSON.

### Implementation notes

- SVG built with small pure functions (`_bar`, `_scatter`, axis/tick helpers) in report.py — no new
  dependencies. Log-scale ticks at 1-2-5 steps.
- Colors/spacing as module constants; no theme system.

## Out of scope
- GitHub Pages/CI publishing; interactive filtering; dark mode; PNG export.
- Any metric computation — report.py renders what BackendReport/export already contain.

## Done when
- **unit**: rendering a 3-backend fixture (incl. one with KO scores ≠ EN and one error row)
  produces HTML containing the three SVG charts and the table; output contains no `http(s)://`
  URLs except repo links in fine print; `--report-from` round-trips the JSON export.
- **manual**: open the page locally — charts legible at 1280px and on a phone-width window;
  ten-second test passes on an uninvolved reader.
