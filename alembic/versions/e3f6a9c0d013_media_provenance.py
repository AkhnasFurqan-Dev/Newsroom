"""Record classification timestamps and full media provenance.

Adds the fields required to audit a selected asset long after its cached file
has been deleted: download state and time, original filename, byte size,
rights grant, attempt count and the selected video-frame timestamp.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3f6a9c0d013"
down_revision: Union[str, None] = "d2e5f7a8b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_posts") as batch:
        batch.add_column(sa.Column("classified_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("post_media") as batch:
        batch.add_column(sa.Column("download_state", sa.String(length=30), nullable=False, server_default="discovered"))
        batch.add_column(sa.Column("original_filename", sa.String(length=240), nullable=False, server_default=""))
        batch.add_column(sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("downloaded_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("rights_grant_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("article_media") as batch:
        batch.add_column(sa.Column("frame_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("article_media") as batch:
        batch.drop_column("frame_seconds")
    with op.batch_alter_table("post_media") as batch:
        batch.drop_column("rights_grant_id")
        batch.drop_column("downloaded_at")
        batch.drop_column("download_attempts")
        batch.drop_column("bytes")
        batch.drop_column("original_filename")
        batch.drop_column("download_state")
    with op.batch_alter_table("source_posts") as batch:
        batch.drop_column("classified_at")
