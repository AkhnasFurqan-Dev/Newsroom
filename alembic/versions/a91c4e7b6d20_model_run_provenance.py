"""Add model-run provenance without rewriting historical run records.

Revision ID: a91c4e7b6d20
Revises: b7f2c1d9a310
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "a91c4e7b6d20"
down_revision = "b7f2c1d9a310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_runs") as batch:
        batch.add_column(sa.Column("article_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_model_runs_article_id_articles",
            "articles",
            ["article_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_model_runs_article_id", "model_runs", ["article_id"])
    op.add_column(
        "model_runs",
        sa.Column("model_digest", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "model_runs",
        sa.Column("provider_version", sa.String(length=80), nullable=False, server_default=""),
    )
    op.add_column(
        "model_runs",
        sa.Column(
            "generation_settings_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "model_runs",
        sa.Column(
            "evidence_manifest_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "model_runs",
        sa.Column("status", sa.String(length=30), nullable=False, server_default="historical"),
    )


def downgrade() -> None:
    op.drop_column("model_runs", "status")
    op.drop_column("model_runs", "evidence_manifest_json")
    op.drop_column("model_runs", "generation_settings_json")
    op.drop_column("model_runs", "provider_version")
    op.drop_column("model_runs", "model_digest")
    op.drop_index("ix_model_runs_article_id", table_name="model_runs")
    with op.batch_alter_table("model_runs") as batch:
        batch.drop_constraint(
            "fk_model_runs_article_id_articles",
            type_="foreignkey",
        )
        batch.drop_column("article_id")
