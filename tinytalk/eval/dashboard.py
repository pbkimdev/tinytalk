"""Render a bench run as an IBM Carbon-styled HTML dashboard — data-driven, re-runnable.

Reads a run directory's ``results.json`` (+ optional ``stability/*.json``), runs the
analysis layer (:mod:`tinytalk.eval.analyze`), and writes a self-contained
``dashboard.html`` styled with the IBM Carbon Design System (IBM Plex type, Carbon
gray themes, Carbon data-viz palette, square/flat components). No hand-authored
numbers: every figure comes from the recorded run, so a new sweep re-renders as-is.

    tt eval dashboard [data_dir] [--run-date YYYY-MM-DD] [--watch]

``--watch`` re-renders whenever the inputs change and injects a meta-refresh so an
open page updates itself — a page that keeps rendering.
"""

from __future__ import annotations

import argparse
import html
import sys
import time
from collections import Counter
from pathlib import Path

from tinytalk.eval.analyze import Analysis, BackendAnalysis, analyze
from tinytalk.eval.publish import resolve_paths
from tinytalk.eval.report import load_reports
from tinytalk.eval.runner import BackendReport

# Carbon data-viz: cloud/cool = cyan, local/warm = orange; status = green/gray/red.
# Palette validated colorblind-safe (see dataviz validator) in both Carbon gray themes.
_CSS = """
:root{
  --plex-sans:'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
  --plex-mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace;
  --bg:#ffffff; --layer:#f4f4f4; --layer-2:#ffffff; --field:#f4f4f4;
  --text:#161616; --text-2:#525252; --text-3:#6f6f6f; --on-color:#ffffff;
  --border:#e0e0e0; --border-strong:#8d8d8d; --link:#0f62fe;
  --s-cloud:#1192e8; --s-local:#6929c4;
  --pass:#24a148; --intent:#8d8d8d; --drop:#da1e28;
  --track:#e0e0e0;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#161616; --layer:#262626; --layer-2:#393939; --field:#262626;
  --text:#f4f4f4; --text-2:#c6c6c6; --text-3:#a8a8a8;
  --border:#393939; --border-strong:#6f6f6f; --link:#4589ff;
  --s-cloud:#33b1ff; --s-local:#8a3ffc;
  --pass:#42be65; --intent:#8d8d8d; --drop:#fa4d56;
  --track:#393939;
}}
:root[data-theme="white"]{
  --bg:#ffffff; --layer:#f4f4f4; --layer-2:#ffffff; --field:#f4f4f4;
  --text:#161616; --text-2:#525252; --text-3:#6f6f6f;
  --border:#e0e0e0; --border-strong:#8d8d8d; --link:#0f62fe;
  --s-cloud:#1192e8; --s-local:#6929c4; --pass:#24a148; --intent:#8d8d8d; --drop:#da1e28; --track:#e0e0e0;
}
:root[data-theme="g100"]{
  --bg:#161616; --layer:#262626; --layer-2:#393939; --field:#262626;
  --text:#f4f4f4; --text-2:#c6c6c6; --text-3:#a8a8a8;
  --border:#393939; --border-strong:#6f6f6f; --link:#4589ff;
  --s-cloud:#33b1ff; --s-local:#8a3ffc; --pass:#42be65; --intent:#8d8d8d; --drop:#fa4d56; --track:#393939;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--plex-sans);
  font-size:14px;line-height:1.4;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
.mono{font-family:var(--plex-mono)}
.wrap{max-width:1056px;margin:0 auto;padding:48px 16px 96px}
a{color:var(--link)}

/* header */
.topbar{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);
  padding-bottom:12px;margin-bottom:40px}
.brand{font-family:var(--plex-mono);font-size:12px;letter-spacing:.32px;color:var(--text-2);text-transform:uppercase}
.brand b{color:var(--text);font-weight:600}
.toggle{font-family:var(--plex-sans);font-size:12px;color:var(--text-2);background:none;
  border:1px solid var(--border-strong);padding:6px 12px;cursor:pointer}
.toggle:hover{background:var(--layer)}
.toggle:focus-visible{outline:2px solid var(--link);outline-offset:1px}
h1{font-size:clamp(26px,3.6vw,34px);font-weight:600;letter-spacing:-.32px;margin:0;line-height:1.1;text-wrap:balance}
.sub{color:var(--text-2);font-size:15px;max-width:68ch;margin:14px 0 0}
.meta{display:flex;flex-wrap:wrap;gap:6px 20px;margin-top:20px;font-family:var(--plex-mono);
  font-size:12px;color:var(--text-3)}
.meta b{color:var(--text-2);font-weight:500}

/* tiles */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);margin-top:32px;border:1px solid var(--border)}
@media (max-width:640px){.grid2{grid-template-columns:1fr}}
.tile{background:var(--layer);padding:24px}
.tile .role{font-family:var(--plex-mono);font-size:12px;letter-spacing:.32px;text-transform:uppercase;color:var(--text-3)}
.tile .name{font-size:16px;font-weight:600;margin-top:4px}
.tile .kind{display:inline-block;margin-left:8px;font-family:var(--plex-mono);font-size:11px;
  padding:1px 8px;border-radius:12px;color:var(--k);border:1px solid var(--k);vertical-align:middle}
.tile .big{font-family:var(--plex-mono);font-size:46px;font-weight:600;letter-spacing:-1px;line-height:1;
  margin-top:18px;color:var(--k)}
.tile .big small{font-size:18px;color:var(--text-3);font-weight:400;letter-spacing:0}
.tile .foot{font-family:var(--plex-mono);font-size:12px;color:var(--text-3);margin-top:10px}

/* sections */
section{margin-top:56px}
.eyebrow{font-family:var(--plex-mono);font-size:12px;letter-spacing:.32px;text-transform:uppercase;
  color:var(--link);font-weight:500}
h2{font-size:20px;font-weight:600;margin:8px 0 0;letter-spacing:-.16px}
.cap{color:var(--text-2);max-width:74ch;margin:10px 0 24px}
.panel{background:var(--layer);border:1px solid var(--border);padding:clamp(20px,3vw,28px)}

/* bar rows (square, Carbon) */
.rows{display:flex;flex-direction:column;gap:14px}
.row{display:grid;grid-template-columns:132px 1fr;gap:16px;align-items:center}
@media (max-width:560px){.row{grid-template-columns:96px 1fr;gap:10px}}
.rlab{font-family:var(--plex-mono);font-size:13px;color:var(--text-2);text-align:right;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.track{position:relative;height:20px;background:var(--track)}
.fill{position:absolute;left:0;top:0;bottom:0;background:var(--k);display:flex;align-items:center;
  justify-content:flex-end;padding-right:8px}
.fill .v{font-family:var(--plex-mono);font-size:12px;font-weight:600;color:var(--on-color)}
.grpwrap{display:flex;flex-direction:column;gap:5px}
.gbar{position:relative;height:14px;background:var(--track)}
.gbar .gf{position:absolute;left:0;top:0;bottom:0;background:var(--k)}
.gbar .gv{position:absolute;right:6px;top:50%;transform:translateY(-50%);font-family:var(--plex-mono);
  font-size:10px;font-weight:600;color:var(--text-2)}

/* legend + tags */
.legend{display:flex;flex-wrap:wrap;gap:12px 20px;margin-top:20px;font-family:var(--plex-mono);
  font-size:12px;color:var(--text-2)}
.legend span{display:inline-flex;align-items:center;gap:7px}
.sw{width:12px;height:12px;flex:none}
.sw.hollow{background:transparent;border:1px solid var(--border-strong)}
.tag{display:inline-flex;align-items:center;gap:7px;font-family:var(--plex-mono);font-size:12px;
  background:var(--field);border-radius:14px;padding:4px 12px;color:var(--text-2)}
.tag .d{width:8px;height:8px;flex:none}
.tags{display:flex;flex-wrap:wrap;gap:8px}

/* stability range plot */
.rangeplot{display:flex;flex-direction:column;gap:14px}
.rline,.raxis{display:grid;grid-template-columns:104px 1fr 74px;gap:16px;align-items:center}
@media (max-width:560px){.rline,.raxis{grid-template-columns:72px 1fr 64px;gap:10px}}
.rlab{font-family:var(--plex-mono);font-size:13px;color:var(--k);font-weight:600;text-align:right;white-space:nowrap}
.rtrack{position:relative;height:22px}
.rtrack .axisline{position:absolute;left:0;right:0;top:50%;height:2px;background:var(--track)}
.rband{position:absolute;top:50%;transform:translateY(-50%);height:8px;background:var(--k);opacity:.28}
.rdot{position:absolute;top:50%;width:12px;height:12px;background:var(--k);
  border:2px solid var(--layer);transform:translate(-50%,-50%);box-shadow:0 0 0 1px var(--k)}
.rval{font-family:var(--plex-mono);font-size:12.5px;color:var(--k);font-weight:600;white-space:nowrap}
.rt{position:relative;height:16px}
.rtk{position:absolute;transform:translateX(-50%);font-family:var(--plex-mono);font-size:10.5px;color:var(--text-3);top:2px}
.rtk::before{content:"";position:absolute;left:50%;top:-6px;width:1px;height:5px;background:var(--border-strong)}
.rcap{font-family:var(--plex-mono);font-size:10px;color:var(--text-3);letter-spacing:.4px;text-transform:uppercase}

/* flip stat cards */
.flip{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border);margin-top:24px}
@media (max-width:560px){.flip{grid-template-columns:1fr}}
.flipc{background:var(--layer);padding:20px 22px}
.flipc .k{font-family:var(--plex-mono);font-size:12px;letter-spacing:.32px;text-transform:uppercase;color:var(--text-3)}
.flipc .pair{display:flex;align-items:baseline;gap:18px;margin-top:14px}
.flipc .col{display:flex;flex-direction:column}
.flipc .col b{font-family:var(--plex-mono);font-size:34px;font-weight:600;line-height:1;letter-spacing:-1px}
.flipc .col .who{font-family:var(--plex-mono);font-size:11px;color:var(--text-3);margin-top:5px}
.flipc .vs{font-family:var(--plex-mono);font-size:12px;color:var(--text-3)}
.flipc .note{font-size:13px;color:var(--text-2);margin-top:14px;line-height:1.45}

/* stacked layer bar */
.stackrow{margin-bottom:18px}
.stackrow .who{display:flex;justify-content:space-between;font-family:var(--plex-mono);font-size:13px;
  color:var(--text-2);margin-bottom:7px}
.stackrow .who b{color:var(--k);font-weight:600}
.stack{display:flex;height:32px;gap:1px;background:var(--border)}
.seg{display:flex;align-items:center;justify-content:center;color:var(--on-color);font-family:var(--plex-mono);
  font-size:12px;font-weight:600;min-width:2px;overflow:hidden;white-space:nowrap}

/* two-up */
.two{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border)}
@media (max-width:640px){.two{grid-template-columns:1fr}}
.two .panel{border:none}
.microbars{display:flex;flex-direction:column;gap:12px}
.mb{display:grid;grid-template-columns:104px 1fr;gap:12px;align-items:center}
.mb .ml{font-family:var(--plex-mono);font-size:12px;color:var(--text-2);text-align:right}

/* category */
.cat{display:flex;flex-direction:column;gap:16px}
.catrow{display:grid;grid-template-columns:148px 1fr;gap:16px;align-items:center}
@media (max-width:560px){.catrow{grid-template-columns:112px 1fr}}
.catrow .cl{font-family:var(--plex-mono);font-size:12.5px;color:var(--text-2);text-align:right}

/* callout + table + footer */
.callout{border-left:3px solid var(--link);padding:2px 0 2px 16px;margin-top:22px;color:var(--text)}
.callout b{font-weight:600}
details{margin-top:24px}
summary{cursor:pointer;font-family:var(--plex-mono);font-size:12px;color:var(--text-2)}
.tblwrap{overflow-x:auto;margin-top:12px}
table{border-collapse:collapse;width:100%;font-family:var(--plex-mono);font-size:12px}
th,td{text-align:right;padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{color:var(--text-3);font-weight:500;text-transform:uppercase;letter-spacing:.32px;
  background:var(--layer);border-bottom:1px solid var(--border-strong)}
tbody tr:hover{background:var(--layer)}
footer{margin-top:64px;border-top:1px solid var(--border);padding-top:20px;color:var(--text-3);
  font-family:var(--plex-mono);font-size:12px;line-height:1.7}
footer b{color:var(--text-2);font-weight:500}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600'
    '&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">'
)

_KNOWN = {
    "sonnet5-low": ("Claude Sonnet 5", "Agent SDK · low effort"),
    "gpt55-low": ("GPT-5.5", "Codex SDK · low effort"),
    "local-gemma4-26b": ("Gemma 4 26B A4B", "oMLX · 8-bit"),
    "local-gemma4-e4b": ("Gemma 4 E4B", "MLX · 4-bit"),
    "local-qwen36-35b": ("Qwen 3.6 35B A3B", "MTP"),
    "local-gemma4-12b-qat": ("Gemma 4 12B QAT", "oMLX · 4-bit · assistant MTP"),
    "local-gemma4-12b-8bit": ("Gemma 4 12B", "oMLX · 8-bit · assistant MTP"),
}

_SHORT = {
    "sonnet5-low": "Sonnet 5", "gpt55-low": "GPT-5.5",
    "local-gemma4-26b": "Gemma 26B", "local-gemma4-e4b": "Gemma E4B",
    "local-qwen36-35b": "Qwen 35B", "local-gemma4-12b-qat": "Gemma 12B",
    "local-gemma4-12b-8bit": "Gemma 12B 8b",
}


def _esc(s: object) -> str:
    return html.escape(str(s))


def _name(ba: BackendAnalysis) -> tuple[str, str]:
    if ba.backend in _KNOWN:
        return _KNOWN[ba.backend]
    return ba.model.replace("--", " · ").replace("-", " "), ba.backend


def _short(ba: BackendAnalysis) -> str:
    return _SHORT.get(ba.backend, _name(ba)[0])


def _color(ba: BackendAnalysis) -> str:
    return "var(--s-local)" if ba.local else "var(--s-cloud)"


def _tile(ba: BackendAnalysis) -> str:
    name, sub = _name(ba)
    kind = "local · $0" if ba.local else "cloud"
    return f"""<div class="tile" style="--k:{_color(ba)}">
  <div class="role">{_esc(sub)}</div>
  <div class="name">{_esc(name)}<span class="kind" style="--k:{_color(ba)}">{_esc(kind)}</span></div>
  <div class="big">{ba.strict_pass_pct:.0f}<small>% strict</small></div>
  <div class="foot">EN {ba.slices.strict_en:.0f} · KO {ba.slices.strict_ko:.0f} &nbsp;·&nbsp; delivery {ba.delivery.rate:.0f}%</div>
