"""Is the restaurant open right now? Amman time, including hours that end after midnight."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
    RESTAURANT_TZ = ZoneInfo("Asia/Amman")
except Exception:  # no time-zone database (Windows without the tzdata package)
    RESTAURANT_TZ = timezone(timedelta(hours=3))  # Jordan keeps UTC+3 all year

MINUTES_PER_DAY = 24 * 60


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _window(hours: Dict[str, Any]) -> tuple:
    """(opens, closes) in minutes from that day's midnight; closing after midnight goes past 1440."""
    opens, closes = _minutes(hours["opens_at"]), _minutes(hours["closes_at"])
    if closes <= opens:
        closes += MINUTES_PER_DAY  # e.g. 12:00–01:00, or the same time = open all day
    return opens, closes


def open_status(opening_hours: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Whether the restaurant is open at ``now`` (default: this moment), by its weekly hours.

    ``day_of_week`` counts from Sunday (0) to Saturday (6).
    """
    local = (now or datetime.now(timezone.utc)).astimezone(RESTAURANT_TZ)
    today = (local.weekday() + 1) % 7
    minute = local.hour * 60 + local.minute
    by_day = {hours["day_of_week"]: hours for hours in opening_hours}
    status: Dict[str, Any] = {"local_time": local.strftime("%H:%M"), "day_of_week": today}

    # Today's hours, or yesterday's if they run past midnight into today.
    for day, shift in ((today, 0), ((today - 1) % 7, MINUTES_PER_DAY)):
        if day in by_day:
            opens, closes = _window(by_day[day])
            if opens <= minute + shift < closes:
                return {**status, "open_now": True, "closes_at": by_day[day]["closes_at"]}

    for ahead in range(0, 8):
        day = (today + ahead) % 7
        if day in by_day:
            opens, _ = _window(by_day[day])
            if ahead > 0 or opens > minute:
                return {**status, "open_now": False,
                        "next_opening": {"day_of_week": day, "opens_at": by_day[day]["opens_at"]}}
    return {**status, "open_now": False, "next_opening": None}
