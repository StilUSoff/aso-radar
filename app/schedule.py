"""When automatic scans are due: on given weekdays at a given time, or every N hours.

SCHEDULE_WEEKDAYS=fri SCHEDULE_TIME=00:00 SCHEDULE_TZ=Europe/Moscow starts a run
every Friday at midnight Moscow time. Without weekdays, the per-job interval
in hours is used instead (0 = never).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

WEEKDAYS = [DAYS.index(d.strip().lower()[:3]) for d in os.environ.get("SCHEDULE_WEEKDAYS", "").split(",")
            if d.strip().lower()[:3] in DAYS]
_hh, _mm = (int(x) for x in os.environ.get("SCHEDULE_TIME", "00:00").split(":"))
TZ = ZoneInfo(os.environ.get("SCHEDULE_TZ", "Europe/Moscow"))


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def last_slot(now: datetime) -> datetime | None:
    """Most recent scheduled start at or before now (weekday mode)."""
    local = now.astimezone(TZ)
    for back in range(8):
        day = local - timedelta(days=back)
        slot = day.replace(hour=_hh, minute=_mm, second=0, microsecond=0)
        if slot.weekday() in WEEKDAYS and slot <= local:
            return slot
    return None


def next_slot(now: datetime) -> datetime | None:
    local = now.astimezone(TZ)
    for ahead in range(8):
        slot = (local + timedelta(days=ahead)).replace(hour=_hh, minute=_mm, second=0, microsecond=0)
        if slot.weekday() in WEEKDAYS and slot > local:
            return slot
    return None


def is_due(last_started: str | None, interval_hours: float, now: datetime | None = None,
           since: str | None = None) -> bool:
    """last_started: start time (UTC) of the latest run that counts.
    since: when auto-update was (re)enabled; slots before it are not caught up."""
    now = now or datetime.now(timezone.utc)
    if WEEKDAYS:
        slot = last_slot(now)
        if slot is None or (since and slot < _parse(since)):
            return False
        return last_started is None or _parse(last_started) < slot
    if interval_hours <= 0:
        return False
    if since and (last_started is None or last_started < since):
        last_started = since
    return last_started is None or (now - _parse(last_started)).total_seconds() >= interval_hours * 3600


def auto_enabled() -> bool:
    """The dashboard's on/off switch (on unless turned off)."""
    return db.get_setting("auto_update", "1") == "1"


def auto_since() -> str | None:
    return db.get_setting("auto_update_since")


def set_auto(enabled: bool) -> None:
    db.set_setting("auto_update", "1" if enabled else "0")
    if enabled:
        db.set_setting("auto_update_since", db.now())


def enabled(interval_hours: float) -> bool:
    return bool(WEEKDAYS) or interval_hours > 0


def describe(interval_hours: float) -> dict:
    """What the dashboard shows next to the on/off switch."""
    on = auto_enabled()
    if WEEKDAYS:
        days = ", ".join(["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d] for d in sorted(WEEKDAYS))
        nxt = next_slot(datetime.now(timezone.utc))
        city = TZ.key.split("/")[-1].replace("_", " ")
        return {"configured": True, "enabled": on, "label": f"{days} {_hh:02d}:{_mm:02d} ({city})",
                "next": nxt.strftime("%d.%m %H:%M") if nxt and on else None}
    if interval_hours > 0:
        return {"configured": True, "enabled": on, "label": f"каждые {interval_hours:g} ч", "next": None}
    return {"configured": False, "enabled": False, "label": "не настроено", "next": None}
