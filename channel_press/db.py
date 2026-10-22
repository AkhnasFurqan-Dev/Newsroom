from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings
from .timeutil import as_utc


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store UTC as naive ISO-compatible values, return aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        value = as_utc(value)
        return value.replace(tzinfo=None) if value else None

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        return as_utc(value)


def _configure_sqlite(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    engine = create_engine(
        url or settings.effective_database_url,
        connect_args={"check_same_thread": False} if (url or settings.effective_database_url).startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if (url or settings.effective_database_url).startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_dependency() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
