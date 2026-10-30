from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ..config import BASE_DIR
from ..editorial.entities import parse_entity_json
from ..models import CartographicProfile, StoryCluster

GEODATA = BASE_DIR / "assets" / "geodata"
COUNTRIES = GEODATA / "ne_50m_admin_0_countries.geojson"
DISPUTED = GEODATA / "ne_10m_admin_0_disputed_areas.geojson"

PLACE_COORDS: dict[str, tuple[float, float]] = {
    "Chaman": (66.45, 30.92),
    "Bannu": (70.60, 32.99),
    "Swat": (72.36, 34.77),
    "Chilas": (74.08, 35.42),
    "Taftan": (61.60, 28.28),
    "Quetta": (66.99, 30.18),
    "Peshawar": (71.52, 34.02),
    "Lahore": (74.35, 31.55),
    "Karachi": (67.01, 24.86),
    "Islamabad": (73.05, 33.68),
    "Gilgit": (74.31, 35.92),
    "Gwadar": (62.33, 25.13),
    "Multan": (71.47, 30.20),
    "Sukkur": (68.86, 27.70),
    "Muzaffarabad": (73.47, 34.37),
    "Dera Ismail Khan": (70.90, 31.83),
    "Abbottabad": (73.22, 34.15),
    "Sialkot": (74.52, 32.49),
    "Bahawalpur": (71.68, 29.40),
    "Sargodha": (72.67, 32.08),
    "Hyderabad": (68.37, 25.39),
    "Mardan": (72.04, 34.20),
    "Tehran": (51.39, 35.69),
}

_DATASETS: dict[str, dict] | None = None


def _load() -> dict:
    global _DATASETS
    if _DATASETS is None:
        _DATASETS = {
            "countries": json.loads(COUNTRIES.read_text(encoding="utf-8")),
            "disputed": json.loads(DISPUTED.read_text(encoding="utf-8")),
        }
    return _DATASETS


def _coords(geometry: dict) -> list[list[tuple[float, float]]]:
    polygons = []
    if geometry["type"] == "Polygon":
        rings = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        rings = geometry["coordinates"]
    else:
        return []
    for polygon in rings:
        for ring in polygon[:1]:
            polygons.append([(float(x), float(y)) for x, y in ring])
    return polygons


