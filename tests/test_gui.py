"""The desktop GUI against a fake treadmill, using Qt's offscreen platform."""
import os
import sys
import time
import uuid

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pitpat import ble, config, controller as controller_module, protocol  # noqa: E402
from pitpat.config import Config  # noqa: E402
from pitpat.controller import View  # noqa: E402
from pitpat.gui import autostart  # noqa: E402
from pitpat.gui.app import PitPatGui  # noqa: E402
from pitpat.gui.icons import icon_kind, icon_text, make_view_icon  # noqa: E402
from pitpat.gui.runner import AsyncRunner  # noqa: E402
from pitpat.gui.single import SingleInstance  # noqa: E402
from pitpat.gui.tray import Tray, tooltip  # noqa: E402
from pitpat.protocol import Status  # noqa: E402

from fakes import IMPERIAL, PAUSED, RUNNING, STOPPED, FakeTreadmill  # noqa: E402
from test_protocol import frame  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(condition, timeout=3.0):
    """Runs Qt events until `condition()` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the condition")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(controller_module, "RESEND_AFTER", 0)
    FakeTreadmill.instances.clear()


@pytest.fixture
def make_gui(qapp):
    made = []

    def make(cfg: Config) -> PitPatGui:
        runner = AsyncRunner()
        runner.start()
        gui = PitPatGui(qapp, cfg, runner, make_treadmill=FakeTreadmill)
        made.append(gui)
        return gui

    yield make
    for gui in made:
        gui.window.hide_on_close = True  # Closing would otherwise start a shutdown the loop is about to lose
        gui.window.close()
        gui.backend.runner.stop()


def connect(gui: PitPatGui) -> FakeTreadmill:
    gui.start(minimized=True)
    wait_for(lambda: gui.backend.controller.treadmill is not None)
    return FakeTreadmill.instances[-1]


def feed(gui: PitPatGui, t: FakeTreadmill, **kwargs) -> None:
    """Delivers a status frame the way bleak would: on the asyncio thread."""
    gui.backend.runner.call(lambda: t.feed(**kwargs))


def wire(mph):
    return round(mph * protocol.KM_PER_MILE * 1000)


def test_window_shows_status_and_controls_drive_the_treadmill(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    assert not window.toggle.isEnabled()  # Nothing to control yet

    t = connect(gui)
    feed(gui, t, flags=RUNNING, target=3000, speed=3000)
    wait_for(lambda: window.speed.text() == "3.0")
    assert window.state.text() == "RUNNING"
    assert window.toggle.text() == "Pause" and window.toggle.isEnabled()
    assert "Connected to AA:BB" in window.connection.text()

    window.faster.click()
    wait_for(lambda: t.sent and t.sent[-1] == protocol.set_speed(3100, imperial=False))

    window.presets[2].click()  # Default preset 3 is 2.0
    wait_for(lambda: t.sent[-1] == protocol.set_speed(2000, imperial=False))

    window.exact.setValue(4.5)
    window.go.click()
    wait_for(lambda: t.sent[-1] == protocol.set_speed(4500, imperial=False))

    feed(gui, t, flags=RUNNING, target=4500, speed=4500)
    wait_for(lambda: "Target 4.5" in window.target.text())
    assert window.unit.text() == "km/h"
    window.toggle.click()
    wait_for(lambda: t.sent[-1] == protocol.pause(imperial=False))

    feed(gui, t, flags=PAUSED)
    wait_for(lambda: window.toggle.text() == "Resume")
    window.stop.click()
    wait_for(lambda: t.sent[-1] == protocol.stop(imperial=False))


def test_imperial_units_and_preset_highlight(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    t = connect(gui)
    feed(gui, t, flags=IMPERIAL | RUNNING, target=wire(2.0), speed=wire(2.0))
    wait_for(lambda: gui.window.speed.text() == "2.0")
    assert gui.window.unit.text() == "mph"
    assert gui.window.presets[2].property("active") is True  # Preset 3 is 2.0
    assert not gui.window.presets[0].property("active")


def test_save_preset_persists(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    t = connect(gui)
    feed(gui, t, flags=RUNNING, target=3300)
    wait_for(lambda: gui.window.view.current_speed == 3.3)
    gui.backend.save_preset(9)
    wait_for(lambda: Config.load().presets[8] == 3.3)
    wait_for(lambda: gui.window.presets[8].text().endswith("3.3"))


def test_first_run_picker_autoconnects_to_only_treadmill(make_gui, monkeypatch):
    async def discover():
        return [ble.Device("CC:DD", "PitPat-T01", -60)]

    monkeypatch.setattr(ble, "discover", discover)
    gui = make_gui(Config())
    gui.start(minimized=True)
    wait_for(lambda: FakeTreadmill.instances and gui.backend.controller.treadmill is not None)
    assert FakeTreadmill.instances[-1].address == "CC:DD"
    wait_for(lambda: Config.load().address == "CC:DD")


def test_picker_lists_devices_and_connects_to_the_chosen_one(make_gui, monkeypatch):
    async def discover():
        return [ble.Device("CC:DD", "PitPat-T01", -60), ble.Device("EE:FF", "PitPat-T01", -70)]

    monkeypatch.setattr(ble, "discover", discover)
    gui = make_gui(Config(address="EE:FF"))
    gui.pick_device()
    picker = gui.picker
    wait_for(lambda: picker.devices.count() == 2)
    assert "last used" in picker.devices.item(1).text()
    picker.devices.setCurrentRow(1)
    picker.connect_button.click()
    wait_for(lambda: FakeTreadmill.instances and FakeTreadmill.instances[-1].address == "EE:FF")
    wait_for(lambda: gui.picker is None)


def test_quit_pauses_a_moving_belt(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    t = connect(gui)
    feed(gui, t, flags=RUNNING, target=3000)
    wait_for(lambda: gui.window.speed.text() != "--")

    quit_calls = []

    class FakeApp:
        def quit(self):
            quit_calls.append(1)

    gui.app = FakeApp()
    gui.quit()
    wait_for(lambda: quit_calls)
    assert t.sent[-1] == protocol.pause(imperial=False)


def test_closing_the_window_hides_it_when_there_is_a_tray(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    gui.window.hide_on_close = True
    gui.window.show()
    gui.window.close()
    assert not gui.window.isVisible()
    assert not gui.quitting


def test_tray_menu_tooltip_and_icon(make_gui, qapp):
    gui = make_gui(Config(address="AA:BB"))
    t = connect(gui)
    tray = Tray(gui.backend, gui.backend.initial_view())
    assert not tray.toggle.isEnabled()

    feed(gui, t, flags=RUNNING, target=3000, speed=3000, distance=1200, duration_ms=754000)
    wait_for(lambda: gui.backend.controller.status is not None)
    view = gui.backend.controller.view()
    tray.update_view(view)

    assert tray.toggle.text() == "Pause" and tray.toggle.isEnabled()
    assert "3.0 km/h" in tray.tray.toolTip()
    assert "1.20 km" in tray.tray.toolTip()
    assert tray.preset_actions[2].text() == "3   2.0 km/h"
    assert not tray.tray.icon().isNull()

    tray.faster.trigger()
    wait_for(lambda: t.sent and t.sent[-1] == protocol.set_speed(3100, imperial=False))
    tray.preset_actions[0].trigger()
    wait_for(lambda: t.sent[-1] == protocol.set_speed(1000, imperial=False))
    tray.toggle.trigger()
    wait_for(lambda: t.sent[-1] == protocol.pause(imperial=False))


def view_of(conn_state="connected", flags=None, **kwargs) -> View:
    status = Status.parse(frame(flags=flags, **kwargs)) if flags is not None else None
    return View(conn_state, "AA:BB", "fba0", status, None, (1.0,) * 9, None)


def test_icon_reflects_state_and_speed(qapp):
    assert icon_kind(view_of("idle")) == "offline" and icon_text(view_of("idle")) == "–"
    assert icon_kind(view_of("connecting")) == "connecting"
    assert icon_kind(view_of(flags=STOPPED)) == "stopped" and icon_text(view_of(flags=STOPPED)) == "0"
    assert icon_kind(view_of(flags=PAUSED)) == "paused" and icon_text(view_of(flags=PAUSED)) == "II"
    running = view_of(flags=RUNNING, speed=2540, target=2500)
    assert icon_kind(running) == "running" and icon_text(running) == "2.5"
    assert not make_view_icon(running).pixmap(16, 16).isNull()
    assert "2.5 km/h" in tooltip(running)
    assert tooltip(view_of("reconnecting")).endswith("Reconnecting to AA:BB…")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_round_trips_through_the_run_key(monkeypatch):
    import winreg

    key = rf"Software\PitPatTest-{uuid.uuid4().hex}\Run"
    monkeypatch.setattr(autostart, "RUN_KEY", key)
    try:
        assert not autostart.is_enabled()
        autostart.set_enabled(True)
        assert autostart.is_enabled()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, autostart.APP_NAME)
        assert "pitpat.gui" in value and value.endswith("--minimized")
        autostart.set_enabled(False)
        assert not autostart.is_enabled()
    finally:
        for sub in (key, key.rsplit("\\", 1)[0]):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass


@pytest.mark.skipif(sys.platform == "win32", reason="XDG autostart")
def test_autostart_writes_a_desktop_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    assert "--minimized" in (tmp_path / "autostart" / "pitpat.desktop").read_text()
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_second_instance_pokes_the_first(qapp):
    key = f"pitpat-test-{uuid.uuid4().hex}"
    first, second = SingleInstance(key), SingleInstance(key)
    poked = []
    assert first.claim()
    first.activated.connect(lambda: poked.append(1))
    assert not second.claim()
    wait_for(lambda: poked)


# --- Countdown mode ---------------------------------------------------------------------

from pitpat.gui.plan_editor import PlanEditor  # noqa: E402
from pitpat.plans import DISTANCE, KM, MILES, Plan, Point  # noqa: E402

MILE_M = protocol.KM_PER_MILE * 1000


def sample_plan(name="Hills") -> Plan:
    return Plan(name, MILES, DISTANCE, 2.0, (Point(0, 2.0), Point(0.5, 3.0, 2), Point(1.5, 2.5)))


def with_plans(*plans) -> Config:
    return Config(address="AA:BB", plans=[p.to_dict() for p in plans])


def walk(gui, t, mph, **kw):
    kw.setdefault("max_speed", 10000)
    feed(gui, t, flags=IMPERIAL | RUNNING, target=wire(mph), speed=wire(mph), **kw)


def test_countdown_page_lists_plans_and_one_click_runs_one(make_gui):
    gui = make_gui(with_plans(sample_plan("Hills"), sample_plan("Flat")))
    window = gui.window
    assert window.modes.currentIndex() == 0 and window.pages.currentIndex() == 0
    rows = window.countdown.rows
    assert [r.plan.name for r in rows] == ["Hills", "Flat"]
    assert not rows[0].run.isEnabled()  # Not connected yet

    t = connect(gui)
    walk(gui, t, 1.0)
    wait_for(lambda: rows[0].run.isEnabled())

    rows[0].run.click()  # One click: the first speed goes out and the window shows the run
    wait_for(lambda: t.sent and t.sent[-1] == protocol.set_speed(wire(2.0), imperial=True))
    wait_for(lambda: window.view.run is not None)
    assert window.modes.currentIndex() == 1
    assert window.countdown.currentIndex() == 1
    assert window.countdown.run_name.text() == "Hills"
    assert window.countdown.run_left.text() == "2.00" and "mi to go" in window.countdown.run_unit.text()
    assert "0% done" in window.countdown.run_pct.text()
    assert "2 mph" in window.countdown.run_now.text() and "incline 2" not in window.countdown.run_now.text()
    assert "3 mph · incline 2" in window.countdown.run_next.text() and "in 0.50 mi" in window.countdown.run_next_note.text()


def test_run_panel_tracks_progress_override_and_incline(make_gui):
    gui = make_gui(with_plans(sample_plan()))
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0)
    wait_for(lambda: window.countdown.rows[0].run.isEnabled())
    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)

    walk(gui, t, 2.0, distance=round(0.75 * MILE_M))
    wait_for(lambda: window.view.run.index == 1)
    wait_for(lambda: "37% done" in window.countdown.run_pct.text())
    assert "incline 2" in window.countdown.run_now.text()
    assert "treadmill reports 0" in window.countdown.run_incline.text()
    assert "set this on the treadmill" in window.countdown.run_incline.text()

    window.faster.click()  # Overriding by hand is allowed at any time
    wait_for(lambda: window.view.run.overridden)
    assert "changed the speed" in window.countdown.run_override.text()

    window.countdown.skip.click()
    wait_for(lambda: window.view.run.index == 2)
    assert not window.countdown.skip.isEnabled()  # No point after the last
    assert not window.view.run.overridden


def test_switching_tabs_never_loses_a_plan_in_progress(make_gui):
    gui = make_gui(with_plans(sample_plan()))
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0)
    wait_for(lambda: window.countdown.rows[0].run.isEnabled())

    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)
    walk(gui, t, 2.0, distance=round(0.5 * MILE_M))
    wait_for(lambda: "25% done" in window.countdown.run_pct.text())

    for index in (0, 2, 3, 0, 1):  # Normal, History, Settings, Normal, back to Countdown
        window.modes.setCurrentIndex(index)
        assert window.pages.currentIndex() == index
        walk(gui, t, 2.0, distance=round(0.5 * MILE_M))  # A status frame landing mid-switch must not flip the tab
        wait_for(lambda: window.view.run is not None)
        assert window.modes.currentIndex() == index
        assert "25%" in window.modes.tabText(1)  # The tab itself says it is running

    assert window.countdown.currentIndex() == 1
    assert window.countdown.run_name.text() == "Hills" and "25% done" in window.countdown.run_pct.text()

    window.modes.setCurrentIndex(0)  # Normal shows it too, and a click on that goes back to it
    wait_for(lambda: not window.plan_banner.isHidden())
    assert "Hills is running" in window.plan_banner.text() and "1.50 mi left" in window.plan_banner.text()
    window.plan_banner.click()
    assert window.modes.currentIndex() == 1


def test_end_plan_hands_control_back(make_gui):
    gui = make_gui(with_plans(sample_plan()))
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0)
    wait_for(lambda: window.countdown.rows[0].run.isEnabled())
    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)

    window.modes.setCurrentIndex(0)
    gui.backend.end_plan()
    wait_for(lambda: window.view.run is None)
    assert window.countdown.currentIndex() == 0 and window.plan_banner.isHidden()
    assert window.modes.tabText(1) == "Countdown" and window.modes.currentIndex() == 0


def test_cards_count_down_to_zero_in_a_plan(make_gui):
    gui = make_gui(with_plans(sample_plan()))  # 2 miles
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0, distance=round(0.3 * MILE_M))
    wait_for(lambda: window.cards["distance"].value.text() == "0.30 mi")
    assert window.cards["distance"].caption.text() == "Distance"  # Counting up outside a plan

    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)
    walk(gui, t, 2.0, distance=round(0.5 * MILE_M))
    wait_for(lambda: window.cards["distance"].value.text() == "1.80 mi")  # Counts from where the run began (0.3)
    assert window.cards["distance"].caption.text() == "Distance left"
    assert window.cards["time"].caption.text() == "Time"

    walk(gui, t, 2.0, distance=round(1.5 * MILE_M))
    wait_for(lambda: window.cards["distance"].value.text() == "0.80 mi")

    gui.backend.end_plan()
    wait_for(lambda: window.cards["distance"].caption.text() == "Distance")
    wait_for(lambda: window.cards["distance"].value.text() == "1.50 mi")


def test_time_plans_count_the_time_down(make_gui):
    plan = Plan("Quick", MILES, "time", 30.0, (Point(0, 2.0), Point(10, 3.0)))
    gui = make_gui(with_plans(plan))
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0)
    wait_for(lambda: window.countdown.rows[0].run.isEnabled())
    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)
    walk(gui, t, 2.0, duration_ms=5 * 60_000)
    wait_for(lambda: window.cards["time"].value.text() == "25:00")
    assert window.cards["time"].caption.text() == "Time left" and window.cards["distance"].caption.text() == "Distance"


def test_plan_editor_builds_a_plan_and_explains_mistakes(qapp):
    editor = PlanEditor()
    assert editor.plan is None
    save = editor.buttons.button(editor.buttons.StandardButton.Save)
    assert not save.isEnabled() and "name" in editor.summary.text()  # Unnamed

    editor.name.setText("Morning hills")
    assert save.isEnabled()
    assert editor.table.rowCount() == 2 and not editor.rows[0][0].isEnabled()  # The start is fixed at 0

    editor.total.setValue(3.0)
    editor.add_point()  # Halfway between the last point (1.0) and the end
    assert editor.table.rowCount() == 3 and editor.rows[2][0].value() == 2.0
    editor.rows[2][1].setValue(4.5)
    editor.rows[2][2].setValue(5)
    editor.rows[1][0].setValue(2.0)  # Same place as the new point
    assert not save.isEnabled() and "in order" in editor.summary.text()
    editor.rows[1][0].setValue(1.5)
    assert save.isEnabled()

    editor.stop_at_end.setChecked(False)
    save.click()
    assert editor.plan == Plan(
        "Morning hills", MILES, DISTANCE, 3.0, (Point(0, 2.5), Point(1.5, 3.0), Point(2.0, 4.5, 5)), stop_at_end=False
    )

    editor.remove_point()  # Nothing selected: ignored
    assert editor.table.rowCount() == 3
    editor.table.setCurrentCell(0, 0)
    editor.remove_point()  # The start can't be removed
    assert editor.table.rowCount() == 3
    editor.table.setCurrentCell(2, 0)
    editor.remove_point()
    assert editor.table.rowCount() == 2


def test_plan_editor_edits_in_place_and_rejects_duplicate_names(qapp):
    original = sample_plan("Hills")
    editor = PlanEditor(original, frozenset({"Hills", "Flat"}))
    save = editor.buttons.button(editor.buttons.StandardButton.Save)
    assert editor.name.text() == "Hills" and save.isEnabled()  # Keeping its own name is fine
    assert editor.current() == original

    editor.name.setText("Flat")
    assert not save.isEnabled() and "already have" in editor.summary.text()

    editor.name.setText("Hills")
    editor.unit.setCurrentIndex(1)
    editor.measure.setCurrentIndex(1)
    assert editor.table.horizontalHeaderItem(0).text() == "At (min)"
    assert editor.table.horizontalHeaderItem(1).text() == "Speed (km/h)"
    assert editor.current().unit == KM and editor.current().measure == "time"


def test_saving_plans_persists_and_updates_the_page(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    assert gui.window.countdown.rows == [] and not gui.window.countdown.empty.isHidden()
    gui.backend.save_plans((sample_plan("Hills"), sample_plan("Flat")))
    wait_for(lambda: len(gui.window.countdown.rows) == 2)
    assert gui.window.countdown.empty.isHidden()
    assert [p["name"] for p in Config.load().plans] == ["Hills", "Flat"]

    gui.backend.save_plans((sample_plan("Flat"),))
    wait_for(lambda: len(gui.window.countdown.rows) == 1)


def test_tray_plans_menu_runs_a_plan_and_shows_progress(make_gui, qapp):
    gui = make_gui(with_plans(sample_plan("Hills")))
    t = connect(gui)
    tray = Tray(gui.backend, gui.backend.initial_view())
    gui.backend.changed.connect(tray.update_view)
    assert not tray.plans_menu.menuAction().isEnabled()
    assert not tray.skip_point.isVisible() and not tray.end_plan.isVisible()

    walk(gui, t, 1.0)
    wait_for(lambda: tray.plans_menu.menuAction().isEnabled())
    (action,) = tray.plans_menu.actions()
    assert action.text().startswith("Hills")
    action.trigger()
    wait_for(lambda: t.sent and t.sent[-1] == protocol.set_speed(wire(2.0), imperial=True))
    wait_for(lambda: tray.end_plan.isVisible())
    walk(gui, t, 2.0, distance=round(0.5 * MILE_M) + 5)
    wait_for(lambda: "Hills: 25%" in tray.tray.toolTip())
    assert tray.skip_point.isEnabled()

    tray.end_plan.trigger()
    wait_for(lambda: not tray.end_plan.isVisible())
    assert "Hills:" not in tray.tray.toolTip()


# --- Incline control -----------------------------------------------------------------------

def test_incline_controls_wait_for_permission_then_drive_the_treadmill(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    tray = Tray(gui.backend, gui.backend.initial_view())
    t = connect(gui)
    walk(gui, t, 2.0, incline=3, max_incline=12)
    wait_for(lambda: window.incline_label.text() == "3" and "of 12" in window.incline_sub.text())
    tray.update_view(gui.backend.controller.view())
    assert not window.settings.auto_incline.isChecked()
    assert not window.incline_up.isEnabled() and not window.incline_down.isEnabled()
    assert not tray.incline_up.isVisible()

    window.settings.auto_incline.setChecked(True)  # The person allows it, on the Settings page
    wait_for(lambda: window.view.auto_incline and window.incline_up.isEnabled())
    tray.update_view(window.view)
    assert tray.incline_up.isVisible() and tray.incline_up.isEnabled()
    assert Config.load().auto_incline

    window.incline_up.click()
    wait_for(lambda: t.sent and t.sent[-1] == protocol.set_speed(wire(2.0), imperial=True, incline=4))
    wait_for(lambda: "→ 4" in window.incline_sub.text())
    walk(gui, t, 2.0, incline=4, max_incline=12)  # The treadmill gets there
    wait_for(lambda: window.view.incline_goal is None and "→" not in window.incline_sub.text())
    tray.incline_down.trigger()
    wait_for(lambda: t.sent[-1] == protocol.set_speed(wire(2.0), imperial=True, incline=3))

    window.settings.auto_incline.setChecked(False)
    wait_for(lambda: not window.view.auto_incline and not window.incline_up.isEnabled())


def test_run_panel_shows_the_incline_moving_when_the_app_sets_it(make_gui):
    cfg = with_plans(sample_plan())
    cfg.auto_incline = True
    gui = make_gui(cfg)
    window = gui.window
    t = connect(gui)
    walk(gui, t, 1.0, max_incline=12)
    wait_for(lambda: window.countdown.rows[0].run.isEnabled())
    window.countdown.rows[0].run.click()
    wait_for(lambda: window.view.run is not None)

    walk(gui, t, 2.0, max_incline=12, distance=round(0.75 * MILE_M))
    wait_for(lambda: window.view.run.index == 1)
    wait_for(lambda: t.sent[-1] == protocol.set_speed(wire(3.0), imperial=True, incline=2))
    text = window.countdown.run_incline.text()
    assert "Incline target 2" in text and "moving" in text and "set this on the treadmill" not in text


# --- Pause when off the belt ---------------------------------------------------------------

def test_off_belt_controls_save_the_setting_and_show_the_countdown(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    tray = Tray(gui.backend, gui.backend.initial_view())
    t = connect(gui)
    feed(gui, t, flags=RUNNING, target=1600, speed=1600, load=50)
    wait_for(lambda: window.presence.text() == "On the belt")
    settings = window.settings
    assert not settings.off_belt.isChecked() and not settings.off_seconds.isEnabled()

    settings.off_belt.setChecked(True)
    wait_for(lambda: window.view.off_belt_pause and settings.off_seconds.isEnabled())
    settings.off_seconds.setValue(20)
    wait_for(lambda: window.view.off_belt_seconds == 20)
    saved = Config.load()
    assert saved.off_belt_pause and saved.off_belt_seconds == 20

    feed(gui, t, flags=RUNNING, target=1600, speed=1600, load=10)  # Steps off
    wait_for(lambda: window.presence.text().startswith("Off the belt · pausing in"))

    settings.off_belt.setChecked(False)
    wait_for(lambda: not window.view.off_belt_pause)
    wait_for(lambda: window.presence.text() == "Off the belt")
    assert protocol.pause(imperial=False) not in t.sent


def test_the_tray_opens_the_settings_page(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    tray = Tray(gui.backend, gui.backend.initial_view())
    asked = []
    tray.settings_requested.connect(lambda: asked.append(True))
    tray.settings.trigger()
    assert asked
    gui.show_settings()
    assert gui.window.modes.currentIndex() == 3 and gui.window.pages.currentIndex() == 3


def test_rest_settings_are_saved_and_show_their_state(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    settings = window.settings
    t = connect(gui)
    assert settings.rest_limit.isChecked() and settings.rest_after.value() == 120 and settings.rest_for.value() == 15

    settings.rest_after.setValue(90)
    settings.rest_for.setValue(20)
    wait_for(lambda: window.view.rest_after_seconds == 5400 and window.view.rest_seconds == 1200)
    saved = Config.load()
    assert saved.rest_after_minutes == 90 and saved.rest_minutes == 20

    feed(gui, t, flags=RUNNING, target=1600, speed=1600)
    wait_for(lambda: window.rest.text().startswith("Rest in 1:"))
    assert "Run time since its last rest" in settings.rest_status.text()

    settings.rest_limit.setChecked(False)
    wait_for(lambda: not window.view.rest_limit and not settings.rest_after.isEnabled())
    assert window.rest.text() == "" and not Config.load().rest_limit


def test_the_window_says_when_the_treadmill_cannot_report_presence(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    t = connect(gui)
    feed(gui, t, flags=RUNNING, target=1600, speed=1600)
    wait_for(lambda: gui.window.presence.text() == "This treadmill doesn't report it")
    feed(gui, t, flags=PAUSED)
    wait_for(lambda: gui.window.presence.text() == "")


# --- Floors, History and the always-visible warnings ------------------------------------------

from dataclasses import replace  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from pitpat.history import History, Workout, week_start  # noqa: E402


def wk(day: date, miles: float, floors: float = 0.0, minutes: int = 20) -> Workout:
    return Workout(day.isoformat(), f"{day.isoformat()}T07:00:00", miles * 1609.344, floors, minutes * 60)


def seed_history(*items: Workout) -> None:
    history = History()
    for item in items:
        history.add(item)


def test_the_floors_card_counts_live_and_resets_after_a_workout(make_gui, monkeypatch):
    monkeypatch.setattr("pitpat.controller.WORKOUT_END_AFTER", 0)  # A stop ends it at once
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    t = connect(gui)
    walk(gui, t, 2.0, incline=4, max_incline=12, distance=0)
    walk(gui, t, 2.0, incline=4, max_incline=12, distance=round(MILE_M))
    wait_for(lambda: window.cards["floors"].value.text() == "21.1")  # A mile at a 4 % grade
    assert window.cards["floors"].caption.text() == "Floors climbed"

    feed(gui, t, flags=IMPERIAL | STOPPED, distance=round(MILE_M))
    feed(gui, t, flags=IMPERIAL | STOPPED, distance=round(MILE_M))
    wait_for(lambda: window.cards["floors"].value.text() == "0.0")
    wait_for(lambda: len(window.view.workouts) == 1)
    assert Config.load().imperial and len(History.load().workouts) == 1


def test_history_page_totals_this_week_and_other_ranges(make_gui):
    today = date.today()
    monday = week_start(today)
    seed_history(wk(monday, 2, 10), wk(today, 1.5, 4.4), wk(monday - timedelta(days=1), 3, 6), wk(monday - timedelta(days=20), 4, 2))
    gui = make_gui(Config(address="AA:BB"))
    page = gui.window.history
    values = page.tile_values
    wait_for(lambda: values["workouts"].text() == "2")
    assert values["distance"].text() == "3.5" and values["floors"].text() == "14"
    assert page.heading.text() == "Weekly summary" and not page.body.isHidden() and page.empty.isHidden()
    assert page.weeks_card.isHidden()  # A single week needs no week-by-week table
    assert len(page.chart_distance.bars) == 7 and len(page.chart_floors.bars) == 7
    assert page.days_table.rowCount() == (1 if monday == today else 2)

    page.select("last")
    assert page.heading.text() == "Last week" and values["workouts"].text() == "1" and values["distance"].text() == "3.0"

    page.select("four")
    assert values["workouts"].text() == "4" and values["distance"].text() == "10.5"
    assert not page.weeks_card.isHidden() and page.weeks_table.rowCount() == 5  # Four weeks and the total row
    assert page.weeks_table.item(4, 0).text() == "Total" and page.weeks_table.item(4, 1).text() == "10.5"
    assert len(page.chart_distance.bars) == 28  # Under a month: a bar per day

    page.select("custom")  # Two dates of your own
    page.first_edit.setDate(QDate(today.year, today.month, today.day).addDays(-60))
    page.last_edit.setDate(QDate(today.year, today.month, today.day))
    assert values["workouts"].text() == "4" and "Pick" not in page.heading.text()
    assert len(page.chart_distance.bars) > 8  # More than a month, so a bar per week
    assert page.chart_titles[0].text().endswith("PER WEEK")

    page.first_edit.setDate(QDate(today.year, today.month, today.day).addDays(-100))
    page.last_edit.setDate(QDate(today.year, today.month, today.day).addDays(-90))
    assert not page.empty.isHidden() and "No workouts" in page.empty.text() and page.body.isHidden()

    page.first_edit.setDate(QDate(today.year, today.month, today.day))
    page.last_edit.setDate(QDate(today.year, today.month, today.day).addDays(-5))  # Backwards
    assert "first date" in page.date_message.text() and "start date" in page.empty.text()


def test_history_summary_text_can_be_generated_and_copied(make_gui):
    today = date.today()
    seed_history(wk(week_start(today), 2, 10), wk(today, 1.5, 4.4, minutes=30))
    gui = make_gui(Config(address="AA:BB"))
    page = gui.window.history
    wait_for(lambda: page.tile_values["workouts"].text() == "2")
    assert page.summary_text.isHidden() and not page.summary_hint.isHidden() and page.copy_button.isHidden()

    page.summary_button.click()
    assert page.summary_text.text().startswith("PitPat summary · ") and "Distance: 3.5 mi" in page.summary_text.text()
    assert "Floors climbed: 14" in page.summary_text.text()
    assert page.summary_button.text() == "Hide" and not page.copy_button.isHidden()

    page.copy_button.click()
    assert QApplication.clipboard().text() == page.summary_text.text() and page.copy_button.text() == "Copied"

    page.select("four")  # A new range starts without a stale summary
    assert page.summary_text.isHidden()


def test_history_follows_the_treadmills_units(make_gui):
    seed_history(wk(date.today(), 1.0))
    gui = make_gui(Config(address="AA:BB", imperial=False))
    page = gui.window.history
    wait_for(lambda: page.tile_values["workouts"].text() == "1")
    assert page.tile_values["distance"].text() == "1.6" and "KM" in page.tile_titles["distance"].text()
    assert page.days_table.item(0, 2).text() == "1.61 km"


def test_warnings_stay_on_every_page_and_the_running_bar_shows_off_the_run_page(make_gui):
    gui = make_gui(Config(address="AA:BB"))
    window = gui.window
    t = connect(gui)
    feed(gui, t, flags=PAUSED)
    wait_for(lambda: window.view.status is not None)
    assert window.cool_banner.isHidden() and window.away_banner.isHidden()

    window.update_view(replace(window.view, rest_left=300.0))  # Cooling down: the belt is paused
    assert not window.cool_banner.isHidden() and window.cool_time.text() == "0:05:00"
    assert not window.toggle.isEnabled()  # Start is locked
    window.update_view(replace(window.view, rest_left=None))
    assert window.cool_banner.isHidden() and window.toggle.isEnabled()

    feed(gui, t, flags=RUNNING, target=1600, speed=1600)
    wait_for(lambda: window.view.status.state == protocol.State.RUNNING)
    window.update_view(replace(window.view, on_belt=False, off_belt_pause=True, off_belt_for=4.0, off_belt_seconds=10.0))
    assert not window.away_banner.isHidden() and window.away_time.text() == "6 s"

    assert window.mini.isHidden()  # The Normal page already shows the belt
    for page in (1, 2, 3):
        window.modes.setCurrentIndex(page)
        assert not window.mini.isHidden()
        assert "km/h" in window.mini_open.text()
    window.modes.setCurrentIndex(0)
    assert window.mini.isHidden()


def test_history_lists_each_workout_with_its_plan_and_time(make_gui):
    today = date.today()
    seed_history(
        Workout(today.isoformat(), f"{today.isoformat()}T07:30:00", 3218.688, 20.0, 3569, "2 Up and Down"),
        Workout(today.isoformat(), f"{today.isoformat()}T18:10:00", 1609.344, 0.0, 1500, None),
    )
    gui = make_gui(Config(address="AA:BB"))
    page = gui.window.history
    wait_for(lambda: page.list_table.rowCount() == 2)
    cells = [[page.list_table.item(r, c).text() for c in range(5)] for r in range(2)]
    assert cells[0][1:] == ["No plan", "25:00", "1.00 mi", "0.0"] and cells[0][0].endswith("6:10 PM")  # Newest first
    assert cells[1][1:] == ["2 Up and Down", "59:29", "2.00 mi", "20.0"] and cells[1][0].endswith("7:30 AM")
