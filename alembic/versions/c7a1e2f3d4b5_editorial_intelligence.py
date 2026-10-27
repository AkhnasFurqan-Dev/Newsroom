"""Classifier signals and researched external references.

Revision ID: c7a1e2f3d4b5
Revises: b5d7e1a9c234
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a1e2f3d4b5"
down_revision: Union[str, None] = "b5d7e1a9c234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_posts") as batch:
        batch.add_column(
            sa.Column(
                "classification_signals_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )

    with op.batch_alter_table("external_references") as batch:
        # Existing rows (source-cited links) keep their previous behaviour:
        # they stay printable and are treated as already accepted.
        batch.add_column(
            sa.Column(
                "review_status",
                sa.String(length=20),
                nullable=False,
                server_default="accepted",
            )
        )
        batch.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "content_sha256", sa.String(length=64), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "terms_note", sa.String(length=300), nullable=False, server_default=""
            )
        )
        batch.create_index("ix_external_references_review_status", ["review_status"])


def downgrade() -> None:
    with op.batch_alter_table("external_references") as batch:
        batch.drop_index("ix_external_references_review_status")
        batch.drop_column("terms_note")
        batch.drop_column("content_sha256")
        batch.drop_column("http_status")
        batch.drop_column("review_status")

    with op.batch_alter_table("source_posts") as batch:
        batch.drop_column("classification_signals_json")
