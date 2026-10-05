"""Spider (radar) charts as inline SVG, no JavaScript library.

Rules (spec/09 "Spider charts", dataviz skill): axes always in the same order on a 0–100% scale with
rings at 25/50/75/100; at most three runs overlaid, colours from the validated palette's first three
slots (all-pairs CVD-safe) with dashes and marker shapes as a second channel; a numbers table beside
every chart; must-pass failures flagged on the Safety axis. Colours come from CSS variables, so the
same SVG works in light and dark themes.
"""

import html
import math
from typing import Any

MAX_SERIES = 3
SERIES_DASH = ["", "7 4", "2 4"]
SERIES_MARKER = ["circle", "square", "diamond"]


def _pt(cx: float, cy: float, r: float, i: int, n: int) -> tuple[float, float]:
    a = -math.pi / 2 + 2 * math.pi * i / n
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _marker(kind: str, x: float, y: float, cls: str, title: str) -> str:
    t = f"<title>{html.escape(title)}</title>"
    if kind == "square":
        return f'<rect class="{cls}" x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" rx="1.5">{t}</rect>'
    if kind == "diamond":
        return f'<path class="{cls}" d="M{x:.1f} {y - 5.5:.1f} L{x + 5.5:.1f} {y:.1f} L{x:.1f} {y + 5.5:.1f} L{x - 5.5:.1f} {y:.1f} Z">{t}</path>'
    return f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="4.5">{t}</circle>'


