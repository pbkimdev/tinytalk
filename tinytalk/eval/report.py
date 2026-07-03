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

from tinytalk.config import Price
from tinytalk.eval.runner import BackendReport, PromptResult, _cost
from tinytalk.eval.suite import SUITE, EvalPrompt
from tinytalk.provider.base import Usage

# Paid (API) = cool blues; local (on-device) = warm amber/olive. Fixed per backend so
# colors stay stable regardless of rank order.
_CLOUD_COLORS = ("#2E5078", "#4A90B8", "#39506B", "#6E8898")
_LOCAL_COLORS = ("#B8753D", "#6E8B4E", "#8F6B4F", "#C4A484")
_BACKEND_COLORS: dict[str, str] = {
    "sonnet5-low": _CLOUD_COLORS[0],
    "gpt55-low": _CLOUD_COLORS[1],
    "local-qwen36-35b": _LOCAL_COLORS[1],
    "local-gemma4-26b": _LOCAL_COLORS[0],
    "local-gemma4-e4b": _LOCAL_COLORS[2],
}

_PAPER = "#FAF9F5"
_INK = "#141413"
_MUTED = "#6B6960"
_RULE = "#E8E6DC"
_ACCENT = "#D97757"
_CLOUD_TINT = "#EEF3F8"
_LOCAL_TINT = "#F6F0E8"


@dataclass(frozen=True)
class RunMeta:
    run_date: str = ""
    machine: str = ""
    basis: str = "Based on 25 mixture of natural language commands"
    protocol: str = (
        "single backend, no cross-backend escalation · temperature 0 · "
        "1 run per prompt · warmup excluded"
    )
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
    ranked = sorted(reports, key=lambda r: (-r.strict_pass_pct, _chart_cost(r), r.backend))
    colors = _assign_colors(ranked)
    any_local = any(r.local for r in ranked)
    sections = [
        _header(meta),
        _section(
            "Pass rate",
            "Strict pass over the full suite — a prompt counts only when the output is valid, "
            "the command parses, every binary exists, and all assertions hold.",
            _chart_block(_bars_svg(ranked, colors), _legend(ranked, colors)),
        ),
        _section(
            "Score vs. cost",
            "Up and left is better. Pass rate is expanded from 60% upward so top-model "
            "gaps are visible; the bottom fifth compresses everything below 60%. The dashed "
            "terracotta line is the Pareto frontier — no model sits above it with both a "
            "higher score and lower cost.",
            _chart_block(_score_cost_svg(ranked, colors), _legend(ranked, colors)),
        ),
        _section(
            "Speed vs. cost",
            "Down and left is better; bubble area is the pass rate — big low bubbles are "
            "fast, cheap, and actually right.",
            _chart_block(_speed_cost_svg(ranked, colors), _legend(ranked, colors)),
        ),
        _section(
            "Test suite",
            "Each target is a natural-language command in English and Korean. Pass means "
            "the generated command satisfies every assertion — there is no single golden command.",
            _suite_section(ranked, colors),
        ),
        _section(
            "All numbers",
            "Danger label is scored separately from strict pass: each prompt expects "
            "safe, caution, or destructive, and the column is how often the model tagged it correctly.",
            _table(ranked, colors),
        ),
        _fine_print(meta, any_local),
    ]
    body = "\n".join(sections)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>TinyTalk CLI Bench</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


