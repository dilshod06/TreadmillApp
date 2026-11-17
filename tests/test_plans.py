"""Countdown plans: the data model, and following one through the controller."""
import pytest

from pitpat import config, controller as controller_module, protocol
from pitpat.config import Config
from pitpat.controller import Controller
from pitpat.plans import (
    DISTANCE, KM, MILES, TIME, Plan, Point, duplicate, fmt_clock, fmt_minutes, load_plans, validate,
)

from fakes import IMPERIAL, PAUSED, RUNNING, FakeTreadmill

MILE_M = protocol.KM_PER_MILE * 1000


def hills(**kw) -> Plan:
    """2 miles: easy start, a hill at half a mile, eased off at a mile and a half."""
    fields = dict(
        name="Hills", unit=MILES, measure=DISTANCE, total=2.0,
        points=(Point(0, 2.0), Point(0.5, 3.0, 2), Point(1.5, 2.5, 0)),
    )
    fields.update(kw)
    return Plan(**fields)


def wire(mph):
    return round(mph * protocol.KM_PER_MILE * 1000)


# --- The model --------------------------------------------------------------------

def test_a_plain_x_at_y_plan_is_valid():
    assert validate(Plan("Easy 3", MILES, DISTANCE, 3.0, (Point(0, 3.0, 1),))) == []


def test_validation_explains_what_is_wrong():
    def first_error(**kw):
        errors = validate(hills(**kw))
        assert errors, kw
        return errors[0]

    assert "name" in first_error(name="  ")
    assert "total" in first_error(total=0)
    assert "very start" in first_error(points=(Point(0.5, 2.0),))
    assert "in order" in first_error(points=(Point(0, 2.0), Point(0.5, 3.0), Point(0.5, 4.0)))
    assert "before the end" in first_error(points=(Point(0, 2.0), Point(2.0, 3.0)))
    assert "Speed" in first_error(points=(Point(0, 0.0),))
    assert "Incline" in first_error(points=(Point(0, 2.0, 99),))
    assert "Add at least one point" in validate(hills(points=()))[0]


def test_plans_survive_saving_and_hand_edits():
    plan = hills()
    assert Plan.from_dict(plan.to_dict()) == plan
    raw = [plan.to_dict(), {"name": "broken"}, "nonsense", {**plan.to_dict(), "total": -1}, {**plan.to_dict(), "name": "B"}]
    assert [p.name for p in load_plans(raw)] == ["Hills", "B"]
    # Out-of-order points from a hand edit are sorted rather than rejected.
    shuffled = {**plan.to_dict(), "points": list(reversed(plan.to_dict()["points"]))}
    assert Plan.from_dict(shuffled) == plan


def test_estimates_and_summaries():
    by_distance = Plan("Three", MILES, DISTANCE, 3.0, (Point(0, 3.0),))
    seconds, metres = by_distance.estimate()
    assert seconds == pytest.approx(3600) and metres == pytest.approx(3 * MILE_M)
    assert by_distance.summary() == "3 mi · 1 point · about 1 h 0 min"

    by_time = Plan("Half hour", KM, TIME, 30.0, (Point(0, 4.0), Point(15, 6.0)))
    seconds, metres = by_time.estimate()
    assert seconds == 1800 and metres == pytest.approx(900 * 4 / 3.6 + 900 * 6 / 3.6)
    assert by_time.summary() == "30 min · 2 points · about 2.5 km"


def test_point_lookup_and_formatting():
    plan = hills()
    assert [plan.point_index(m) for m in (0, 800, 805, 2000, 9999)] == [0, 0, 1, 1, 2]
    assert fmt_clock(65) == "1:05" and fmt_clock(3725) == "1:02:05"
    assert fmt_minutes(45 * 60) == "45 min" and fmt_minutes(75 * 60) == "1 h 15 min"
    assert duplicate(plan, {"Hills copy"}).name == "Hills copy 2"


# --- Following a plan ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    FakeTreadmill.instances.clear()


async def connected(cfg=None):
    c = Controller(cfg or Config(address="AA:BB"), FakeTreadmill)
    c.notices = []
    c.on_notify = lambda message, severity, title: c.notices.append(message)
    c.start()
    await c._connect_task
    return c, FakeTreadmill.instances[-1]


def walking(t, mph=1.0, **kw):
    kw.setdefault("max_speed", 10000)  # The default test frame tops out at 6 km/h
    t.feed(flags=IMPERIAL | RUNNING, target=wire(mph), speed=wire(mph), **kw)


