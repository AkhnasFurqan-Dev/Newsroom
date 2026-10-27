from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from channel_press.fonts import (
    FontError,
    catalog,
    glyph_coverage,
    import_font,
    inspect_font_file,
    resolve_face,
)
from channel_press.models import Edition, FontAsset, PublicationProfile, ThemeRevision
from channel_press.theming import (
    ThemeError,
    compile_payload,
    contrast_ratio,
    current_theme,
    default_payload,
    fonts_used_by_themes,
    normalise_hex,
    normalise_payload,
    payload_hash,
    pin_edition_theme,
    resolve_edition_theme,
    save_theme,
    theme_css,
    theme_manifest,
    theme_payload,
)
from channel_press.timeutil import utcnow
from tests.font_factory import make_font

pytestmark = pytest.mark.usefixtures("installed_font_stub")


@pytest.fixture
def publication(db, owner):
    from channel_press import bootstrap

    bootstrap.ensure_defaults(db)
    db.commit()
    return db.scalar(select(PublicationProfile).limit(1))


@pytest.fixture
def installed_font_stub(monkeypatch):
    """Keep discovery deterministic and cheap inside the test session."""
    faces = []
    for family, styles in {
        "Georgia": [("normal", 400), ("normal", 700), ("italic", 400)],
        "Segoe UI": [("normal", 400), ("normal", 600), ("normal", 700)],
    }.items():
        for style, weight in styles:
            faces.append(
                {
                    "family": family,
                    "style": style,
                    "weight": weight,
                    "path": "",
                    "source": "system",
                    "face_count": 1,
                    "variable": False,
                    "unavailable": True,
                    "reason": "Stub face used by unit tests.",
                    "asset_id": None,
                    "mtime": 0,
                }
            )
    monkeypatch.setattr(
        "channel_press.theming.discover_installed_fonts", lambda **_kwargs: faces
    )
    monkeypatch.setattr(
        "channel_press.fonts.discover_installed_fonts", lambda **_kwargs: faces
    )
    return faces


def _complete_edition(db, channel, publication) -> Edition:
    edition = Edition(
        channel_id=channel.id,
        publication_id=publication.id,
        kind="manual",
        title="Theme test edition",
        status="approved",
        window_start=datetime(2026, 10, 20, tzinfo=UTC),
        window_end=datetime(2026, 10, 21, tzinfo=UTC),
        cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
        target_release_at=utcnow(),
    )
    db.add(edition)
    db.flush()
    return edition


def test_default_theme_is_created_once_and_keeps_the_existing_accent(db, publication):
    publication.accent = "#24506b"
    db.flush()

    theme = current_theme(db, publication)
    payload = theme_payload(theme)

    assert theme.revision == 1
    assert theme.is_current is True
    assert payload["colors"]["accent"] == "#24506b"
    assert payload["version"].startswith("print-theme-")
    again = current_theme(db, publication)
    assert again.id == theme.id
    assert len(list(db.scalars(select(ThemeRevision)))) == 1


def test_colour_normalisation_accepts_short_hex_and_rejects_unsafe_values():
    assert normalise_hex("#ABC") == "#aabbcc"
    assert normalise_hex("7B1F24") == "#7b1f24"
    with pytest.raises(ThemeError):
        normalise_hex("red")
    with pytest.raises(ThemeError):
        normalise_hex("#12")


def test_saving_a_theme_is_an_immutable_new_revision(db, publication, owner):
    first = save_theme(
        db,
        publication,
        default_payload(),
        actor_id=owner.id,
        name="Launch palette",
        notes="initial identity",
    )
    db.commit()
    original = theme_payload(first)

    payload = default_payload()
    payload["colors"]["accent"] = "#1f4e79"
    payload["metrics"]["body_size_pt"] = 10.1
    second = save_theme(
        db, publication, payload, actor_id=owner.id, name="Cooler accent"
    )
    db.commit()

    db.refresh(first)
    assert first.revision == 1
    assert first.is_current is False
    assert theme_payload(first) == original, "an archived revision must never be rewritten"
    assert second.revision == 2
    assert second.is_current is True
    assert theme_payload(second)["colors"]["accent"] == "#1f4e79"
    assert theme_payload(second)["metrics"]["body_size_pt"] == 10.1
    assert payload_hash(theme_payload(first)) != payload_hash(theme_payload(second))


def test_illegible_ink_is_refused_instead_of_saved_with_a_warning(db, publication, owner):
    payload = default_payload()
    payload["colors"]["ink"] = "#dddddd"
    assert contrast_ratio("#dddddd", "#fffdf8") < 3.0
    with pytest.raises(ThemeError, match="contrast"):
        save_theme(db, publication, payload, actor_id=owner.id)
    assert db.scalar(select(ThemeRevision)) is None


