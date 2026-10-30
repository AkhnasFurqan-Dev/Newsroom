"""Reader-facing print/edition refinement and automatic research settings.

Revision ID: a1c7e4f90b21
Revises: d8a4b1c2e345
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7e4f90b21"
down_revision: Union[str, None] = "d8a4b1c2e345"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("publication_profiles") as batch:
        batch.add_column(
            sa.Column("show_slogan", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("public_base_url", sa.String(length=500), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column(
                "public_base_url_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("show_chronology", sa.String(length=10), nullable=False, server_default="auto")
        )
        batch.add_column(
            sa.Column(
                "research_policy",
                sa.String(length=30),
                nullable=False,
                server_default="editor_triggered",
            )
        )
        batch.add_column(
            sa.Column(
                "research_provider", sa.String(length=30), nullable=False, server_default="auto"
            )
        )
        batch.add_column(
            sa.Column(
                "research_queries_per_story",
                sa.Integer(),
                nullable=False,
                server_default="3",
            )
        )
        batch.add_column(
            sa.Column(
                "research_max_per_story", sa.Integer(), nullable=False, server_default="5"
            )
        )

    with op.batch_alter_table("editions") as batch:
        batch.add_column(
            sa.Column("display_name", sa.String(length=160), nullable=False, server_default="")
        )

    # The printed source appendix no longer exists. A legacy "appendix" policy
    # means "use the compact per-story code", not "print a provenance page".
    op.execute(
        "UPDATE publication_profiles SET qr_strategy = 'story' "
        "WHERE qr_strategy = 'appendix'"
    )


def downgrade() -> None:
    with op.batch_alter_table("editions") as batch:
        batch.drop_column("display_name")

    with op.batch_alter_table("publication_profiles") as batch:
        batch.drop_column("research_max_per_story")
        batch.drop_column("research_queries_per_story")
        batch.drop_column("research_provider")
        batch.drop_column("research_policy")
        batch.drop_column("show_chronology")
        batch.drop_column("public_base_url_verified")
        batch.drop_column("public_base_url")
        batch.drop_column("show_slogan")
