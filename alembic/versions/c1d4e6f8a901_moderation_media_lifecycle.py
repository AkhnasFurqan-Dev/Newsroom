"""Persist post moderation and media retention state."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1d4e6f8a901"
down_revision: Union[str, None] = "b7c1f2d4e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_posts") as batch:
        batch.add_column(sa.Column("classification", sa.String(length=40), nullable=False, server_default="editorially_relevant"))
        batch.add_column(sa.Column("classification_reason", sa.String(length=240), nullable=False, server_default=""))
        batch.add_column(sa.Column("classification_version", sa.String(length=60), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("moderation_status", sa.String(length=30), nullable=False, server_default="none"))
        batch.add_column(sa.Column("editor_override", sa.String(length=40), nullable=False, server_default=""))
    with op.batch_alter_table("post_media") as batch:
        batch.add_column(sa.Column("retention_state", sa.String(length=30), nullable=False, server_default="shared_source_cache"))
        batch.add_column(sa.Column("last_download_error", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("edition_id", sa.Integer(), nullable=True))
        batch.create_index("ix_post_media_edition_id", ["edition_id"])


def downgrade() -> None:
    with op.batch_alter_table("post_media") as batch:
        batch.drop_index("ix_post_media_edition_id")
        batch.drop_column("edition_id")
        batch.drop_column("deleted_at")
        batch.drop_column("last_download_error")
        batch.drop_column("retention_state")
    with op.batch_alter_table("source_posts") as batch:
        for column in ("editor_override", "moderation_status", "classification_version", "classification_reason", "classification"):
            batch.drop_column(column)