def radar_svg(
    axes: list[tuple[str, str]],
    series: list[dict[str, Any]],
    title: str,
    desc: str = "",
    flagged_axes: dict[str, str] | None = None,
    size: int = 420,
    chart_id: str = "radar",
) -> str:
    """`axes`: [(key, label)]; `series`: [{"name", "scores": {key: 0..1 or None}}] (at most three)."""
    series = series[:MAX_SERIES]
    flagged_axes = flagged_axes or {}
    n = len(axes)
    pad_x, pad_y = 128, 50
    w, h = size + 2 * pad_x, size + 2 * pad_y
    cx, cy, r = w / 2, h / 2, size / 2 - 8
    out = [
        f'<svg class="radar" id="{chart_id}" viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-labelledby="{chart_id}-t {chart_id}-d" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{chart_id}-t">{html.escape(title)}</title>',
        f'<desc id="{chart_id}-d">{html.escape(desc or "Spider chart; the table beside it lists every value.")}</desc>',
    ]
    # rings and spokes (recessive)
    for pct in (25, 50, 75, 100):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (_pt(cx, cy, r * pct / 100, i, n) for i in range(n)))
        out.append(f'<polygon class="ring{" ring-outer" if pct == 100 else ""}" points="{pts}"/>')
        lx, ly = _pt(cx, cy, r * pct / 100, 0, n)
        out.append(f'<text class="ring-label" x="{lx + 10:.1f}" y="{ly + 12:.1f}">{pct}%</text>')
    for i in range(n):
        x, y = _pt(cx, cy, r, i, n)
        out.append(f'<line class="spoke" x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}"/>')
    # axis labels (two lines when long); "n/a" when no series has data on the axis
    for i, (key, label) in enumerate(axes):
        x, y = _pt(cx, cy, r + 22, i, n)
        anchor = "middle" if abs(x - cx) < 8 else ("start" if x > cx else "end")
        flag = flagged_axes.get(key)
        empty = all((s.get("scores") or {}).get(key) is None for s in series)
        words = label.split()
        lines = [label] if len(label) <= 13 or len(words) == 1 else [" ".join(words[: len(words) // 2 + len(words) % 2]), " ".join(words[len(words) // 2 + len(words) % 2 :])]
        if flag:
            lines[-1] += " ⚠"
        if empty:
            lines.append("(n/a)")
        top = y < cy - r * 0.7
        bottom = y > cy + r * 0.7
        y0 = y - 6 - 14 * (len(lines) - 1) if top else (y + 14 if bottom else y + 4 - 7 * (len(lines) - 1))
        cls = "axis-label flagged" if flag else ("axis-label empty" if empty else "axis-label")
        spans = "".join(f'<tspan x="{x:.1f}" dy="{0 if k == 0 else 14}">{html.escape(t)}</tspan>' for k, t in enumerate(lines))
        tip = f"<title>{html.escape(flag)}</title>" if flag else ""
        out.append(f'<text class="{cls}" x="{x:.1f}" y="{y0:.1f}" text-anchor="{anchor}">{spans}{tip}</text>')
        if flag:
            mx, my = _pt(cx, cy, r, i, n)
            out.append(f'<circle class="must-flag" cx="{mx:.1f}" cy="{my:.1f}" r="7"><title>{html.escape(flag)}</title></circle>')
    # series
    for s_i, s in enumerate(series):
        cls = f"s{s_i + 1}"
        pts = []
        for i, (key, label) in enumerate(axes):
            v = (s.get("scores") or {}).get(key)
            if v is None:
                continue
            x, y = _pt(cx, cy, r * max(0.0, min(1.0, v)), i, n)
            pts.append((x, y, key, label, v))
        dash = f' stroke-dasharray="{SERIES_DASH[s_i]}"' if SERIES_DASH[s_i] else ""
        complete = len(pts) == n
        if complete:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, *_ in pts)
            out.append(f'<polygon class="area {cls}" points="{poly}"/>')
            out.append(f'<polygon class="outline {cls}" points="{poly}"{dash}/>')
        else:
            # A missing axis breaks the outline: never join across an axis with no data.
            idx = {key: j for j, (key, _) in enumerate(axes)}
            have = {idx[p[2]]: p for p in pts}
            for j in range(n):
                a, b = have.get(j), have.get((j + 1) % n)
                if a and b:
                    out.append(f'<line class="outline {cls}" x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}"{dash}/>')
        for x, y, key, label, v in pts:
            out.append(_marker(SERIES_MARKER[s_i], x, y, f"pt {cls}", f"{s.get('name', '')} · {label}: {v * 100:.0f}%"))
    out.append("</svg>")
    return "\n".join(out)


def legend_html(series: list[dict[str, Any]]) -> str:
    items = []
    for i, s in enumerate(series[:MAX_SERIES]):
        shape = SERIES_MARKER[i]
        dash = f' stroke-dasharray="{SERIES_DASH[i]}"' if SERIES_DASH[i] else ""
        sw = (f'<svg width="34" height="12" aria-hidden="true"><line class="outline s{i + 1}" x1="1" y1="6" x2="33" y2="6"{dash}/>'
              + _marker(shape, 17, 6, f"pt s{i + 1}", "") + "</svg>")
        items.append(f'<li>{sw}<span>{html.escape(s.get("name", ""))}</span></li>')
    return f'<ul class="legend">{"".join(items)}</ul>'


def table_html(axes: list[tuple[str, str]], series: list[dict[str, Any]], counts: list[dict[str, int]] | None = None) -> str:
    """Exact values, trial counts and deltas against the first series."""
    series = series[:MAX_SERIES]
    head = "".join(f"<th scope='col'>{html.escape(s.get('name', ''))}</th>" for s in series)
    if len(series) > 1:
        head += "".join(f"<th scope='col'>Δ {html.escape(s.get('name', ''))}</th>" for s in series[1:])
    rows = []
    for key, label in axes:
        cells = []
        base = (series[0].get("scores") or {}).get(key) if series else None
        for j, s in enumerate(series):
            v = (s.get("scores") or {}).get(key)
            n = (counts[j] if counts and j < len(counts) else {}).get(key) if counts else None
            cells.append(f"<td>{'—' if v is None else f'{v * 100:.0f}%'}{f' <span class=n>n={n}</span>' if n is not None else ''}</td>")
        for s in series[1:]:
            v = (s.get("scores") or {}).get(key)
            if v is None or base is None:
                cells.append("<td>—</td>")
            else:
                d = (v - base) * 100
                cells.append(f"<td class='{'up' if d > 0 else 'down' if d < 0 else ''}'>{d:+.0f} pts</td>")
        rows.append(f"<tr><th scope='row'>{html.escape(label)}</th>{''.join(cells)}</tr>")
    return f"<table class='nums'><thead><tr><th scope='col'>Axis</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


RADAR_CSS = """
.radar { width: 100%; max-width: 560px; height: auto; display: block; }
.radar .ring { fill: none; stroke: var(--grid); stroke-width: 1; }
.radar .ring-outer { stroke: var(--axis); }
.radar .spoke { stroke: var(--grid); stroke-width: 1; }
.radar .ring-label { fill: var(--muted); font-size: 10px; }
.radar .axis-label { fill: var(--ink-2); font-size: 12.5px; }
.radar .axis-label.flagged { fill: var(--critical-ink); font-weight: 600; }
.radar .axis-label.empty { fill: var(--muted); }
.radar .must-flag { fill: none; stroke: var(--critical); stroke-width: 2.5; }
.radar .area { stroke: none; opacity: .10; }
.radar .outline { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.radar .pt { stroke: var(--surface); stroke-width: 2; }
.radar .pt:hover { stroke: var(--ink); }
.radar .s1, .legend .s1 { --c: var(--series-1); }
.radar .s2, .legend .s2 { --c: var(--series-2); }
.radar .s3, .legend .s3 { --c: var(--series-3); }
.radar .area, .radar .pt, .legend .pt { fill: var(--c); }
.radar .outline, .legend .outline { stroke: var(--c); }
.legend .outline { fill: none; stroke-width: 2; }
.legend .pt { stroke: var(--surface); stroke-width: 1.5; }
.legend { list-style: none; display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 0; margin: 4px 0 8px; font-size: 13px; color: var(--ink-2); }
.legend li { display: flex; align-items: center; gap: 6px; }
"""
