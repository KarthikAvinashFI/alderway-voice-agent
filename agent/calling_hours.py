"""Whether it is acceptable to dial this lead right now.

In the property's own local time, not ours. A campaign run from one timezone into four is how an agency
ends up calling somebody at seven in the morning, which is both a complaint and, in most states, a
breach of the rules on when a solicitation call may be placed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SATURDAY = 5
SUNDAY = 6


@dataclass(frozen=True)
class CallingWindow:
    earliest_hour: int = 9
    latest_hour: int = 20
    # Sunday is 6 in Python's weekday numbering, and calling on it generates complaints.
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    saturday_earliest_hour: int = 10
    saturday_latest_hour: int = 17


def window_from_env() -> CallingWindow:
    return CallingWindow(
        earliest_hour=int(os.environ.get("CALLING_EARLIEST_HOUR", "9")),
        latest_hour=int(os.environ.get("CALLING_LATEST_HOUR", "20")),
        saturday_earliest_hour=int(os.environ.get("CALLING_SATURDAY_EARLIEST_HOUR", "10")),
        saturday_latest_hour=int(os.environ.get("CALLING_SATURDAY_LATEST_HOUR", "17")),
    )


@dataclass(frozen=True)
class WindowVerdict:
    allowed: bool
    reason: str = ""
    local_time: str = ""


def local_now(time_zone: str, *, at: datetime | None = None) -> datetime:
    """The lead's local time. An unknown zone is not guessed; it raises so the lead is skipped."""
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {time_zone!r}") from exc
    moment = at or datetime.now(tz=zone)
    return moment.astimezone(zone)


def within_window(
    window: CallingWindow, time_zone: str, *, at: datetime | None = None
) -> WindowVerdict:
    try:
        moment = local_now(time_zone, at=at)
    except ValueError as exc:
        return WindowVerdict(False, str(exc))

    stamp = moment.strftime("%Y-%m-%d %H:%M %Z")
    weekday = moment.weekday()
    if weekday not in window.allowed_weekdays:
        return WindowVerdict(False, "outside the days this campaign calls on", stamp)
    if weekday == SATURDAY:
        earliest, latest = window.saturday_earliest_hour, window.saturday_latest_hour
    else:
        earliest, latest = window.earliest_hour, window.latest_hour
    if moment.hour < earliest:
        return WindowVerdict(False, f"too early locally, opens at {earliest}", stamp)
    if moment.hour >= latest:
        return WindowVerdict(False, f"too late locally, closes at {latest}", stamp)
    return WindowVerdict(True, "", stamp)
