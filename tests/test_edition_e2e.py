from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import fitz
import pytest

from channel_press.models import (
    ArticleMedia,
    Channel,
    EditionArticle,
    EditionPage,
    SourceAppendix,
    SourcePost,
)
from channel_press.pipeline import PipelineRequest, RightsError, run_pipeline
from channel_press.timeutil import utcnow
from tests.conftest import import_fixture

pytestmark = pytest.mark.slow


def _run(db, channel, owner, tmp_path, filename):
    before = db.query(SourcePost).count()
    import_fixture(db, channel, owner, filename)
    assert db.query(SourcePost).count() > before
    result = run_pipeline(
        db,
        PipelineRequest(
            channel=channel,
            window_start=datetime(2026, 10, 18, tzinfo=UTC),
            window_end=datetime(2026, 10, 22, tzinfo=UTC),
            cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
            target_release_at=utcnow(),
            kind="manual",
            title="End-to-end test edition",
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=True,
            output_root=tmp_path,
        ),
    )
    db.commit()
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_fixture_to_printable_edition_and_epaper(db, demo_channel, owner, tmp_path):
    result = _run(db, demo_channel, owner, tmp_path, "telegram_export_sample.json")
    edition = result.edition
    published = result.publish_result
    assert published is not None
    assert edition.status == "published"
    assert published.pdf_path.exists() and published.pdf_path.stat().st_size > 5_000
    assert published.epaper_dir.joinpath("index.html").exists()
    assert published.epaper_dir.joinpath("text.html").exists()
    assert published.manifest_path.exists()

    manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "channel_press.manifest.v1"
    assert manifest["demo_edition"] is True
    assert manifest["edition"]["status"] == "published"
    assert manifest["edition"]["published_at"]
    assert manifest["pdf"]["page_count"] >= 4
    assert manifest["pdf"]["page_size_pt"] == {"width": 792, "height": 1224}
    assert manifest["quality"]["warnings"] == []
    assert manifest["quality"]["office_print_proof"] is True
    assert manifest["pdf"]["sha256"] == _sha256(published.pdf_path)
    assert manifest["sources"]
    assert manifest["articles"]

    checksums = json.loads((published.output_dir / "checksums.json").read_text(encoding="utf-8"))
    for relative, expected in checksums.items():
        assert _sha256(published.output_dir / relative) == expected

    # Stored page previews must point at the final bundle, never the staging folder.
    pages = list(db.query(EditionPage).filter_by(edition_id=edition.id).order_by(EditionPage.page_number))
    assert len(pages) == manifest["pdf"]["page_count"]
    for page in pages:
        assert page.preview_path
        assert ".staging-" not in page.preview_path
        assert str(published.output_dir) in page.preview_path
        assert Path(page.preview_path).exists()
    appendix = db.query(SourceAppendix).filter_by(edition_id=edition.id).one()
    assert json.loads(appendix.entries_json)

    # Every source still is used at most once in the edition.
    article_ids = [
        row.article_id
        for row in db.query(EditionArticle).filter_by(edition_id=edition.id).all()
    ]
    media_ids = [
        row.media_id
        for row in db.query(ArticleMedia).filter(ArticleMedia.article_id.in_(article_ids)).all()
        if row.media_id is not None
    ]
    assert len(media_ids) == len(set(media_ids))

    document = fitz.open(published.pdf_path)
    try:
        assert document.page_count == manifest["pdf"]["page_count"]
        text = "\n".join(page.get_text("text") for page in document)
        for page in document:
            assert abs(page.rect.width - 792) < 0.5
            assert abs(page.rect.height - 1224) < 0.5
    finally:
        document.close()
    assert len(text) > 3_000
    assert "DEMONSTRATION" in text.upper() or "SYNTHETIC" in text.upper()
    for forbidden in ("localhost", "127.0.0.1", "file:///"):
        assert forbidden not in text.lower()

    epaper_index = published.epaper_dir.joinpath("index.html").read_text(encoding="utf-8")
    assert "The Channel Dispatch" in epaper_index
    assert "Synthetic fixture edition" in epaper_index
    assert "sample_issue.pdf" in epaper_index
    assert "localhost" not in epaper_index
    assert "127.0.0.1" not in epaper_index


@pytest.mark.parametrize("filename,expected_posts", [("posts_30.json", 30), ("posts_70.json", 70), ("posts_150.json", 150)])
def test_capacity_fixture_sets_build_within_a_reasonable_time(
    db, demo_channel, owner, tmp_path, filename, expected_posts
):
    result = _run(db, demo_channel, owner, tmp_path, filename)
    assert result.publish_result is not None
    assert result.stats["stories"] >= 1
    assert result.stats["articles"] >= 1
    assert result.duration_seconds < 900
    manifest = json.loads(result.edition.manifest_json or "{}")
    assert manifest["pdf"]["page_count"] >= 2
    assert manifest["quality"]["warnings"] == []
    print(
        f"CAPACITY {filename}: {expected_posts} posts, {result.stats['stories']} stories, "
        f"{result.stats['articles']} articles, {manifest['pdf']['page_count']} pages, "
        f"{result.duration_seconds}s"
    )


def test_live_channel_without_rights_cannot_build(db, owner, tmp_path):
    channel = Channel(slug="uncleared", title="Uncleared", is_demo=False)
    db.add(channel)
    db.commit()
    with pytest.raises(RightsError):
        run_pipeline(
            db,
            PipelineRequest(
                channel=channel,
                window_start=datetime(2026, 10, 18, tzinfo=UTC),
                window_end=datetime(2026, 10, 22, tzinfo=UTC),
                cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
                target_release_at=utcnow(),
                publish=False,
                output_root=tmp_path,
            ),
        )
