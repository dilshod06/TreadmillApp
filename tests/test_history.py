"""Saved workouts, the summaries made from them, and recording them as the belt runs."""
import json
from datetime import date, datetime

import pytest

from pitpat import history as h
from pitpat.config import Config
from pitpat.controller import Controller
from pitpat.history import History, Workout

from fakes import PAUSED, RUNNING, STOPPED, FakeTreadmill

MILE = 1609.344


def workout(day="2026-11-17", miles=1.0, floors=0.0, minutes=20, plan=None):
    return Workout(day, f"{day}T07:00:00", miles * MILE, floors, minutes * 60, plan)


# --- The file ---------------------------------------------------------------------------------

def test_workouts_round_trip_through_the_file(tmp_path):
    path = tmp_path / "history.json"
    history = History(path)
    history.add(workout(miles=2.5, floors=12.345, plan="Hills"))
    history.add(workout("2026-11-18"))
    loaded = History.load(path)
    assert [w.day for w in loaded.workouts] == ["2026-11-17", "2026-11-18"]
    assert loaded.workouts[0].plan == "Hills" and loaded.workouts[0].floors == 12.35
    assert not (tmp_path / "history.json.tmp").exists()  # The swap leaves no temporary file


def test_a_missing_file_is_just_empty_history(tmp_path):
    assert History.load(tmp_path / "nothing.json").workouts == []


