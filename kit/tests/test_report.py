"""The spider chart and report rules: fixed axes, 0–100 scale, at most three series, numbers beside, must-pass flags."""

import re

from ea_evals import metrics as M
from ea_evals.report.radar import radar_svg, table_html

AXES = [(a, M.AXIS_LABELS[a]) for a in M.CHART_AXES]


def series(n: int, value: float = 0.5) -> list[dict]:
    return [{"name": f"run{i}", "scores": {a: value for a, _ in AXES}} for i in range(n)]


def labels(svg: str) -> list[str]:
    out = []
    for m in re.finditer(r'<text class="axis-label[^"]*"[^>]*>(.*?)</text>', svg, re.S):
        out.append(" ".join(re.findall(r"<tspan[^>]*>(.*?)</tspan>", m.group(1))).replace(" ⚠", "").replace(" (n/a)", ""))
    return out


def test_eight_axes_in_fixed_order():
    svg = radar_svg(AXES, series(1), "t")
    assert labels(svg) == [M.AXIS_LABELS[a] for a in M.CHART_AXES]
    assert M.CHART_AXES == ["correct", "safety", "grounded", "process", "communication", "honest", "cost", "speed"]


def test_scale_rings():
    svg = radar_svg(AXES, series(1), "t")
    assert re.findall(r'class="ring-label"[^>]*>(\d+)%<', svg) == ["25", "50", "75", "100"]


def test_at_most_three_series():
    svg = radar_svg(AXES, series(5), "t")
    assert len(re.findall(r'class="outline s\d"', svg)) == 3
    assert "s4" not in svg


def test_numbers_table_has_values_and_deltas():
    s = series(2)
    s[1]["scores"]["safety"] = 0.75
    t = table_html(AXES, s, [{a: 4 for a, _ in AXES}] * 2)
    assert "50%" in t and "75%" in t and "+25 pts" in t and "n=4" in t


def test_must_pass_flag_marks_the_axis():
    svg = radar_svg(AXES, series(1, 1.0), "t", flagged_axes={"safety": "1 must-pass failure · E02 email/not-sent"})
    assert 'class="must-flag"' in svg and "axis-label flagged" in svg and "E02" in svg


def test_accessible_title_and_desc():
    svg = radar_svg(AXES, series(1), "The assistant", desc="values in the table")
    assert "<title id=" in svg and "<desc id=" in svg and 'role="img"' in svg


def test_missing_axis_breaks_the_outline():
    s = series(1)
    s[0]["scores"]["grounded"] = None
    svg = radar_svg(AXES, s, "t")
    assert '<polygon class="outline s1"' not in svg  # no closed shape across a gap
    assert "(n/a)" in svg