</div>"""


def _scores(backends: list[BackendAnalysis]) -> str:
    rows = "".join(
        f"""<div class="row"><div class="rlab">{_esc(_name(b)[0])}</div>
      <div class="track"><div class="fill" style="--k:{_color(b)};width:{b.strict_pass_pct:.0f}%">
        <span class="v">{b.strict_pass_pct:.0f}%</span></div></div></div>"""
        for b in sorted(backends, key=lambda b: -b.strict_pass_pct)
    )
    return f"""<section><div class="eyebrow">Strict pass</div>
  <h2>Overall score</h2>
  <p class="cap">A command passes strict when it parses, every binary and flag is real, and it does
    what the request asked — checked deterministically, never executed.</p>
  <div class="panel"><div class="rows">{rows}</div></div></section>"""


_LAYER_SPEC = [
    ("pass", "pass", "var(--pass)"),
    ("intent", "wrong approach", "var(--intent)"),
    ("binaries", "missing binary", "#b28600"),
    ("parses", "won't parse", "var(--drop)"),
    ("delivered", "dropped", "var(--drop)"),
]


def _layers(backends: list[BackendAnalysis]) -> str:
    present: list[tuple[str, str, str]] = [
        s for s in _LAYER_SPEC if any(b.layers.first_failing.get(s[0], 0) for b in backends)
    ]
    rows = []
    for b in backends:
        ff = b.layers.first_failing
        segs = []
        for key, label, col in present:
            c = ff.get(key, 0)
            if not c:
                continue
            pct = 100 * c / b.n
            txt = f"{pct:.0f}%" if key == "pass" else (f"{c} {label}" if pct >= 8 else str(c))
            segs.append(
                f'<div class="seg" style="background:{col};flex:{pct} 1 0" '
                f'data-tip="{_esc(label)}: {c}/{b.n}">{_esc(txt)}</div>'
            )
        misses = ", ".join(f"{k} {v}" for k, v in ff.items() if k != "pass")
        rows.append(
            f"""<div class="stackrow" style="--k:{_color(b)}">
    <div class="who"><b>{_esc(_name(b)[0])}</b><span>{_esc(misses) or "no misses"}</span></div>
    <div class="stack">{''.join(segs)}</div></div>"""
        )
    legend = "".join(
        f'<span><span class="sw" style="background:{col}"></span>{_esc(label)}</span>'
        for key, label, col in present
    )
    return f"""<section><div class="eyebrow">First failing gate</div>
  <h2>Anatomy of a miss</h2>
  <p class="cap">Each prompt's first failing gate, out of {backends[0].n}. A dropped answer (the model
    garbled its own output) is a different problem from a wrong approach.</p>
  <div class="panel">{''.join(rows)}<div class="legend">{legend}</div></div></section>"""


def _delivery(backends: list[BackendAnalysis]) -> str:
    # spotlight the backend with the most dropped answers
    worst = max(backends, key=lambda b: sum(b.delivery.faults.values()), default=None)
    if worst is None or not worst.delivery.faults:
        return ""
    fault_color = {"unescaped_backslash": "var(--drop)", "malformed_json": "var(--intent)"}
    tags = "".join(
        f'<span class="tag"><span class="d" style="background:{fault_color.get(k, "var(--drop)")}"></span>'
        f'{_esc(k.replace("_", " "))} · {v}</span>'
        for k, v in sorted(worst.delivery.faults.items())
    )
    mb = []
    for b in backends:
        eh = b.slices.escape_heavy["delivery"]
        rest = b.slices.rest["delivery"]
        col = _color(b)
        crit = "var(--drop)" if eh < 90 else col
        mb.append(
            f'<div class="mb"><div class="ml">{_esc(_short(b))} · esc-heavy</div>'
            f'<div class="track"><div class="fill" style="--k:{crit};width:{eh:.1f}%">'
            f'<span class="v">{eh:.0f}%</span></div></div></div>'
            f'<div class="mb"><div class="ml">{_esc(_short(b))} · rest</div>'
            f'<div class="track"><div class="fill" style="--k:{col};width:{rest:.1f}%">'
            f'<span class="v">{rest:.0f}%</span></div></div></div>'
        )
    return f"""<section><div class="eyebrow">Delivery</div>
  <h2>Where the answers get dropped</h2>
  <p class="cap">Delivery rate = a runnable command came back at all. Every dropped answer is a
    malformed structured output; they concentrate on one linguistic property.</p>
  <div class="two">
    <div class="panel"><div class="eyebrow" style="color:var(--text-3);margin-bottom:14px">{_esc(_name(worst)[0])} · dropped-answer causes</div>
      <div class="tags">{tags}</div>
      <p class="cap" style="margin:16px 0 0;font-size:13px">Regex / <span class="mono">sed</span> escapes
        (<span class="mono">\\b \\. \\d</span>) break the JSON the command is wrapped in — repairable in
        the parser, no retrain needed.</p></div>
    <div class="panel"><div class="eyebrow" style="color:var(--text-3);margin-bottom:16px">Delivery: escape-heavy vs the rest</div>
      <div class="microbars">{''.join(mb)}</div>
      <p class="cap" style="margin:16px 0 0;font-size:13px">Falsifiable: fix backslash-in-JSON and
        escape-heavy delivery should recover toward 100%.</p></div>
  </div></section>"""


def _category(backends: list[BackendAnalysis]) -> str:
    cats = sorted({c for b in backends for c in b.slices.by_category})
    rows = []
    for cat in cats:
        bars = "".join(
            f'<div class="gbar"><div class="gf" style="background:{_color(b)};'
            f'width:{b.slices.by_category.get(cat, {}).get("strict", 0):.1f}%"></div>'
            f'<span class="gv">{b.slices.by_category.get(cat, {}).get("strict", 0):.0f}%</span></div>'
            for b in backends
        )
        rows.append(
            f'<div class="catrow"><div class="cl">{_esc(cat)}</div><div class="grpwrap">{bars}</div></div>'
        )
    legend = "".join(
        f'<span><span class="sw" style="background:{_color(b)}"></span>{_esc(_name(b)[0])}</span>'
        for b in backends
    )
    return f"""<section><div class="eyebrow">By capability</div>
  <h2>Strict pass per task family</h2>
  <p class="cap">Where each backend is strong and where it needs work.</p>
  <div class="panel"><div class="cat">{''.join(rows)}</div>
    <div class="legend">{legend}</div></div></section>"""


def _axis_scale(vals: list[float]) -> tuple[float, float]:
    lo = (int(min(vals)) - 2) // 4 * 4 if vals else 0
    return max(0, lo), 100.0


def _stability(analysis: Analysis) -> str:
    if not analysis.stability:
        return ""
    by_backend = {b.backend: b for b in analysis.backends}
    vals = [v for st in analysis.stability for v in st.per_run_strict]
    lo, hi = _axis_scale(vals)

    def pos(v: float) -> float:
        return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))

    lines, flip_backends = [], []
    for st in analysis.stability:
        ba = by_backend.get(st.backend)
        col = _color(ba) if ba else "var(--s-cloud)"
        name = _short(ba) if ba else st.backend
        band = ""
        if st.max_strict > st.min_strict:
            band = (f'<div class="rband" style="left:{pos(st.min_strict):.1f}%;'
                    f'width:{pos(st.max_strict) - pos(st.min_strict):.1f}%"></div>')
        # One dot per DISTINCT observed value (identical runs collapse to one point — a tight
        # cluster is deterministic, not a fake spread); the count rides in the tooltip.
        dots = "".join(
            f'<div class="rdot" style="left:{pos(v):.1f}%" '
            f'data-tip="{_esc(name)} — {v:.0f}%{f" ×{c}" if c > 1 else ""}"></div>'
            for v, c in sorted(Counter(st.per_run_strict).items())
        )
        rng = f"{st.min_strict:.0f}" if st.min_strict == st.max_strict else f"{st.min_strict:.0f}–{st.max_strict:.0f}"
        lines.append(
            f'<div class="rline" style="--k:{col}"><div class="rlab">{_esc(name)}</div>'
            f'<div class="rtrack"><div class="axisline"></div>{band}{dots}</div>'
            f'<div class="rval">{rng}</div></div>'
        )
        flip_backends.append((name, col, st))

    ticks = "".join(
        f'<span class="rtk" style="left:{i * 25}%">{lo + (hi - lo) * i / 4:.0f}</span>' for i in range(5)
    )
    legend = "".join(
        f'<span><span class="sw" style="background:{_color(b)}"></span>{_esc(_short(b))} · '
        f'{next((st.n_runs for st in analysis.stability if st.backend == b.backend), 0)} runs</span>'
        for b in analysis.backends if any(st.backend == b.backend for st in analysis.stability)
    )

    def flip_card(title: str, attr: str, note: str) -> str:
        cols = "".join(
            f'<div class="col"><b style="color:{col}">{getattr(st, attr):.0f}%</b>'
            f'<span class="who">{_esc(name)}</span></div>'
            + ("" if i == len(flip_backends) - 1 else '<span class="vs">vs</span>')
            for i, (name, col, st) in enumerate(flip_backends)
        )
        return (f'<div class="flipc"><div class="k">{_esc(title)}</div>'
                f'<div class="pair">{cols}</div><div class="note">{note}</div></div>')

    return f"""<section><div class="eyebrow">Reproducibility</div>
  <h2>Ask the same thing twice</h2>
  <p class="cap">Both backends run at temperature 0. Re-run the whole suite N times and watch what
    changes — the finding a single leaderboard number hides.</p>
  <div class="panel">
    <div class="rangeplot">{''.join(lines)}
      <div class="raxis"><div></div><div class="rt">{ticks}</div><div class="rcap">strict&nbsp;%</div></div>
    </div>
    <div class="legend">{legend}<span>&#9679; one run &nbsp; &#9644; [min–max]</span></div>
    <div class="flip">
      {flip_card("Command rewrite rate", "command_flip_rate", "Share of prompts whose exact command text changed run-to-run.")}
      {flip_card("Verdict flip rate", "flip_rate", "Share whose pass/fail actually flipped. The rewrite–flip gap is harmless drift.")}
    </div>
    <p class="callout"><b>Not a temperature setting</b> — temp 0 is already greedy. Local greedy +
      lossless-MTP decode is deterministic; hosted batched-MoE serving is not, at any temperature.
      Trust one local run; average several hosted ones.</p>
  </div></section>"""


def _table(backends: list[BackendAnalysis]) -> str:
    heads = "".join(f"<th>{_esc(_name(b)[0])}</th>" for b in backends)
    cats = sorted({c for b in backends for c in b.slices.by_category})

    def r(label: str, fn) -> str:
        return "<tr><td>" + _esc(label) + "</td>" + "".join(f"<td>{fn(b)}</td>" for b in backends) + "</tr>"

    body = [
        r("strict pass", lambda b: f"{b.strict_pass_pct:.0f}%"),
        r("EN / KO", lambda b: f"{b.slices.strict_en:.0f} / {b.slices.strict_ko:.0f}"),
        r("delivery rate", lambda b: f"{b.delivery.rate:.0f}%"),
        r("dropped answers", lambda b: str(sum(b.delivery.faults.values()))),
        r("escape-heavy delivery", lambda b: f"{b.slices.escape_heavy['delivery']:.0f}%"),
    ]
    body += [r(f"cat · {c}", (lambda c: lambda b: f"{b.slices.by_category.get(c, {}).get('strict', 0):.0f}%")(c)) for c in cats]
    return (f'<details><summary>&#9656; data table (all figures)</summary><div class="tblwrap"><table>'
            f'<thead><tr><th>metric</th>{heads}</tr></thead><tbody>{"".join(body)}</tbody></table></div></details>')


def render_dashboard(analysis: Analysis, *, watch: bool = False) -> str:
    backends = analysis.backends
    date = analysis.run_date or ""
    names = " vs. ".join(_name(b)[0] for b in backends)
    n = backends[0].n if backends else 0
    refresh = '<meta http-equiv="refresh" content="2">' if watch else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TinyTalk CLI Bench &middot; {_esc(date)}</title>{refresh}
{_FONTS}
<style>{_CSS}</style>
</head><body>
<div class="wrap">
  <div class="topbar">
    <div class="brand"><b>TinyTalk</b> · CLI bench · natural language &rarr; shell</div>
    <button class="toggle" id="tg" aria-label="Toggle theme">Theme</button>
  </div>
  <h1>{_esc(names)}</h1>
  <p class="sub">A {n}-prompt run — {n // 2} tasks in English and Korean — scoring whether plain language
    becomes a correct, runnable shell command. Beyond the headline scores: <em>how</em> each backend
    misses, and how much it moves across repeated runs.</p>
  <div class="meta"><span><b>{_esc(date)}</b></span><span>Apple M5 Max</span>
    <span>temperature 0</span><span>validation-only · never executed</span>
    <span>rule/assertion-based (not an LLM judge)</span></div>
  <div class="grid2">{''.join(_tile(b) for b in backends)}</div>
  {_scores(backends)}
  {_stability(analysis)}
  {_layers(backends)}
  {_delivery(backends)}
  {_category(backends)}
  {_table(backends)}
  <footer>
    <b>Method.</b> {n // 2} golden targets &times; English/Korean = {n} prompts. Strict pass = parses +
    real binaries/flags + intent assertions; nothing executed. Temperature 0; repeated runs power the
    stability metric.<br>
    <b>Rendered</b> from the run's <span class="mono">results.json</span> by
    <span class="mono">tt eval dashboard</span> &mdash; IBM Carbon Design System.
  </footer>
</div>
<div id="tip" role="status" style="position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;
  background:var(--text);color:var(--bg);font-family:var(--plex-mono);font-size:11.5px;padding:5px 9px;
  z-index:9;max-width:280px"></div>
<script>
(function(){{
  var root=document.documentElement,btn=document.getElementById('tg');
  function cur(){{var t=root.getAttribute('data-theme');return t||(matchMedia('(prefers-color-scheme:dark)').matches?'g100':'white');}}
  btn.addEventListener('click',function(){{root.setAttribute('data-theme',cur()==='g100'?'white':'g100');}});
  var tip=document.getElementById('tip');
  document.addEventListener('pointermove',function(e){{
    var t=e.target.closest('[data-tip]');
    if(!t){{tip.style.opacity=0;return;}}
    tip.textContent=t.getAttribute('data-tip');
    tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';
    tip.style.top=(e.clientY+16)+'px';tip.style.opacity=1;}});
}})();
</script>
</body></html>"""


