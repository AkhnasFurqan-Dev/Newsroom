from __future__ import annotations

from contextlib import contextmanager
import logging
import os
import threading
import time
from typing import Iterator

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.exc import OperationalError
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
    cursor.execute("PRAGMA busy_timeout=1000")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    engine = create_engine(
        url or settings.effective_database_url,
        connect_args={"check_same_thread": False} if (url or settings.effective_database_url).startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if (url or settings.effective_database_url).startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.info["transaction_started"] = time.monotonic()

    def ended(connection):
        started = connection.info.pop("transaction_started", time.monotonic())
        duration_ms = round((time.monotonic() - started) * 1000)
        # Instrumentation only: normal short commits stay at DEBUG so the log
        # records lock owners without becoming the bottleneck itself.
        logging.getLogger("channel_press.db").log(
            logging.WARNING if duration_ms > 2000 else logging.DEBUG,
            "transaction_end pid=%s thread=%s connection=%s duration_ms=%s",
            os.getpid(), threading.get_ident(), id(connection), duration_ms,
        )
    event.listen(engine, "commit", ended)
    event.listen(engine, "rollback", ended)
    return engine


def is_busy(exc: OperationalError) -> bool:
    return "locked" in str(exc.orig).lower() or "busy" in str(exc.orig).lower()


def retry_busy(operation, attempts: int = 3):
    """Replay only caller-declared idempotent units, each in a fresh session."""
    for attempt in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            if not is_busy(exc) or attempt == attempts - 1:
                raise
            logging.getLogger("channel_press.db").warning("sqlite_busy_retry attempt=%s", attempt + 1)
            time.sleep(0.1 * (2 ** attempt))


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
