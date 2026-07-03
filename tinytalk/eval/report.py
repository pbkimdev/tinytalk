"""Self-contained HTML benchmark report (#90/#99, docs/specs/bench-03-report.md).

One .html file, zero external requests: inline CSS, charts as server-side inline
SVG (native `<title>` tooltips, no JS). Deterministic for a given input — stable
ordering and fixed precision — so re-renders diff cleanly in git. Rendering only:
every number comes from `BackendReport`; nothing is computed here.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from pathlib import Path

from tinytalk.eval.runner import BackendReport, PromptResult

# Lab palette — paper and ink with one terracotta accent; local models read warm,
# cloud models cool, consistently across every chart.
_PAPER = "#FAF9F5"
_INK = "#141413"
_MUTED = "#6B6960"
_RULE = "#E8E6DC"
_ACCENT = "#D97757"
_LOCAL_COLORS = ("#C08552", "#A98467", "#8A7250", "#C4A484")
_CLOUD_COLORS = ("#46627F", "#6E8898", "#39506B", "#8199AC")


@dataclass(frozen=True)
class RunMeta:
    run_date: str = ""
    machine: str = ""
    protocol: str = (
        "single backend, no cross-backend escalation · temperature 0 · "
        "1 run per prompt · warmup excluded"
    )
    suite: str = "25 golden targets × EN/KO parallel pairs (50 prompts)"
    pricing_notes: tuple[str, ...] = ()


def load_reports(path: Path) -> list[BackendReport]:
    """Rebuild `BackendReport`s from a `tt eval --export results.json` file."""
    data = json.loads(path.read_text("utf-8"))
    fields = PromptResult.__dataclass_fields__
    reports = []
    for entry in data:
        results = [
            PromptResult(**{k: v for k, v in row.items() if k in fields})
            for row in entry["results"]
        ]
        reports.append(
            BackendReport(
                backend=entry["backend"],
                model=entry["model"],
                local=bool(entry.get("local", False)),
                results=results,
            )
        )
    return reports


def render_report(reports: list[BackendReport], meta: RunMeta) -> str:
    ranked = sorted(reports, key=lambda r: (-r.strict_pass_pct, r.total_cost_usd, r.backend))
    colors = _assign_colors(ranked)
    any_local = any(r.local for r in ranked)
    sections = [
        _header(meta),
        _section(
            "Pass rate",
            "Strict pass over the full suite — a prompt counts only when the output is valid, "
            "the command parses, every binary exists, and all assertions hold.",
            _bars_svg(ranked, colors),
        ),
        _section(
            "Score vs. cost",
            "Up and left is better. The accent line is the Pareto frontier: no model above it "
            "is cheaper without being worse.",
            _score_cost_svg(ranked, colors),
        ),
        _section(
            "Speed vs. cost",
            "Down and left is better; bubble area is the pass rate — big low bubbles are "
            "fast, cheap, and actually right.",
            _speed_cost_svg(ranked, colors),
        ),
        _section("All numbers", "", _table(ranked)),
        _fine_print(meta, any_local),
    ]
    body = "\n".join(sections)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>TinyTalk Bench</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


_CSS = f"""
* {{ box-sizing: border-box; margin: 0; }}
body {{ background: {_PAPER}; color: {_INK};
  font: 16px/1.55 system-ui, -apple-system, 'Segoe UI', sans-serif; }}
main {{ max-width: 880px; margin: 0 auto; padding: 48px 20px 72px; }}
h1 {{ font-family: ui-serif, Georgia, serif; font-size: 40px; font-weight: 600;
  letter-spacing: -0.5px; }}
h2 {{ font-family: ui-serif, Georgia, serif; font-size: 24px; font-weight: 600;
  margin: 56px 0 4px; }}
