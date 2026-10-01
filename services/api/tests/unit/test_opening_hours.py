"""Open-now answers in Amman time, including hours that end after midnight."""
from datetime import datetime, timezone

from jubran.application.opening_hours import open_status

# Every day 12:00–01:00 (closes after midnight); Friday (5) 14:00–23:00.
HOURS = [{"day_of_week": day, "opens_at": "12:00", "closes_at": "01:00"} for day in range(7) if day != 5]
HOURS.append({"day_of_week": 5, "opens_at": "14:00", "closes_at": "23:00"})


def at(year, month, day, hour, minute):  # a time in Amman (UTC+3)
    return datetime(year, month, day, hour - 3, minute, tzinfo=timezone.utc) if hour >= 3 else \
        datetime(year, month, day - 1, hour + 21, minute, tzinfo=timezone.utc)


def test_open_in_the_evening():
    status = open_status(HOURS, at(2026, 9, 28, 20, 0))  # Monday 20:00
    assert status["open_now"] is True and status["closes_at"] == "01:00" and status["local_time"] == "20:00"


def test_still_open_after_midnight_on_yesterdays_hours():
    status = open_status(HOURS, at(2026, 9, 29, 0, 30))  # Tuesday 00:30, Monday's hours still running
    assert status["open_now"] is True


def test_closed_between_closing_and_opening():
    status = open_status(HOURS, at(2026, 9, 29, 9, 0))  # Tuesday 09:00
    assert status["open_now"] is False and status["next_opening"] == {"day_of_week": 2, "opens_at": "12:00"}


def test_a_different_day_uses_its_own_hours():
    assert open_status(HOURS, at(2026, 10, 2, 13, 0))["open_now"] is False   # Friday 13:00 (opens 14:00)
    assert open_status(HOURS, at(2026, 10, 3, 0, 30))["open_now"] is False   # Saturday 00:30 (Friday closed 23:00)
