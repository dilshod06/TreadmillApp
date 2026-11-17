"""Countdown plans: a distance or a duration, with speed and incline changes
at points along the way. Pure data and arithmetic; no UI and no Bluetooth.

A plan is measured either by distance ("1.5 mi, then 3.0 mi") or by time
("20 minutes"). Its points say where along that measure the speed and incline
change. The first point sits at 0 and gives the starting values, so a plan of
"X miles at speed Y and incline Z" is just one point.
"""
import logging
from dataclasses import dataclass, replace
from typing import Optional

from .protocol import KM_PER_MILE

log = logging.getLogger(__name__)

MILES, KM = "mi", "km"
DISTANCE, TIME = "distance", "time"

METRES_PER_UNIT = {MILES: KM_PER_MILE * 1000, KM: 1000.0}
MAX_SPEED = 30.0  # Sanity limit, in the plan's own unit per hour
MAX_INCLINE = 30


@dataclass(frozen=True)
class Point:
    at: float  # Distance in the plan's unit, or minutes, from the start
    speed: float  # In the plan's unit per hour
    incline: int = 0


@dataclass(frozen=True)
class Plan:
    name: str
    unit: str = MILES
    measure: str = DISTANCE
    total: float = 1.0  # Distance in `unit`, or minutes
    points: tuple[Point, ...] = (Point(0.0, 2.0),)
    stop_at_end: bool = True

    # --- Storage ----------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "unit": self.unit,
            "measure": self.measure,
            "total": self.total,
            "points": [{"at": p.at, "speed": p.speed, "incline": p.incline} for p in self.points],
            "stop_at_end": self.stop_at_end,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        """Raises ValueError for something that isn't shaped like a plan."""
        try:
            points = tuple(
                Point(float(p["at"]), float(p["speed"]), int(p.get("incline", 0))) for p in data["points"]
            )
            return cls(
                name=str(data["name"]),
                unit=str(data.get("unit", MILES)),
                measure=str(data.get("measure", DISTANCE)),
                total=float(data["total"]),
                points=tuple(sorted(points, key=lambda p: p.at)),
                stop_at_end=bool(data.get("stop_at_end", True)),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"not a plan: {e!r}") from e

    # --- Units ------------------------------------------------------------------

    @property
    def speed_unit(self) -> str:
        return "mph" if self.unit == MILES else "km/h"

    @property
    def total_unit(self) -> str:
        """What `total` and each point's `at` are counted in."""
        return self.unit if self.measure == DISTANCE else "min"

    def raw(self, amount: float) -> float:
        """A distance or minutes amount in metres or seconds."""
        return amount * METRES_PER_UNIT[self.unit] if self.measure == DISTANCE else amount * 60

    @property
    def total_raw(self) -> float:
        return self.raw(self.total)

    def speed_kph(self, speed: float) -> float:
        return speed * KM_PER_MILE if self.unit == MILES else speed

    @property
    def uses_incline(self) -> bool:
        return any(p.incline for p in self.points)

    # --- Reading a plan ------------------------------------------------------------

    def point_index(self, position_raw: float) -> int:
        """The point in effect after `position_raw` metres or seconds."""
        index = 0
        for i, p in enumerate(self.points):
            if self.raw(p.at) <= position_raw:
                index = i
        return index

    def estimate(self) -> tuple[float, float]:
        """(seconds, metres) the whole plan should take if followed exactly."""
        seconds = metres = 0.0
        for i, p in enumerate(self.points):
            end = self.points[i + 1].at if i + 1 < len(self.points) else self.total
            mps = self.speed_kph(p.speed) / 3.6
            if self.measure == DISTANCE:
                length = self.raw(end) - self.raw(p.at)
                metres += length
                seconds += length / mps if mps else 0.0
            else:
                length = self.raw(end) - self.raw(p.at)
                seconds += length
                metres += length * mps
        return seconds, metres

    def summary(self) -> str:
        seconds, metres = self.estimate()
        points = f"{len(self.points)} point{'s' if len(self.points) != 1 else ''}"
        if self.measure == DISTANCE:
            return f"{self.total:g} {self.unit} · {points} · about {fmt_minutes(seconds)}"
        return f"{self.total:g} min · {points} · about {metres / METRES_PER_UNIT[self.unit]:.1f} {self.unit}"


