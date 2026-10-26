"""Newspaper identity, source membership and server-side publication access.

A *publication* is one newspaper title with its own branding, enabled source
channels, schedules, drafts, editions, archive and editors. Source channels are
authorized independently; linking a channel to a publication never grants
Telegram access by itself.

The resolver is deliberately conservative:

* an owner may open every active publication;
* a user with an explicit ``PublicationEditor`` row needs that role;
* otherwise, compatibility falls back to channel-level access for an in-place
  single-paper upgrade;
* an unknown, disabled or unauthorized publication falls back to the first one
  the user can actually use, so no page leaks another paper's data.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .constants import ROLE_RANK
from .models import (
    Channel,
    Edition,
    PublicationEditor,
    PublicationProfile,
    PublicationSource,
    User,
)

DEFAULT_SLUG = "main"
PUBLICATION_COOKIE = "cp_publication"


def slugify(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")
    return candidate[:70] or "publication"


def unique_slug(db: Session, value: str, *, exclude_id: int | None = None) -> str:
    base = slugify(value)
    candidate = base
    counter = 2
    while True:
        row = db.scalar(select(PublicationProfile).where(PublicationProfile.slug == candidate))
        if row is None or row.id == exclude_id:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


def default_publication(db: Session) -> PublicationProfile | None:
    return db.scalar(
        select(PublicationProfile)
        .where(PublicationProfile.is_active.is_(True))
        .order_by(PublicationProfile.id.asc())
        .limit(1)
    ) or db.scalar(select(PublicationProfile).order_by(PublicationProfile.id.asc()).limit(1))


def publication_sources(
    db: Session,
    publication: PublicationProfile,
    *,
    enabled_only: bool = False,
) -> list[PublicationSource]:
    stmt = (
        select(PublicationSource)
        .where(PublicationSource.publication_id == publication.id)
        .order_by(PublicationSource.priority.asc(), PublicationSource.id.asc())
    )
    if enabled_only:
        stmt = stmt.where(PublicationSource.is_enabled.is_(True))
    return list(db.scalars(stmt))


def publication_channels(
    db: Session,
    publication: PublicationProfile,
    *,
    enabled_only: bool = True,
) -> list[Channel]:
    links = publication_sources(db, publication, enabled_only=enabled_only)
    if not links:
        return []
    channels = {
        channel.id: channel
        for channel in db.scalars(
            select(Channel).where(Channel.id.in_([link.channel_id for link in links]))
        )
    }
    return [channels[link.channel_id] for link in links if link.channel_id in channels]


def unassigned_channels(db: Session, user: User, minimum: str = "viewer") -> list[Channel]:
    """Authorized channels that are not yet linked to any newspaper.

    An upgraded single-paper installation or a channel created directly through
    the CLI can be built before an editor links it. It must never be treated as
    belonging to another paper, so it is offered only as an explicitly
    unassigned source.
    """
    from .auth import can_access_channel

    linked = {row for row in db.scalars(select(PublicationSource.channel_id))}
    return [
        channel
        for channel in db.scalars(select(Channel).order_by(Channel.id))
        if channel.id not in linked and can_access_channel(db, user, channel, minimum)
    ]


def channel_is_unassigned(db: Session, channel_id: int) -> bool:
    return (
        db.scalar(select(PublicationSource.id).where(PublicationSource.channel_id == channel_id))
        is None
    )


def can_access_publication(
    db: Session,
    user: User,
    publication: PublicationProfile | None,
    minimum: str = "viewer",
) -> bool:
    if publication is None:
        return False
    if user.role == "owner":
        return True
    membership = db.scalar(
        select(PublicationEditor).where(
            PublicationEditor.publication_id == publication.id,
            PublicationEditor.user_id == user.id,
        )
    )
    if membership is not None:
        return ROLE_RANK.get(membership.role, 0) >= ROLE_RANK.get(minimum, 0)
    # Compatibility for a single-paper installation upgraded in place: a user
    # who can already open one of the paper's channels keeps that access until
    # an owner records explicit per-newspaper membership. The check is still
    # server-side and never reaches a paper whose channels the user cannot see.
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(minimum, 0):
        return False
    from .auth import can_access_channel

    return any(
        can_access_channel(db, user, channel)
        for channel in publication_channels(db, publication, enabled_only=False)
    )


def accessible_publications(db: Session, user: User) -> list[PublicationProfile]:
    rows = list(
        db.scalars(
            select(PublicationProfile)
            .where(PublicationProfile.is_active.is_(True))
            .order_by(PublicationProfile.id.asc())
        )
    )
    return [row for row in rows if can_access_publication(db, user, row)]


def resolve_publication(db: Session, request, user: User) -> PublicationProfile | None:
    """Resolve the active paper from query, cookie, then the first accessible one."""
    options = accessible_publications(db, user)
    if not options:
        return None
    allowed = {row.id: row for row in options}
    by_slug = {(row.slug or ""): row for row in options}

    requested = ""
    try:
        requested = str(request.query_params.get("publication") or "").strip()
    except AttributeError:
        requested = ""
    if not requested:
        requested = str(getattr(request, "cookies", {}).get(PUBLICATION_COOKIE) or "").strip()
    if requested:
        if requested.isdigit() and int(requested) in allowed:
            return allowed[int(requested)]
        if requested in by_slug:
            return by_slug[requested]
        lowered = requested.casefold()
        for row in options:
            if (row.slug or "").casefold() == lowered:
                return row
    return options[0]


def publication_for_edition(db: Session, edition: Edition) -> PublicationProfile | None:
    if edition.publication_id:
        found = db.get(PublicationProfile, edition.publication_id)
        if found is not None:
            return found
    channel = db.get(Channel, edition.channel_id)
    if channel is not None:
        link = db.scalar(
            select(PublicationSource)
            .where(PublicationSource.channel_id == channel.id)
            .order_by(PublicationSource.priority.asc(), PublicationSource.id.asc())
            .limit(1)
        )
        if link is not None:
            return db.get(PublicationProfile, link.publication_id)
    return default_publication(db)


def edition_source_channels(db: Session, edition: Edition) -> list[Channel]:
    import json

    try:
        ids = [int(value) for value in json.loads(edition.source_channel_ids_json or "[]")]
    except (TypeError, ValueError):
        ids = []
    if not ids:
        ids = [edition.channel_id]
    rows = list(db.scalars(select(Channel).where(Channel.id.in_(ids)).order_by(Channel.id.asc())))
    return rows


def source_priority_map(db: Session, publication: PublicationProfile | None) -> dict[int, tuple[int, float]]:
    if publication is None:
        return {}
    return {
        link.channel_id: (link.priority, link.weight)
        for link in publication_sources(db, publication, enabled_only=False)
    }


def serialise_channel_ids(channels: list[Channel]) -> str:
    import json

    return json.dumps(sorted({channel.id for channel in channels}))
