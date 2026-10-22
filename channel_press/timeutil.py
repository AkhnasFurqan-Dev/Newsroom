from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from dateutil import parser as dt_parser


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Return an aware UTC datetime, accepting naive values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    parsed = dt_parser.isoparse(value)
    return as_utc(parsed)


def to_local(value: datetime | None, tz_name: str) -> datetime | None:
    value = as_utc(value)
    if value is None:
        return None
    return value.astimezone(ZoneInfo(tz_name))


def local_boundary_utc(day: date, clock: time, tz_name: str) -> datetime:
    """Convert a local wall-clock boundary to UTC.

    ZoneInfo resolves DST gaps/folds deterministically. Boundaries are compared
    as ``posted_at >= start`` and ``posted_at < end`` everywhere.
    """
    local = datetime.combine(day, clock, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(UTC)


def human_local(value: datetime | None, tz_name: str, fmt: str = "%d %b %Y, %H:%M") -> str:
    local = to_local(value, tz_name)
    if local is None:
        return "—"
    return local.strftime(fmt) + f" {local.tzname() or ''}".rstrip()


def iso_utc(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value else None
