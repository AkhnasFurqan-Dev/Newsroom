from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import StoryClaim, StoryTimelineEvent
from ..timeutil import to_local


@dataclass
class ChartPoint:
    label: str
    value: float
    unit: str
    claim_id: int
    source_span: str


@dataclass
class ChartSpec:
    title: str
    unit: str
    points: list[ChartPoint]
    source_note: str
    width: int = 1000
    height: int = 560

    def validate(self) -> None:
        if len(self.points) < 2:
            raise ValueError("A chart requires at least two sourced points.")
        for point in self.points:
            if point.unit != self.unit:
                raise ValueError(f"Mixed units in chart: {point.unit!r} vs {self.unit!r}.")
            if f"{point.value:g}" not in point.source_span.replace(",", ""):
                raise ValueError(f"Value {point.value!r} is not present in its source claim.")
            if not point.claim_id:
                raise ValueError("Every chart point must cite a claim.")
        if not self.source_note.strip():
            raise ValueError("A chart requires a source note.")


LABEL_PATTERNS = [
    ("Policy rate", r"policy rate"),
    ("Inflation", r"inflation"),
    ("Growth", r"\bgrowth\b"),
    ("Unemployment", r"unemployment"),
    ("Trade", r"\btrade\b"),
]


def build_percentage_chart(claims: list[StoryClaim], title: str = "Figures cited in the report") -> ChartSpec | None:
    points: list[ChartPoint] = []
    for claim in claims:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:percent|per cent|%)", claim.text)
        if not match:
            continue
        value = float(match.group(1))
        label = None
        for candidate, pattern in LABEL_PATTERNS:
            if re.search(pattern, claim.text, re.I):
                label = candidate
                break
        if label is None:
            continue
        if any(p.label == label for p in points):
            continue
        points.append(
            ChartPoint(
                label=label,
                value=value,
                unit="percent",
                claim_id=claim.id,
                source_span=claim.text,
            )
        )
    if len(points) < 2:
        return None
    spec = ChartSpec(
        title=title,
        unit="percent",
        points=points[:5],
        source_note="Values are percentages stated in the cited source claims; no interpolation or estimate has been added.",
    )
    spec.validate()
    return spec


def render_chart_svg(
    spec: ChartSpec,
    *,
    palette: dict[str, str] | None = None,
    font_family: str = "",
) -> str:
    spec.validate()
    colors = palette or {}
    paper = colors.get("surface") or "#f4f1e9"
    ink = colors.get("ink") or "#211f1d"
    muted = colors.get("muted") or "#5b5852"
    accent = colors.get("accent") or "#7b1f24"
    grid = colors.get("hairline") or "#d3cec3"
    display = font_family or "Georgia,serif"
    caption = font_family or "Arial,sans-serif"
    width, height = spec.width, spec.height
    margin_left, margin_right, margin_top, margin_bottom = 150, 60, 110, 110
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    max_value = max(point.value for point in spec.points)
    axis_max = max(10.0, max_value * 1.25)
    bar_gap = 26
    bar_width = max(40, (plot_width - bar_gap * (len(spec.points) - 1)) / len(spec.points))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_escape(spec.title)}">',
        f'<rect width="100%" height="100%" fill="{_escape(paper)}"/>',
        f'<text x="{margin_left}" y="52" font-family="{_escape(display)}" font-size="30" fill="{_escape(ink)}">{_escape(spec.title)}</text>',
        f'<text x="{margin_left}" y="82" font-family="{_escape(caption)}" font-size="18" fill="{_escape(muted)}">Unit: {_escape(spec.unit)}</text>',
    ]
    for tick in range(0, 6):
        value = axis_max * tick / 5
        y = margin_top + plot_height - (value / axis_max) * plot_height
        parts.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="{_escape(grid)}"/>')
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 6:.1f}" text-anchor="end" font-family="{_escape(caption)}" '
            f'font-size="17" fill="{_escape(muted)}">{value:g}</text>'
        )
    for index, point in enumerate(spec.points):
        bar_height = point.value / axis_max * plot_height
        x = margin_left + index * (bar_width + bar_gap)
        y = margin_top + plot_height - bar_height
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{_escape(accent)}"/>')
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 12:.1f}" text-anchor="middle" '
            f'font-family="{_escape(display)}" font-size="24" fill="{_escape(ink)}">{point.value:g}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - margin_bottom + 30}" text-anchor="middle" '
            f'font-family="{_escape(caption)}" font-size="19" fill="{_escape(ink)}">{_escape(point.label)}</text>'
        )
    parts.append(
        f'<text x="{margin_left}" y="{height - 38}" font-family="{_escape(caption)}" font-size="15" fill="{_escape(muted)}">'
        f'{_escape(spec.source_note)}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def render_timeline_html(
    events: list[StoryTimelineEvent], timezone: str, *, max_events: int | None = None
) -> str:
    """Render a clean printed chronology.

    Internal certainty labels (``reported``, ``attributed``) and raw channel
    message references are deliberately not printed: they are audit vocabulary,
    not newspaper copy. Decorative bulletin furniture is stripped from each
    description, and near-identical repeated entries are collapsed.
    """
    from ..editorial.chronology import distinct_entries

    entries = distinct_entries(events)
    if len(entries) < 2:
        return ""
    shown = entries[:max_events] if max_events else entries
    rows = []
    for entry in shown:
        local = to_local(entry.when, timezone)
        stamp = local.strftime("%d %b, %H:%M") if local else ""
        rows.append(
            f'<li><span class="timeline-time">{_escape(stamp)}</span>'
            f'<span class="timeline-text">{_escape(entry.text)}</span></li>'
        )
    html = '<ol class="timeline">' + "".join(rows) + "</ol>"
    if max_events and len(entries) > max_events:
        # Print space is finite. Never imply the printed list is complete.
        html += (
            f'<p class="timeline-more">Earlier developments are summarised in the '
            f"report above.</p>"
        )
    return html


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
