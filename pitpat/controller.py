"""UI-neutral control of a treadmill: connecting, reconnecting and driving it
towards a requested speed. Both the terminal UI and the desktop GUI sit on top
of this.

Everything here runs on one asyncio event loop. The UI hooks the `on_*`
callbacks, which are called on that loop, and calls the action methods from it.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Union

from . import protocol
from .config import Config
from .plans import Plan, PlanRun, Point, RunView, load_plans, validate
from .protocol import KM_PER_MILE, State, Status

log = logging.getLogger(__name__)

# How long to keep nudging the treadmill towards a requested speed or stop
# before giving up. Starting from stopped includes a few seconds' countdown.
GOAL_TIMEOUT = 15.0
# Don't repeat an identical command more often than this while waiting for the
# treadmill to act on it (it reports status about once a second).
RESEND_AFTER = 2.5
RECONNECT_DELAY = 3.0
# The range allowed for "pause when I'm off the belt for N seconds". The load
# reading takes a couple of seconds to react, so less than 3 would pause on a stumble.
MIN_OFF_BELT_SECONDS = 3.0
MAX_OFF_BELT_SECONDS = 600.0
# The ranges for "rest for B minutes after A minutes of running".
MIN_REST_AFTER, MAX_REST_AFTER = 10.0, 600.0
MIN_REST, MAX_REST = 1.0, 120.0
# Status arrives about twice a second; a longer gap (a dropped connection) isn't counted as either.
MAX_TICK = 5.0

STATE_LABELS = {
    State.STARTING: "STARTING",
    State.RUNNING: "RUNNING",
    State.PAUSED: "PAUSED",
    State.STOPPED: "STOPPED",
}

# Connection states.
IDLE = "idle"
CONNECTING = "connecting"
RECONNECTING = "reconnecting"
CONNECTED = "connected"
FAILED = "failed"

# Builds the Treadmill; replaced in tests. Defaults to ble.Treadmill.
TreadmillFactory = Callable[..., "object"]


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}"


@dataclass(frozen=True)
class View:
    """A snapshot of everything a UI shows, safe to hand to another thread."""
    conn_state: str
    address: Optional[str]
    variant: Optional[str]
    status: Optional[Status]
    goal: Union[float, str, None]
    presets: tuple[float, ...]
    current_speed: Optional[float]
    plans: tuple[Plan, ...] = ()
    run: Optional[RunView] = None  # A Countdown plan in progress; None in Normal mode
    auto_incline: bool = False  # The person has let the app change the incline
    incline_goal: Optional[int] = None  # An incline the treadmill hasn't reached yet
    on_belt: Optional[bool] = None  # Whether someone is on the belt; None if unknown or not running
    off_belt_for: Optional[float] = None  # Seconds they have been off it, while counting
    off_belt_pause: bool = False
    off_belt_seconds: float = 10.0
    rest_limit: bool = True
    rest_after_seconds: float = 7200.0
    rest_seconds: float = 900.0  # How long a rest must be
    run_seconds: float = 0.0  # Belt-running time since the last full rest
    rest_left: Optional[float] = None  # Seconds of rest still needed, once the limit is reached


def detail_rows(view: View) -> list[tuple[str, str]]:
    """The field/value pairs for a details panel; empty until a status arrives."""
    s = view.status
    if s is None:
        return []
    unit = s.speed_unit
    return [
        ("Address", view.address or "-"),
        ("Variant", view.variant or "-"),
        ("State", STATE_LABELS[s.state]),
        ("Speed", f"{s.to_display(s.speed):.2f} {unit}"),
        ("Target speed", f"{s.to_display(s.target_speed):.2f} {unit}"),
        ("Max speed", f"{s.to_display(s.max_speed):.2f} {unit}"),
        ("Incline", f"{s.incline} (target {s.target_incline}, max {s.max_incline})"),
        ("On the belt", {True: "yes", False: "no", None: "not reported"}[s.on_belt]
         + (f" (load {s.load})" if s.load is not None else "")),
        ("Run time since rest", fmt_duration(view.run_seconds)
         + (f" (cooling down, {fmt_duration(view.rest_left)} to go)" if view.rest_left is not None else "")),
        ("Heart rate", f"{s.heart_rate} bpm" if s.heart_rate else "-"),
        ("Units", "Imperial" if s.imperial else "Metric"),
        ("Firmware", str(s.firmware)),
        ("Device type", str(s.device_type)),
        ("Serial", s.serial or "-"),
    ]


class Controller:
    def __init__(
        self,
        config: Config,
        make_treadmill: Optional[TreadmillFactory] = None,
        startup_speed: Optional[float] = None,
    ):
        self.config = config
        self.make_treadmill = make_treadmill
        self.treadmill = None
        self.status: Optional[Status] = None
        # What the user asked for and the treadmill hasn't done yet: a speed in
        # display units, "stop", or None. Re-sent on each status until reached.
        self.goal: Union[float, str, None] = startup_speed
        self.goal_deadline: Optional[float] = None
        self.last_sent: tuple[bytes, float] = (b"", 0.0)
        # The speed last sent for the current goal.
        self.sent_wire: Optional[int] = None
        # An incline level the app is taking the treadmill to, like `goal` for speed.
        # Only ever set while config.auto_incline is on.
        self.incline_goal: Optional[int] = None
        self.incline_deadline: Optional[float] = None
        # Whether someone is on the belt, from the last status that said so; and for the
        # pause-when-off setting: whether they have been on since the belt started (it
        # isn't enforced before that, so starting the belt then stepping on is fine),
        # and when they stepped off.
        self.clock: Callable[[], float] = time.monotonic
        self.on_belt: Optional[bool] = None
        self.seen_on_belt = False
        self.off_since: Optional[float] = None
        # Rest: seconds the belt has run since it last had a full rest, and how long it has
        # been still since. Kept only while the app runs, so quitting forgets them.
        self.run_seconds = 0.0
        self.still_seconds = 0.0
        self.last_tick: Optional[float] = None
        self.resume_speed: Optional[float] = None
        self.unconfirmed_steps: Optional[int] = None
        self.conn_state = IDLE
        self.conn_address: Optional[str] = None
        self.plans: tuple[Plan, ...] = load_plans(config.plans)
        self.run: Optional[PlanRun] = None
        self._connect_task: Optional[asyncio.Future] = None

        # Hooks, called on the event loop.
        self.on_change: Callable[[], None] = lambda: None
        self.on_notify: Callable[[str, str, str], None] = lambda message, severity, title: None
        # Asks the UI to let the user choose a treadmill; it then calls connect().
        self.on_pick_device: Callable[[bool], None] = lambda auto_pick: None

    # --- State ----------------------------------------------------------------

    @property
    def connecting(self) -> bool:
        return self.conn_state in (CONNECTING, RECONNECTING)

    def current_speed(self) -> Optional[float]:
        s = self.status
        if s and s.state == State.RUNNING and s.target_speed:
            return round(s.to_display(s.target_speed), 1)
        return None

    def view(self) -> View:
        return View(
            conn_state=self.conn_state,
            address=self.conn_address,
            variant=self.treadmill.variant if self.treadmill else None,
            status=self.status,
            goal=self.goal,
            presets=tuple(self.config.presets),
            current_speed=self.current_speed(),
            plans=self.plans,
            run=self.run.view() if self.run else None,
            auto_incline=self.config.auto_incline,
            incline_goal=self.incline_goal,
            on_belt=self.on_belt,
            off_belt_for=None if self.off_since is None else self.clock() - self.off_since,
            off_belt_pause=self.config.off_belt_pause,
            off_belt_seconds=self._off_belt_seconds(),
            rest_limit=self.config.rest_limit,
            rest_after_seconds=self._rest_after(),
            rest_seconds=self._rest_needed(),
            run_seconds=self.run_seconds,
            rest_left=self._rest_left(),
        )

    def notify(self, message: str, severity: str = "information", title: str = "") -> None:
        self.on_notify(message, severity, title)

    def _changed(self) -> None:
        self.on_change()

    def _set_conn(self, state: str, address: Optional[str]) -> None:
        self.conn_state, self.conn_address = state, address
        self._changed()

    # --- Connection -----------------------------------------------------------

    def start(self, retry: bool = False) -> None:
        """Connects to the saved treadmill, or asks the UI to pick one.

        With `retry`, a saved treadmill that can't be reached is retried until
        it turns up instead of sending the user to the picker.
        """
        if self.config.address:
            self.connect(self.config.address, retry=retry)
        else:
            self.on_pick_device(True)

    def connect(self, address: str, retry: bool = False) -> None:
        """Connects to `address`, replacing any connection attempt in progress.
        With `retry`, keeps trying until it works."""
        self.cancel_connect(quiet=True)
        self._set_conn(RECONNECTING if retry else CONNECTING, address)
        self._connect_task = asyncio.ensure_future(self._connect(address, retry))

    def cancel_connect(self, quiet: bool = False) -> None:
        """Stops an attempt in progress. Connecting scans for the device too, and
        BlueZ allows one scan at a time, so callers about to scan cancel first."""
        task, self._connect_task = self._connect_task, None
        if task and not task.done():
            task.cancel()
        if not quiet and self.connecting:
            self._set_conn(IDLE, None)

    async def _connect(self, address: str, retry: bool) -> None:
        await self._drop_treadmill()
        factory = self.make_treadmill
        if factory is None:
            from . import ble  # Imported here so UIs can choose which thread loads bleak.
            factory = ble.Treadmill
        variant = self.config.variant if address.upper() == (self.config.address or "").upper() else None
        while True:
            treadmill = factory(address, self._on_status, self._on_lost, variant)
            try:
                await treadmill.connect()
                break
            except Exception as e:
                log.warning("Connect to %s failed: %s", address, e)
                if not retry:
                    self._set_conn(FAILED, address)
                    self.notify(f"{e or type(e).__name__}", "error", "Couldn't connect")
                    self.on_pick_device(False)
                    return
                await asyncio.sleep(RECONNECT_DELAY)

        self.treadmill = treadmill
        self.config.address, self.config.variant = address, treadmill.variant
        self.config.save()
        self._set_conn(CONNECTED, address)

    def _on_lost(self) -> None:
        if self.treadmill is None:
            return
        address = self.treadmill.address
        self.treadmill = None
        self.status = None
        self.goal = None
        self._clear_incline_goal()
        self._reset_presence()
        self.last_tick = None  # The time we were out of touch counts as neither running nor rest
        if self.run:  # The treadmill's counters can't be trusted across a reconnect
            self.run = None
            self.notify("The plan ended because the connection dropped.", "warning")
        self.notify("Lost connection, reconnecting…", "warning")
        self.connect(address, retry=True)

    async def _drop_treadmill(self) -> None:
        treadmill, self.treadmill, self.status = self.treadmill, None, None
        if treadmill:
            try:
                await treadmill.disconnect()
            except Exception as e:
                log.warning("Disconnect failed: %s", e)
        self._changed()

    async def shutdown(self) -> None:
        """Pauses the belt if it's moving, then disconnects."""
        self.cancel_connect(quiet=True)
        if self.treadmill and self.status and self.status.state in (State.RUNNING, State.STARTING):
            self.notify("Pausing the belt…")
            try:
                await asyncio.wait_for(self.treadmill.send(protocol.pause(self.status.imperial)), 3)
            except Exception as e:
                log.warning("Couldn't pause before quitting: %s", e)
        await self._drop_treadmill()
        self.conn_state = IDLE
        self._changed()

    # --- Driving the treadmill ------------------------------------------------

    def _on_status(self, status: Status) -> None:
        if self.status is None or status.target_speed != self.status.target_speed:
            log.info("Treadmill target now %d (%.2f %s)", status.target_speed,
                     status.to_display(status.target_speed), status.speed_unit)
        if self.status:
            # Frame kinds alternate, and not every kind carries these.
            status.steps = self.filter_steps(status.steps, self.status.steps)
            status.serial = status.serial or self.status.serial
        self.status = status
        if status.state == State.RUNNING and status.target_speed:
            self.resume_speed = status.to_display(status.target_speed)
        self._advance_plan(status)
        self._watch_presence(status)
        self._watch_rest(status)
        self._drive()
        self._changed()

    def filter_steps(self, new: Optional[int], old: Optional[int]) -> Optional[int]:
        """Keeps the last count through frames without one, and ignores a lone
        implausible reading (seen once as the belt starts) until a second agrees."""
        if new is None or old is None or 0 <= new - old <= 20 or new == self.unconfirmed_steps:
            self.unconfirmed_steps = None
            return old if new is None else new
        self.unconfirmed_steps = new
        return old

    def _set_goal(self, goal: Union[float, str, None], drive: bool = True) -> None:
        self.goal = goal
        self.goal_deadline = None
        self.last_sent = (b"", 0.0)
        self.sent_wire = None
        if drive:  # Status handling drives right after, so it passes False
            self._drive()
            self._changed()

    def _send(self, packet: bytes) -> None:
        """Queues `packet`, unless the identical one went out moments ago."""
        if self.treadmill is None:
            return
        last_packet, last_time = self.last_sent
        if packet == last_packet and time.monotonic() - last_time < RESEND_AFTER:
            return
        self.last_sent = (packet, time.monotonic())
        self.treadmill.send(packet)

    def _drive(self) -> None:
        """Sends whatever command moves the treadmill towards self.goal."""
        s = self.status
        if s is None or self.treadmill is None:
            return
        if self.goal is None:
            self._drive_incline(s)
            return
        if self.goal_deadline is None:
            self.goal_deadline = time.monotonic() + GOAL_TIMEOUT
        elif time.monotonic() > self.goal_deadline:
            self.notify("The treadmill didn't respond to the last command.", "warning")
            self.goal = None
            return

        if self.goal == "pause":
            if s.state in (State.RUNNING, State.STARTING):
                self._send(protocol.pause(s.imperial))
            else:
                self.goal = None
            return

        if self.goal == "stop":
            self._clear_incline_goal()
            if s.state == State.RUNNING:
                self._send(protocol.pause(s.imperial))  # It only stops from paused.
            elif s.state == State.PAUSED:
                self._send(protocol.stop(s.imperial))
            elif s.state == State.STOPPED:
                self.goal = None
            return

        wanted = s.to_wire(self.goal)
        incline = self._incline_byte(s)
        if s.state in (State.PAUSED, State.STOPPED):
            self._send(protocol.start(wanted, s.imperial, incline))
        elif s.state == State.RUNNING:
            if round(s.to_display(s.target_speed), 1) == round(s.to_display(wanted), 1):
                self.goal = None
                self._drive_incline(s)  # The speed is right; the incline may still be on its way
                return
            if self.sent_wire is None:
                wire = wanted
            elif time.monotonic() - self.last_sent[1] >= RESEND_AFTER:
                # The treadmill rounds to its own 0.1 steps and some values land
                # on the step below, so aim off by half the miss and try again.
                wire = self.sent_wire + (wanted - s.target_speed) // 2
                wire = max(0, min(wire, s.max_speed or 0xFFFF))
            else:
                wire = self.sent_wire  # Give it time to act first.
            self.sent_wire = wire
            self._send(protocol.set_speed(wire, s.imperial, incline))

    # --- Off the belt --------------------------------------------------------------
    # Status says whether someone is standing on the belt (see Status.on_belt). With
    # the setting on, the belt is paused once they have been off it long enough.

    def _off_belt_seconds(self) -> float:
        try:
            seconds = float(self.config.off_belt_seconds)
        except (TypeError, ValueError):
            seconds = 10.0
        return min(max(seconds, MIN_OFF_BELT_SECONDS), MAX_OFF_BELT_SECONDS)

    def _reset_presence(self) -> None:
        self.on_belt, self.seen_on_belt, self.off_since = None, False, None

    def _watch_presence(self, s: Status) -> None:
        if s.state != State.RUNNING or not s.target_speed:
            self._reset_presence()  # Counting starts afresh each time the belt gets going
            return
        on = s.on_belt
        if on is None:
            return  # This kind of frame doesn't say; keep what we knew
        self.on_belt = on
        if on:
            self.seen_on_belt, self.off_since = True, None
            return
        if not self.seen_on_belt:
            return  # They haven't stepped on since the belt started
        now = self.clock()
        if self.off_since is None:
            self.off_since = now
        seconds = self._off_belt_seconds()
        if self.config.off_belt_pause and now - self.off_since >= seconds:
            self.notify(f"You were off the belt for {seconds:g} seconds.", "warning", "Treadmill paused")
            self._reset_presence()
            self._set_goal("pause", drive=False)

    def set_off_belt_pause(self, enabled: bool) -> None:
        """Turns "pause the belt when I'm off it" on or off."""
        if enabled == self.config.off_belt_pause:
            return
        self.config.off_belt_pause = enabled
        self.config.save()
        self.off_since = None  # Counting starts from now, not from when they stepped off earlier
        if enabled and self.status is not None and self.status.on_belt is None and self.status.state == State.RUNNING:
            self.notify("This treadmill doesn't report whether you're on the belt, so this can't work.", "warning")
        else:
            self.notify(
                f"The belt will pause if you are off it for {self._off_belt_seconds():g} seconds."
                if enabled else "The belt won't pause when you step off."
            )
        self._changed()

    def set_off_belt_seconds(self, seconds: float) -> None:
        self.config.off_belt_seconds = min(max(float(seconds), MIN_OFF_BELT_SECONDS), MAX_OFF_BELT_SECONDS)
        self.config.save()
        self._changed()

    # --- Resting -------------------------------------------------------------------
    # The manual asks for a rest after a long run. Belt-running time is added up from
    # the status frames; a rest as long as the setting asks for starts it from zero
    # again, while a shorter pause doesn't. At the limit the belt is paused, and the
    # app's own start commands wait until the rest is done.

    def _minutes(self, value, default: float, low: float, high: float) -> float:
        try:
            minutes = float(value)
        except (TypeError, ValueError):
            minutes = default
        return min(max(minutes, low), high) * 60

    def _rest_after(self) -> float:
        return self._minutes(self.config.rest_after_minutes, 120.0, MIN_REST_AFTER, MAX_REST_AFTER)

    def _rest_needed(self) -> float:
        return self._minutes(self.config.rest_minutes, 15.0, MIN_REST, MAX_REST)

    def _rest_left(self) -> Optional[float]:
        """Seconds of rest still needed, or None while there is no limit in force."""
        if not self.config.rest_limit or self.run_seconds < self._rest_after():
            return None
        return max(0.0, self._rest_needed() - self.still_seconds)

    def _watch_rest(self, s: Status) -> None:
        now = self.clock()
        last, self.last_tick = self.last_tick, now
        if last is None:
            return
        elapsed = min(max(now - last, 0.0), MAX_TICK)
        if s.state == State.RUNNING and s.target_speed:
            self.run_seconds += elapsed
            self.still_seconds = 0.0
        else:
            self.still_seconds += elapsed
            if self.still_seconds >= self._rest_needed():
                self.run_seconds = 0.0
        if self._rest_left() is None or s.state != State.RUNNING or self.goal == "pause":
            return
        minutes = self._rest_after() / 60
        self.notify(
            f"It has run for {minutes:g} minutes. Let it cool down for {self._rest_needed() / 60:g} minutes before you start again.",
            "warning", "Treadmill needs to cool down",
        )
        self._set_goal("pause", drive=False)

    def _start_blocked(self) -> bool:
        """True, with a notice, if the app shouldn't start the belt because it is still resting."""
        s = self.status
        left = self._rest_left()
        if left is None or s is None or s.state in (State.RUNNING, State.STARTING):
            return False
        self.notify(f"The treadmill is cooling down. {fmt_duration(left)} to go. "
                    "Turn off the rest limit in Settings to skip it.", "warning", "Not yet")
        return True

    def set_rest(self, enabled: Optional[bool] = None, after_minutes: Optional[float] = None,
                 rest_minutes: Optional[float] = None) -> None:
        """Changes the rest limit: on or off, minutes of running allowed, minutes of rest needed."""
        if enabled is not None:
            self.config.rest_limit = enabled
        if after_minutes is not None:
            self.config.rest_after_minutes = min(max(float(after_minutes), MIN_REST_AFTER), MAX_REST_AFTER)
        if rest_minutes is not None:
            self.config.rest_minutes = min(max(float(rest_minutes), MIN_REST), MAX_REST)
        self.config.save()
        if enabled is not None:
            self.notify(
                f"The belt will pause after {self._rest_after() / 60:g} minutes of running, for a "
                f"{self._rest_needed() / 60:g} minute rest." if enabled else "The belt won't pause for a rest."
            )
        self._changed()

    # --- Incline -------------------------------------------------------------------
    # Byte 9 of a "start or set speed" command is the incline level, and it is
    # absolute: a command that leaves it 0 puts a raised treadmill back to flat.
    # So every such command carries an incline on purpose: the goal, if there is
    # one, otherwise whatever the treadmill is at now.

    def _incline_byte(self, s: Status) -> int:
        if self.incline_goal is not None:
            return self.incline_goal
        for level in (s.target_incline, s.incline):  # The target can read as a marker above the max
            if 0 <= level <= s.max_incline:
                return level
        return 0

    def _clear_incline_goal(self) -> None:
        self.incline_goal = self.incline_deadline = None

    def _drive_incline(self, s: Status) -> None:
        """Takes the treadmill to `incline_goal` by re-sending its own speed with that incline."""
        goal = self.incline_goal
        if goal is None:
            return
        if s.target_incline == goal:
            self._clear_incline_goal()
            self._changed()
            return
        if s.state in (State.PAUSED, State.STOPPED):  # It only moves while the belt runs; don't hold on to it
            self._clear_incline_goal()
            self._changed()
            return
        if s.state != State.RUNNING or not s.target_speed:
            return  # Still starting
        now = time.monotonic()
        if self.incline_deadline is None:
            self.incline_deadline = now + GOAL_TIMEOUT
        elif now > self.incline_deadline:
            self.notify("The treadmill didn't change its incline.", "warning")
            self._clear_incline_goal()
            self._changed()
            return
        self._send(protocol.set_speed(s.target_speed, s.imperial, goal))

    def _set_incline_goal(self, level: int) -> int:
        """Aims for `level`, within what the treadmill allows. Returns the level used."""
        s = self.status
        level = max(0, min(level, s.max_incline)) if s else 0
        self.incline_goal = level
        self.incline_deadline = None
        return level

    def set_auto_incline(self, enabled: bool) -> None:
        """Lets the app change the incline (plans and the incline buttons), or not."""
        if enabled == self.config.auto_incline:
            return
        self.config.auto_incline = enabled
        self.config.save()
        if not enabled:
            self._clear_incline_goal()
        self.notify("The app will now set the incline." if enabled else "The app will leave the incline to you.")
        self._changed()

    def set_incline(self, level: int) -> None:
        """Goes to an incline level, while the belt is running."""
        s = self._require_status()
        if s is None:
            return
        if not self.config.auto_incline:
            self.notify("Turn on “Let the app set the incline” first.")
        elif s.max_incline <= 0:
            self.notify("This treadmill doesn't report an incline range, so the app won't set it.", "warning")
        elif s.state != State.RUNNING:
            self.notify("The incline only changes while the belt is running.")
        else:
            self._override()
            self._set_incline_goal(level)
            if self.goal is None:  # With a speed goal pending, its command carries the incline
                self._drive_incline(s)
            self._changed()

    def nudge_incline(self, step: int) -> None:
        s = self.status
        base = self.incline_goal if self.incline_goal is not None else (s.target_incline if s else 0)
        self.set_incline(base + step)

    def _require_status(self) -> Optional[Status]:
        if self.status is None:
            self.notify("Not connected to a treadmill yet.", "warning")
        return self.status

    # --- Actions ----------------------------------------------------------------

    def toggle(self) -> None:
        """Pauses a moving belt; otherwise starts it at the last speed."""
        s = self._require_status()
        if s is None:
            return
        if s.state in (State.RUNNING, State.STARTING) or isinstance(self.goal, float):
            self.goal = None
            self._send(protocol.pause(s.imperial))
            self._changed()
        elif not self._start_blocked():
            self._set_goal(self.resume_speed or self.config.presets[0])

    def stop(self) -> None:
        if self._require_status():
            if self.run:
                self.run = None
                self.notify("Plan ended.")
            self._set_goal("stop")

    def nudge(self, step: float) -> None:
        s = self._require_status()
        if s is None:
            return
        if isinstance(self.goal, float):
            base = self.goal
        elif s.state == State.RUNNING:
            base = s.to_display(s.target_speed)
        else:
            self.notify("Start the belt first (Space or a preset).")
            return
        max_speed = s.to_display(s.max_speed) if s.max_speed else float("inf")
        self._override()
        self._set_goal(round(min(max(base + step, 0.1), max_speed), 1))

    def go_preset(self, n: int) -> None:
        """Goes to preset `n` (1-9), starting the belt if it's stopped."""
        if self._require_status() and not self._start_blocked():
            self._override()
            self._set_goal(self.config.presets[n - 1])

    def set_speed(self, speed: float) -> None:
        """Goes to an exact speed in the treadmill's display unit."""
        if speed > 0 and self._require_status() and not self._start_blocked():
            self._override()
            self._set_goal(speed)

    def save_preset(self, n: int) -> None:
        """Saves the current running speed as preset `n` (1-9)."""
        speed = self.current_speed()
        if speed is None:
            self.notify("Get the belt running at the speed you want first.")
            return
        self.config.presets[n - 1] = speed
        self.config.save()
        self.notify(f"Saved {speed:.1f} to key {n}.")
        self._changed()

    # --- Countdown plans ----------------------------------------------------------

    def set_plans(self, plans) -> None:
        """Replaces the saved plans. A plan already running keeps its own copy."""
        valid = [p for p in plans if not validate(p)]
        self.plans = tuple(valid)
        self.config.plans = [p.to_dict() for p in valid]
        self.config.save()
        self._changed()

    def start_plan(self, plan: Plan) -> None:
        """Runs `plan` from the top: goes to its first speed, starting the belt if it is stopped."""
        s = self._require_status()
        if s is None:
            return
        problems = validate(plan)
        if problems:
            self.notify(problems[0], "error", "Can't run this plan")
            return
        if self._start_blocked():
            return
        self.run = PlanRun(plan, s.distance_m, s.duration_s)
        self._enter_point(self.run.point, first=True)
        self._changed()

    def end_plan(self) -> None:
        """Back to Normal mode. The belt carries on at whatever speed it is at."""
        if self.run:
            self.run = None
            self.notify("Plan ended. You are in control.")
            self._changed()

    def skip_point(self) -> None:
        if self.run is None:
            return
        point = self.run.skip()
        if point is None:
            self.notify("That is the last point.")
            return
        self._enter_point(point)
        self._changed()

    def _override(self) -> None:
        if self.run:
            self.run.overridden = True

    def _advance_plan(self, status: Status) -> None:
        run = self.run
        if run is None:
            return
        began = run.update(status.distance_m, status.duration_s)
        if run.finished:
            plan, self.run = run.plan, None
            self.notify(f"Plan complete: {plan.name}.", title="Done")
            if plan.stop_at_end:
                self._set_goal("stop", drive=False)
            return
        if began:
            self._enter_point(run.point, drive=False)

    def _enter_point(self, point: Point, first: bool = False, drive: bool = True) -> None:
        """Goes to a plan point's speed and, if the person allowed it, its incline."""
        run, s = self.run, self.status
        if run is None or s is None:
            return
        kph = run.plan.speed_kph(point.speed)
        speed = kph / KM_PER_MILE if s.imperial else kph
        if s.max_speed:
            speed = min(speed, s.to_display(s.max_speed))
        previous = 0 if first or run.index == 0 else run.plan.points[run.index - 1].incline
        if run.plan.uses_incline and self.config.auto_incline and s.max_incline > 0:
            self._set_incline_goal(point.incline)  # Rides on the speed command below
        self._set_goal(round(speed, 1), drive=drive)
        # Otherwise it is the person's to set, so say so when it changes.
        if run.plan.uses_incline and point.incline != previous and self.incline_goal is None:
            self.notify(f"Set the incline to {point.incline}.", title=run.plan.name)