p.caption {{ color: {_MUTED}; font-size: 14px; margin: 2px 0 18px; max-width: 640px; }}
p.meta {{ color: {_MUTED}; font-size: 14px; margin-top: 8px; }}
svg {{ display: block; width: 100%; height: auto; }}
.tablewrap {{ overflow-x: auto; margin-top: 12px; }}
table {{ border-collapse: collapse; font-size: 13px; white-space: nowrap;
  font-variant-numeric: tabular-nums; }}
th, td {{ padding: 7px 12px; text-align: right; border-bottom: 1px solid {_RULE}; }}
th {{ color: {_MUTED}; font-weight: 500; }}
th:first-child, td:first-child {{ text-align: left; }}
td.best {{ color: {_ACCENT}; font-weight: 600; }}
.fineprint {{ margin-top: 56px; padding-top: 16px; border-top: 1px solid {_RULE};
  color: {_MUTED}; font-size: 13px; }}
.fineprint li {{ margin: 3px 0 3px 18px; }}
"""


def _assign_colors(ranked: list[BackendReport]) -> dict[str, str]:
    colors: dict[str, str] = {}
    local_i = cloud_i = 0
    for r in ranked:
        if r.local:
            colors[r.backend] = _LOCAL_COLORS[local_i % len(_LOCAL_COLORS)]
            local_i += 1
        else:
            colors[r.backend] = _CLOUD_COLORS[cloud_i % len(_CLOUD_COLORS)]
            cloud_i += 1
    return colors


def _label(r: BackendReport) -> str:
    return f"{r.backend}†" if r.local else r.backend


def _header(meta: RunMeta) -> str:
    bits = [b for b in (meta.run_date, meta.machine, meta.protocol) if b]
    line = html.escape(" · ".join(bits))
    return f"<h1>TinyTalk Bench</h1>\n<p class=\"meta\">{line}</p>\n<p class=\"meta\">{html.escape(meta.suite)}</p>"


def _section(title: str, caption: str, content: str) -> str:
    cap = f"\n<p class=\"caption\">{html.escape(caption)}</p>" if caption else ""
    return f"<h2>{html.escape(title)}</h2>{cap}\n{content}"


# --- charts ------------------------------------------------------------------

_W = 800  # shared viewBox width


def _svg(height: int, parts: list[str]) -> str:
    inner = "\n".join(parts)
    return (
        f'<svg viewBox="0 0 {_W} {height}" role="img" '
        f'font-family="system-ui, sans-serif">\n{inner}\n</svg>'
    )


def _text(x: float, y: float, s: str, *, size=13, fill=_INK, anchor="start", weight="") -> str:
    w = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}"{w}>{html.escape(s)}</text>'
    )


def _bars_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    label_w, right_pad, row_h = 240, 56, 72
    chart_w = _W - label_w - right_pad
    parts = []
    for i, r in enumerate(ranked):
        y = i * row_h
        color = colors[r.backend]
        parts.append(_text(0, y + 24, _label(r), size=15, weight="600"))
        parts.append(
            _text(label_w - 16, y + 24, f"{r.strict_pass_pct:.0f}%", size=15, fill=color,
                  anchor="end", weight="600")
        )
        for j, (tag, pct) in enumerate(
            (("EN", r.strict_pass_pct_en), ("KO", r.strict_pass_pct_ko))
        ):
            by = y + 12 + j * 22
            bw = max(chart_w * pct / 100.0, 1.5)
            parts.append(_text(label_w + 22, by + 12, tag, size=11, fill=_MUTED, anchor="end"))
            parts.append(
                f'<rect x="{label_w + 30}" y="{by}" width="{chart_w:.1f}" height="14" '
                f'rx="3" fill="{_RULE}"/>'
            )
            parts.append(
                f'<rect x="{label_w + 30}" y="{by}" width="{bw:.1f}" height="14" rx="3" '
                f'fill="{color}" fill-opacity="{1.0 if tag == "EN" else 0.62}">'
                f"<title>{html.escape(r.backend)} {tag}: {pct:.0f}%</title></rect>"
            )
            parts.append(
                _text(label_w + 36 + bw, by + 11.5, f"{pct:.0f}%", size=11, fill=_MUTED)
            )
    return _svg(len(ranked) * row_h, parts)


def _ticks_125(lo: float, hi: float) -> list[float]:
    ticks = []
    exp = math.floor(math.log10(lo))
    while 10**exp <= hi:
        for m in (1, 2, 5):
            v = m * 10**exp
            if lo <= v <= hi:
                ticks.append(v)
        exp += 1
    return ticks


def _cost_axis(ranked: list[BackendReport]) -> tuple[float, float]:
    """(lo, hi) for the log cost axis; zero-cost points are clamped to lo."""
    costs = [r.total_cost_usd for r in ranked if r.total_cost_usd > 0]
    if not costs:
        return 0.0001, 1.0
    return min(costs) / 1.8, max(costs) * 1.8


def _fmt_cost(v: float) -> str:
    return f"${v:g}" if v >= 0.01 else f"${v:.4f}"


def _score_cost_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    height, ml, mr, mt, mb = 420, 56, 30, 16, 46
    pw, ph = _W - ml - mr, height - mt - mb
    lo, hi = _cost_axis(ranked)

    def px(cost: float) -> float:
        c = max(cost, lo)
        return ml + pw * (math.log10(c) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def py(score: float) -> float:
        return mt + ph * (1 - score / 100.0)

    parts = [_axes_frame(ml, mt, pw, ph)]
    for s in range(0, 101, 20):
        parts.append(f'<line x1="{ml}" y1="{py(s):.1f}" x2="{ml + pw}" y2="{py(s):.1f}" stroke="{_RULE}"/>')
        parts.append(_text(ml - 8, py(s) + 4, f"{s}%", size=11, fill=_MUTED, anchor="end"))
    for t in _ticks_125(lo, hi):
        parts.append(_text(px(t), mt + ph + 18, _fmt_cost(t), size=11, fill=_MUTED, anchor="middle"))
    parts.append(_text(ml + pw / 2, height - 8, "cost per full sweep (USD, log scale)", size=12,
                       fill=_MUTED, anchor="middle"))

    frontier, best = [], -1.0
    for r in sorted(ranked, key=lambda r: (max(r.total_cost_usd, lo), -r.strict_pass_pct)):
        if r.strict_pass_pct > best:
            frontier.append(r)
            best = r.strict_pass_pct
    if len(frontier) > 1:
        d = f"M {px(frontier[0].total_cost_usd):.1f} {py(frontier[0].strict_pass_pct):.1f}"
        for prev, cur in zip(frontier, frontier[1:]):
            d += (
                f" H {px(cur.total_cost_usd):.1f}"
                f" V {py(cur.strict_pass_pct):.1f}"
            )
        parts.append(
            f'<path d="{d}" fill="none" stroke="{_ACCENT}" stroke-width="1.5" '
            'stroke-dasharray="5 4" opacity="0.85"/>'
        )

    for r in ranked:
        x, y = px(r.total_cost_usd), py(r.strict_pass_pct)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colors[r.backend]}">'
            f"<title>{html.escape(r.backend)}: {r.strict_pass_pct:.0f}% at "
            f"{_fmt_cost(r.total_cost_usd)}</title></circle>"
        )
        parts.append(_text(x + 11, y + 4, _label(r), size=12))
    return _svg(height, parts)


def _speed_cost_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    height, ml, mr, mt, mb = 420, 56, 30, 16, 46
    pw, ph = _W - ml - mr, height - mt - mb
    lo, hi = _cost_axis(ranked)
    lat_hi = _nice_ceiling(max((r.median_latency_s for r in ranked), default=1.0))

    def px(cost: float) -> float:
        c = max(cost, lo)
        return ml + pw * (math.log10(c) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def py(lat: float) -> float:
        return mt + ph * (1 - lat / lat_hi)

    parts = [_axes_frame(ml, mt, pw, ph)]
    for k in range(0, 6):
        v = lat_hi * k / 5
        parts.append(f'<line x1="{ml}" y1="{py(v):.1f}" x2="{ml + pw}" y2="{py(v):.1f}" stroke="{_RULE}"/>')
        parts.append(_text(ml - 8, py(v) + 4, f"{v:g}s", size=11, fill=_MUTED, anchor="end"))
    for t in _ticks_125(lo, hi):
        parts.append(_text(px(t), mt + ph + 18, _fmt_cost(t), size=11, fill=_MUTED, anchor="middle"))
    parts.append(_text(ml + pw / 2, height - 8, "cost per full sweep (USD, log scale)", size=12,
                       fill=_MUTED, anchor="middle"))
    parts.append(_text(14, mt + 12, "p50 latency", size=12, fill=_MUTED))

    for r in ranked:
        x, y = px(r.total_cost_usd), py(r.median_latency_s)
        radius = 6 + 16 * math.sqrt(max(r.strict_pass_pct, 0) / 100.0)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{colors[r.backend]}" '
            f'fill-opacity="0.75">'
            f"<title>{html.escape(r.backend)}: {r.median_latency_s:.2f}s median, "
            f"{r.strict_pass_pct:.0f}% pass</title></circle>"
        )
        parts.append(_text(x + radius + 5, y + 4, _label(r), size=12))
    return _svg(height, parts)


def _axes_frame(ml: int, mt: int, pw: int, ph: int) -> str:
    return (
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="{_MUTED}"/>'
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="{_MUTED}"/>'
    )


def _nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if m * 10**exp >= v:
            return m * 10**exp
    return 10 ** (exp + 1)


# --- table & fine print ------------------------------------------------------

_COLUMNS = (
    "model", "pass", "EN", "KO", "assert", "format", "parses", "danger",
    "tok in", "tok out", "cached", "p50 lat", "cost",
)


def _table(ranked: list[BackendReport]) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in _COLUMNS)
    rows = []
    best = ranked[0].backend if ranked else ""
    for r in ranked:
        tok_in = sum(x.prompt_tokens for x in r.results)
        tok_out = sum(x.completion_tokens for x in r.results)
        cached = sum(x.cached_prompt_tokens for x in r.results)
        cells = [
            f"<td>{html.escape(_label(r))}<br><small>{html.escape(r.model)}</small></td>",
            f'<td class="{"best" if r.backend == best else ""}">{r.strict_pass_pct:.0f}%</td>',
            f"<td>{r.strict_pass_pct_en:.0f}%</td>",
            f"<td>{r.strict_pass_pct_ko:.0f}%</td>",
            f"<td>{r.assertions_pct:.0f}%</td>",
            f"<td>{r.format_ok_pct:.0f}%</td>",
            f"<td>{r.parses_pct:.0f}%</td>",
            f"<td>{r.danger_pct:.0f}%</td>",
            f"<td>{tok_in:,}</td>",
            f"<td>{tok_out:,}</td>",
            f"<td>{cached:,}</td>",
            f"<td>{r.median_latency_s:.2f}s</td>",
            f"<td>{_fmt_cost(r.total_cost_usd)}</td>",
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _fine_print(meta: RunMeta, any_local: bool) -> str:
    items = [f"Protocol: {meta.protocol}", f"Suite: {meta.suite}"]
    if any_local:
        items.append(
            "† Local models run on-device at $0 marginal cost; they are plotted at their "
            "public hosted per-token quote so every model shares one cost axis."
        )
    items.extend(meta.pricing_notes)
    items.append(
        "Strict pass = contract-valid JSON + command parses (zsh -n) + binaries exist + "
        "all assertions pass. Danger accuracy is scored separately. Commands are never executed."
    )
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    return f'<div class="fineprint"><ul>{lis}</ul></div>'
