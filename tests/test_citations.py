"""Printed citations: compact QR codes, truthful targets and digital provenance."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import cv2
import fitz
import numpy as np

from channel_press.layout.engine import _attach_citations
from channel_press.models import SourcePost
from channel_press.pipeline import PipelineRequest, run_pipeline
from channel_press.publishing.qr import citation_code
from channel_press.timeutil import utcnow
from tests.conftest import import_fixture

WINDOW = dict(
    window_start=datetime(2026, 10, 18, tzinfo=UTC),
    window_end=datetime(2026, 10, 22, tzinfo=UTC),
    cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
)


def _decode(data_uri: str) -> str:
    payload = data_uri.split(",", 1)[1]
    buffer = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    value, _points, _straight = cv2.QRCodeDetector().detectAndDecode(image)
    return value


def test_citation_rejects_local_file_and_private_targets():
    for target in (
        "http://localhost:8765/edition",
        "http://127.0.0.1/evidence",
        "file:///E:/newsroom/output/edition.pdf",
        "http://192.168.1.10/edition",
        "",
    ):
        code = citation_code(target)
        assert not code.available, target
        assert not code.data_uri
    assert "local_or_offline_target" in citation_code("http://localhost:8765/x").reason


def test_citation_round_trips_a_public_telegram_url():
    target = "https://t.me/example_channel/101"
    code = citation_code(target, label="Message 101")
    assert code.available and code.public
    assert _decode(code.data_uri) == target
    assert code.label == "Message 101"


def test_attach_citations_never_invents_a_target():
    class Plan:
        appendix_entries = [
            {"kind": "fixture", "url": "", "message_id": 1},
            {"kind": "external_link_unverified", "url": "https://example.org/report", "message_id": None},
            {"kind": "telegram", "url": "http://127.0.0.1/private", "message_id": 2},
        ]
        packages: list = []

    plan = Plan()
    _attach_citations(plan, demo_edition=False)
    assert [entry["qr_printed"] for entry in plan.appendix_entries] == [False, False, False]
    assert plan.appendix_entries[0]["citation_label"] == "No public link"
    assert plan.appendix_entries[1]["citation_label"] == "External link not printed"
    assert "not_public" in plan.appendix_entries[2]["qr_reason"] or "local" in plan.appendix_entries[2]["qr_reason"]


def test_printed_story_qr_uses_a_real_target_and_no_appendix(db, demo_channel, owner):
    import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    post = db.query(SourcePost).filter(SourcePost.telegram_message_id == 101).one()
    target = "https://t.me/example_channel/101"
    post.source_url = target
    db.commit()

    result = run_pipeline(
        db,
        PipelineRequest(
            channel=demo_channel,
            target_release_at=utcnow(),
            kind="manual",
            title="Citation QR proof",
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=False,
            **WINDOW,
        ),
    )
    db.commit()
    edition = result.edition
    assert edition.output_dir
    html = (result.publish_result.output_dir / "print.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in html
    # The target is encoded in the code, never printed as a text list.
    assert target not in html
    assert "story__qr" in html
    # The reader-facing appendix is gone.
    assert "Sources &amp; provenance" not in html
    assert "source-table" not in html

    manifest = json.loads((result.publish_result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    source = next(entry for entry in manifest["sources"] if entry["message_id"] == 101)
    assert source["url"] == target
    qr = next(item for item in manifest["qr"] if item["target"] == target)
    assert qr["status"] == "ok"
    assert "qr_data_uri" not in source  # image bytes stay out of the manifest

    # A code printed on the page must decode back to the same public target.
    db.expire_all()
    with fitz.open(edition.pdf_path) as document:
        found = None
        for page in document:
            pixmap = page.get_pixmap(dpi=200)
            image = cv2.imdecode(
                np.frombuffer(pixmap.tobytes("png"), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            value, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
            if value == target and points is not None:
                found = points
                break
    assert found is not None, "the printed story code must decode to the public source URL"
    decoded_corners = found.reshape(-1, 2)
    width_px = float(np.linalg.norm(decoded_corners[1] - decoded_corners[0]))
    width_inches = width_px / 200
    # A 0.6in code at 200dpi is ~120px. Allow for detector inset but reject a
    # tiny code that would not scan reliably on newsprint.
    assert 0.45 <= width_inches <= 0.9


def test_fixture_demo_edition_prints_no_fake_qr(db, demo_channel, owner):
    import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    result = run_pipeline(
        db,
        PipelineRequest(
            channel=demo_channel,
            target_release_at=utcnow(),
            kind="manual",
            title="Fixture citation proof",
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=True,
            **WINDOW,
        ),
    )
    html = (result.publish_result.output_dir / "print.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," not in html
    assert "Sources &amp; provenance" not in html
    manifest = json.loads((result.publish_result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["qr"]
    assert all(item["status"] == "unavailable" for item in manifest["qr"])
    assert all(item["reason"] == "demo_edition_no_public_target" for item in manifest["qr"])