_CSS = f"""
* {{ box-sizing: border-box; margin: 0; }}
body {{ background: {_PAPER}; color: {_INK};
  font: 16px/1.55 system-ui, -apple-system, 'Segoe UI', sans-serif; }}
main {{ max-width: 920px; margin: 0 auto; padding: 48px 24px 80px; }}
h1 {{ font-family: ui-serif, Georgia, serif; font-size: 40px; font-weight: 600;
  letter-spacing: -0.5px; margin-bottom: 16px; }}
.block {{ margin-top: 48px; }}
.block:first-of-type {{ margin-top: 32px; }}
h2 {{ font-family: ui-serif, Georgia, serif; font-size: 24px; font-weight: 600;
  margin: 0 0 4px; }}
p.caption {{ color: {_MUTED}; font-size: 14px; margin: 2px 0 16px; max-width: 680px;
  line-height: 1.5; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }}
.meta span {{ color: {_MUTED}; font-size: 13px; line-height: 1.4; padding: 5px 10px;
  border: 1px solid {_RULE}; border-radius: 999px; background: #fff; }}
.chart {{ margin-top: 4px; padding: 1rem 12px 8px; background: #fff;
  border: 1px solid {_RULE}; border-radius: 10px; }}
svg {{ display: block; width: 100%; height: auto; }}
.chart-point {{ cursor: pointer; }}
.chart-point-label {{ opacity: 0; pointer-events: none; paint-order: stroke fill;
  stroke: #fff; stroke-width: 3px; stroke-linejoin: round; }}
.chart-point:hover .chart-point-label {{ opacity: 1; }}
.chart-point:hover circle {{ fill-opacity: 1; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 10px 4px 0;
  font-size: 13px; align-items: center; }}
.legend-group {{ display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }}
.legend-group-label {{ color: {_MUTED}; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; margin-right: 2px; }}
.legend-item {{ display: inline-flex; align-items: center; gap: 7px; color: {_INK}; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
.tablewrap {{ overflow-x: auto; max-width: 100%; margin-top: 8px; border: 1px solid {_RULE};
  border-radius: 10px; background: #fff; -webkit-overflow-scrolling: touch; }}
table {{ border-collapse: collapse; font-size: 13px; white-space: nowrap; table-layout: fixed;
  width: max-content; min-width: 100%; font-variant-numeric: tabular-nums; }}
thead th {{ position: sticky; top: 0; background: #fff; z-index: 1; vertical-align: middle; }}
th, td {{ padding: 9px 14px; text-align: right; border-bottom: 1px solid {_RULE};
  vertical-align: middle; }}
tbody tr:last-child td {{ border-bottom: none; }}
tr.paid {{ background: {_CLOUD_TINT}; }}
tr.local {{ background: {_LOCAL_TINT}; }}
th {{ color: {_MUTED}; font-weight: 500; font-size: 12px; text-transform: uppercase; }}
th[title] {{ cursor: help; text-decoration: underline dotted {_RULE}; text-underline-offset: 3px; }}
th.model, td.model {{ text-align: left; }}
th.model, td.model {{ white-space: normal; min-width: 168px; }}
th.model .model-row, td.model .model-row {{ display: flex; align-items: flex-start; gap: 10px; }}
th.model .model-swatch, td.model .model-swatch {{ width: 10px; height: 10px; border-radius: 50%;
  flex-shrink: 0; margin-top: 5px; }}
th.model .model-swatch {{ visibility: hidden; }}
td.model strong {{ display: block; font-weight: 600; font-size: 14px; }}
td.model small {{ display: block; color: {_MUTED}; font-size: 12px; margin-top: 2px;
  font-weight: 400; }}
td.pass.best {{ color: {_ACCENT}; font-weight: 600; }}
th.en, td.en, th.ko, td.ko {{ text-align: center; padding-left: 10px; padding-right: 10px; }}
th.en, th.ko {{ font-size: 13px; }}
col.col-lang {{ width: 4rem; }}
col.col-pct {{ width: 4.5rem; }}
col.col-model {{ width: 11rem; }}
col.col-tokens {{ width: 5.5rem; }}
col.col-latency {{ width: 4.5rem; }}
col.col-cost {{ width: 8rem; }}
td.cost {{ white-space: nowrap; }}
td.cost .cost-main {{ font-weight: 500; }}
td.cost .cost-note {{ color: {_MUTED}; font-size: 11px; font-weight: 400; }}
.suite {{ margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }}
.suite-item {{ border: 1px solid {_RULE}; border-radius: 10px; background: #fff; }}
.suite-item summary {{ cursor: pointer; padding: 11px 14px; font-weight: 600; font-size: 14px;
  list-style: none; display: flex; align-items: center; gap: 10px; }}
.suite-item summary::-webkit-details-marker {{ display: none; }}
.suite-caret {{ flex-shrink: 0; width: 14px; color: {_MUTED}; font-size: 11px; line-height: 1;
  text-align: center; }}
.suite-caret-open {{ display: none; }}
.suite-item[open] .suite-caret-closed {{ display: none; }}
.suite-item[open] .suite-caret-open {{ display: inline; }}
.suite-title {{ flex: 1; min-width: 0; }}
.suite-score {{ flex-shrink: 0; color: {_MUTED}; font-size: 12px; font-weight: 500; }}
.suite-body {{ padding: 4px 14px 12px; border-top: 1px solid {_RULE}; }}
.suite-outcomes {{ font-size: 13px; padding: 10px 0 12px; border-bottom: 1px solid {_RULE};
  line-height: 1.5; }}
.suite-outcomes p + p {{ margin-top: 6px; }}
.suite-outcomes strong {{ color: {_MUTED}; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; margin-right: 8px; }}
.suite-outcome-name {{ font-weight: 600; }}
.suite-row {{ display: flex; gap: 10px; padding: 10px 0; }}
.suite-row + .suite-row {{ border-top: 1px solid {_RULE}; }}
.suite-lang {{ flex-shrink: 0; width: 2rem; color: {_MUTED}; font-size: 11px;
  font-weight: 600; text-transform: uppercase; padding-top: 2px; }}
.suite-prompt {{ font-size: 14px; line-height: 1.45; }}
.suite-expected {{ color: {_MUTED}; font-size: 13px; margin-top: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.fineprint {{ margin-top: 56px; padding-top: 20px; border-top: 1px solid {_RULE};
  color: {_MUTED}; font-size: 13px; line-height: 1.55; }}
.fineprint li {{ margin: 6px 0 6px 18px; }}
"""


