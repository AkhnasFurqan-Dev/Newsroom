from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from channel_press.config import BASE_DIR, settings
from channel_press.graphics.cartography import render_locator_map
from channel_press.graphics.charts import (
    ChartPoint,
    ChartSpec,
    build_percentage_chart,
    render_chart_svg,
    render_timeline_html,
)
from channel_press.media.service import (
    extract_video_frames,
    image_metrics,
    make_collage,
    make_derivative,
    rank_media,
    select_lead_media,
)
from channel_press.models import PostMedia, StoryCluster, StoryTimelineEvent
from channel_press.ai.providers import FixtureDraftProvider
from channel_press.editorial.clustering import build_stories
from channel_press.editorial.drafting import draft_story

FIXTURE_MEDIA = BASE_DIR / "fixtures" / "media"


def test_image_derivative_keeps_original_and_records_adjustments(tmp_path):
    source = FIXTURE_MEDIA / "chaman_vehicle.png"
    before = source.read_bytes()
    metrics = image_metrics(source)
    assert metrics["width"] and metrics["height"]
    target = make_derivative(source, tmp_path, "lead.jpg", crop_ratio=1.5)
    assert target.exists()
    assert source.read_bytes() == before
    with Image.open(target) as image:
        assert abs(image.width / image.height - 1.5) < 0.02
    metadata = json.loads(Path(str(target) + ".json").read_text(encoding="utf-8"))
    assert metadata["source_sha256"]
    assert metadata["content_generation"] == "none"
    assert metadata["adjustments"]


def test_video_frame_extraction_records_traceable_stills(db, demo_channel, sample_import):
    media = db.query(PostMedia).filter(PostMedia.kind == "video").first()
    assert media is not None
    frames = extract_video_frames(media, max_frames=2)
    assert 1 <= len(frames) <= 2
    for frame in frames:
        assert Path(frame["path"]).exists()
        assert frame["sha256"]
        assert ":" in frame["timestamp_label"]
        assert frame["seconds"] >= 0


def test_media_ranking_and_lead_selection_use_source_media(db, demo_channel, sample_import):
    build_stories(db, demo_channel.id, datetime(2026, 10, 18, tzinfo=UTC), datetime(2026, 10, 22, tzinfo=UTC))
    story = db.query(StoryCluster).filter(StoryCluster.canonical_title.contains("Chaman")).first()
    assert story is not None
    choices = rank_media(db, story)
    assert choices
    assert all(choice.media.kind in {"photo", "video", "document"} for choice in choices)
    assert any("fixture" in " ".join(choice.reasons).lower() for choice in choices)

    article = draft_story(db, story, FixtureDraftProvider())
    db.commit()
    selected = select_lead_media(db, story, article.id, settings.media_dir)
    assert selected is not None
    assert Path(selected.media.original_path).exists()
    from channel_press.models import ArticleMedia

    record = db.query(ArticleMedia).filter_by(article_id=article.id, role="lead").one()
    assert Path(record.path).exists()
    assert record.checksum
    assert record.graphic_kind in {"photo", "video_still"}


def test_collage_combines_labelled_images(tmp_path):
    first = make_derivative(FIXTURE_MEDIA / "chaman_scene_a.png", tmp_path, "a.jpg")
    second = make_derivative(FIXTURE_MEDIA / "chaman_scene_b.png", tmp_path, "b.jpg")
    target = make_collage(
        [{"path": str(first), "credit": "Fixture plate A"}, {"path": str(second), "credit": "Fixture plate B"}],
        tmp_path / "collage.jpg",
        width=1000,
    )
    assert target.exists()
    with Image.open(target) as image:
        assert image.width == 1000
        assert image.height > 300


def test_locator_map_draws_real_geometry_with_profile_disclosure(db):
    from channel_press.bootstrap import ensure_defaults

    ensure_defaults(db)
    db.commit()
    from channel_press.models import CartographicProfile

    neutral = db.query(CartographicProfile).filter_by(key="neutral").one()
    story = StoryCluster(
        canonical_title="Chaman border crossing remains closed after bombing",
        summary="Traffic backed up on the Pakistani side of the Chaman crossing.",
        entity_json=json.dumps({"places": ["Chaman"]}),
    )
    result = render_locator_map(story, neutral)
    assert result.fallback is False
    assert result.place == "Chaman"
    assert "<svg" in result.svg and "<path" in result.svg
    # Attribution is printed by the figure caption, never drawn over the map.
    assert "Natural Earth" in result.source_note
    assert "Natural Earth" not in result.svg
    assert "disputed" in result.source_note.lower()
    assert "Chaman" in result.svg

    unknown = StoryCluster(canonical_title="A report without a known place", summary="")
    missing = render_locator_map(unknown, neutral)
    assert missing.fallback is True
    assert missing.svg == ""


def test_charts_require_sourced_matching_values_and_render_svg():
    claims = []
    for index, (label, value) in enumerate([("Policy rate", "19.5"), ("Inflation", "23.4")], start=1):
        claim = type("Claim", (), {})()
        claim.id = index
        claim.text = f"The {label.lower()} was {value} percent, according to the central bank."
        claims.append(claim)
    assert all(claim.id for claim in claims)
    spec = build_percentage_chart(claims)
    assert spec is not None
    assert spec.unit == "percent"
    assert len(spec.points) == 2
    svg = render_chart_svg(spec)
    assert "<svg" in svg and "Policy rate" in svg
    assert "no interpolation" in svg

    bad = ChartSpec(
        title="Bad chart",
        unit="percent",
        points=[
            ChartPoint(label="A", value=99.0, unit="percent", claim_id=1, source_span="Value was 98 percent."),
            ChartPoint(label="B", value=10.0, unit="percent", claim_id=2, source_span="Value was 10 percent."),
        ],
        source_note="source",
    )
    try:
        render_chart_svg(bad)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("chart accepted a value absent from its source claim")


def test_timeline_is_ordered_and_escaped():
    events = [
        StoryTimelineEvent(
            event_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
            description="The second inspection found <script> damage.",
            certainty="reported",
        ),
        StoryTimelineEvent(
            event_at=datetime(2026, 10, 20, 6, 0, tzinfo=UTC),
            description="The first inspection closed the market.",
            certainty="attributed",
        ),
    ]
    html = render_timeline_html(events, "Asia/Karachi")
    assert html.index("first inspection") < html.index("second inspection")
    assert "&lt;script&gt;" in html
    assert "20 Oct" in html


def test_timeline_cap_states_the_omitted_count():
    events = [
        StoryTimelineEvent(
            event_at=datetime(2026, 10, 20, hour, 0, tzinfo=UTC),
            description=f"The relief operation issued update {hour} for the district.",
            certainty="reported",
        )
        for hour in range(1, 10)
    ]
    html = render_timeline_html(events, "Asia/Karachi", max_events=3)
    assert html.count("<li>") == 3
    assert "update 1" in html and "update 3" in html
    assert "update 4" not in html
    # The printed notice states that the list is incomplete without exposing
    # internal counters or message IDs.
    assert "Earlier developments are summarised" in html
    assert "remaining" not in html.lower()
