"""Per-newspaper print themes and imported font assets.

Revision ID: d8a4b1c2e345
Revises: c7a1e2f3d4b5
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8a4b1c2e345"
down_revision: Union[str, None] = "c7a1e2f3d4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "font_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("family", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("style", sa.String(length=20), nullable=False, server_default="normal"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("filename", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("stored_name", sa.String(length=240), nullable=False, unique=True),
        sa.Column("relative_path", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("license_note", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("license_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_font_assets_sha256", "font_assets", ["sha256"])

    op.create_table(
        "theme_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "publication_id",
            sa.Integer(),
            sa.ForeignKey("publication_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(length=160), nullable=False, server_default="Standard edition"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("colors_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fonts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("publication_id", "revision", name="uq_theme_revision"),
    )
    op.create_index("ix_theme_revisions_publication_id", "theme_revisions", ["publication_id"])
    op.create_index("ix_theme_revisions_is_current", "theme_revisions", ["is_current"])

    with op.batch_alter_table("editions") as batch:
        batch.add_column(sa.Column("theme_revision_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("theme_snapshot_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column(
                "preview_theme_hash", sa.String(length=64), nullable=False, server_default=""
            )
        )
        batch.create_foreign_key(
            "fk_editions_theme_revision_id",
            "theme_revisions",
            ["theme_revision_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("editions") as batch:
        batch.drop_constraint("fk_editions_theme_revision_id", type_="foreignkey")
        batch.drop_column("preview_theme_hash")
        batch.drop_column("theme_snapshot_json")
        batch.drop_column("theme_revision_id")
    op.drop_index("ix_theme_revisions_is_current", table_name="theme_revisions")
    op.drop_index("ix_theme_revisions_publication_id", table_name="theme_revisions")
    op.drop_table("theme_revisions")
    op.drop_index("ix_font_assets_sha256", table_name="font_assets")
    op.drop_table("font_assets")