def _assign_colors(ranked: list[BackendReport]) -> dict[str, str]:
    colors: dict[str, str] = {}
    local_i = cloud_i = 0
    for r in ranked:
        if r.backend in _BACKEND_COLORS:
            colors[r.backend] = _BACKEND_COLORS[r.backend]
        elif r.local:
            colors[r.backend] = _LOCAL_COLORS[local_i % len(_LOCAL_COLORS)]
            local_i += 1
        else:
            colors[r.backend] = _CLOUD_COLORS[cloud_i % len(_CLOUD_COLORS)]
            cloud_i += 1
    return colors


_KNOWN_LABELS: dict[str, tuple[str, str]] = {
    "sonnet5-low": ("Claude Sonnet 5", "Agent SDK · low effort"),
    "gpt55-low": ("GPT-5.5", "Codex SDK · low effort"),
    "local-gemma4-26b": ("Gemma 4 26B A4B", "local · oMLX 8-bit"),
    "local-gemma4-e4b": ("Gemma 4 E4B", "local · MLX 4-bit"),
    "local-qwen36-35b": ("Qwen 3.6 35B A3B", "local · MTP"),
}


def _display(r: BackendReport) -> tuple[str, str]:
    if r.backend in _KNOWN_LABELS:
        return _KNOWN_LABELS[r.backend]
    title = r.model.replace("_", " ").replace("--", " · ").replace("-", " ")
    return title, r.backend.replace("-", " ")


def _label(r: BackendReport) -> str:
    name, _ = _display(r)
    return f"{name}†" if r.local else name


def _header(meta: RunMeta) -> str:
    chips = []
    for bit in (meta.run_date, meta.machine, meta.basis):
        if bit:
            chips.append(f"<span>{html.escape(bit)}</span>")
    chips_html = "".join(chips)
    return f"<h1>TinyTalk CLI Bench</h1>\n<div class=\"meta\">{chips_html}</div>"


def _section(title: str, caption: str, content: str) -> str:
    cap = f"\n<p class=\"caption\">{html.escape(caption)}</p>" if caption else ""
    return f"<section class=\"block\"><h2>{html.escape(title)}</h2>{cap}\n{content}</section>"


