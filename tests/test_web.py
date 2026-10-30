from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from channel_press.config import settings
from channel_press.db import SessionLocal
from channel_press.models import (
    Channel,
    Edition,
    EditionPage,
    LoginAttempt,
    PostMedia,
    RightsGrant,
    SourceImport,
    SourcePost,
    User,
)
from channel_press.security import hash_password
from tests.conftest import FIXTURES, OWNER_PASSWORD, login


def test_healthz_and_first_run_setup_flow(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["setup_complete"] is False

    assert client.get("/setup").status_code == 200
    response = client.post(
        "/setup",
        data={
            "username": "firstowner",
            "display_name": "First Owner",
            "password": OWNER_PASSWORD,
            "confirm": OWNER_PASSWORD,
            "demo_channel": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/connections")
    assert "cp_session" in response.cookies

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Channel Press" in dashboard.text
    assert "Dashboard" in dashboard.text
    assert client.get("/healthz").json()["setup_complete"] is True

    # Setup cannot be run twice.
    again = client.post(
        "/setup",
        data={
            "username": "intruder",
            "display_name": "Intruder",
            "password": OWNER_PASSWORD,
            "confirm": OWNER_PASSWORD,
        },
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert "/login" in again.headers["location"]


def test_login_throttling_after_repeated_failures(client, owner):
    for _ in range(settings.login_max_attempts):
        response = client.post(
            "/login",
            data={"username": "owner", "password": "definitely wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 303
    blocked = client.post(
        "/login",
        data={"username": "owner", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert "Too+many" in blocked.headers["location"] or "Too%20many" in blocked.headers["location"]
    with SessionLocal() as session:
        assert session.query(LoginAttempt).count() >= settings.login_max_attempts


def test_csrf_and_role_boundaries(client, owner, demo_channel, auth):
    # Mutating request without a CSRF token is rejected.
    missing = client.post("/sources/new", data={"title": "No token"}, follow_redirects=False)
    assert missing.status_code == 403

    # The owner can open owner-only pages.
    assert client.get("/users").status_code == 200
    assert client.get("/diagnostics").status_code == 200

    with SessionLocal() as session:
        editor = User(
            username="editor",
            password_hash=hash_password(OWNER_PASSWORD),
            display_name="Editor",
            role="editor",
        )
        viewer = User(
            username="viewer",
            password_hash=hash_password(OWNER_PASSWORD),
            display_name="Viewer",
            role="viewer",
        )
        session.add_all([editor, viewer])
        session.commit()

    editor_client = TestClient(client.app, base_url="http://testserver")
    try:
        editor_csrf = login(editor_client, "editor", OWNER_PASSWORD)
        assert editor_client.get("/users").status_code == 403
        assert editor_client.get("/diagnostics").status_code == 403
        assert editor_client.get("/audit").status_code == 200
        created = editor_client.post(
            "/sources/new",
            data={
                "title": "Editor registered source",
                "slug": "editor-source",
                "grantor": "Source owner",
                "authority_basis": "Written permission",
                "csrf_token": editor_csrf,
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert "/sources/" in created.headers["location"]
    finally:
        editor_client.close()

    viewer_client = TestClient(client.app, base_url="http://testserver")
    try:
        login(viewer_client, "viewer", OWNER_PASSWORD)
        assert viewer_client.get("/sources").status_code == 200
        assert viewer_client.get(f"/sources/{demo_channel.id}").status_code == 403
    finally:
        viewer_client.close()


def test_web_import_and_withdraw_use_the_same_ingestion_rules(client, owner, demo_channel, auth):
    files = [("file", ("sample.json", (FIXTURES / "telegram_export_sample.json").read_bytes(), "application/json"))]
    for media_path in sorted((FIXTURES / "media").iterdir()):
        files.append(("media", (media_path.name, media_path.read_bytes(), "application/octet-stream")))
    response = client.post(
        f"/sources/{demo_channel.id}/import",
        data={"csrf_token": auth},
        files=files,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Imported+26" in response.headers["location"] or "Imported%2026" in response.headers["location"]
    source_page = client.get(f"/sources/{demo_channel.id}")
    assert source_page.status_code == 200
    assert "Why this classification" in source_page.text

    with SessionLocal() as session:
        assert session.query(SourcePost).count() == 26
        assert session.query(PostMedia).count() == 6
        post = session.query(SourcePost).filter_by(telegram_message_id=904).one()
        post_id = post.id

    withdraw = client.post(
        f"/sources/{demo_channel.id}/posts/{post_id}/withdraw",
        data={"reason": "rights_withdrawn", "csrf_token": auth},
        follow_redirects=False,
    )
    assert withdraw.status_code == 303
    with SessionLocal() as session:
        post = session.get(SourcePost, post_id)
        assert post.inclusion_status == "excluded"
        assert post.exclusion_reason == "rights_withdrawn"


def test_live_channel_rights_gate_blocks_import_until_a_grant_is_recorded(client, owner, auth):
    created = client.post(
        "/sources/new",
        data={
            "title": "Live source",
            "slug": "live-source",
            "grantor": "Rights holder",
            "authority_basis": "Signed permission letter",
            "csrf_token": auth,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as session:
        channel = session.query(Channel).filter_by(slug="live-source").one()
        channel_id = channel.id
        grant = session.query(RightsGrant).filter_by(channel_id=channel_id).one()
        grant_id = grant.id

    import_files = [("file", ("sample.json", (FIXTURES / "telegram_export_sample.json").read_bytes(), "application/json"))]
    imported = client.post(
        f"/sources/{channel_id}/import",
        data={"csrf_token": auth},
        files=import_files,
        follow_redirects=False,
    )
    assert imported.status_code == 303
    assert "Imported+26" in imported.headers["location"] or "Imported%2026" in imported.headers["location"]

    revoked = client.post(
        f"/sources/{channel_id}/rights/{grant_id}/revoke",
        data={"reason": "Permission withdrawn by source", "csrf_token": auth},
        follow_redirects=False,
    )
    assert revoked.status_code == 303

    with SessionLocal() as session:
        before = session.query(SourceImport).filter_by(channel_id=channel_id).count()
    blocked = client.post(
        f"/sources/{channel_id}/import",
        data={"csrf_token": auth},
        files=[("file", ("sample.json", b'{"messages":[]}', "application/json"))],
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert "Import+blocked" in blocked.headers["location"] or "Import%20blocked" in blocked.headers["location"]
    with SessionLocal() as session:
        assert session.query(SourceImport).filter_by(channel_id=channel_id).count() == before


def test_edition_preview_and_epaper_paths_are_served_safely(
    client, owner, demo_channel, auth, tmp_path, monkeypatch
):
    from channel_press.web.app import _ENV

    output = tmp_path / "edition"
    pages = output / "pages"
    epaper = output / "epaper"
    pages.mkdir(parents=True)
    epaper.mkdir(parents=True)
    preview = pages / "page-01.png"
    preview.write_bytes(
        (FIXTURES / "media" / "chaman_scene_a.png").read_bytes()
    )
    (epaper / "index.html").write_text("<html><body>E-paper proof</body></html>", encoding="utf-8")
    (epaper / "page-01.jpg").write_bytes((FIXTURES / "media" / "chaman_scene_b.png").read_bytes())
    (output / "sample_issue.pdf").write_bytes(b"%PDF-1.4\n")
    (output / "working_preview.pdf").write_bytes(b"%PDF-1.4\n% working preview\n")
    (tmp_path / "secret.txt").write_text("outside the bundle", encoding="utf-8")

    with SessionLocal() as session:
        now = datetime.now(UTC)
        edition = Edition(
            channel_id=demo_channel.id,
            kind="manual",
            title="Preview test",
            status="awaiting_review",
            window_start=now - timedelta(days=1),
            window_end=now,
            cutoff_at=now,
            target_release_at=now,
            output_dir=str(output),
            pdf_path=str(output / "sample_issue.pdf"),
            preview_pdf_path=str(output / "working_preview.pdf"),
            epaper_dir=str(epaper),
        )
        session.add(edition)
        session.flush()
        session.add(EditionPage(edition_id=edition.id, page_number=1, preview_path=str(preview)))
        session.commit()
        edition_id = edition.id

    monkeypatch.setitem(_ENV.globals, "desktop_mode", True)
    monkeypatch.setenv("CHANNEL_PRESS_DESKTOP", "1")
    detail = client.get(f"/editions/{edition_id}")
    assert detail.status_code == 200
    for local_path in (
        f"/editions/{edition_id}/pdf",
        f"/editions/{edition_id}/epaper",
        f"/editions/{edition_id}/preview/1?",
        f"/editions/{edition_id}/working.pdf",
    ):
        assert f'href="{local_path}' in detail.text
        assert f'href="{local_path}" target="_blank"' not in detail.text

    working_pdf = client.get(f"/editions/{edition_id}/working.pdf")
    published_pdf = client.get(f"/editions/{edition_id}/pdf")
    assert working_pdf.status_code == published_pdf.status_code == 200
    assert working_pdf.headers["content-type"] == "application/pdf"
    assert "content-disposition" not in working_pdf.headers
    assert "content-disposition" not in published_pdf.headers

    page_response = client.get(f"/editions/{edition_id}/preview/1")
    assert page_response.status_code == 200
    assert page_response.headers["content-type"].startswith("image/")

    redirect = client.get(f"/editions/{edition_id}/epaper", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].endswith(f"/editions/{edition_id}/epaper/")
    assert client.get(f"/editions/{edition_id}/epaper/").status_code == 200
    assert client.get(f"/editions/{edition_id}/epaper/page-01.jpg").status_code == 200
    assert client.get(f"/editions/{edition_id}/epaper/assets/../secret.txt").status_code == 404
    assert client.get(f"/editions/{edition_id}/epaper/%2e%2e/secret.txt").status_code == 404

    # The bundle links to ../sample_issue.pdf, which must resolve from /epaper/.
    pdf_redirect = client.get(f"/editions/{edition_id}/sample_issue.pdf", follow_redirects=False)
    assert pdf_redirect.status_code == 307
    assert pdf_redirect.headers["location"].endswith(f"/editions/{edition_id}/pdf")