def test_theme_payload_is_validated_and_bounded():
    payload = default_payload()
    payload["metrics"]["body_size_pt"] = 200
    with pytest.raises(ThemeError):
        normalise_payload(payload)

    payload = default_payload()
    payload["fonts"]["body"]["weight"] = 450
    with pytest.raises(ThemeError):
        normalise_payload(payload)

    payload = default_payload()
    payload["sections"] = {"national": "#123456", "not-a-section": "#000000"}
    assert normalise_payload(payload)["sections"] == {"national": "#123456"}


def test_compiled_css_carries_palette_metrics_and_section_accents(db, publication, owner):
    payload = default_payload()
    payload["colors"]["paper"] = "#fbf7ee"
    payload["colors"]["accent"] = "#345c3a"
    payload["metrics"]["body_size_pt"] = 9.8
    payload["metrics"]["headline_tracking"] = -0.015
    payload["sections"] = {"national": "#345c3a"}
    row = save_theme(db, publication, payload, actor_id=owner.id)
    db.commit()

    css, warnings = theme_css(theme_payload(row), assets=[], font_dir=None)
    assert "--paper: #fbf7ee;" in css
    assert "--accent: #345c3a;" in css
    assert "--body-size: 9.8pt;" in css
    assert "--headline-tracking: -0.015em;" in css
    assert '[data-section="national"] .kicker' in css
    assert isinstance(warnings, list)


def test_published_edition_keeps_its_pinned_theme_after_a_later_save(
    db, publication, owner, demo_channel
):
    first = save_theme(
        db, publication, default_payload(), actor_id=owner.id, name="Print v1"
    )
    edition = _complete_edition(db, demo_channel, publication)
    render = compile_payload(
        theme_payload(first), theme=first, source="current", assets=[]
    )
    pin_edition_theme(db, edition, publication, render)
    db.flush()
    pinned_hash = edition.preview_theme_hash
    assert pinned_hash == render.hash

    changed = default_payload()
    changed["colors"]["accent"] = "#946b1e"
    save_theme(db, publication, changed, actor_id=owner.id, name="Print v2")
    db.commit()

    theme, payload, source = resolve_edition_theme(db, publication, edition)
    assert source == "pinned"
    assert theme is not None and theme.revision == 1
    assert payload["colors"]["accent"] == theme_payload(first)["colors"]["accent"]
    assert payload_hash(payload) == pinned_hash

    # A brand-new draft resolves to the current revision instead.
    draft_theme, draft_payload, draft_source = resolve_edition_theme(db, publication, None)
    assert draft_source == "current"
    assert draft_theme is not None and draft_theme.revision == 2
    assert draft_payload["colors"]["accent"] == "#946b1e"


def test_imported_font_is_embedded_and_manifested(db, publication, owner, tmp_path):
    data = make_font(family="Test Proof Serif", weight=700)
    metadata = import_font(
        data=data,
        filename="ProofSerif-Bold.ttf",
        license_note="SIL OFL 1.1, test fixture",
        license_acknowledged=True,
        uploaded_by=owner.id,
    )
    asset = FontAsset(**metadata)
    db.add(asset)
    db.commit()

    payload = default_payload()
    for role in ("display", "headline", "section", "body"):
        payload["fonts"][role] = {
            "family": "Test Proof Serif",
            "weight": 700,
            "style": "normal",
            "fallback": "serif",
            "asset_id": asset.id,
        }
    row = save_theme(db, publication, payload, actor_id=owner.id)
    db.commit()

    render = compile_payload(
        theme_payload(row), theme=row, assets=[asset], font_dir=tmp_path
    )
    assert "@font-face" in render.css
    assert f"fonts/{asset.stored_name}" in render.css
    assert (tmp_path / "fonts" / asset.stored_name).read_bytes() == data
    manifest = theme_manifest(row, render.payload, [asset])
    assert manifest["font_files"][0]["sha256"] == asset.sha256
    assert manifest["font_files"][0]["licence"] == "SIL OFL 1.1, test fixture"
    assert manifest["qr"]["ink"] == "#111111"
    assert fonts_used_by_themes(db, asset.id) == [publication.name]

    # The library refuses to delete a font that an archived design depends on.
    from channel_press.theming import available_font_assets

    assert [item.id for item in available_font_assets(db)] == [asset.id]