def _legend(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    def items(group: list[BackendReport]) -> str:
        return "".join(
            f'<span class="legend-item">'
            f'<span class="legend-dot" style="background:{colors[r.backend]}"></span>'
            f"{html.escape(_label(r))}</span>"
            for r in group
        )

    paid = [r for r in ranked if not r.local]
    local = [r for r in ranked if r.local]
    groups = []
    if paid:
        groups.append(
            f'<span class="legend-group"><span class="legend-group-label">Paid</span>{items(paid)}</span>'
        )
    if local:
        groups.append(
            f'<span class="legend-group"><span class="legend-group-label">Local</span>{items(local)}</span>'
        )
    return f'<div class="legend">{"".join(groups)}</div>'


# --- charts ------------------------------------------------------------------

_W = 800  # shared viewBox width


def _chart_block(svg: str, footer: str = "") -> str:
    return f'<div class="chart">{svg}{footer}</div>'


def _svg(height: int, parts: list[str]) -> str:
    inner = "\n".join(parts)
    return (
        f'<svg viewBox="0 0 {_W} {height}" role="img" '
        f'font-family="system-ui, sans-serif">\n{inner}\n</svg>'
    )


def _text(
    x: float,
    y: float,
    s: str,
    *,
    size=13,
    fill=_INK,
    anchor="start",
    weight="",
    baseline="",
    title="",
) -> str:
    w = f' font-weight="{weight}"' if weight else ""
    bl = f' dominant-baseline="{baseline}"' if baseline else ""
    label = html.escape(s)
    inner = f"<title>{html.escape(title)}</title>{label}" if title else label
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}"{w}{bl}>{inner}</text>'
    )


def _text_vertical(x: float, y: float, s: str, *, size=12, fill=_MUTED) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="middle" '
        f'transform="rotate(-90 {x:.1f} {y:.1f})">{html.escape(s)}</text>'
    )


def _chart_point(
    x: float,
    y: float,
    radius: float,
    color: str,
    label: str,
    title: str,
    *,
    fill_opacity: float = 1.0,
) -> str:
    ly = y - radius - 8
    return (
        f'<g class="chart-point">'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" '
        f'fill-opacity="{fill_opacity}">'
        f"<title>{html.escape(title)}</title></circle>"
        f'<text class="chart-point-label" x="{x:.1f}" y="{ly:.1f}" '
        f'text-anchor="middle" font-size="12" font-weight="600" fill="{_INK}">'
        f"{html.escape(label)}</text>"
        f"</g>"
    )


def _bar_label(
    by: float,
    bar_x: float,
    bw: float,
    pct: float,
    *,
    bar_h: int = 14,
    bar_w: float | None = None,
) -> str:
    """Percentage at the fill edge — aligned with where the bar ends."""
    label = f"{pct:.0f}%"
    cy = by + bar_h / 2 + 4
    edge = bar_x + bw
    inside = bar_w if bar_w is not None else bw
    if bw >= inside * 0.55:
        return _text(edge - 4, cy, label, size=9, fill=_PAPER, anchor="end", weight="600")
    if bw >= inside * 0.3:
        return _text(bar_x + bw / 2, cy, label, size=8, fill=_PAPER, anchor="middle", weight="600")
    return _text(edge + 3, cy, label, size=9, fill=_INK, anchor="start", weight="600")


