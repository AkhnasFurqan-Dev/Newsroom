from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import audit
from .config import BASE_DIR
from .models import (
    AppSetting,
    CartographicProfile,
    Channel,
    ChannelOwnership,
    EditionSchedule,
    ExportTarget,
    PublicationProfile,
    RightsGrant,
    User,
)
from .timeutil import utcnow

FIXTURES = BASE_DIR / "fixtures"


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()


def ensure_defaults(db: Session) -> PublicationProfile:
    profile = db.scalar(select(PublicationProfile).order_by(PublicationProfile.id.asc()))
    if profile is None:
        profile = PublicationProfile(
            name="The Channel Dispatch",
            channel_handle="@fixture_newsroom",
            slogan="A source-transparent roundup for the region",
            footer_text=(
                "An editorial roundup prepared from the owner's authorized Telegram channel. "
                "Demonstration edition produced from synthetic fixtures."
            ),
            city="Karachi",
            timezone="Asia/Karachi",
            accent="#7b1f24",
            paper_size="tabloid",
            map_profile="neutral",
            qr_strategy="appendix",
            issue_volume=1,
            section_order_json=json.dumps(
                ["national", "regional", "international", "defence", "conflict", "analysis", "opinion", "briefs"]
            ),
            allow_closing_light=True,
        )
        db.add(profile)
        db.flush()
    if db.scalar(select(CartographicProfile).limit(1)) is None:
        profiles = [
            CartographicProfile(
                key="neutral",
                name="Neutral locator convention",
                dataset="Natural Earth 1:50m Admin 0 Countries",
                dataset_version="ne_50m_admin_0_countries + ne_10m_admin_0_disputed_areas",
                license="Public domain (Natural Earth)",
                attribution="Natural Earth (public domain)",
                disputed_rule=(
                    "Disputed areas are drawn with a neutral dashed outline. The map does not adjudicate "
                    "sovereignty. Territorial status is disputed."
                ),
                legend="Boundaries: Natural Earth. Disputed areas shown for reference only.",
                enabled=True,
            ),
            CartographicProfile(
                key="pakistan",
                name="Pakistan publication convention",
                dataset="Natural Earth 1:50m Admin 0 Countries",
                dataset_version="ne_50m_admin_0_countries + ne_10m_admin_0_disputed_areas",
                license="Public domain (Natural Earth)",
                attribution="Natural Earth (public domain); Pakistan cartographic convention",
                disputed_rule=(
                    "Boundary representation follows the publication's Pakistan cartographic convention. "
                    "Territorial status is disputed; the map does not constitute a legal determination."
                ),
                legend="Boundary representation follows the publication's Pakistan convention; territorial status is disputed.",
                enabled=True,
            ),
            CartographicProfile(
                key="egypt",
                name="Egypt publication convention",
                dataset="Natural Earth 1:50m Admin 0 Countries",
                dataset_version="ne_50m_admin_0_countries + ne_10m_admin_0_disputed_areas",
                license="Public domain (Natural Earth)",
                attribution="Natural Earth (public domain); Egypt edition convention",
                disputed_rule=(
                    "Disputed areas are shown for reference with a disclosure note. The map does not "
                    "adjudicate sovereignty."
                ),
                legend="Boundaries: Natural Earth. Disputed areas shown for reference; status disputed.",
                enabled=True,
            ),
        ]
        db.add_all(profiles)
        db.flush()
    if db.scalar(select(ExportTarget).limit(1)) is None:
        db.add(
            ExportTarget(
                name="Local output",
                kind="directory",
                config_json=json.dumps({"path": str(BASE_DIR / "output")}),
                enabled=True,
                validated_at=utcnow(),
            )
        )
        db.flush()
    return profile


def get_or_create_demo_channel(db: Session, owner: User) -> Channel:
    channel = db.scalar(select(Channel).where(Channel.slug == "fixture-desk"))
    if channel is None:
        channel = Channel(
            slug="fixture-desk",
            title="Channel Press Fixture Desk",
            handle="@fixture_newsroom",
            telegram_channel_id=-1001234567890,
            is_public=False,
            is_demo=True,
        )
        db.add(channel)
        db.flush()
        db.add(
            ChannelOwnership(
                channel_id=channel.id,
                owner_user_id=owner.id,
                granted_by_id=owner.id,
                note="Demo fixture channel created during setup.",
            )
        )
        db.flush()
    grant = db.scalar(
        select(RightsGrant).where(RightsGrant.channel_id == channel.id, RightsGrant.revoked_at.is_(None))
    )
    if grant is None:
        db.add(
            RightsGrant(
                channel_id=channel.id,
                grantor_name="Repository owner (synthetic fixture data)",
                authority_basis="Owner-supplied synthetic demonstration content; no real platform content",
                scope_json=json.dumps(["ingest", "ai_process", "publish_fixture_edition"]),
                terms_version="CP-FIXTURE-2026-01",
                evidence_ref="fixtures/README.md",
                created_by=owner.id,
                notes=(
                    "This declaration covers only synthetic fixture content committed to this repository. "
                    "It does not authorize processing of any real Telegram channel."
                ),
            )
        )
        db.flush()
    schedule = db.scalar(
        select(EditionSchedule).where(EditionSchedule.channel_id == channel.id, EditionSchedule.kind == "daily")
    )
    if schedule is None:
        db.add(
            EditionSchedule(
                channel_id=channel.id,
                kind="daily",
                timezone="Asia/Karachi",
                cutoff_time="05:00",
                release_time="08:00",
                weekdays="0,1,2,3,4,5,6",
                enabled=False,
                requires_review=True,
                auto_publish_nonrisk=False,
            )
        )
        db.flush()
    return channel


def mark_setup_complete(db: Session) -> None:
    set_setting(db, "setup_complete", "1")
    set_setting(db, "setup_completed_at", utcnow().isoformat())


def setup_complete(db: Session) -> bool:
    return bool(db.scalar(select(User.id).limit(1))) and get_setting(db, "setup_complete") == "1"