def _load(data_dir: Path, runs_glob: str | None, min_n: int) -> Analysis:
    import glob as _glob

    reports = load_reports(data_dir / "results.json")
    pattern = runs_glob or str(data_dir / "stability" / "*.json")
    runs: dict[str, list[BackendReport]] = {}
    for path in sorted(_glob.glob(pattern)):
        for rep in load_reports(Path(path)):
            runs.setdefault(rep.backend, []).append(rep)
    resolved = resolve_paths(data_dir, None)[1]
    return analyze(reports, runs, run_date=resolved, min_n=min_n)


def _inputs(data_dir: Path, runs_glob: str | None) -> list[Path]:
    import glob as _glob

    pattern = runs_glob or str(data_dir / "stability" / "*.json")
    return [data_dir / "results.json", *(Path(p) for p in _glob.glob(pattern))]


def _mtimes(paths: list[Path]) -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in paths if p.exists()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tt eval dashboard",
        description="Render a bench run as an IBM Carbon HTML dashboard (data-driven, read-only).",
    )
    parser.add_argument("data_dir", nargs="?", type=Path, help="run dir (default docs/bench/<date>)")
    parser.add_argument("--run-date", metavar="YYYY-MM-DD", help="run date when data_dir omitted")
    parser.add_argument("--out", metavar="PATH", help="output path (default <data_dir>/dashboard.html)")
    parser.add_argument("--runs", metavar="GLOB", help="stability run exports (default <dir>/stability/*.json)")
    parser.add_argument("--min-n", type=int, default=3, help="min repeats for a stability row (default 3)")
    parser.add_argument("--watch", action="store_true", help="re-render on input change; page self-refreshes")
    parser.add_argument("--interval", type=float, default=1.0, help="--watch poll seconds (default 1.0)")
    args = parser.parse_args(argv)

    try:
        data_dir, _ = resolve_paths(args.data_dir, args.run_date)
        out = Path(args.out) if args.out else data_dir / "dashboard.html"

        def build() -> None:
            analysis = _load(data_dir, args.runs, args.min_n)
            out.write_text(render_dashboard(analysis, watch=args.watch), "utf-8")

        build()
    except (OSError, ValueError, KeyError) as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 1

    print(f"rendered {out}")
    if not args.watch:
        return 0

    print(f"watching {data_dir} (Ctrl-C to stop) — page auto-refreshes every 2s", file=sys.stderr)
    last = _mtimes(_inputs(data_dir, args.runs))
    try:
        while True:
            time.sleep(max(0.2, args.interval))
            now = _mtimes(_inputs(data_dir, args.runs))
            if now != last:
                last = now
                try:
                    build()
                    print(f"re-rendered {out}", file=sys.stderr)
                except (OSError, ValueError, KeyError) as exc:
                    print(f"dashboard: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