def _bars_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    name_w, avg_w, lang_w, bar_h, bar_gap = 210, 52, 28, 14, 6
    avg_text_shift = 24  # ~1.5rem — nudge average % left, closer to model names
    row_gap = bar_h + bar_gap
    bars_h = bar_h + bar_gap + bar_h
    avg_x = name_w + 4
    bar_x = name_w + avg_w + 10 + lang_w
    bar_w = _W - bar_x - 8
    row_pad = 18
    row_h = bars_h + row_pad
    bars_top_offset = 0
    parts = []
    for i, r in enumerate(ranked):
        y = i * row_h
        color = colors[r.backend]
        bars_top = y + bars_top_offset
        parts.append(
            _text(0, bars_top + bars_h / 2 + 5, _label(r), size=15, fill=color, weight="600")
        )
        avg_pct = r.strict_pass_pct
        avg_label = f"{avg_pct:.0f}%"
        parts.append(
            _text(
                avg_x + avg_w - 2 - avg_text_shift,
                bars_top + bars_h / 2,
                avg_label,
                size=22,
                fill=color,
                anchor="end",
                weight="600",
                baseline="central",
                title=f"{_label(r)}: {avg_label}",
            )
        )
        for j, (tag, pct, opacity) in enumerate(
            (("EN", r.strict_pass_pct_en, 1.0), ("KO", r.strict_pass_pct_ko, 0.62))
        ):
            by = bars_top + j * row_gap
            bw = max(bar_w * pct / 100.0, 1.5)
            parts.append(
                _text(bar_x - 6, by + bar_h - 3, tag, size=11, fill=_MUTED, anchor="end")
            )
            parts.append(
                f'<rect x="{bar_x}" y="{by}" width="{bar_w:.1f}" height="{bar_h}" '
                f'rx="3" fill="{_RULE}"/>'
            )
            parts.append(
                f'<rect x="{bar_x}" y="{by}" width="{bw:.1f}" height="{bar_h}" rx="3" '
                f'fill="{color}" fill-opacity="{opacity}">'
                f"<title>{html.escape(_label(r))} {tag}: {pct:.0f}%</title></rect>"
            )
            parts.append(_bar_label(by, bar_x, bw, pct, bar_h=bar_h, bar_w=bar_w))
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
    costs = [_chart_cost(r) for r in ranked if _chart_cost(r) > 0]
    if not costs:
        return 0.0001, 1.0
    return min(costs) / 1.8, max(costs) * 1.8


def _fmt_cost(v: float) -> str:
    if v >= 1:
        return f"${v:.2f}"
    if v >= 0.01:
        return f"${v:.3f}"
    return f"${v:.4f}"


# Anthropic standard Sonnet 5 rates from 2026-09-01; bench exports use intro rates ($2/$10).
_SONNET5_BACKEND = "sonnet5-low"
_SONNET5_REGULAR = Price(3.0, 15.0, 0.3, 3.75)


def _sonnet5_regular_cost(r: BackendReport) -> float:
    return sum(
        _cost(
            Usage(
                prompt_tokens=x.prompt_tokens,
                completion_tokens=x.completion_tokens,
                cached_prompt_tokens=x.cached_prompt_tokens,
                cache_write_tokens=x.cache_write_tokens,
            ),
            _SONNET5_REGULAR,
        )
        for x in r.results
    )


def _intro_discount_pct(intro: float, regular: float) -> int:
    if regular <= 0:
        return 0
    return round((1 - intro / regular) * 100)


def _chart_cost(r: BackendReport) -> float:
    """Cost for charts and tie-breaks — Sonnet uses standard (post-intro) rates."""
    if r.backend == _SONNET5_BACKEND:
        return _sonnet5_regular_cost(r)
    return r.total_cost_usd


def _cost_tooltip(r: BackendReport) -> str:
    if r.backend != _SONNET5_BACKEND:
        return _fmt_cost(r.total_cost_usd)
    intro = r.total_cost_usd
    regular = _sonnet5_regular_cost(r)
    pct = _intro_discount_pct(intro, regular)
    return f"{_fmt_cost(regular)} + ({pct}% intro, {_fmt_cost(intro)})"


def _fmt_cost_cell(r: BackendReport) -> str:
    if r.backend != _SONNET5_BACKEND:
        return _fmt_cost(r.total_cost_usd)
    intro = r.total_cost_usd
    regular = _sonnet5_regular_cost(r)
    pct = _intro_discount_pct(intro, regular)
    return (
        f'<span class="cost-main">{_fmt_cost(regular)}</span>'
        f' + <small class="cost-note">({pct}% intro)</small>'
    )


_SCORE_BREAK = 60.0
_SCORE_LOW_FRAC = 0.20  # 0–60% occupies the bottom fifth of the plot height


