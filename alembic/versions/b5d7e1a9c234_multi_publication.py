"""Multi-publication identities, source membership and cross-source attribution.

Revision ID: b5d7e1a9c234
Revises: f4b8c2d6e123
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5d7e1a9c234"
down_revision: Union[str, None] = "f4b8c2d6e123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("publication_profiles") as batch:
        batch.add_column(
            sa.Column("slug", sa.String(length=80), nullable=False, server_default="main")
        )
        batch.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1"))
        )
        batch.create_unique_constraint("uq_publication_profile_slug", ["slug"])

    with op.batch_alter_table("editions") as batch:
        batch.add_column(sa.Column("publication_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "source_channel_ids_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
        batch.add_column(
            sa.Column("source_status", sa.String(length=20), nullable=False, server_default="complete")
        )
        batch.create_foreign_key(
            "fk_editions_publication_id",
            "publication_profiles",
            ["publication_id"],
            ["id"],
        )
        batch.create_index("ix_editions_publication_id", ["publication_id"])

    with op.batch_alter_table("edition_schedules") as batch:
        batch.add_column(sa.Column("publication_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_edition_schedules_publication_id",
            "publication_profiles",
            ["publication_id"],
            ["id"],
        )
        batch.create_index("ix_edition_schedules_publication_id", ["publication_id"])

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("publication_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_publication_id",
            "publication_profiles",
            ["publication_id"],
            ["id"],
        )
        batch.create_index("ix_jobs_publication_id", ["publication_id"])

    op.create_table(
        "publication_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publication_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("publication_id", "channel_id", name="uq_publication_source"),
    )
    op.create_index(
        "ix_publication_sources_publication_id", "publication_sources", ["publication_id"]
    )
    op.create_index(
        "ix_publication_sources_channel_id", "publication_sources", ["channel_id"]
    )

    op.create_table(
        "publication_editors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="editor"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publication_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("publication_id", "user_id", name="uq_publication_editor"),
    )
    op.create_index(
        "ix_publication_editors_publication_id", "publication_editors", ["publication_id"]
    )
    op.create_index("ix_publication_editors_user_id", "publication_editors", ["user_id"])

    op.create_table(
        "article_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("article_id", "channel_id", name="uq_article_source"),
    )
    op.create_index("ix_article_sources_article_id", "article_sources", ["article_id"])
    op.create_index("ix_article_sources_channel_id", "article_sources", ["channel_id"])

    connection = op.get_bind()
    # One existing installation became the first publication. Every channel it
    # already used stays enabled, and existing editions/schedules/jobs are
    # attached so nothing disappears from the archive after the upgrade.
    connection.execute(
        sa.text(
            "UPDATE publication_profiles SET slug = 'main', is_active = 1 "
            "WHERE id = (SELECT MIN(id) FROM publication_profiles)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO publication_sources (publication_id, channel_id, priority, weight, is_enabled, created_at) "
            "SELECT (SELECT MIN(id) FROM publication_profiles), channels.id, channels.id, 1.0, 1, "
            "CURRENT_TIMESTAMP FROM channels "
            "WHERE (SELECT MIN(id) FROM publication_profiles) IS NOT NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE editions SET publication_id = (SELECT MIN(id) FROM publication_profiles), "
            "source_channel_ids_json = '[' || channel_id || ']', source_status = 'complete' "
            "WHERE publication_id IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE edition_schedules SET publication_id = (SELECT MIN(id) FROM publication_profiles) "
            "WHERE publication_id IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE jobs SET publication_id = (SELECT MIN(id) FROM publication_profiles) "
            "WHERE publication_id IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_publication_id")
        batch.drop_constraint("fk_jobs_publication_id", type_="foreignkey")
        batch.drop_column("publication_id")
    with op.batch_alter_table("edition_schedules") as batch:
        batch.drop_index("ix_edition_schedules_publication_id")
        batch.drop_constraint("fk_edition_schedules_publication_id", type_="foreignkey")
        batch.drop_column("publication_id")
    with op.batch_alter_table("editions") as batch:
        batch.drop_index("ix_editions_publication_id")
        batch.drop_constraint("fk_editions_publication_id", type_="foreignkey")
        batch.drop_column("source_status")
        batch.drop_column("source_channel_ids_json")
        batch.drop_column("publication_id")
    op.drop_table("article_sources")
    op.drop_table("publication_editors")
    op.drop_table("publication_sources")
    with op.batch_alter_table("publication_profiles") as batch:
        batch.drop_constraint("uq_publication_profile_slug", type_="unique")
        batch.drop_column("is_active")
        batch.drop_column("slug")