async def test_running_a_plan_follows_its_points_by_distance():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills())
    assert t.sent[-1] == protocol.set_speed(wire(2.0), imperial=True)
    assert c.view().run.index == 0
    assert c.notices == []  # No incline asked for at the flat start

    walking(t, 2.0, distance=round(0.4 * MILE_M))
    assert c.view().run.index == 0 and c.goal is None

    walking(t, 2.0, distance=round(0.5 * MILE_M) + 5)
    assert c.view().run.index == 1
    assert t.sent[-1] == protocol.set_speed(wire(3.0), imperial=True)
    assert c.notices == ["Set the incline to 2."]

    walking(t, 3.0, distance=round(1.5 * MILE_M) + 5)
    assert c.view().run.index == 2
    assert t.sent[-1] == protocol.set_speed(wire(2.5), imperial=True)
    assert c.notices[-1] == "Set the incline to 0."


async def test_running_a_plan_by_time():
    c, t = await connected()
    walking(t, 1.0)
    plan = Plan("Intervals", MILES, TIME, 10.0, (Point(0, 2.0), Point(5, 4.0)))
    c.start_plan(plan)
    walking(t, 2.0, duration_ms=4 * 60 * 1000)
    assert c.view().run.index == 0
    walking(t, 2.0, duration_ms=5 * 60 * 1000 + 1000)
    assert c.view().run.index == 1
    assert t.sent[-1] == protocol.set_speed(wire(4.0), imperial=True)


async def test_plan_units_convert_to_what_the_treadmill_uses():
    c, t = await connected()
    t.feed(flags=RUNNING, target=1000, speed=1000)  # A metric treadmill
    c.start_plan(Plan("Metric", MILES, DISTANCE, 1.0, (Point(0, 3.0),)))
    assert t.sent[-1] == protocol.set_speed(4800, imperial=False)  # 3 mph is 4.8 km/h

    c2, t2 = await connected()
    t2.feed(flags=IMPERIAL | RUNNING, target=1000, speed=1000)
    c2.start_plan(Plan("Km", KM, DISTANCE, 1.0, (Point(0, 5.0),)))
    assert c2.goal == 3.1  # 5 km/h in mph


async def test_plan_speed_is_held_to_what_the_treadmill_allows():
    c, t = await connected()
    t.feed(flags=RUNNING, target=1000, speed=1000, max_speed=6000)
    c.start_plan(Plan("Fast", KM, DISTANCE, 1.0, (Point(0, 12.0),)))
    assert t.sent[-1] == protocol.set_speed(6000, imperial=False)


async def test_changing_speed_by_hand_holds_until_the_next_point():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills())
    walking(t, 2.0)
    assert not c.view().run.overridden

    c.nudge(0.3)  # Hand-tuned to 2.3
    assert c.goal == 2.3 and c.view().run.overridden
    walking(t, 2.3, distance=round(0.3 * MILE_M))
    assert c.view().run.index == 0 and c.view().run.overridden  # The plan leaves it alone

    c.go_preset(5)  # Preset 5 is 3.0, also an override
    c.set_speed(2.2)
    assert c.goal == 2.2

    walking(t, 2.2, distance=round(0.5 * MILE_M) + 5)  # Next point takes over again
    assert not c.view().run.overridden and c.view().run.index == 1
    assert t.sent[-1] == protocol.set_speed(wire(3.0), imperial=True)


async def test_a_treadmill_counter_reset_does_not_lose_progress():
    c, t = await connected()
    walking(t, 1.0, distance=0)
    c.start_plan(hills())
    walking(t, 2.0, distance=1000)
    assert c.run.position == 1000
    walking(t, 2.0, distance=100)  # The treadmill began a new session
    assert c.run.position == 1100


async def test_finishing_a_plan_stops_the_belt():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills())
    walking(t, 2.5, distance=round(2.0 * MILE_M) + 5)
    assert c.run is None
    assert any("Plan complete: Hills" in n for n in c.notices)
    assert t.sent[-1] == protocol.pause(imperial=True)  # Stops by pausing first
    t.feed(flags=IMPERIAL | PAUSED)
    assert t.sent[-1] == protocol.stop(imperial=True)


