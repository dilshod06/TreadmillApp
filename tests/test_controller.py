"""The UI-neutral controller, driven directly against a fake treadmill."""
import pytest

from pitpat import config, controller as controller_module, protocol
from pitpat.config import Config
from pitpat.controller import CONNECTED, FAILED, IDLE, RECONNECTING, Controller, detail_rows

from fakes import PAUSED, RUNNING, FakeTreadmill


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    monkeypatch.setattr(controller_module, "RECONNECT_DELAY", 0)
    FakeTreadmill.instances.clear()


class Hooks:
    """Records what a UI would be told."""

    def __init__(self, controller):
        self.changes = 0
        self.notices = []
        self.picks = []
        controller.on_change = self.on_change
        controller.on_notify = lambda message, severity, title: self.notices.append((message, severity, title))
        controller.on_pick_device = self.picks.append

    def on_change(self):
        self.changes += 1


class FlakyTreadmill(FakeTreadmill):
    """Refuses its first `failures` connection attempts."""
    failures = 2
    attempts = 0

    async def connect(self):
        FlakyTreadmill.attempts += 1
        if FlakyTreadmill.attempts <= FlakyTreadmill.failures:
            raise OSError("not in range")


async def settle(controller):
    if controller._connect_task:
        await controller._connect_task


async def test_start_without_saved_treadmill_asks_ui_to_pick():
    c = Controller(Config(), FakeTreadmill)
    hooks = Hooks(c)
    c.start()
    assert hooks.picks == [True]
    assert c.conn_state == IDLE


async def test_start_connects_to_saved_treadmill_and_remembers_variant():
    c = Controller(Config(address="AA:BB"), FakeTreadmill)
    Hooks(c)
    c.start()
    await settle(c)
    assert c.conn_state == CONNECTED and c.conn_address == "AA:BB"
    assert c.view().variant == "fba0"
    assert Config.load().variant == "fba0"


async def test_failed_connect_reports_and_asks_ui_to_pick():
    FlakyTreadmill.attempts, FlakyTreadmill.failures = 0, 99
    c = Controller(Config(address="AA:BB"), FlakyTreadmill)
    hooks = Hooks(c)
    c.start()
    await settle(c)
    assert c.conn_state == FAILED
    assert hooks.picks == [False]
    assert hooks.notices == [("not in range", "error", "Couldn't connect")]


async def test_start_with_retry_keeps_trying_quietly():
    FlakyTreadmill.attempts, FlakyTreadmill.failures = 0, 2
    c = Controller(Config(address="AA:BB"), FlakyTreadmill)
    hooks = Hooks(c)
    c.start(retry=True)
    assert c.conn_state == RECONNECTING
    await settle(c)
    assert c.conn_state == CONNECTED
    assert FlakyTreadmill.attempts == 3
    assert hooks.picks == [] and hooks.notices == []


async def test_cancel_connect_returns_to_idle():
    FlakyTreadmill.attempts, FlakyTreadmill.failures = 0, 10**6
    c = Controller(Config(address="AA:BB"), FlakyTreadmill)
    Hooks(c)
    c.start(retry=True)
    assert c.connecting
    c.cancel_connect()
    assert c.conn_state == IDLE and not c.connecting


async def test_actions_without_a_status_say_so():
    c = Controller(Config(address="AA:BB"), FakeTreadmill)
    hooks = Hooks(c)
    for action in (c.toggle, c.stop, lambda: c.nudge(0.1), lambda: c.go_preset(1), lambda: c.set_speed(2.0)):
        action()
    assert [n[0] for n in hooks.notices] == ["Not connected to a treadmill yet."] * 5


async def test_view_snapshot_and_shutdown_pauses_moving_belt():
    c = Controller(Config(address="AA:BB"), FakeTreadmill)
    hooks = Hooks(c)
    c.start()
    await settle(c)
    t = FakeTreadmill.instances[-1]
    t.feed(flags=RUNNING, target=3300, speed=3300)

    view = c.view()
    assert view.current_speed == 3.3
    assert view.status.speed == 3300
    assert view.presets[2] == 2.0
    assert dict(detail_rows(view))["Address"] == "AA:BB"
    assert hooks.changes > 0

    await c.shutdown()
    assert t.sent[-1] == protocol.pause(imperial=False)
    assert c.view().status is None and c.conn_state == IDLE


async def test_save_preset_needs_a_running_belt():
    c = Controller(Config(address="AA:BB"), FakeTreadmill)
    hooks = Hooks(c)
    c.start()
    await settle(c)
    t = FakeTreadmill.instances[-1]

    t.feed(flags=PAUSED)
    c.save_preset(9)
    assert Config.load().presets[8] == 6.0
    assert hooks.notices[-1][0].startswith("Get the belt running")

    t.feed(flags=RUNNING, target=3300)
    c.save_preset(9)
    assert Config.load().presets[8] == 3.3