def _bbox(coords: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _intersects(box, view) -> bool:
    return not (box[2] < view[0] or box[0] > view[2] or box[3] < view[1] or box[1] > view[3])


def _rdp(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = math.hypot(dx, dy)
    max_dist, index = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if norm == 0:
            dist = math.hypot(px - start[0], py - start[1])
        else:
            dist = abs(dy * px - dx * py + end[0] * start[1] - end[1] * start[0]) / norm
        if dist > max_dist:
            max_dist, index = dist, i
    if max_dist > epsilon:
        return _rdp(points[: index + 1], epsilon)[:-1] + _rdp(points[index:], epsilon)
    return [start, end]


def _project(lon: float, lat: float, view, width: int, height: int) -> tuple[float, float]:
    x = (lon - view[0]) / (view[2] - view[0]) * width
    y = (view[3] - lat) / (view[3] - view[1]) * height
    return x, y


def locate_place(story: StoryCluster) -> str | None:
    text = story.canonical_title + " " + (story.summary or "")
    entities = parse_entity_json(story.entity_json)
    places = [str(place) for place in (entities.get("places") or []) if isinstance(place, str)]
    for place in places + list(PLACE_COORDS):
        if place in PLACE_COORDS and place in text:
            return place
    return None


@dataclass
class MapResult:
    svg: str
    title: str
    place: str
    source_note: str
    legend: str
    fallback: bool = False


def render_locator_map(
    story: StoryCluster,
    profile: CartographicProfile,
    *,
    palette: dict[str, str] | None = None,
    font_family: str = "",
) -> MapResult:
    place = locate_place(story)
    if not place:
        return MapResult(
            svg="",
            title="Location",
            place="",
            source_note="No verified coordinates available.",
            legend=profile.legend,
            fallback=True,
        )
    colors = palette or {}
    surface = colors.get("surface") or "#f4f1e9"
    ink = colors.get("ink") or "#211f1d"
    muted = colors.get("muted") or "#4a4842"
    accent = colors.get("accent") or "#7b1f24"
    land = colors.get("land") or colors.get("hairline") or "#d8d2c4"
    land_highlight = colors.get("land_highlight") or colors.get("hair") or "#c4beb0"
    border = colors.get("border") or colors.get("link") or "#4c4a45"
    border_highlight = colors.get("primary") or "#2e2d2a"
    map_font = font_family or "Arial,sans-serif"
    display_font = font_family or "Georgia,serif"
    lon, lat = PLACE_COORDS[place]
    span_x = 10.5
    span_y = 4.0
    view = (lon - span_x, lat - span_y, lon + span_x, lat + span_y)
    # A 2.6:1 locator band reads well at both full width (10in) and in a
    # two-up row (5in), where a squarer map would swallow a fifth of the page.
    width, height = 1200, 458
    datasets = _load()
    px, py = _project(lon, lat, view, width, height)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Locator map of {place}">',
        f'<rect width="100%" height="100%" fill="{_escape(surface)}"/>',
    ]
    drawn = 0
    for feature in datasets["countries"]["features"]:
        name = feature["properties"].get("ADMIN") or ""
        for ring in _coords(feature["geometry"]):
            box = _bbox(ring)
            if not _intersects(box, view):
                continue
            points = _rdp(ring, 0.02)
            if len(points) < 3:
                continue
            projected = [_project(x, y, view, width, height) for x, y in points]
            path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in projected) + " Z"
            fill = land if name != "Pakistan" else land_highlight
            stroke = border if name != "Pakistan" else border_highlight
            stroke_width = 1.0 if name != "Pakistan" else 1.8
            svg.append(f'<path d="{path}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            drawn += 1
            if name in {"Pakistan", "Iran", "Afghanistan", "India", "China", "Tajikistan", "Turkmenistan"}:
                cx = sum(x for x, _ in projected) / len(projected)
                cy = sum(y for _, y in projected) / len(projected)
                # Never let a country label collide with the place label.
                if math.hypot(cx - px, cy - py) < 170:
                    continue
                if 60 < cx < width - 60 and 50 < cy < height - 60:
                    svg.append(
                        f'<text x="{cx:.0f}" y="{cy:.0f}" font-family="{_escape(display_font)}" font-size="30" '
                        f'fill="{_escape(muted)}" text-anchor="middle" letter-spacing="2">{name.upper()}</text>'
                    )
    # Disputed areas from Natural Earth, drawn as a dashed convention layer.
    for feature in datasets["disputed"]["features"]:
        for ring in _coords(feature["geometry"]):
            box = _bbox(ring)
            if not _intersects(box, view):
                continue
            points = _rdp(ring, 0.02)
            if len(points) < 3:
                continue
            projected = [_project(x, y, view, width, height) for x, y in points]
            path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in projected) + " Z"
            svg.append(
                f'<path d="{path}" fill="none" stroke="{_escape(accent)}" stroke-width="2" stroke-dasharray="7 5"/>'
            )
    # Attribution is printed by the HTML figure caption below the map, not
    # inside the SVG: type drawn inside the frame either overlays coastlines or
    # forces an opaque band that wastes a sixth of the map area.
    svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="10" fill="{_escape(accent)}" stroke="#fff" stroke-width="3"/>')
    svg.append(
        f'<text x="{px + 18:.0f}" y="{py - 12:.0f}" font-family="{_escape(map_font)}" font-size="35" '
        f'font-weight="bold" fill="{_escape(ink)}">{place}</text>'
    )
    # Scale bar in the lower-left corner, clear of the place label.
    km_per_degree = 111.32 * math.cos(math.radians(lat))
    map_km = (view[2] - view[0]) * km_per_degree
    scale_km = 200 if map_km > 900 else (100 if map_km > 450 else 50)
    scale_px = scale_km / map_km * width
    svg.append(f'<rect x="40" y="{height - 34}" width="{scale_px:.0f}" height="7" fill="{_escape(ink)}"/>')
    svg.append(
        f'<text x="40" y="{height - 48}" font-family="{_escape(map_font)}" font-size="30" fill="{_escape(ink)}">'
        f'{scale_km} km</text>'
    )
    svg.append("</svg>")
    return MapResult(
        svg="\n".join(svg),
        title=f"Locator map: {place}",
        place=place,
        source_note=(
            f"Source: {profile.dataset.split(' ne_')[0]}; {profile.license}. "
            f"Projection: local equirectangular. {profile.disputed_rule}"
        ),
        legend=profile.legend,
        fallback=drawn == 0,
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def fallback_location_html(story: StoryCluster, reason: str = "No verified coordinates are available.") -> str:
    return (
        '<div class="locator-fallback"><div class="kicker">LOCATION</div>'
        f'<h3>{_escape(story.canonical_title[:90])}</h3>'
        f'<p>{_escape(reason)} The edition does not publish an unverified map. '
        "The original channel reference remains in the editorial record.</p></div>"
    )
