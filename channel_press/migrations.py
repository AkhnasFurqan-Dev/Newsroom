from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import BASE_DIR, settings


def alembic_config() -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return cfg


def upgrade_database() -> None:
    settings.ensure_dirs()
    command.upgrade(alembic_config(), "head")


def current_revision() -> str | None:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    engine = create_engine(settings.effective_database_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()
