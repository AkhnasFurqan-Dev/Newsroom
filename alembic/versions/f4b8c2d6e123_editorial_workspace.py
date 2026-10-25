"""Editorial workspace, revisioned article content and edition previews.

Revision ID: f4b8c2d6e123
Revises: e3f6a9c0d013
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4b8c2d6e123"
down_revision: Union[str, None] = "e3f6a9c0d013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("articles") as batch:
        batch.add_column(sa.Column("byline", sa.String(length=120), nullable=False, server_default=""))
    with op.batch_alter_table("editions") as batch:
        batch.add_column(sa.Column("preview_state", sa.String(length=20), nullable=False, server_default="missing"))
        batch.add_column(sa.Column("preview_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("preview_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("preview_dir", sa.String(length=500), nullable=False, server_default=""))
        batch.add_column(sa.Column("preview_pdf_path", sa.String(length=500), nullable=False, server_default=""))
    with op.batch_alter_table("edition_articles") as batch:
        batch.add_column(sa.Column("article_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("edition_articles") as batch:
        batch.drop_column("snapshot_json")
        batch.drop_column("article_revision")
    with op.batch_alter_table("editions") as batch:
        batch.drop_column("preview_pdf_path")
        batch.drop_column("preview_dir")
        batch.drop_column("preview_updated_at")
        batch.drop_column("preview_revision")
        batch.drop_column("preview_state")
    with op.batch_alter_table("articles") as batch:
        batch.drop_column("byline")
