from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime
from .timeutil import utcnow

# ---------------------------------------------------------------------------
# identity and governance
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(60), default="")
    object_id: Mapped[str] = mapped_column(String(60), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    handle: Mapped[str] = mapped_column(String(120), default="")
    telegram_channel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelOwnership(Base):
    __tablename__ = "channel_ownership"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), unique=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    granted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    note: Mapped[str] = mapped_column(String(500), default="")


class ChannelPermission(Base):
    __tablename__ = "channel_permissions"
    __table_args__ = (UniqueConstraint("channel_id", "user_id", name="uq_channel_permission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    granted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RightsGrant(Base):
    __tablename__ = "rights_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    grantor_name: Mapped[str] = mapped_column(String(160))
    authority_basis: Mapped[str] = mapped_column(String(240))
    scope_json: Mapped[str] = mapped_column(Text, default="[]")
    terms_version: Mapped[str] = mapped_column(String(40), default="CP-CONTENT-2026-01")
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ref: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class ContributorConsent(Base):
    __tablename__ = "contributor_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    contributor: Mapped[str] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(20), default="granted")
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    evidence_ref: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# source ingestion
# ---------------------------------------------------------------------------


class SourceImport(Base):
    __tablename__ = "source_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="json")
    original_name: Mapped[str] = mapped_column(String(255), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    imported_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    raw_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    rights_grant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rights_grants.id"), nullable=True)
    window_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class SourcePost(Base):
    __tablename__ = "source_posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "telegram_message_id", name="uq_source_message"),
        Index("ix_source_posts_window", "channel_id", "posted_at", "inclusion_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_imports.id"), nullable=True)
    rights_grant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rights_grants.id"), nullable=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    grouped_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    edited_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    forward_origin: Mapped[str] = mapped_column(String(160), default="")
    is_forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    inclusion_status: Mapped[str] = mapped_column(String(20), default="included", index=True)
    exclusion_reason: Mapped[str] = mapped_column(String(80), default="")
    classification: Mapped[str] = mapped_column(String(40), default="editorially_relevant")
    classification_reason: Mapped[str] = mapped_column(String(240), default="")
    classification_signals_json: Mapped[str] = mapped_column(Text, default="[]")
    classification_version: Mapped[str] = mapped_column(String(60), default="legacy")
    classified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    moderation_status: Mapped[str] = mapped_column(String(30), default="none")
    editor_override: Mapped[str] = mapped_column(String(40), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class PostVersion(Base):
    __tablename__ = "post_versions"
    __table_args__ = (UniqueConstraint("post_id", "version", name="uq_post_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    edited_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PostMedia(Base):
    __tablename__ = "post_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="photo")
    telegram_file_id: Mapped[str] = mapped_column(String(255), default="")
    original_path: Mapped[str] = mapped_column(String(500), default="")
    derivative_path: Mapped[str] = mapped_column(String(500), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    credit: Mapped[str] = mapped_column(String(240), default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_documentary: Mapped[bool] = mapped_column(Boolean, default=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    retention_state: Mapped[str] = mapped_column(String(30), default="shared_source_cache")
    download_state: Mapped[str] = mapped_column(String(30), default="discovered")
    original_filename: Mapped[str] = mapped_column(String(240), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    download_attempts: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    rights_grant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rights_grants.id"), nullable=True)
    last_download_error: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    edition_id: Mapped[Optional[int]] = mapped_column(ForeignKey("editions.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PostFragment(Base):
    __tablename__ = "post_fragments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[Optional[int]] = mapped_column(ForeignKey("story_clusters.id"), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer, default=0)
    end_offset: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(16), default="en")
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    editorial_unit_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReplyEdge(Base):
    __tablename__ = "reply_edges"
    __table_args__ = (UniqueConstraint("from_post_id", "to_post_id", name="uq_reply_edge"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"))
    to_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(20), default="reply")


# ---------------------------------------------------------------------------
# story memory, evidence and editorial
# ---------------------------------------------------------------------------


class StoryCluster(Base):
    __tablename__ = "story_clusters"
    __table_args__ = (UniqueConstraint("channel_id", "fingerprint", name="uq_story_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    canonical_title: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    section: Mapped[str] = mapped_column(String(40), default="national", index=True)
    genre: Mapped[str] = mapped_column(String(20), default="news")
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    fingerprint: Mapped[str] = mapped_column(String(64))
    entity_json: Mapped[str] = mapped_column(Text, default="[]")
    merged_into_id: Mapped[Optional[int]] = mapped_column(ForeignKey("story_clusters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class StoryRelationship(Base):
    __tablename__ = "story_relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True)
    related_story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(30), default="related")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class StoryClaim(Base):
    __tablename__ = "story_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(30), default="statement")
    certainty: Mapped[str] = mapped_column(String(30), default="attributed")
    verification_status: Mapped[str] = mapped_column(String(30), default="single_source")
    source_post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_posts.id"), nullable=True)
    source_fragment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("post_fragments.id"), nullable=True)
    source_span: Mapped[str] = mapped_column(String(500), default="")
    event_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    correction_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("story_claims.id"), nullable=True)
    contradictions_json: Mapped[str] = mapped_column(Text, default="[]")
    external_support_json: Mapped[str] = mapped_column(Text, default="[]")
    risk_tag: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StoryTimelineEvent(Base):
    __tablename__ = "story_timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True)
    event_at: Mapped[datetime] = mapped_column(UTCDateTime)
    description: Mapped[str] = mapped_column(Text)
    source_post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_posts.id"), nullable=True)
    certainty: Mapped[str] = mapped_column(String(30), default="attributed")


class ExternalReference(Base):
    __tablename__ = "external_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str] = mapped_column(String(160), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    accessed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(80), default="manual")
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Research references stay "candidate" until an editor accepts them. Only
    # accepted references may reach the printed appendix, and even then they
    # remain editorial context rather than claim evidence.
    review_status: Mapped[str] = mapped_column(String(20), default="accepted", index=True)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    terms_note: Mapped[str] = mapped_column(String(300), default="")
    # Discovery metadata: which query found this candidate, how closely its
    # title matched the story, and the outlet's own publication date. These are
    # internal editorial signals, never printed as reader-facing provenance.
    search_query: Mapped[str] = mapped_column(String(200), default="")
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    deck: Mapped[str] = mapped_column(String(300), default="")
    standfirst: Mapped[str] = mapped_column(Text, default="")
    body_json: Mapped[str] = mapped_column(Text, default="[]")
    body_text: Mapped[str] = mapped_column(Text, default="")
    byline: Mapped[str] = mapped_column(String(120), default="")
    genre: Mapped[str] = mapped_column(String(20), default="news")
    section: Mapped[str] = mapped_column(String(40), default="national")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    audit_json: Mapped[str] = mapped_column(Text, default="{}")
    style_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ArticleVersion(Base):
    __tablename__ = "article_versions"
    __table_args__ = (UniqueConstraint("article_id", "revision", name="uq_article_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class ArticleClaimLink(Base):
    __tablename__ = "article_claim_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("story_claims.id", ondelete="CASCADE"))
    sentence: Mapped[str] = mapped_column(Text, default="")
    supported: Mapped[bool] = mapped_column(Boolean, default=True)


class ArticleMedia(Base):
    __tablename__ = "article_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[Optional[int]] = mapped_column(ForeignKey("post_media.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="lead")
    caption: Mapped[str] = mapped_column(Text, default="")
    credit: Mapped[str] = mapped_column(String(240), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    graphic_kind: Mapped[str] = mapped_column(String(30), default="photo")
    frame_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)


class ReviewFlag(Base):
    __tablename__ = "review_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=True, index=True)
    story_id: Mapped[Optional[int]] = mapped_column(ForeignKey("story_clusters.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20), default="review")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    decision: Mapped[str] = mapped_column(String(20), default="")
    resolved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EditorialOverride(Base):
    __tablename__ = "editorial_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    value_json: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# publication configuration and editions
# ---------------------------------------------------------------------------


class PublicationProfile(Base):
    __tablename__ = "publication_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, default="main")
    name: Mapped[str] = mapped_column(String(160), default="The Channel Dispatch")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_handle: Mapped[str] = mapped_column(String(120), default="@channel")
    byline: Mapped[str] = mapped_column(String(120), default="Editorial Desk")
    source_disclosure: Mapped[str] = mapped_column(Text, default="")
    slogan: Mapped[str] = mapped_column(String(240), default="A verified roundup of the day's reporting")
    show_slogan: Mapped[bool] = mapped_column(Boolean, default=False)
    footer_text: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(120), default="Islamabad")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Karachi")
    accent: Mapped[str] = mapped_column(String(20), default="#7b1f24")
    paper_size: Mapped[str] = mapped_column(String(20), default="tabloid")
    contact_url: Mapped[str] = mapped_column(String(500), default="")
    map_profile: Mapped[str] = mapped_column(String(40), default="neutral")
    qr_strategy: Mapped[str] = mapped_column(String(40), default="story")
    # Public evidence-page origin, e.g. https://news.example.org/edition.
    # A printed story QR is only pointed here when the owner has explicitly
    # verified that the origin is deployed and publicly reachable.
    public_base_url: Mapped[str] = mapped_column(String(500), default="")
    public_base_url_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # auto | on | off. ``auto`` is deliberately conservative: a chronology is
    # printed only for a genuinely staged sequence, never for repeated bulletins.
    show_chronology: Mapped[str] = mapped_column(String(10), default="auto")
    # off | editor_triggered | automatic_substantial | automatic_all
    research_policy: Mapped[str] = mapped_column(String(30), default="editor_triggered")
    research_provider: Mapped[str] = mapped_column(String(30), default="auto")
    research_queries_per_story: Mapped[int] = mapped_column(Integer, default=3)
    research_max_per_story: Mapped[int] = mapped_column(Integer, default=5)
    # review | auto_context. ``review`` keeps every discovered page as a
    # candidate for an editor. ``auto_context`` accepts only ordinary, dated,
    # reachable non-social context; contested or high-risk stories still wait.
    research_auto_accept: Mapped[str] = mapped_column(String(20), default="review")
    issue_volume: Mapped[int] = mapped_column(Integer, default=1)
    section_order_json: Mapped[str] = mapped_column(Text, default="[]")
    allow_closing_light: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class PublicationSource(Base):
    """An authorized channel that feeds one newspaper.

    ``priority`` is 1-based and lower wins; ``weight`` scales a source's story
    importance so an editor can say a wire is secondary without disabling it.
    """

    __tablename__ = "publication_sources"
    __table_args__ = (
        UniqueConstraint("publication_id", "channel_id", name="uq_publication_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication_profiles.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FontAsset(Base):
    """An editor-uploaded font file held in the managed font library.

    Assets are content-addressed and never addressed by a user-supplied path.
    Shared assets can be referenced by several newspapers; deletion is only
    allowed when no theme revision references the row.
    """

    __tablename__ = "font_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(String(160), default="")
    style: Mapped[str] = mapped_column(String(20), default="normal")
    weight: Mapped[int] = mapped_column(Integer, default=400)
    filename: Mapped[str] = mapped_column(String(240), default="")
    stored_name: Mapped[str] = mapped_column(String(240), unique=True)
    relative_path: Mapped[str] = mapped_column(String(400), default="")
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    license_note: Mapped[str] = mapped_column(String(400), default="")
    license_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ThemeRevision(Base):
    """Immutable snapshot of one newspaper's print design.

    ``is_current`` marks the revision used for future drafts and previews.
    Published editions pin the revision they rendered with, so changing the
    newspaper's colours or fonts never rewrites an archived issue.
    """

    __tablename__ = "theme_revisions"
    __table_args__ = (
        UniqueConstraint("publication_id", "revision", name="uq_theme_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication_profiles.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(160), default="Standard edition")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    colors_json: Mapped[str] = mapped_column(Text, default="{}")
    fonts_json: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(String(400), default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PublicationEditor(Base):
    """Per-newspaper editorial access, independent of global user role."""

    __tablename__ = "publication_editors"
    __table_args__ = (
        UniqueConstraint("publication_id", "user_id", name="uq_publication_editor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication_profiles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="editor")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ArticleSource(Base):
    """Cross-source attribution for a drafted article.

    A merged story can draw on more than one authorized channel; this table
    records which channels contributed and how many posts each supplied.
    """

    __tablename__ = "article_sources"
    __table_args__ = (UniqueConstraint("article_id", "channel_id", name="uq_article_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    first_posted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CartographicProfile(Base):
    __tablename__ = "cartographic_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    dataset: Mapped[str] = mapped_column(String(240))
    dataset_version: Mapped[str] = mapped_column(String(80))
    license: Mapped[str] = mapped_column(String(120))
    attribution: Mapped[str] = mapped_column(String(300))
    disputed_rule: Mapped[str] = mapped_column(Text, default="")
    legend: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EditionSchedule(Base):
    __tablename__ = "edition_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    publication_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("publication_profiles.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="daily")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Karachi")
    cutoff_time: Mapped[str] = mapped_column(String(5), default="05:00")
    release_time: Mapped[str] = mapped_column(String(5), default="08:00")
    weekdays: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_publish_nonrisk: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EditionWatermark(Base):
    __tablename__ = "edition_watermarks"
    __table_args__ = (UniqueConstraint("channel_id", "kind", name="uq_watermark_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30), default="daily")
    last_cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EditionWindowRecord(Base):
    """Durable record of one ``[start, end)`` collection window.

    Created when a job is enqueued, before retrieval starts. The committed
    watermark is derived from these rows, so a failed retrieval or an
    unapproved draft can never advance it.
    """

    __tablename__ = "edition_windows"
    __table_args__ = (UniqueConstraint("window_key", name="uq_edition_window_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    window_key: Mapped[str] = mapped_column(String(200), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="daily")
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Karachi")
    cutoff_local: Mapped[str] = mapped_column(String(5), default="05:00")
    # pending | collecting | collected | built | awaiting_review | committed |
    # no_content | skipped | failed | superseded
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    skip_reason: Mapped[str] = mapped_column(String(240), default="")
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    edition_id: Mapped[Optional[int]] = mapped_column(ForeignKey("editions.id"), nullable=True)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    lock_owner: Mapped[str] = mapped_column(String(120), default="")
    lock_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    collected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    committed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class RetrievalCheckpoint(Base):
    """Per-window pagination checkpoint, separate from the committed watermark."""

    __tablename__ = "retrieval_checkpoints"
    __table_args__ = (UniqueConstraint("window_key", name="uq_retrieval_window_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    window_key: Mapped[str] = mapped_column(String(200), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    adapter: Mapped[str] = mapped_column(String(40), default="telegram")
    cursor_message_id: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Edition(Base):
    __tablename__ = "editions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    publication_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("publication_profiles.id"), nullable=True, index=True
    )
    source_channel_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_status: Mapped[str] = mapped_column(String(20), default="complete")
    kind: Mapped[str] = mapped_column(String(30), default="daily")
    title: Mapped[str] = mapped_column(String(240), default="")
    # Reader-facing edition name ("Morning Edition", "Friday Magazine").
    # Empty means the editor has not named this issue; templates fall back to
    # a neutral date-based description instead of internal kind labels.
    display_name: Mapped[str] = mapped_column(String(160), default="")
    volume: Mapped[int] = mapped_column(Integer, default=1)
    issue_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime)
    target_release_at: Mapped[datetime] = mapped_column(UTCDateTime)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Karachi")
    version: Mapped[int] = mapped_column(Integer, default=1)
    correction_note: Mapped[str] = mapped_column(Text, default="")
    parent_edition_id: Mapped[Optional[int]] = mapped_column(ForeignKey("editions.id"), nullable=True)
    profile_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    output_dir: Mapped[str] = mapped_column(String(500), default="")
    pdf_path: Mapped[str] = mapped_column(String(500), default="")
    epaper_dir: Mapped[str] = mapped_column(String(500), default="")
    qr_policy: Mapped[str] = mapped_column(String(40), default="appendix")
    # Draft preview state. A preview is never a published version: it lives in
    # a hidden preview folder and is invalidated whenever an article revision
    # used by this edition changes.
    preview_state: Mapped[str] = mapped_column(String(20), default="current")
    preview_revision: Mapped[int] = mapped_column(Integer, default=0)
    preview_updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    preview_dir: Mapped[str] = mapped_column(String(500), default="")
    preview_pdf_path: Mapped[str] = mapped_column(String(500), default="")
    # Print design pinned to this edition (immutable once published).
    theme_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("theme_revisions.id"), nullable=True
    )
    theme_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_theme_hash: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EditionArticle(Base):
    __tablename__ = "edition_articles"
    __table_args__ = (UniqueConstraint("edition_id", "article_id", name="uq_edition_article"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    section: Mapped[str] = mapped_column(String(40), default="national")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    is_front: Mapped[bool] = mapped_column(Boolean, default=False)
    # Frozen copy of the article at the moment this edition version was
    # published. It is what makes V1 immune to later article edits even though
    # the working Article row keeps its revision history.
    article_revision: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")


class EditionPage(Base):
    __tablename__ = "edition_pages"
    __table_args__ = (UniqueConstraint("edition_id", "page_number", name="uq_edition_page"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(String(60), default="national")
    html: Mapped[str] = mapped_column(Text, default="")
    qa_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_path: Mapped[str] = mapped_column(String(500), default="")
    story_map_json: Mapped[str] = mapped_column(Text, default="[]")


class EditionAsset(Base):
    __tablename__ = "edition_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="manifest")
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)


class SourceAppendix(Base):
    __tablename__ = "source_appendices"

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="Sources")
    content_html: Mapped[str] = mapped_column(Text, default="")
    entries_json: Mapped[str] = mapped_column(Text, default="[]")


class ExportTarget(Base):
    __tablename__ = "export_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="directory")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PublicationAttempt(Base):
    __tablename__ = "publication_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    destination: Mapped[str] = mapped_column(String(220), default="local")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


# ---------------------------------------------------------------------------
# jobs, diagnostics and caches
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="edition")
    channel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("channels.id"), nullable=True)
    publication_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("publication_profiles.id"), nullable=True, index=True
    )
    edition_id: Mapped[Optional[int]] = mapped_column(ForeignKey("editions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    lock_owner: Mapped[str] = mapped_column(String(120), default="")
    lock_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class RetryAttempt(Base):
    __tablename__ = "retry_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    task: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    output_hash: Mapped[str] = mapped_column(String(64), default="")
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProcessingCache(Base):
    __tablename__ = "processing_cache"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
