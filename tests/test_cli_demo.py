from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from channel_press import cli
from channel_press.bootstrap import setup_complete
from channel_press.db import SessionLocal
from channel_press.models import Channel, Edition
from tests.conftest import OWNER_PASSWORD, login


def _fake_result(edition_id: int = 1):
    return SimpleNamespace(
        edition=SimpleNamespace(id=edition_id, title="The Channel Dispatch — Demonstration edition"),
        stories=[object()],
        articles=[object()],
        review_count=0,
        provider="fixture",
        provider_note="stub",
        publish_result=None,
    )


def test_demo_command_marks_setup_complete_and_clears_the_dashboard_notice(
    db, client, monkeypatch
):
    """The documented five-minute demo must not leave the setup banner visible."""

    monkeypatch.setattr(cli, "upgrade_database", lambda: None)
    monkeypatch.setattr(cli, "run_pipeline", lambda session, request: _fake_result())

    assert cli.main(["demo", "--posts", "sample", "--password", OWNER_PASSWORD]) == 0

    with SessionLocal() as session:
        assert setup_complete(session) is True
        assert session.scalar(select(Channel.id)) is not None
        # The stub does not publish, so no edition row is expected; the banner
        # depends only on the owner/publication setup flag.
        assert session.scalar(select(Edition.id)) is None

    login(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "Setup is not complete" not in page.text
