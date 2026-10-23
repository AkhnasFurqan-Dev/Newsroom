"""post-M14 collection windows, retrieval checkpoints and import window keys

Revision ID: b7c1f2d4e901
Revises: 54aaea637b58
Create Date: 2026-10-23 09:10:00.000000

Additive only: no existing table is rewritten and no row is deleted. Existing
databases keep their posts, rights, editions and users.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import channel_press.db


revision: str = "b7c1f2d4e901"
down_revision: Union[str, None] = "54aaea637b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edition_windows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("window_key", sa.String(length=200), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("window_start", channel_press.db.UTCDateTime(), nullable=False),
        sa.Column("window_end", channel_press.db.UTCDateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("cutoff_local", sa.String(length=5), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("skip_reason", sa.String(length=240), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("edition_id", sa.Integer(), nullable=True),
        sa.Column("stats_json", sa.Text(), nullable=False),
        sa.Column("lock_owner", sa.String(length=120), nullable=False),
        sa.Column("lock_expires_at", channel_press.db.UTCDateTime(), nullable=True),
        sa.Column("collected_at", channel_press.db.UTCDateTime(), nullable=True),
        sa.Column("committed_at", channel_press.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", channel_press.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", channel_press.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("window_key", name="uq_edition_window_key"),
    )
    op.create_index("ix_edition_windows_window_key", "edition_windows", ["window_key"])
    op.create_index("ix_edition_windows_channel_id", "edition_windows", ["channel_id"])
    op.create_index("ix_edition_windows_window_start", "edition_windows", ["window_start"])
    op.create_index("ix_edition_windows_window_end", "edition_windows", ["window_end"])
    op.create_index("ix_edition_windows_status", "edition_windows", ["status"])

    op.create_table(
        "retrieval_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("window_key", sa.String(length=200), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("cursor_message_id", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("updated_at", channel_press.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("window_key", name="uq_retrieval_window_key"),
    )
    op.create_index("ix_retrieval_checkpoints_window_key", "retrieval_checkpoints", ["window_key"])
    op.create_index("ix_retrieval_checkpoints_channel_id", "retrieval_checkpoints", ["channel_id"])

    with op.batch_alter_table("source_imports") as batch_op:
        batch_op.add_column(
            sa.Column("window_key", sa.String(length=200), nullable=False, server_default="")
        )
        batch_op.create_index("ix_source_imports_window_key", ["window_key"])


def downgrade() -> None:
    with op.batch_alter_table("source_imports") as batch_op:
        batch_op.drop_index("ix_source_imports_window_key")
        batch_op.drop_column("window_key")
    op.drop_table("retrieval_checkpoints")
    op.drop_table("edition_windows")
