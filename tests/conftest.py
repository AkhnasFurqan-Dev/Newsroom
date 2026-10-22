"""Test harness for Channel Press.

Environment variables are set before any application module is imported so the
temporary database and output tree are used for every test. The production data
directory is never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="channel-press-tests-"))
DATA_DIR = TEST_ROOT / "data"
OUTPUT_DIR = TEST_ROOT / "output"
DATABASE_PATH = DATA_DIR / "test.db"

os.environ["CHANNEL_PRESS_DATA_DIR"] = str(DATA_DIR)
os.environ["CHANNEL_PRESS_OUTPUT_DIR"] = str(OUTPUT_DIR)
os.environ["CHANNEL_PRESS_DATABASE_URL"] = f"sqlite:///{DATABASE_PATH.as_posix()}"
os.environ["CHANNEL_PRESS_SECRET_KEY"] = "test-only-secret-key-do-not-use-in-production"
os.environ["CHANNEL_PRESS_DEMO_MODE"] = "true"
os.environ["CHANNEL_PRESS_SYNC_JOBS"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from channel_press import models  # noqa: E402,F401  (register all tables)
from channel_press.auth import active_rights, create_first_owner  # noqa: E402
from channel_press.bootstrap import ensure_defaults, get_or_create_demo_channel  # noqa: E402
from channel_press.config import BASE_DIR, settings  # noqa: E402
from channel_press.db import Base, SessionLocal, engine  # noqa: E402
from channel_press.ingestion.adapters import load_json  # noqa: E402
from channel_press.ingestion.service import ensure_import_media_dir, import_payload  # noqa: E402
from channel_press.models import LoginSession, User  # noqa: E402
from channel_press.web.app import app  # noqa: E402

OWNER_USERNAME = "owner"
OWNER_PASSWORD = "correct horse battery staple"
FIXTURES = BASE_DIR / "fixtures"


@pytest.fixture(autouse=True)
def clean_state():
    """Give every test a fresh schema, empty media directory and output tree."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    settings.ensure_dirs()
    for child in settings.media_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    shutil.rmtree(settings.output_dir, ignore_errors=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def owner(db):
    ensure_defaults(db)
    user = create_first_owner(db, OWNER_USERNAME, OWNER_PASSWORD, "Test Owner")
    db.commit()
    return user


@pytest.fixture
def demo_channel(db, owner):
    channel = get_or_create_demo_channel(db, owner)
    db.commit()
    return channel


def import_fixture(db, channel, owner, filename: str):
    path = FIXTURES / filename
    payload = load_json(path)
    ensure_import_media_dir(db, settings.media_dir)
    stats = import_payload(
        db,
        channel,
        payload,
        imported_by=owner.id if owner else None,
        media_root=path.parent / "media",
        source_kind="fixture",
        original_name=path.name,
        rights_grant=active_rights(db, channel.id),
    )
    db.commit()
    return stats


@pytest.fixture
def sample_import(db, demo_channel, owner):
    return import_fixture(db, demo_channel, owner, "telegram_export_sample.json")


@pytest.fixture
def client():
    test_client = TestClient(app, base_url="http://testserver")
    try:
        yield test_client
    finally:
        test_client.close()


def login(client: TestClient, username: str = OWNER_USERNAME, password: str = OWNER_PASSWORD) -> str:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith("/")
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == username))
        assert user is not None
        login_row = session.scalar(
            select(LoginSession)
            .where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
            .order_by(LoginSession.id.desc())
        )
        assert login_row is not None
        return login_row.csrf_token


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


@pytest.fixture
def auth(client):
    """Return (csrf_token) after logging the owner in."""
    return login(client)


def json_response(response) -> dict:
    return json.loads(response.text)
