"""Pausing the belt when the person has stepped off it."""
import pytest

from pitpat import config, controller as controller_module, protocol
from pitpat.config import Config
from pitpat.controller import MAX_OFF_BELT_SECONDS, MIN_OFF_BELT_SECONDS, Controller
from pitpat.protocol import OFF_BELT_LOAD, Status

from fakes import PAUSED, RUNNING, FakeTreadmill
from test_protocol import SAMPLE, frame

ON, OFF = 50, 10  # Loads seen on the real treadmill: walking on it, and an empty belt

PAUSE = protocol.pause(imperial=False)


# --- Reading it from the frame ------------------------------------------------------------

def test_presence_comes_from_the_load_and_the_treadmills_own_flag():
    assert Status.parse(frame(flags=RUNNING, load=ON)).on_belt is True
    assert Status.parse(frame(flags=RUNNING, load=OFF)).on_belt is False
    assert Status.parse(frame(flags=RUNNING, load=OFF_BELT_LOAD)).on_belt is True  # The edge counts as on
    assert Status.parse(frame(flags=RUNNING, load=OFF_BELT_LOAD - 1)).on_belt is False
    s = Status.parse(frame(flags=RUNNING, load=ON, off_belt=True))  # Its flag says off even if the load doesn't
    assert s.on_belt is False and s.load == ON and s.off_belt_flag is True


def test_other_frame_kinds_say_nothing_about_presence():
    # The identity frame has the serial where the flag would be.
    identity = Status.parse(SAMPLE)
    assert identity.serial and identity.load is None and identity.on_belt is None
    assert Status.parse(frame(flags=RUNNING)).on_belt is None  # Also identity-shaped, no presence data


# --- The setting --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    FakeTreadmill.instances.clear()


class Rig:
    def __init__(self, c, t):
        self.c, self.t, self.now = c, t, 1000.0
        c.clock = lambda: self.now

    def at(self, seconds, load=ON, state=RUNNING, **kw):
        """Moves the clock on and delivers a status frame."""
        self.now = 1000.0 + seconds
        self.t.feed(flags=state, target=1600 if state == RUNNING else 0, speed=1600 if state == RUNNING else 0,
                    load=load, **kw)

    @property
    def paused(self):
        return PAUSE in self.t.sent


async def rig(**cfg):
    cfg = Config(address="AA:BB", **cfg)
    c = Controller(cfg, FakeTreadmill)
    c.notices = []
    c.on_notify = lambda message, severity, title: c.notices.append((message, title))
    c.start()
    await c._connect_task
    return Rig(c, FakeTreadmill.instances[-1])


async def test_stepping_off_for_long_enough_pauses_the_belt():
    r = await rig(off_belt_pause=True, off_belt_seconds=10)
    r.at(0, ON)
    r.at(1, OFF)  # Steps off
    r.at(10.9, OFF)
    assert not r.paused and r.c.view().on_belt is False
    assert r.c.view().off_belt_for == pytest.approx(9.9)

    r.at(11, OFF)  # Ten seconds since the first frame that said off
    assert r.paused
    assert r.c.notices[-1] == ("You were off the belt for 10 seconds.", "Treadmill paused")
    assert r.c.goal == "pause"


async def test_the_pause_is_repeated_until_the_belt_stops_and_then_left_alone():
    r = await rig(off_belt_pause=True, off_belt_seconds=5)
    r.at(0, ON)
    r.at(1, OFF)
    r.at(6, OFF)
    r.at(6.5, OFF)  # Still running: ask again
    assert r.t.sent.count(PAUSE) == 2

    r.at(7, OFF, state=PAUSED)  # It paused
    assert r.c.goal is None
    sent = len(r.t.sent)
    r.at(8, OFF, state=PAUSED)
    assert len(r.t.sent) == sent and r.c.view().on_belt is None  # Nothing counted while paused


async def test_a_short_step_off_is_forgiven():
    r = await rig(off_belt_pause=True, off_belt_seconds=10)
    r.at(0, ON)
    r.at(1, OFF)
    r.at(8, OFF)
    r.at(9, ON)  # Back on in time
    assert r.c.view().off_belt_for is None
    r.at(15, OFF)  # The count starts afresh
    r.at(24, OFF)
    assert not r.paused
    r.at(25, OFF)
    assert r.paused


