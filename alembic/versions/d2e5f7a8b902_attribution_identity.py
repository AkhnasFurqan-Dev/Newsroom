"""Configurable attribution identity: byline and source disclosure."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2e5f7a8b902"
down_revision: Union[str, None] = "c1d4e6f8a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("publication_profiles") as batch:
        batch.add_column(sa.Column("byline", sa.String(length=120), nullable=False, server_default="Editorial Desk"))
        batch.add_column(sa.Column("source_disclosure", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("publication_profiles") as batch:
        batch.drop_column("source_disclosure")
        batch.drop_column("byline")