async def test_a_plan_can_leave_the_belt_running_at_the_end():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills(stop_at_end=False))
    walking(t, 2.0)  # The treadmill reaches the first speed
    sent = len(t.sent)
    walking(t, 2.5, distance=round(2.0 * MILE_M) + 5)
    assert c.run is None and c.goal is None and len(t.sent) == sent


async def test_ending_skipping_and_stopping():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills())

    c.skip_point()
    assert c.view().run.index == 1 and c.run.position == pytest.approx(0.5 * MILE_M)
    assert t.sent[-1] == protocol.set_speed(wire(3.0), imperial=True)
    walking(t, 3.0, distance=round(0.6 * MILE_M))  # Progress carries on from the skip
    assert c.run.position == pytest.approx(0.5 * MILE_M + 0.6 * MILE_M, abs=2)

    c.skip_point()
    c.skip_point()
    assert c.notices[-1] == "That is the last point."

    c.end_plan()
    assert c.run is None and c.view().run is None
    assert t.sent[-1] != protocol.pause(imperial=True)  # The belt carries on

    c.start_plan(hills())
    c.stop()
    assert c.run is None and c.goal == "stop"


async def test_a_dropped_connection_ends_the_plan():
    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills())
    t.on_disconnect()
    assert c.run is None
    assert any("plan ended" in n for n in c.notices)


async def test_plans_need_a_connection_and_a_valid_definition():
    c = Controller(Config(address="AA:BB"), FakeTreadmill)
    notices = []
    c.on_notify = lambda message, severity, title: notices.append((message, severity))
    c.start_plan(hills())
    assert notices == [("Not connected to a treadmill yet.", "warning")] and c.run is None

    c, t = await connected()
    walking(t, 1.0)
    c.start_plan(hills(total=0))
    assert c.run is None and c.notices[-1] == "The total must be more than zero."


async def test_saved_plans_persist_and_reach_the_view():
    cfg = Config(address="AA:BB")
    c = Controller(cfg, FakeTreadmill)
    changes = []
    c.on_change = lambda: changes.append(1)
    c.set_plans([hills(), hills(name="Flat", points=(Point(0, 3.0),)), hills(name="Bad", total=-1)])

    assert [p.name for p in c.view().plans] == ["Hills", "Flat"]
    assert changes
    assert [p.name for p in Controller(Config.load(), FakeTreadmill).view().plans] == ["Hills", "Flat"]


# --- Incline (byte 9 of the speed command) -----------------------------------------------

def sent_incline(t):
    return t.sent[-1][9]


async def test_speed_commands_keep_the_incline_the_treadmill_is_at():
    # Byte 9 is absolute, so a command that left it 0 would flatten an incline set with the remote.
    c, t = await connected()
    walking(t, 1.0, incline=5, max_incline=12)
    c.set_speed(2.0)
    assert t.sent[-1] == protocol.set_speed(wire(2.0), True, 5)

    c, t = await connected()  # The target can read as a marker above the max: use the real incline
    walking(t, 1.0, incline=4, target_incline=250, max_incline=12)
    c.set_speed(2.0)
    assert sent_incline(t) == 4

    c, t = await connected()  # Starting the belt carries it too
    t.feed(flags=IMPERIAL | PAUSED, incline=3, max_incline=12, max_speed=10000)
    c.toggle()
    assert t.sent[-1][12] & 7 == protocol.Command.START_OR_SET and sent_incline(t) == 3


async def test_without_permission_a_plan_only_reminds_and_leaves_the_incline_alone():
    c, t = await connected()  # auto_incline is off by default
    walking(t, 1.0, incline=7, max_incline=12)
    c.start_plan(hills())
    walking(t, 2.0, incline=7, max_incline=12, distance=round(0.5 * MILE_M) + 5)
    assert c.notices == ["Set the incline to 2."]
    assert c.incline_goal is None and sent_incline(t) == 7  # Still at the 7 it was at


async def test_a_plan_sets_the_incline_at_each_point_when_allowed():
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 1.0, max_incline=12)
    c.start_plan(hills())
    assert t.sent[-1] == protocol.set_speed(wire(2.0), True, 0)

    walking(t, 2.0, max_incline=12, distance=round(0.5 * MILE_M) + 5)  # The hill
    assert t.sent[-1] == protocol.set_speed(wire(3.0), True, 2)
    assert c.incline_goal == 2 and c.view().incline_goal == 2
    assert c.notices == []  # No reminder needed

    walking(t, 3.0, max_incline=12, incline=2, distance=round(0.5 * MILE_M) + 6)  # Arrived
    assert c.incline_goal is None

    walking(t, 3.0, max_incline=12, incline=2, distance=round(1.5 * MILE_M) + 5)  # Eased off
    assert t.sent[-1] == protocol.set_speed(wire(2.5), True, 0)