async def test_it_does_not_count_until_the_person_has_stepped_on():
    r = await rig(off_belt_pause=True, off_belt_seconds=10)
    r.at(0, OFF)  # Belt started with the remote, nobody on yet
    r.at(60, OFF)
    assert not r.paused and r.c.view().off_belt_for is None

    r.at(61, ON)  # Steps on: now it is watching
    r.at(62, OFF)
    r.at(72, OFF)
    assert r.paused


async def test_it_does_nothing_when_the_setting_is_off():
    r = await rig()
    assert not r.c.view().off_belt_pause
    r.at(0, ON)
    r.at(1, OFF)
    r.at(500, OFF)
    assert not r.paused and r.c.goal is None
    assert r.c.view().on_belt is False  # Still known, for the display


async def test_frames_that_dont_say_leave_the_count_alone():
    r = await rig(off_belt_pause=True, off_belt_seconds=10)
    r.at(0, ON)
    r.at(1, OFF)
    r.now = 1006.0
    r.t.on_status(Status.parse(frame(flags=RUNNING, target=1600, speed=1600)))  # An identity-shaped frame
    assert r.c.view().off_belt_for == pytest.approx(5.0)  # Not reset by the frame without presence data
    r.at(11, OFF)
    assert r.paused


async def test_turning_it_on_counts_from_then_not_from_when_they_stepped_off():
    r = await rig()
    r.at(0, ON)
    r.at(1, OFF)
    r.at(300, OFF)  # Off for ages with the setting off
    r.c.set_off_belt_pause(True)
    r.at(301, OFF)
    assert not r.paused
    r.at(311, OFF)
    assert r.paused


async def test_seconds_are_clamped_saved_and_reported():
    r = await rig()
    r.c.set_off_belt_seconds(1)
    assert r.c.view().off_belt_seconds == MIN_OFF_BELT_SECONDS
    r.c.set_off_belt_seconds(99999)
    assert r.c.view().off_belt_seconds == MAX_OFF_BELT_SECONDS
    r.c.set_off_belt_seconds(25)
    r.c.set_off_belt_pause(True)
    loaded = Config.load()
    assert loaded.off_belt_pause and loaded.off_belt_seconds == 25
    assert "25 seconds" in r.c.notices[-1][0]

    bad = Controller(Config(address="AA:BB", off_belt_seconds="soon"), FakeTreadmill)  # A hand-edited config
    assert bad.view().off_belt_seconds == 10
    for typed, used in ((0, MIN_OFF_BELT_SECONDS), (-5, MIN_OFF_BELT_SECONDS), (10**6, MAX_OFF_BELT_SECONDS)):
        assert Controller(Config(address="AA:BB", off_belt_seconds=typed), FakeTreadmill).view().off_belt_seconds == used


async def test_a_treadmill_that_does_not_report_presence_says_so():
    r = await rig()
    r.t.feed(flags=RUNNING, target=1600, speed=1600)  # No presence data
    r.c.set_off_belt_pause(True)
    assert "doesn't report whether you're on the belt" in r.c.notices[-1][0]


async def test_resuming_waits_for_the_person_to_step_on_again():
    r = await rig(off_belt_pause=True, off_belt_seconds=5)
    r.at(0, ON)
    r.at(1, OFF)
    r.at(6, OFF)
    r.at(7, OFF, state=PAUSED)
    r.c.toggle()  # Start again from the app while still standing off it
    r.at(8, OFF)
    r.at(100, OFF)
    assert r.t.sent.count(PAUSE) == 1  # Not paused a second time before they've stepped on


async def test_a_dropped_connection_forgets_who_was_on():
    r = await rig(off_belt_pause=True)
    r.at(0, ON)
    r.at(1, OFF)
    r.t.on_disconnect()
    assert r.c.on_belt is None and r.c.off_since is None and not r.c.seen_on_belt


async def test_details_show_presence():
    from pitpat.controller import detail_rows

    r = await rig()
    r.at(0, ON)
    assert ("On the belt", "yes (load 50)") in detail_rows(r.c.view())
    r.t.feed(flags=RUNNING, target=1600, speed=1600)
    assert ("On the belt", "not reported") in detail_rows(r.c.view())
