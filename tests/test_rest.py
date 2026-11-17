"""Pausing the belt for a rest after a long run."""
import pytest

from pitpat import config, controller as controller_module, protocol
from pitpat.config import Config
from pitpat.controller import MAX_REST, MAX_REST_AFTER, MIN_REST, MIN_REST_AFTER, Controller, detail_rows

from fakes import PAUSED, RUNNING, FakeTreadmill

PAUSE = protocol.pause(imperial=False)
MINUTE = 60.0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    FakeTreadmill.instances.clear()


class Rig:
    def __init__(self, c, t):
        self.c, self.t, self.now = c, t, 1000.0
        c.clock = lambda: self.now

    def run_for(self, seconds, state=RUNNING, step=2.0):
        """Frames every `step` seconds for `seconds`, in the given belt state."""
        end = self.now + seconds
        while self.now < end - 1e-9:
            self.now = min(self.now + step, end)
            self.t.feed(flags=state, target=1600 if state == RUNNING else 0, speed=1600 if state == RUNNING else 0)

    def rest_for(self, seconds):
        self.run_for(seconds, state=PAUSED)

    @property
    def pauses(self):
        return self.t.sent.count(PAUSE)


async def rig(**cfg):
    cfg.setdefault("rest_after_minutes", 120)
    cfg.setdefault("rest_minutes", 15)
    c = Controller(Config(address="AA:BB", **cfg), FakeTreadmill)
    c.notices = []
    c.on_notify = lambda message, severity, title: c.notices.append((message, title))
    c.start()
    await c._connect_task
    r = Rig(c, FakeTreadmill.instances[-1])
    r.t.feed(flags=RUNNING, target=1600, speed=1600)  # The first frame only starts the clock
    return r


async def test_the_belt_is_paused_after_the_time_it_may_run_and_the_user_is_told():
    r = await rig()
    r.run_for(119 * MINUTE)
    assert r.pauses == 0 and r.c.view().rest_left is None
    r.run_for(2 * MINUTE)
    assert r.pauses >= 1 and r.c.goal == "pause"
    assert r.c.notices[-1][1] == "Treadmill needs to cool down"
    assert "15 minutes" in r.c.notices[-1][0] and "120 minutes" in r.c.notices[-1][0]
    assert sum(1 for _, title in r.c.notices if title == "Treadmill needs to cool down") == 1  # Said once

    r.rest_for(2 * MINUTE)  # It stops
    assert r.c.goal is None
    assert r.c.view().rest_left == pytest.approx(13 * MINUTE, abs=2)


async def test_it_must_rest_the_full_time_and_then_is_free_to_run_again():
    r = await rig(rest_after_minutes=30, rest_minutes=10)
    r.run_for(31 * MINUTE)
    r.rest_for(9 * MINUTE)
    assert r.c.view().rest_left is not None

    r.c.toggle()  # The app won't start it early
    assert r.c.notices[-1][1] == "Not yet" and "cooling down" in r.c.notices[-1][0]
    assert r.c.goal is None and not any(p[:1] == b"\x6a" and p[6:8] != b"\x00\x00" for p in r.t.sent[-1:])
    sent = len(r.t.sent)
    r.c.go_preset(1)
    r.c.set_speed(2.0)
    assert len(r.t.sent) == sent and r.c.goal is None

    r.rest_for(2 * MINUTE)  # Ten minutes in all
    assert r.c.view().rest_left is None and r.c.run_seconds == 0
    r.c.toggle()
    assert isinstance(r.c.goal, float)  # Free again


async def test_a_short_break_does_not_count_as_a_rest():
    r = await rig(rest_after_minutes=30, rest_minutes=10)
    r.run_for(20 * MINUTE)
    r.rest_for(5 * MINUTE)  # Not long enough to reset the clock
    r.run_for(11 * MINUTE)  # 31 minutes of running in all
    assert r.pauses >= 1 and r.c.notices[-1][1] == "Treadmill needs to cool down"


async def test_a_full_break_resets_the_count():
    r = await rig(rest_after_minutes=30, rest_minutes=10)
    r.run_for(25 * MINUTE)
    r.rest_for(10 * MINUTE)
    assert r.c.run_seconds == 0
    r.run_for(25 * MINUTE)
    assert r.pauses == 0