def test_theme_manifest_records_the_exact_hash(db, publication, owner):
    row = save_theme(db, publication, default_payload(), actor_id=owner.id)
    db.commit()
    payload = theme_payload(row)
    manifest = theme_manifest(row, payload, [])
    assert manifest["hash"] == payload_hash(payload)
    assert manifest["revision"] == row.revision
    assert manifest["colors"]["paper"] == "#fffdf8"


def test_preview_state_is_marked_stale_but_published_editions_are_untouched(
    db, publication, owner, demo_channel
):
    save_theme(db, publication, default_payload(), actor_id=owner.id)
    draft = _complete_edition(db, demo_channel, publication)
    draft.preview_state = "current"
    published = _complete_edition(db, demo_channel, publication)
    published.status = "published"
    published.preview_state = "current"
    db.flush()

    changed = default_payload()
    changed["colors"]["accent"] = "#5d3b76"
    save_theme(db, publication, changed, actor_id=owner.id)
    db.commit()
    db.refresh(draft)
    db.refresh(published)
    assert draft.preview_state == "stale"
    assert published.preview_state == "current"


# ---------------------------------------------------------------------------
# font library
# ---------------------------------------------------------------------------


def test_font_validation_rejects_disguised_executables_and_unknown_extensions():
    with pytest.raises(FontError, match="signature"):
        import_font(
            data=b"MZ\x90\x00not a font",
            filename="payload.ttf",
            license_note="",
            license_acknowledged=True,
            uploaded_by=None,
        )
    with pytest.raises(FontError, match="Only"):
        import_font(
            data=make_font(),
            filename="font.woff2",
            license_note="",
            license_acknowledged=True,
            uploaded_by=None,
        )
    with pytest.raises(FontError, match="licensed"):
        import_font(
            data=make_font(),
            filename="font.ttf",
            license_note="",
            license_acknowledged=False,
            uploaded_by=None,
        )


def test_import_metadata_comes_from_the_file_not_the_upload_name():
    data = make_font(family="Test Proof Serif", style="Italic", weight=700)
    metadata = import_font(
        data=data,
        filename="../../evil family.ttf",
        license_note="OFL",
        license_acknowledged=True,
        uploaded_by=None,
    )
    assert metadata["family"] == "Test Proof Serif"
    assert metadata["style"] == "italic"
    assert metadata["weight"] == 700
    assert metadata["sha256"]
    assert metadata["stored_name"].startswith(metadata["sha256"][:16])
    assert "/" not in metadata["stored_name"] and "\\" not in metadata["stored_name"]


def test_font_resolution_reports_substitution_instead_of_faking_a_face(installed_font_stub):
    face, notes = resolve_face(
        family="Georgia",
        weight=800,
        style="normal",
        asset_id=None,
        assets=[],
        installed=installed_font_stub,
    )
    assert face is not None
    assert face.weight == 700
    assert any("no 800 weight" in note for note in notes)

    italic, notes = resolve_face(
        family="Georgia",
        weight=400,
        style="italic",
        asset_id=None,
        assets=[],
        installed=installed_font_stub,
    )
    assert italic is not None and italic.style == "italic"
    assert notes == []

    missing, notes = resolve_face(
        family="Not Installed",
        weight=400,
        style="normal",
        asset_id=None,
        assets=[],
        installed=installed_font_stub,
    )
    assert missing is None
    assert "not installed" in notes[0]


def test_catalog_groups_installed_and_imported_faces(installed_font_stub):
    from channel_press import fonts as fonts_module

    assert fonts_module.discover_installed_fonts() == installed_font_stub
    data = make_font(family="Test Proof Serif")
    metadata = import_font(
        data=data,
        filename="ProofSerif.ttf",
        license_note="OFL",
        license_acknowledged=True,
        uploaded_by=None,
    )
    asset = type("Asset", (), {**metadata, "id": 7})()
    rows = catalog([asset])
    imported = [row for row in rows if row.source == "asset"]
    assert len(imported) == 1
    assert imported[0].family == "Test Proof Serif"
    assert imported[0].faces[0].asset_id == 7


def test_coverage_reports_missing_characters():
    data = make_font(characters="ABC ")
    metadata = import_font(
        data=data,
        filename="Narrow.ttf",
        license_note="OFL",
        license_acknowledged=True,
        uploaded_by=None,
    )
    from channel_press.config import settings

    path = settings.fonts_dir / metadata["stored_name"]
    report = glyph_coverage(str(path), "A B Z")
    assert report["checked"] is True
    assert "Z" in report["missing"]
    assert "A" not in report["missing"]
    empty = glyph_coverage("", "ABC")
    assert empty["checked"] is False