def _score_py(score: float, mt: int, ph: int) -> float:
    """Piecewise pass-rate axis: 60–100% gets 80% of plot height, 0–60% the bottom 20%."""
    s = max(0.0, min(100.0, score))
    high_frac = 1.0 - _SCORE_LOW_FRAC
    if s >= _SCORE_BREAK:
        return mt + ph * high_frac * (100.0 - s) / (100.0 - _SCORE_BREAK)
    return mt + ph * (high_frac + _SCORE_LOW_FRAC * (_SCORE_BREAK - s) / _SCORE_BREAK)


def _score_cost_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    height, ml, mr, mt, mb = 420, 56, 30, 16, 46
    pw, ph = _W - ml - mr, height - mt - mb
    lo, hi = _cost_axis(ranked)

    def px(cost: float) -> float:
        c = max(cost, lo)
        return ml + pw * (math.log10(c) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def py(score: float) -> float:
        return _score_py(score, mt, ph)

    parts = [_axes_frame(ml, mt, pw, ph)]
    for s in (60, 70, 80, 90, 100):
        parts.append(f'<line x1="{ml}" y1="{py(s):.1f}" x2="{ml + pw}" y2="{py(s):.1f}" stroke="{_RULE}"/>')
        parts.append(_text(ml - 8, py(s) + 4, f"{s}%", size=11, fill=_MUTED, anchor="end"))
    y_break = py(_SCORE_BREAK)
    parts.append(
        f'<line x1="{ml - 5}" y1="{y_break - 4:.1f}" x2="{ml + 5}" y2="{y_break + 4:.1f}" '
        f'stroke="{_MUTED}" stroke-width="1.2"/>'
    )
    for t in _ticks_125(lo, hi):
        parts.append(_text(px(t), mt + ph + 18, _fmt_cost(t), size=11, fill=_MUTED, anchor="middle"))
    parts.append(_text(ml + pw / 2, height - 8, "cost per full sweep (USD, log scale)", size=12,
                       fill=_MUTED, anchor="middle"))

    frontier, best = [], -1.0
    for r in sorted(ranked, key=lambda r: (max(_chart_cost(r), lo), -r.strict_pass_pct)):
        if r.strict_pass_pct > best:
            frontier.append(r)
            best = r.strict_pass_pct
    if len(frontier) > 1:
        d = f"M {px(_chart_cost(frontier[0])):.1f} {py(frontier[0].strict_pass_pct):.1f}"
        for prev, cur in zip(frontier, frontier[1:]):
            d += (
                f" H {px(_chart_cost(cur)):.1f}"
                f" V {py(cur.strict_pass_pct):.1f}"
            )
        parts.append(
            f'<path d="{d}" fill="none" stroke="{_ACCENT}" stroke-width="1.5" '
            'stroke-dasharray="5 4" opacity="0.85"/>'
        )
        last = frontier[-1]
        parts.append(
            _text(
                px(_chart_cost(last)) + 10,
                py(last.strict_pass_pct) - 6,
                "Pareto frontier",
                size=11,
                fill=_ACCENT,
            )
        )

    for r in ranked:
        x, y = px(_chart_cost(r)), py(r.strict_pass_pct)
        parts.append(
            _chart_point(
                x,
                y,
                7,
                colors[r.backend],
                _label(r),
                f"{_label(r)}: {r.strict_pass_pct:.0f}% at {_cost_tooltip(r)}",
            )
        )
    parts.append(_text_vertical(14, mt + ph / 2, "pass rate (%)"))
    return _svg(height, parts)


def _speed_cost_svg(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    height, ml, mr, mt, mb = 420, 56, 30, 16, 46
    pw, ph = _W - ml - mr, height - mt - mb
    lo, hi = _cost_axis(ranked)
    lat_hi = 20.0

    def px(cost: float) -> float:
        c = max(cost, lo)
        return ml + pw * (math.log10(c) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def py(lat: float) -> float:
        return mt + ph * (1 - lat / lat_hi)

    parts = [_axes_frame(ml, mt, pw, ph)]
    for v in (0, 10, 20):
        parts.append(f'<line x1="{ml}" y1="{py(v):.1f}" x2="{ml + pw}" y2="{py(v):.1f}" stroke="{_RULE}"/>')
        parts.append(_text(ml - 8, py(v) + 4, f"{v:g}s", size=11, fill=_MUTED, anchor="end"))
    for t in _ticks_125(lo, hi):
        parts.append(_text(px(t), mt + ph + 18, _fmt_cost(t), size=11, fill=_MUTED, anchor="middle"))
    parts.append(_text(ml + pw / 2, height - 8, "cost per full sweep (USD, log scale)", size=12,
                       fill=_MUTED, anchor="middle"))
    parts.append(_text_vertical(14, mt + ph / 2, "time to finish (seconds)"))

    for r in ranked:
        x, y = px(_chart_cost(r)), py(r.median_latency_s)
        radius = 6 + 16 * math.sqrt(max(r.strict_pass_pct, 0) / 100.0)
        parts.append(
            _chart_point(
                x,
                y,
                radius,
                colors[r.backend],
                _label(r),
                f"{_label(r)}: {r.median_latency_s:.2f}s median, "
                f"{r.strict_pass_pct:.0f}% pass, {_cost_tooltip(r)}",
                fill_opacity=0.75,
            )
        )
    return _svg(height, parts)


def _axes_frame(ml: int, mt: int, pw: int, ph: int) -> str:
    return (
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="{_MUTED}"/>'
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="{_MUTED}"/>'
    )


# --- table & fine print ------------------------------------------------------

_COLUMNS = (
    ("Model", "model"),
    ("Pass", "pass"),
    ("EN", "en"),
    ("KO", "ko"),
    ("Assert", "assert"),
    ("Format", "format"),
    ("Parse", "parses"),
    ("Danger label", "danger"),
    ("Tok in", "tokens"),
    ("Tok out", "tokens"),
    ("Cached", "tokens"),
    ("p50", "latency"),
    ("Cost", "cost"),
)


_COL_COLS = {
    "model": "col-model",
    "pass": "col-pct",
    "en": "col-lang",
    "ko": "col-lang",
    "assert": "col-pct",
    "format": "col-pct",
    "parses": "col-pct",
    "danger": "col-pct",
    "tokens": "col-tokens",
    "latency": "col-latency",
    "cost": "col-cost",
}


def _table_colgroup() -> str:
    cols = "".join(f'<col class="{_COL_COLS[cls]}">' for _, cls in _COLUMNS)
    return f"<colgroup>{cols}</colgroup>"


_COLUMN_TIPS: dict[str, str] = {
    "danger": (
        "How often the model tagged the command safe, caution, or destructive as the "
        "suite expects. Scored separately from strict pass."
    ),
}


def _table_head() -> str:
    cells = []
    for label, cls in _COLUMNS:
        text = html.escape(label)
        if cls == "model":
            inner = (
                '<div class="model-row"><span class="model-swatch" aria-hidden="true"></span>'
                f"<span>{text}</span></div>"
            )
        else:
            inner = text
        tip = _COLUMN_TIPS.get(cls, "")
        tip_attr = f' title="{html.escape(tip)}"' if tip else ""
        cells.append(f'<th scope="col" class="{cls}"{tip_attr}>{inner}</th>')
    return "".join(cells)


def _table(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    head = _table_head()
    rows = []
    best = ranked[0].backend if ranked else ""
    for r in ranked:
        name, subtitle = _display(r)
        if r.local:
            name = f"{name}†"
        kind = "local" if r.local else "paid"
        swatch = colors[r.backend]
        tok_in = sum(x.prompt_tokens for x in r.results)
        tok_out = sum(x.completion_tokens for x in r.results)
        cached = sum(x.cached_prompt_tokens for x in r.results)
        pass_cls = "pass best" if r.backend == best else "pass"
        cells = [
            (
                f'<td class="model"><div class="model-row">'
                f'<span class="model-swatch" style="background:{swatch}"></span>'
                f"<div><strong>{html.escape(name)}</strong>"
                f"<small>{html.escape(subtitle)}</small></div></div></td>"
            ),
            f'<td class="{pass_cls}">{r.strict_pass_pct:.0f}%</td>',
            f'<td class="en">{r.strict_pass_pct_en:.0f}%</td>',
            f'<td class="ko">{r.strict_pass_pct_ko:.0f}%</td>',
            f'<td class="assert">{r.assertions_pct:.0f}%</td>',
            f'<td class="format">{r.format_ok_pct:.0f}%</td>',
            f'<td class="parses">{r.parses_pct:.0f}%</td>',
            f'<td class="danger">{r.danger_pct:.0f}%</td>',
            f'<td class="tokens">{tok_in:,}</td>',
            f'<td class="tokens">{tok_out:,}</td>',
            f'<td class="tokens">{cached:,}</td>',
            f'<td class="latency">{r.median_latency_s:.2f}s</td>',
            f'<td class="cost">{_fmt_cost_cell(r)}</td>',
        ]
        rows.append(f'<tr class="{kind}">' + "".join(cells) + "</tr>")
    return (
        f'<div class="tablewrap"><table>{_table_colgroup()}<thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _target_label(target: str) -> str:
    return target.replace("-", " ")


def _format_expected(prompt: EvalPrompt) -> str:
    checks = " · ".join(prompt.assertions)
    return f"→ {checks} · {prompt.expected_danger}"


def _target_passed(index: dict[str, PromptResult], prompts: list[EvalPrompt]) -> bool:
    return all(
        (row := index.get(p.id)) is not None and BackendReport._strict(row)
        for p in prompts
    )


def _suite_outcomes(
    prompts: list[EvalPrompt],
    reports: list[BackendReport],
    colors: dict[str, str],
) -> str:
    passed, failed = [], []
    for report in reports:
        index = {row.prompt_id: row for row in report.results}
        (passed if _target_passed(index, prompts) else failed).append(report)

    def names(group: list[BackendReport]) -> str:
        return ", ".join(
            f'<span class="suite-outcome-name" style="color:{colors[r.backend]}">'
            f"{html.escape(_label(r))}</span>"
            for r in group
        )

    lines = []
    if passed:
        lines.append(f"<p><strong>Passed</strong>{names(passed)}</p>")
    if failed:
        lines.append(f"<p><strong>Failed</strong>{names(failed)}</p>")
    return f'<div class="suite-outcomes">{"".join(lines)}</div>'


def _suite_prompt_row(prompt: EvalPrompt) -> str:
    return (
        f'<div class="suite-row">'
        f'<span class="suite-lang">{html.escape(prompt.lang.upper())}</span>'
        f'<div><p class="suite-prompt">{html.escape(prompt.text)}</p>'
        f'<p class="suite-expected">{html.escape(_format_expected(prompt))}</p></div>'
        f"</div>"
    )


def _suite_section(ranked: list[BackendReport], colors: dict[str, str]) -> str:
    by_target: dict[str, list[EvalPrompt]] = {}
    order: list[str] = []
    for prompt in SUITE:
        if prompt.target not in by_target:
            order.append(prompt.target)
            by_target[prompt.target] = []
        by_target[prompt.target].append(prompt)

    items = []
    for target in order:
        pair = sorted(by_target[target], key=lambda p: p.lang)
        n_pass = sum(
            1
            for report in ranked
            if _target_passed({row.prompt_id: row for row in report.results}, pair)
        )
        score = f"{n_pass}/{len(ranked)} models" if ranked else ""
        body = _suite_outcomes(pair, ranked, colors) + "".join(
            _suite_prompt_row(p) for p in pair
        )
        items.append(
            f'<details class="suite-item"><summary>'
            f'<span class="suite-caret suite-caret-closed" aria-hidden="true">▼</span>'
            f'<span class="suite-caret suite-caret-open" aria-hidden="true">▲</span>'
            f'<span class="suite-title">{html.escape(_target_label(target))}</span>'
            f'<span class="suite-score">{html.escape(score)}</span>'
            f"</summary>"
            f'<div class="suite-body">{body}</div></details>'
        )
    return f'<div class="suite">{"".join(items)}</div>'


def _fine_print(meta: RunMeta, any_local: bool) -> str:
    items = [f"Protocol: {meta.protocol}"]
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