async def test_a_plan_started_from_stopped_asks_for_its_incline_once_the_belt_runs():
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    t.feed(flags=IMPERIAL, max_incline=12, max_speed=10000)  # Stopped
    c.start_plan(Plan("Hill", MILES, DISTANCE, 1.0, (Point(0, 2.0, 3),)))
    assert t.sent[-1] == protocol.start(wire(2.0), True, 3)

    walking(t, 2.0, max_incline=12)  # Up to speed, incline not yet moved
    assert t.sent[-1] == protocol.set_speed(wire(2.0), True, 3)  # Its own speed, at the incline
    walking(t, 2.0, max_incline=12, incline=3)
    assert c.incline_goal is None


async def test_the_incline_buttons_need_permission_and_a_running_belt():
    c, t = await connected()
    walking(t, 2.0, max_incline=12)
    c.nudge_incline(1)
    assert "Turn on" in c.notices[-1] and t.sent == [] and c.incline_goal is None

    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    t.feed(flags=IMPERIAL | PAUSED, max_incline=12, max_speed=10000)
    c.nudge_incline(1)
    assert "only changes while the belt is running" in c.notices[-1] and t.sent == []

    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0)  # No incline range reported
    c.nudge_incline(1)
    assert "doesn't report an incline range" in c.notices[-1] and t.sent == []


async def test_incline_changes_go_out_at_the_belts_own_speed_and_stop_when_reached():
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0, incline=3, max_incline=12)
    c.nudge_incline(1)
    assert t.sent[-1] == protocol.set_speed(wire(2.0), True, 4)

    c.nudge_incline(1)  # Pressed again before the first one landed: builds on the goal
    assert sent_incline(t) == 5

    walking(t, 2.0, incline=5, max_incline=12)
    assert c.incline_goal is None
    sent = len(t.sent)
    walking(t, 2.0, incline=5, max_incline=12)
    assert len(t.sent) == sent  # Nothing more once it is there


async def test_incline_is_clamped_to_what_the_treadmill_allows():
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0, incline=11, max_incline=12)
    c.set_incline(99)
    assert sent_incline(t) == 12
    c.set_incline(-5)
    assert sent_incline(t) == 0


async def test_an_incline_goal_is_dropped_if_the_belt_stops_or_it_never_arrives(monkeypatch):
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0, max_incline=12)
    c.set_incline(4)
    sent = len(t.sent)
    t.feed(flags=IMPERIAL | PAUSED, max_incline=12, max_speed=10000)  # Paused from the remote
    assert c.incline_goal is None and len(t.sent) == sent  # Not applied later, and nothing restarts the belt

    monkeypatch.setattr(controller_module, "GOAL_TIMEOUT", -1)
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0, max_incline=12)
    c.set_incline(4)
    walking(t, 2.0, max_incline=12)  # It ignored us
    assert c.incline_goal is None and "didn't change its incline" in c.notices[-1]

    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 2.0, max_incline=12)
    c.set_incline(4)
    c.stop()
    assert c.incline_goal is None


async def test_changing_the_incline_during_a_plan_is_an_override():
    c, t = await connected(Config(address="AA:BB", auto_incline=True))
    walking(t, 1.0, max_incline=12)
    c.start_plan(hills())
    assert not c.view().run.overridden
    c.nudge_incline(1)
    assert c.view().run.overridden


async def test_the_permission_is_saved_and_withdrawing_it_drops_a_pending_incline():
    c, t = await connected()
    assert not c.view().auto_incline
    c.set_auto_incline(True)
    assert c.view().auto_incline and Config.load().auto_incline

    walking(t, 2.0, max_incline=12)
    c.set_incline(6)
    c.set_auto_incline(False)
    assert c.incline_goal is None and not Config.load().auto_incline


def test_start_and_set_speed_can_carry_an_incline_and_default_to_flat():
    assert protocol.start(1000, True)[9] == 0 and protocol.set_speed(1000, True)[9] == 0
    assert protocol.start(1000, True, 4)[9] == 4 and protocol.set_speed(1000, True, 4)[9] == 4
    assert protocol.set_speed(1000, True, 4)[8] == 5  # Still the same command otherwise