def validate(plan: Plan) -> list[str]:
    """Everything wrong with a plan, in plain words; empty if it can run."""
    errors = []
    if not plan.name.strip():
        errors.append("Give the plan a name.")
    if plan.unit not in METRES_PER_UNIT:
        errors.append("Unit must be miles or kilometres.")
    if plan.measure not in (DISTANCE, TIME):
        errors.append("A plan counts either distance or time.")
    if not plan.total > 0:
        errors.append("The total must be more than zero.")
    if not plan.points:
        errors.append("Add at least one point.")
        return errors
    if plan.points[0].at != 0:
        errors.append("The first point has to be at the very start (0).")
    for earlier, later in zip(plan.points, plan.points[1:]):
        if later.at <= earlier.at:
            errors.append(f"Points must be in order, with no two at {later.at:g}.")
            break
    if plan.points[-1].at >= plan.total:
        errors.append(f"The last point ({plan.points[-1].at:g}) must come before the end ({plan.total:g}).")
    for p in plan.points:
        if not 0 < p.speed <= MAX_SPEED:
            errors.append(f"Speed at {p.at:g} must be between 0 and {MAX_SPEED:g}.")
            break
    for p in plan.points:
        if not 0 <= p.incline <= MAX_INCLINE:
            errors.append(f"Incline at {p.at:g} must be between 0 and {MAX_INCLINE}.")
            break
    return errors


def load_plans(raw: list) -> tuple[Plan, ...]:
    """Plans from saved settings, skipping any that were hand-edited into nonsense."""
    plans = []
    for item in raw or ():
        try:
            plan = Plan.from_dict(item)
        except ValueError as e:
            log.warning("Ignoring unreadable plan: %s", e)
            continue
        problems = validate(plan)
        if problems:
            log.warning("Ignoring plan %r: %s", plan.name, problems[0])
            continue
        plans.append(plan)
    return tuple(plans)


def fmt_clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, minutes, secs = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def fmt_minutes(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def fmt_amount(plan: Plan, raw: float) -> str:
    """Metres or seconds, as the plan counts them."""
    if plan.measure == DISTANCE:
        return f"{raw / METRES_PER_UNIT[plan.unit]:.2f} {plan.unit}"
    return fmt_clock(raw)


def duplicate(plan: Plan, taken: set[str]) -> Plan:
    """A copy with a name not in `taken`."""
    name = f"{plan.name} copy"
    n = 2
    while name in taken:
        name = f"{plan.name} copy {n}"
        n += 1
    return replace(plan, name=name)


# --- Following a plan -------------------------------------------------------------

@dataclass(frozen=True)
class RunView:
    """A snapshot of a plan in progress, safe to hand to another thread."""
    plan: Plan
    position: float  # Metres or seconds done
    index: int  # The point in effect
    overridden: bool  # Speed was changed by hand since this point began

    @property
    def point(self) -> Point:
        return self.plan.points[self.index]

    @property
    def next_point(self) -> Optional[Point]:
        return self.plan.points[self.index + 1] if self.index + 1 < len(self.plan.points) else None

    @property
    def fraction(self) -> float:
        return min(1.0, self.position / self.plan.total_raw)

    @property
    def remaining(self) -> float:
        return max(0.0, self.plan.total_raw - self.position)

    @property
    def until_next(self) -> Optional[float]:
        nxt = self.next_point
        return None if nxt is None else max(0.0, self.plan.raw(nxt.at) - self.position)


class PlanRun:
    """Tracks progress through a plan from the treadmill's own distance and time
    counters, which restart from zero when a session ends."""

    def __init__(self, plan: Plan, distance_m: float, duration_s: float):
        self.plan = plan
        self.index = 0
        self.overridden = False
        self.position = 0.0
        self._offset = 0.0  # Progress banked by skipping ahead
        self._carried = 0.0  # Progress banked from before a counter reset
        self._base = self._last = self._raw(distance_m, duration_s)

    def _raw(self, distance_m: float, duration_s: float) -> float:
        return distance_m if self.plan.measure == DISTANCE else duration_s

    @property
    def point(self) -> Point:
        return self.plan.points[self.index]

    @property
    def finished(self) -> bool:
        return self.position >= self.plan.total_raw

    def update(self, distance_m: float, duration_s: float) -> bool:
        """Takes a status reading. True if a new point has begun."""
        raw = self._raw(distance_m, duration_s)
        if raw < self._last:  # The treadmill started a new session
            self._carried += self._last - self._base
            self._base = 0.0
        self._last = raw
        self.position = self._offset + self._carried + raw - self._base
        index = self.plan.point_index(self.position)
        if index > self.index:
            self.index = index
            self.overridden = False
            return True
        return False

    def skip(self) -> Optional[Point]:
        """Jumps to the start of the next point, returning it (None on the last)."""
        if self.index + 1 >= len(self.plan.points):
            return None
        target = self.plan.raw(self.plan.points[self.index + 1].at)
        self._offset += max(0.0, target - self.position)
        self.position = max(self.position, target)
        self.index += 1
        self.overridden = False
        return self.point

    def view(self) -> RunView:
        return RunView(self.plan, self.position, self.index, self.overridden)
