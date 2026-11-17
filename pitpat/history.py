"""Saved workouts, and the totals made from them. No UI and no Bluetooth.

One entry per workout goes in history.json next to config.json. Daily records,
weekly totals and any date range are worked out from those entries when they
are asked for, so nothing else needs to be kept in step.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from . import config

log = logging.getLogger(__name__)

METRES_PER_MILE = 1609.344
# 5280 ft of travel at a 1 % grade climbs 52.8 ft, which at 10 ft a floor is 5.28 floors.
# The treadmill reports its incline as a level; this takes level N to be an N % grade.
FLOORS_PER_MILE_PER_PERCENT = 5.28
MIN_METRES = 16.0  # Under about 0.01 mi isn't worth a line in the history
SHORT_RANGE_DAYS = 31  # Longer than this is charted by week instead of by day


def history_path() -> Path:
    return config.CONFIG_PATH.parent / "history.json"


@dataclass(frozen=True)
class Workout:
    day: str  # ISO date, local time
    start: str  # ISO date and time it began
    metres: float
    floors: float
    seconds: float
    plan: Optional[str] = None

    @property
    def date(self) -> date:
        return date.fromisoformat(self.day)

    def to_dict(self) -> dict:
        return {"day": self.day, "start": self.start, "metres": round(self.metres, 1),
                "floors": round(self.floors, 2), "seconds": round(self.seconds), "plan": self.plan}

    @classmethod
    def from_dict(cls, data: dict) -> "Workout":
        """Raises ValueError for something that isn't a workout."""
        try:
            day = str(data["day"])
            date.fromisoformat(day)
            return cls(day, str(data.get("start", day)), float(data["metres"]), float(data["floors"]),
                       float(data["seconds"]), data.get("plan"))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"not a workout: {e!r}") from e


class History:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or history_path()
        self.workouts: list[Workout] = []

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "History":
        """Reads the saved workouts. A file that can't be read is set aside, not overwritten."""
        history = cls(path)
        try:
            raw = json.loads(history.path.read_text(encoding="utf-8"))
            items = raw["workouts"]
        except FileNotFoundError:
            return history
        except Exception as e:
            log.warning("Setting aside unreadable %s: %s", history.path, e)
            history._set_aside()
            return history
        for item in items:
            try:
                history.workouts.append(Workout.from_dict(item))
            except ValueError as e:
                log.warning("Skipping an unreadable workout: %s", e)
        return history

    def _set_aside(self) -> None:
        target = self.path.with_name(f"{self.path.name}.bad-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            os.replace(self.path, target)
        except OSError as e:
            log.warning("Couldn't move %s aside: %s", self.path, e)

    def add(self, workout: Workout) -> None:
        self.workouts.append(workout)
        self.save()

    def save(self) -> None:
        """Writes to a temporary file and swaps it in, so a crash can't leave half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        data = {"version": 1, "workouts": [w.to_dict() for w in self.workouts]}
        temp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        os.replace(temp, self.path)


# --- Summaries ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Totals:
    metres: float = 0.0
    floors: float = 0.0
    seconds: float = 0.0
    workouts: int = 0
    days: int = 0  # Days with at least one workout


def totals(workouts: Iterable[Workout]) -> Totals:
    items = list(workouts)
    return Totals(
        metres=sum(w.metres for w in items), floors=sum(w.floors for w in items),
        seconds=sum(w.seconds for w in items), workouts=len(items), days=len({w.day for w in items}),
    )


def within(workouts: Iterable[Workout], first: date, last: date) -> list[Workout]:
    return [w for w in workouts if first <= w.date <= last]


def week_start(day: date) -> date:
    """Weeks run Monday to Sunday."""
    return day - timedelta(days=day.weekday())


def days_in(first: date, last: date) -> int:
    return (last - first).days + 1


@dataclass(frozen=True)
class Bucket:
    """A day or a week of a range, with what was done in it."""
    first: date
    last: date
    label: str  # Short, for a chart axis
    totals: Totals


def _day_label(day: date, short: bool) -> str:
    return day.strftime("%a") if short else str(day.day)


def weeks(workouts: Iterable[Workout], first: date, last: date) -> list[Bucket]:
    """Each Monday-to-Sunday week the range touches, cut to the range."""
    items = list(workouts)
    out = []
    start = week_start(first)
    while start <= last:
        a, b = max(start, first), min(start + timedelta(days=6), last)
        out.append(Bucket(a, b, f"{start:%b} {start.day}", totals(within(items, a, b))))
        start += timedelta(days=7)
    return out


def buckets(workouts: Iterable[Workout], first: date, last: date) -> list[Bucket]:
    """A bar per day for a month or less, otherwise a bar per week."""
    items = list(workouts)
    count = days_in(first, last)
    if count > SHORT_RANGE_DAYS:
        return weeks(items, first, last)
    return [
        Bucket(d, d, _day_label(d, count <= 7), totals(within(items, d, d)))
        for d in (first + timedelta(days=i) for i in range(count))
    ]


@dataclass(frozen=True)
class DayRecord:
    day: date
    totals: Totals


def daily_records(workouts: Iterable[Workout], first: date, last: date) -> list[DayRecord]:
    """Days that have workouts, newest first."""
    items = within(workouts, first, last)
    return [DayRecord(d, totals(w for w in items if w.date == d)) for d in sorted({w.date for w in items}, reverse=True)]


def preset_range(name: str, today: date) -> tuple[date, date]:
    """The dates for a named range: "week", "last" (week) or "four" (weeks, ending this week)."""
    monday = week_start(today)
    if name == "last":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if name == "four":
        return monday - timedelta(days=21), monday + timedelta(days=6)
    return monday, monday + timedelta(days=6)


# --- Words and units ---------------------------------------------------------------------------

def distance(metres: float, imperial: bool) -> float:
    return metres / METRES_PER_MILE if imperial else metres / 1000


def distance_unit(imperial: bool) -> str:
    return "mi" if imperial else "km"


def fmt_floors(floors: float) -> str:
    whole = round(floors)
    return "1 floor" if whole == 1 else f"{whole} floors"


def fmt_minutes(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def fmt_range(first: date, last: date) -> str:
    if first.year == last.year:
        return f"{first:%b} {first.day} – {last:%b} {last.day}, {last.year}"
    return f"{first:%b} {first.day}, {first.year} – {last:%b} {last.day}, {last.year}"


def summary_text(workouts: Iterable[Workout], first: date, last: date, imperial: bool) -> str:
    """A short plain-text summary of a date range, ready to paste."""
    items = list(workouts)
    total = totals(within(items, first, last))
    count = days_in(first, last)
    unit = distance_unit(imperial)
    lines = [
        f"PitPat summary · {fmt_range(first, last)}",
        f"Distance: {distance(total.metres, imperial):.1f} {unit}",
        f"Floors climbed: {round(total.floors)}",
        f"Workouts: {total.workouts} (active on {total.days} of {count} days)",
        f"Time: {fmt_minutes(total.seconds)}",
    ]
    if count > 7:
        lines += ["", "Week by week"]
        for week in weeks(items, first, last):
            start = week_start(week.first)
            lines.append(f"{start:%b} {start.day}: {distance(week.totals.metres, imperial):.1f} {unit}, "
                         f"{fmt_floors(week.totals.floors)}")
    return "\n".join(lines)