async def test_starting_again_during_the_rest_with_the_remote_is_paused_again():
    r = await rig(rest_after_minutes=30, rest_minutes=10)
    r.run_for(31 * MINUTE)
    r.rest_for(2 * MINUTE)
    before = r.pauses
    r.run_for(10)  # Someone pressed start on the remote
    assert r.pauses > before and r.c.goal == "pause"


async def test_each_frame_while_pausing_does_not_repeat_the_notice():
    r = await rig(rest_after_minutes=10, rest_minutes=5)
    r.run_for(10 * MINUTE + 10)
    shown = len(r.c.notices)
    r.run_for(6, step=0.5)  # Still running for a moment: the belt takes a second to respond
    assert len(r.c.notices) == shown


async def test_it_does_nothing_when_switched_off_but_keeps_counting():
    r = await rig(rest_limit=False, rest_after_minutes=10)
    r.run_for(30 * MINUTE)
    assert r.pauses == 0 and r.c.view().rest_left is None
    assert r.c.run_seconds == pytest.approx(30 * MINUTE, abs=2)
    r.c.set_rest(enabled=True)  # Turned on after a long run: the belt needs its rest now
    r.run_for(2)
    assert r.pauses >= 1


async def test_time_out_of_touch_is_not_counted():
    r = await rig(rest_after_minutes=10)
    r.run_for(5 * MINUTE)
    r.t.on_disconnect()
    r.now += 3600  # An hour away
    r.c.start()
    await r.c._connect_task
    r.t = FakeTreadmill.instances[-1]
    r.run_for(2)
    assert r.c.run_seconds < 5.5 * MINUTE
    r.now += 3600
    r.t.feed(flags=RUNNING, target=1600, speed=1600)  # One frame after a long silence adds at most a few seconds
    assert r.c.run_seconds < 5.6 * MINUTE


async def test_settings_are_clamped_saved_and_reported():
    r = await rig()
    r.c.set_rest(after_minutes=1, rest_minutes=0)
    v = r.c.view()
    assert v.rest_after_seconds == MIN_REST_AFTER * 60 and v.rest_seconds == MIN_REST * 60
    r.c.set_rest(after_minutes=99999, rest_minutes=99999)
    v = r.c.view()
    assert v.rest_after_seconds == MAX_REST_AFTER * 60 and v.rest_seconds == MAX_REST * 60
    r.c.set_rest(enabled=True, after_minutes=90, rest_minutes=20)
    assert "90 minutes" in r.c.notices[-1][0] and "20 minute" in r.c.notices[-1][0]
    saved = Config.load()
    assert (saved.rest_limit, saved.rest_after_minutes, saved.rest_minutes) == (True, 90, 20)
    r.c.set_rest(enabled=False)
    assert not Config.load().rest_limit and "won't pause" in r.c.notices[-1][0]

    bad = Controller(Config(address="AA:BB", rest_after_minutes="long", rest_minutes=None), FakeTreadmill).view()
    assert bad.rest_after_seconds == 7200 and bad.rest_seconds == 900  # A hand-edited config falls back


async def test_a_plan_waits_for_the_rest_too():
    from pitpat.plans import DISTANCE, MILES, Plan, Point

    r = await rig(rest_after_minutes=10, rest_minutes=5)
    r.run_for(11 * MINUTE)
    r.rest_for(MINUTE)
    r.c.start_plan(Plan("Easy", MILES, DISTANCE, 1.0, (Point(0, 2.0),)))
    assert r.c.run is None and r.c.notices[-1][1] == "Not yet"


async def test_details_show_the_run_time():
    r = await rig(rest_after_minutes=10, rest_minutes=5)
    r.run_for(3 * MINUTE)
    assert ("Run time since rest", "0:03:00") in detail_rows(r.c.view()) or any(
        k == "Run time since rest" and v.startswith("0:03:") for k, v in detail_rows(r.c.view())
    )
    r.run_for(8 * MINUTE)
    r.rest_for(MINUTE)
    row = dict(detail_rows(r.c.view()))["Run time since rest"]
    assert "cooling down" in row
