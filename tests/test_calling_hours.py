"""When it is acceptable to dial, in the property's own local time."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from alderway_voice_agent.calling_hours import CallingWindow, local_now, within_window

WINDOW = CallingWindow()


def _at(year: int, month: int, day: int, hour: int, zone: str = "America/New_York") -> datetime:
    return datetime(year, month, day, hour, tzinfo=ZoneInfo(zone))


def test_a_weekday_afternoon_is_allowed():
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 9, 14)).allowed is True


def test_too_early_is_refused_with_the_local_hour():
    verdict = within_window(WINDOW, "America/New_York", at=_at(2026, 9, 9, 7))
    assert verdict.allowed is False
    assert "too early" in verdict.reason
    assert "07:00" in verdict.local_time


def test_too_late_is_refused():
    verdict = within_window(WINDOW, "America/New_York", at=_at(2026, 9, 9, 21))
    assert verdict.allowed is False
    assert "too late" in verdict.reason


def test_eight_in_the_evening_is_already_too_late():
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 9, 20)).allowed is False


def test_sunday_is_never_called():
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 13, 14)).allowed is False


def test_saturday_has_its_own_shorter_window():
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 12, 11)).allowed is True
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 12, 9)).allowed is False
    assert within_window(WINDOW, "America/New_York", at=_at(2026, 9, 12, 18)).allowed is False


def test_the_window_follows_the_property_rather_than_the_caller():
    """Nine in the morning in New York is six in California, which is not a time to ring anybody."""
    moment = _at(2026, 9, 9, 9)
    assert within_window(WINDOW, "America/New_York", at=moment).allowed is True
    assert within_window(WINDOW, "America/Los_Angeles", at=moment).allowed is False


def test_an_unknown_timezone_is_refused_rather_than_guessed():
    verdict = within_window(WINDOW, "Mars/Olympus_Mons", at=_at(2026, 9, 9, 14))
    assert verdict.allowed is False
    assert "unknown time zone" in verdict.reason


def test_local_now_raises_on_a_timezone_it_does_not_know():
    try:
        local_now("Nowhere/At_All")
    except ValueError as exc:
        assert "unknown time zone" in str(exc)
    else:
        raise AssertionError("an unknown zone must raise rather than default")
