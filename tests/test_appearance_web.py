from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from channel_press.models import FontAsset, ThemeRevision
from tests.conftest import csrf_from_html
from tests.font_factory import make_font


@pytest.fixture(autouse=True)
def deterministic_fonts(monkeypatch):
    faces = [
        {
            "family": "Georgia",
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
        for style, weight in (("normal", 400), ("normal", 700), ("italic", 400))
    ] + [
        {
            "family": "Segoe UI",
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
        for style, weight in (("normal", 400), ("normal", 700))
    ]
    monkeypatch.setattr("channel_press.fonts.discover_installed_fonts", lambda **_kw: faces)
    monkeypatch.setattr("channel_press.theming.discover_installed_fonts", lambda **_kw: faces)
    return faces


def test_appearance_page_renders_real_controls(client, owner, auth):
    response = client.get("/appearance")
    assert response.status_code == 200
    html = response.text
    assert "Appearance" in html
    assert 'action="/appearance"' in html
    assert "Primary / masthead" in html
    assert "Article body" in html
    assert 'type="color"' in html
    assert "Render print proof" in html
    assert "Revision history" in html
    assert "Palette presets" in html
    # The nav entry is available to an editor.
    assert 'href="/appearance"' in html


def test_saving_the_form_creates_a_revision_in_the_database(client, owner, auth, db):
    page = client.get("/appearance")
    csrf = csrf_from_html(page.text)
    response = client.post(
        "/appearance",
        data={
            "csrf_token": csrf,
            "theme_name": "Weekend palette",
            "theme_notes": "Warmer paper",
            "color_paper": "#f7f1e3",
            "color_accent": "#24506b",
            "metric_body_size_pt": "9.9",
            "font_body": "system:Georgia",
            "weight_body": "400",
            "style_body": "normal",
            "section_national": "#24506b",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/appearance")

    theme = db.scalar(select(ThemeRevision).order_by(ThemeRevision.revision.desc()))
    assert theme is not None
    assert theme.name == "Weekend palette"
    assert theme.notes == "Warmer paper"
    assert theme.is_current is True
    colors = json.loads(theme.colors_json)
    fonts = json.loads(theme.fonts_json)
    assert colors["paper"] == "#f7f1e3"
    assert colors["accent"] == "#24506b"
    assert fonts["sections"] == {"national": "#24506b"}
    assert fonts["metrics"]["body_size_pt"] == 9.9


def test_illegible_colour_is_refused_and_no_revision_is_written(client, owner, auth, db):
    csrf = csrf_from_html(client.get("/appearance").text)
    response = client.post(
        "/appearance",
        data={
            "csrf_token": csrf,
            "color_ink": "#dddddd",
            "color_paper": "#ffffff",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "contrast" in response.text.lower()
    rows = list(db.scalars(select(ThemeRevision)))
    assert [row.revision for row in rows] == [1], "only the initial default revision may exist"


def test_preset_and_reset_create_new_revisions(client, owner, auth, db):
    csrf = csrf_from_html(client.get("/appearance").text)
    preset = client.post(
        "/appearance/preset",
        data={"csrf_token": csrf, "preset": "monochrome"},
        follow_redirects=False,
    )
    assert preset.status_code == 303
    rows = list(db.scalars(select(ThemeRevision).order_by(ThemeRevision.revision)))
    assert [row.revision for row in rows] == [1, 2]
    assert json.loads(rows[-1].colors_json)["accent"] == "#333333"

    csrf = csrf_from_html(client.get("/appearance").text)
    reset = client.post(
        "/appearance/reset", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert reset.status_code == 303
    db.expire_all()
    rows = list(db.scalars(select(ThemeRevision).order_by(ThemeRevision.revision)))
    assert len(rows) == 3
    assert rows[0].is_current is False
    assert rows[-1].is_current is True
    assert json.loads(rows[-1].colors_json)["accent"] == "#7b1f24"


def test_unknown_preset_is_reported(client, owner, auth, db):
    csrf = csrf_from_html(client.get("/appearance").text)
    response = client.post(
        "/appearance/preset",
        data={"csrf_token": csrf, "preset": "neon"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Unknown+palette+preset" in response.headers["location"]
    rows = list(db.scalars(select(ThemeRevision)))
    assert [row.revision for row in rows] == [1]


def test_font_upload_is_validated_library_scoped_and_deletable(client, owner, auth, db):
    csrf = csrf_from_html(client.get("/appearance").text)
    data = make_font(family="Test Upload Serif", style="Regular", weight=400)
    upload = client.post(
        "/appearance/fonts",
        data={
            "csrf_token": csrf,
            "license_note": "SIL OFL 1.1 test fixture",
            "license_acknowledged": "1",
        },
        files={"file": ("TestUpload.ttf", data, "font/ttf")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert upload.headers["location"].startswith("/appearance")
    asset = db.scalar(select(FontAsset))
    assert asset is not None
    assert asset.family == "Test Upload Serif"
    assert asset.license_acknowledged is True
    assert asset.license_note == "SIL OFL 1.1 test fixture"
    assert asset.stored_name.startswith(asset.sha256[:16])

    # The uploaded file is offered as a real selectable face.
    page = client.get("/appearance")
    assert "Test Upload Serif" in page.text
    csrf = csrf_from_html(page.text)
    saved = client.post(
        "/appearance",
        data={
            "csrf_token": csrf,
            "font_body": f"asset:{asset.id}",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    theme = db.scalar(select(ThemeRevision).order_by(ThemeRevision.revision.desc()))
    assert json.loads(theme.fonts_json)["fonts"]["body"]["asset_id"] == asset.id

    # Referenced by the saved theme: deletion is refused with an explanation.
    blocked = client.post(
        f"/appearance/fonts/{asset.id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert "referenced+by+a+saved+theme" in blocked.headers["location"]
    assert db.get(FontAsset, asset.id) is not None


def test_disguised_font_upload_is_rejected(client, owner, auth, db):
    csrf = csrf_from_html(client.get("/appearance").text)
    response = client.post(
        "/appearance/fonts",
        data={"csrf_token": csrf, "license_acknowledged": "1", "license_note": "x"},
        files={"file": ("evil.ttf", b"MZ\x90\x00payload", "font/ttf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "signature" in response.headers["location"]
    assert db.scalar(select(FontAsset)) is None


@pytest.mark.slow
def test_print_proof_is_a_real_tabloid_render_served_only_to_editors(client, owner, auth, db):
    from PIL import Image

    csrf = csrf_from_html(client.get("/appearance").text)
    response = client.post(
        "/appearance/proof",
        data={
            "csrf_token": csrf,
            "color_paper": "#f6efe0",
            "color_accent": "#24506b",
            "color_ink": "#141311",
            "font_body": "system:Georgia",
            "weight_body": "400",
            "style_body": "normal",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/appearance?proof=theme-proof-")
    from urllib.parse import parse_qs, urlparse

    filename = parse_qs(urlparse(location).query)["proof"][0]

    page = client.get(location)
    assert page.status_code == 200
    assert filename in page.text
    proof = client.get(f"/appearance/proof/{filename}")
    assert proof.status_code == 200
    assert proof.headers["content-type"] == "image/png"
    assert len(proof.content) > 20_000
    import io

    with Image.open(io.BytesIO(proof.content)) as image:
        assert image.size == (1056, 1632)
        assert image.convert("RGB").getpixel((10, 10)) != (255, 255, 255)

    assert client.get("/appearance/proof/../secret.png").status_code == 404
    assert client.get("/appearance/proof/theme-proof-notahash.png").status_code == 404


def test_appearance_requires_editor_access(client, owner, demo_channel, auth, db):
    from channel_press.security import hash_password
    from channel_press.models import User

    viewer = User(
        username="viewer",
        display_name="Viewer",
        role="viewer",
        password_hash=hash_password("a good viewer password"),
    )
    db.add(viewer)
    db.commit()
    from tests.conftest import login

    csrf = login(client, "viewer", "a good viewer password")
    assert csrf
    assert client.get("/appearance").status_code == 403
