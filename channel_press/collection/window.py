"""Half-open edition windows and local cutoff arithmetic.

The core rule, applied everywhere:

    eligible = previous_cutoff_utc <= posted_at < current_cutoff_utc

The start instant is included, the end instant excluded. A post published at
exactly today's cutoff belongs to the next normal issue.

Cutoffs are local wall-clock boundaries converted through an IANA timezone.
Two daylight-saving policies are fixed and tested rather than left to chance:

* A local time that does not exist (spring-forward gap) resolves forward to the
  instant produced by the pre-transition offset. For example 02:30 in
  America/New_York on 2026-03-08 becomes 03:30 EDT (07:30 UTC).
* A local time that occurs twice (fall-back fold) resolves to its first
  occurrence (``fold=0``), the earlier UTC instant.

Pakistan (the default) has no DST, so these rules only affect configured
publications in other zones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..timeutil import as_utc, to_local


def parse_clock(value: str, fallback: time = time(5, 0)) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return fallback


def local_cutoff_utc(day: date, clock: time, tz_name: str) -> datetime:
    """Resolve a local wall-clock cutoff to UTC with the documented DST policy."""
    zone = ZoneInfo(tz_name)
    local = datetime.combine(day, clock, tzinfo=zone)
    return local.astimezone(UTC)


@dataclass(frozen=True)
class EditionWindow:
    channel_id: int
    kind: str
    start: datetime
    end: datetime
    timezone: str = "Asia/Karachi"
    cutoff_local: str = "05:00"

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start))
        object.__setattr__(self, "end", as_utc(self.end))
        if self.end <= self.start:
            raise ValueError("Edition window end must be after its start.")

    @property
    def key(self) -> str:
        return window_key(self.channel_id, self.kind, self.start, self.end)

    def contains(self, posted_at: datetime) -> bool:
        moment = as_utc(posted_at)
        return self.start <= moment < self.end

    def local_range(self) -> tuple[datetime, datetime]:
        return to_local(self.start, self.timezone), to_local(self.end, self.timezone)

    def label(self) -> str:
        start_local, end_local = self.local_range()
        return (
            f"[{start_local:%Y-%m-%d %H:%M} – {end_local:%Y-%m-%d %H:%M} "
            f"{end_local.tzname() or self.timezone})"
        )

    def as_dict(self) -> dict:
        start_local, end_local = self.local_range()
        return {
            "key": self.key,
            "channel_id": self.channel_id,
            "kind": self.kind,
            "start_utc": self.start.isoformat(),
            "end_utc": self.end.isoformat(),
            "start_local": start_local.isoformat(),
            "end_local": end_local.isoformat(),
            "timezone": self.timezone,
            "cutoff_local": self.cutoff_local,
            "label": self.label(),
            "semantics": "start inclusive, end exclusive",
        }


def window_key(channel_id: int, kind: str, start: datetime, end: datetime) -> str:
    begin = as_utc(start).isoformat()
    finish = as_utc(end).isoformat()
    return f"{channel_id}:{kind}:{begin}:{finish}"


def enabled_weekdays(value: str) -> set[int]:
    days: set[int] = set()
    for part in (value or "").split(","):
        try:
            number = int(part.strip())
        except ValueError:
            continue
        if 0 <= number <= 6:
            days.add(number)
    return days or {0, 1, 2, 3, 4, 5, 6}


def previous_enabled_day(weekdays: str, day: date) -> date:
    enabled = enabled_weekdays(weekdays)
    cursor = day - timedelta(days=1)
    for _ in range(21):
        if cursor.weekday() in enabled:
            return cursor
        cursor -= timedelta(days=1)
    return cursor


def window_for_cutoff(
    *,
    channel_id: int,
    kind: str,
    timezone: str,
    cutoff_clock: str,
    weekdays: str,
    cutoff: datetime,
) -> EditionWindow:
    """Build the window ending at ``cutoff`` whose start is the previous cutoff."""
    local_day = to_local(cutoff, timezone).date()
    start_day = previous_enabled_day(weekdays, local_day)
    clock = parse_clock(cutoff_clock)
    start = local_cutoff_utc(start_day, clock, timezone)
    return EditionWindow(
        channel_id=channel_id,
        kind=kind,
        start=start,
        end=as_utc(cutoff),
        timezone=timezone,
        cutoff_local=cutoff_clock,
    )


def window_for_schedule(schedule, cutoff: datetime) -> EditionWindow:  # noqa: ANN001 - ORM object
    return window_for_cutoff(
        channel_id=schedule.channel_id,
        kind=schedule.kind,
        timezone=schedule.timezone,
        cutoff_clock=schedule.cutoff_time,
        weekdays=schedule.weekdays,
        cutoff=cutoff,
    )


def cutoff_for_day(schedule, day: date) -> datetime:  # noqa: ANN001 - ORM object
    return local_cutoff_utc(day, parse_clock(schedule.cutoff_time), schedule.timezone)


def release_for_day(schedule, day: date) -> datetime:  # noqa: ANN001 - ORM object
    return local_cutoff_utc(day, parse_clock(schedule.release_time, time(8, 0)), schedule.timezone)


def due_cutoffs(
    schedule,  # noqa: ANN001 - ORM object
    *,
    watermark: datetime | None,
    now: datetime,
    max_catchup: int = 3,
    horizon_days: int = 31,
) -> tuple[list[datetime], list[datetime]]:
    """Return (windows to enqueue, windows skipped by the catch-up bound).

    The skipped list is explicit so a workstation that was off for a month
    cannot leave an invisible date gap.
    """
    local_today = to_local(now, schedule.timezone).date()
    enabled = enabled_weekdays(schedule.weekdays)
    candidates: list[datetime] = []
    for offset in range(0, horizon_days):
        day = local_today - timedelta(days=offset)
        if day.weekday() not in enabled:
            continue
        cutoff = cutoff_for_day(schedule, day)
        if cutoff > now:
            continue
        if watermark is not None and cutoff <= as_utc(watermark):
            continue
        candidates.append(cutoff)
    candidates.sort()
    if len(candidates) <= max_catchup:
        return candidates, []
    return candidates[-max_catchup:], candidates[: -max_catchup]