def test_an_unreadable_file_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{ not json")
    history = History.load(path)
    assert history.workouts == [] and not path.exists()
    (kept,) = tmp_path.glob("history.json.bad-*")
    assert kept.read_text() == "{ not json"
    history.add(workout())  # A fresh file starts cleanly
    assert len(History.load(path).workouts) == 1


def test_one_bad_entry_does_not_lose_the_rest(tmp_path):
    path = tmp_path / "history.json"
    good = workout().to_dict()
    path.write_text(json.dumps({"version": 1, "workouts": [good, {"day": "yesterday"}, {"nothing": 1}, good]}))
    assert len(History.load(path).workouts) == 2


def test_the_default_path_sits_next_to_the_settings(tmp_path):
    assert h.history_path() == tmp_path / "history.json"


# --- Summaries --------------------------------------------------------------------------------

WED = date(2026, 11, 18)


def test_weeks_run_monday_to_sunday_and_presets_follow():
    assert h.week_start(WED) == date(2026, 11, 16)
    assert h.week_start(date(2026, 11, 22)) == date(2026, 11, 16)  # A Sunday belongs to the week before it
    assert h.preset_range("week", WED) == (date(2026, 11, 16), date(2026, 11, 22))
    assert h.preset_range("last", WED) == (date(2026, 11, 9), date(2026, 11, 15))
    assert h.preset_range("four", WED) == (date(2026, 10, 26), date(2026, 11, 22))


def test_totals_and_ranges():
    items = [workout("2026-11-16", 1, 5), workout("2026-11-16", 2, 7.5), workout("2026-11-20", 3), workout("2026-11-09", 9)]
    inside = h.within(items, date(2026, 11, 16), date(2026, 11, 22))
    assert len(inside) == 3
    t = h.totals(inside)
    assert t.workouts == 3 and t.days == 2 and t.floors == 12.5
    assert t.metres == pytest.approx(6 * MILE) and t.seconds == 3 * 20 * 60
    assert h.totals([]) == h.Totals()
    assert h.within(items, date(2026, 11, 22), date(2026, 11, 16)) == []  # Backwards range


def test_short_ranges_chart_by_day_and_long_ones_by_week():
    items = [workout("2026-11-17", 2), workout("2026-11-19", 1)]
    days = h.buckets(items, date(2026, 11, 16), date(2026, 11, 22))
    assert [b.label for b in days] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert [round(h.distance(b.totals.metres, True)) for b in days] == [0, 2, 0, 1, 0, 0, 0]
    month = h.buckets(items, date(2026, 11, 1), date(2026, 11, 30))
    assert len(month) == 30 and month[0].label == "1"
    long = h.buckets(items, date(2026, 10, 1), date(2026, 11, 30))  # 61 days
    assert [b.label for b in long][:2] == ["Sep 28", "Oct 5"] and long[-1].label == "Nov 30"
    assert sum(b.totals.workouts for b in long) == 2


def test_weeks_are_cut_to_the_range():
    items = [workout("2026-11-10"), workout("2026-11-17")]
    ws = h.weeks(items, date(2026, 11, 11), date(2026, 11, 18))
    assert [(w.first, w.last) for w in ws] == [(date(2026, 11, 11), date(2026, 11, 15)), (date(2026, 11, 16), date(2026, 11, 18))]
    assert [w.totals.workouts for w in ws] == [0, 1]  # The 10th falls before the range


def test_daily_records_are_newest_first_and_skip_empty_days():
    items = [workout("2026-11-16", 1), workout("2026-11-20", 2), workout("2026-11-20", 1, 3)]
    records = h.daily_records(items, date(2026, 11, 16), date(2026, 11, 22))
    assert [r.day for r in records] == [date(2026, 11, 20), date(2026, 11, 16)]
    assert records[0].totals.workouts == 2 and records[0].totals.floors == 3


def test_units_and_words():
    assert h.distance(MILE, True) == pytest.approx(1.0) and h.distance(2000, False) == 2.0
    assert (h.distance_unit(True), h.distance_unit(False)) == ("mi", "km")
    assert h.fmt_floors(0.4) == "0 floors" and h.fmt_floors(1.2) == "1 floor" and h.fmt_floors(2.6) == "3 floors"
    assert h.fmt_minutes(50 * 60) == "50 min" and h.fmt_minutes(3720) == "1 h 2 min"
    assert h.fmt_range(date(2026, 11, 16), date(2026, 11, 22)) == "Nov 16 – Nov 22, 2026"
    assert h.fmt_range(date(2025, 12, 29), date(2026, 1, 4)) == "Dec 29, 2025 – Jan 4, 2026"


def test_summary_text_for_a_week_and_for_longer_ranges():
    items = [workout("2026-11-16", 2, 10), workout("2026-11-18", 1.5, 4.4, minutes=30), workout("2026-11-10", 5, 1)]
    week = h.summary_text(items, date(2026, 11, 16), date(2026, 11, 22), imperial=True)
    assert week.splitlines() == [
        "PitPat summary · Nov 16 – Nov 22, 2026", "Distance: 3.5 mi", "Floors climbed: 14",
        "Workouts: 2 (active on 2 of 7 days)", "Time: 50 min", "", "Workouts",
        "Wed, Nov 18 · 7:00 AM · No plan · 30:00 · 1.50 mi · 4 floors",
        "Mon, Nov 16 · 7:00 AM · No plan · 20:00 · 2.00 mi · 10 floors",
    ]
    long = h.summary_text(items, date(2026, 11, 9), date(2026, 11, 22), imperial=False)
    assert "Week by week" in long and "Nov 9: 8.0 km, 1 floor" in long and "Nov 16: 5.6 km, 14 floors" in long


# --- Recording workouts as the belt runs ----------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from pitpat import controller as controller_module

    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    FakeTreadmill.instances.clear()


async def connected(**cfg):
    c = Controller(Config(address="AA:BB", **cfg), FakeTreadmill)
    c.notices = []
    c.on_notify = lambda message, severity, title: c.notices.append((message, severity, title))
    c.wall = lambda: datetime(2026, 11, 17, 9, 30, 15)
    c.now = 1000.0
    c.clock = lambda: c.now
    c.start()
    await c._connect_task
    return c, FakeTreadmill.instances[-1]


def saved():
    return History.load().workouts


def stop_for_good(c, t, **counters):
    """The treadmill reports stopped, and stays that way long enough for the workout to be over."""
    t.feed(flags=STOPPED, **counters)
    c.now += 181
    t.feed(flags=STOPPED, **counters)


async def test_a_workout_is_saved_when_the_belt_stops_with_its_floors():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0, incline=5, max_incline=12)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=1609, duration_ms=600_000, incline=5, max_incline=12)
    assert c.view().floors == pytest.approx(1609 / MILE * 5 * 5.28)  # A mile at a 5 % grade
    assert saved() == []  # Nothing yet: it's still going
    t.feed(flags=PAUSED, distance=1609, duration_ms=600_000, incline=5)
    assert saved() == []  # Pausing doesn't end it
    t.feed(flags=STOPPED, distance=1609, duration_ms=600_000)
    assert saved() == []  # A stop alone could still be a pause
    c.now += 181
    t.feed(flags=STOPPED, distance=1609, duration_ms=600_000)

    (w,) = saved()
    assert w.day == "2026-11-17" and w.start == "2026-11-17T09:30:15"
    assert w.metres == pytest.approx(1609) and w.seconds == 600 and w.floors == pytest.approx(26.4, abs=0.05)
    assert c.view().workouts == (w,) and c.view().floors == 0
    assert c.notices[-1][2] == "Workout saved" and "1.61 km and 26.4 floors added to today" in c.notices[-1][0]
    t.feed(flags=STOPPED)
    c.now += 181
    t.feed(flags=STOPPED)
    assert len(saved()) == 1  # Further stopped frames don't add more


