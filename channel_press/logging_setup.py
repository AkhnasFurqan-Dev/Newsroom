"""Durable local logging for the web server and the background worker.

Unexpected failures must leave a full traceback on disk even when the console
that started the server has been closed or has scrolled. Each event is written
as one JSON line to ``data/logs/<kind>-YYYYMMDD.log``.

Only operational metadata is recorded: event name, request method and path, the
authenticated user id, job and channel ids, the exception type/message and the
traceback. Request bodies, form values, cookies, headers, Telegram session
strings and environment values are never passed to the logger, so credentials
cannot leak through this channel.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings

LOGGER_NAME = "channel_press"
_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "user_id",
    "job_id",
    "kind",
    "channel_id",
    "status",
)
_configured: set[str] = set()


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line; tracebacks stay readable via ``\\n``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def log_directory() -> Path:
    path = settings.data_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(kind: str = "web", *, console: bool = True) -> Path:
    """Attach file (and optional stderr) handlers once per process.

    Returns the log file for the current day so callers can show it to the
    operator without guessing the filename. Alembic's ``fileConfig`` disables
    existing loggers during ``upgrade_database``, so this function re-enables
    the application logger on every call; ``cmd_worker`` and ``cmd_serve`` call
    it after the migration step.
    """
    directory = log_directory()
    path = directory / f"{kind}-{datetime.now():%Y%m%d}.log"
    key = f"{kind}:{path}"
    logger = logging.getLogger(LOGGER_NAME)
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Migrations (and some third-party libraries) disable loggers that already
    # exist. Re-enable every logger in our own namespace, not just the parent.
    for name, obj in logging.Logger.manager.loggerDict.items():
        if isinstance(obj, logging.Logger) and (name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}.")):
            obj.disabled = False
    if key in _configured:
        return path
    formatter = JsonLineFormatter()
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if console and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    _configured.add(key)
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
