"""Automatic story discovery metadata and acceptance policy.

Revision ID: b7f2c1d9a310
Revises: a1c7e4f90b21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7f2c1d9a310"
down_revision: Union[str, None] = "a1c7e4f90b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("external_references") as batch:
        batch.add_column(
            sa.Column("search_query", sa.String(length=200), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("relevance", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("publication_profiles") as batch:
        batch.add_column(
            sa.Column(
                "research_auto_accept",
                sa.String(length=20),
                nullable=False,
                server_default="review",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("publication_profiles") as batch:
        batch.drop_column("research_auto_accept")

    with op.batch_alter_table("external_references") as batch:
        batch.drop_column("published_at")
        batch.drop_column("relevance")
        batch.drop_column("search_query")