async def test_only_climbing_while_the_belt_runs_counts_as_floors():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, incline=0, max_incline=12)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=800, incline=0, max_incline=12)  # Flat
    assert c.view().floors == 0
    t.feed(flags=PAUSED, distance=1200, incline=6)  # Moving counters while paused don't climb
    assert c.view().floors == 0 and c.wk_metres == 1200


async def test_a_session_the_treadmill_restarts_is_closed_off():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=2000, duration_ms=900_000)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=40, duration_ms=5_000)  # Counters went back to zero
    (w,) = saved()
    assert w.metres == 2000 and w.seconds == 900
    assert c.wk_metres == 40  # The new one has begun


async def test_a_few_steps_are_not_a_workout():
    c, t = await connected()
    t.feed(flags=RUNNING, target=1000, speed=1000, distance=0)
    t.feed(flags=RUNNING, target=1000, speed=1000, distance=10)
    stop_for_good(c, t)
    assert saved() == [] and not any(n[2] == "Workout saved" for n in c.notices)


async def test_joining_a_session_already_under_way_counts_it_from_the_treadmills_counters():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=1500, duration_ms=480_000, incline=2, max_incline=12)
    stop_for_good(c, t, distance=1500, duration_ms=480_000)
    (w,) = saved()
    assert w.metres == 1500 and w.seconds == 480 and w.floors == 0  # Floors only from when we joined


async def test_losing_the_connection_or_quitting_saves_what_was_done():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=3000, duration_ms=1_000_000)
    t.on_disconnect()
    assert [w.metres for w in saved()] == [3000]

    c2, t2 = await connected()
    t2.feed(flags=RUNNING, target=3000, speed=3000, distance=0)
    t2.feed(flags=RUNNING, target=3000, speed=3000, distance=2500, duration_ms=800_000)
    await c2.shutdown()
    assert [w.metres for w in saved()] == [3000, 2500]


async def test_the_plan_that_was_run_is_recorded_and_units_are_remembered():
    from pitpat.plans import DISTANCE, MILES, Plan, Point

    c, t = await connected()
    assert c.view().imperial is True
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0)
    assert c.view().imperial is False and Config.load().imperial is False  # This treadmill is metric
    c.start_plan(Plan("Hills", MILES, DISTANCE, 5.0, (Point(0, 2.0),)))
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=3000)
    stop_for_good(c, t)
    assert saved()[0].plan == "Hills"


async def test_history_loads_when_the_app_starts_and_a_failed_save_is_reported(monkeypatch):
    History().add(workout())
    c, t = await connected()
    assert len(c.view().workouts) == 1

    def refuse(self):
        raise OSError("disk full")

    monkeypatch.setattr(History, "save", refuse)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=2000)
    stop_for_good(c, t)
    assert any(n[1] == "error" and "disk full" in n[0] for n in c.notices)


async def test_a_stop_the_treadmill_carries_on_from_is_one_workout():
    # The treadmill reports "stopped" after some pauses and then resumes with the same
    # counters: that is one workout, not one saved for each stretch.
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0, incline=4, max_incline=12)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=1000, duration_ms=400_000, incline=4, max_incline=12)
    t.feed(flags=STOPPED, distance=1000, duration_ms=400_000)
    c.now += 90
    t.feed(flags=STOPPED, distance=1000, duration_ms=400_000)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=1000, duration_ms=400_000, incline=4, max_incline=12)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=2000, duration_ms=800_000, incline=4, max_incline=12)
    assert saved() == []
    stop_for_good(c, t, distance=2000, duration_ms=800_000)

    (w,) = saved()
    assert w.metres == 2000 and w.seconds == 800  # Not 1000 + 2000
    assert w.floors == pytest.approx(2000 / MILE * 4 * 5.28, abs=0.05)


async def test_a_new_session_after_a_long_stop_is_a_new_workout():
    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=2000, duration_ms=800_000)
    t.feed(flags=STOPPED, distance=2000, duration_ms=800_000)  # Stopped, and the app sees nothing more for a while
    c.now += 3600
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=500, duration_ms=200_000)
    assert [w.metres for w in saved()] == [2000] and c.wk_metres == 500


async def test_a_plan_started_while_the_last_workout_is_still_waiting_names_only_the_new_one():
    from pitpat.plans import DISTANCE, MILES, Plan, Point

    c, t = await connected()
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0)
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=2000, duration_ms=800_000)
    t.feed(flags=STOPPED, distance=2000, duration_ms=800_000)  # The first run, still waiting to be saved
    c.start_plan(Plan("Hills", MILES, DISTANCE, 5.0, (Point(0, 2.0),)))
    t.feed(flags=STOPPED, distance=2000, duration_ms=800_000)  # Frames before the belt moves
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=0, duration_ms=0)  # The treadmill starts over
    t.feed(flags=RUNNING, target=3000, speed=3000, distance=400, duration_ms=100_000)
    (first,) = saved()
    assert first.plan is None and first.metres == 2000
    assert c.wk_plan == "Hills"
    stop_for_good(c, t, distance=400, duration_ms=100_000)
    assert [w.plan for w in saved()] == [None, "Hills"]


def test_workouts_are_listed_newest_first_with_plan_and_time():
    from pitpat.history import fmt_duration, fmt_when, summary_text, workouts_newest_first

    a = Workout("2026-11-16", "2026-11-16T18:05:00", 1609.344, 3.0, 1500, None)
    b = Workout("2026-11-17", "2026-11-17T07:30:00", 3218.688, 20.0, 3569, "2 Up and Down")
    assert workouts_newest_first([a, b], date(2026, 11, 16), date(2026, 11, 17)) == [b, a]
    assert a.plan_name == "No plan" and b.plan_name == "2 Up and Down"
    assert fmt_duration(3569) == "59:29" and fmt_duration(3 * 3600 + 5) == "3:00:05" and fmt_duration(41) == "0:41"
    assert fmt_when(b.began) == "Tue, Nov 17 · 7:30 AM" and fmt_when(a.began) == "Mon, Nov 16 · 6:05 PM"
    text = summary_text([a, b], date(2026, 11, 16), date(2026, 11, 22), True)
    assert "Workouts\nTue, Nov 17 · 7:30 AM · 2 Up and Down · 59:29 · 2.00 mi · 20 floors" in text
    assert "Mon, Nov 16 · 6:05 PM · No plan · 25:00 · 1.00 mi · 3 floors" in text
