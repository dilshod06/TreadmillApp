"""Bridges the Controller (on the runner's asyncio thread) to Qt (main thread).

Qt code calls the plain methods here, which hop onto the runner thread. The
Controller's hooks come back as signals, which Qt queues onto the main thread,
so widgets never touch Controller state directly.
"""
import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..config import Config
from ..controller import Controller, TreadmillFactory, View
from .runner import AsyncRunner

log = logging.getLogger(__name__)


class Backend(QObject):
    changed = Signal(object)  # View
    notified = Signal(str, str, str)  # message, severity ("information"/"warning"/"error"), title
    pick_requested = Signal(bool)  # auto_pick: connect straight away if exactly one is found
    scan_finished = Signal(object, str)  # list[ble.Device], error text ("" on success)
    shutdown_finished = Signal()

    def __init__(
        self,
        config: Config,
        runner: AsyncRunner,
        make_treadmill: Optional[TreadmillFactory] = None,
        startup_speed: Optional[float] = None,
    ):
        super().__init__()
        self.runner = runner
        self.controller = Controller(config, make_treadmill, startup_speed)
        c = self.controller
        c.on_change = lambda: self.changed.emit(c.view())
        c.on_notify = lambda message, severity, title: self.notified.emit(message, severity, title)
        c.on_pick_device = self.pick_requested.emit

    def initial_view(self) -> View:
        return self.controller.view()

    # --- Called from the Qt thread ------------------------------------------------

    def start(self) -> None:
        # A saved treadmill that's off or out of range is retried quietly, since
        # this often runs at login with nobody looking.
        self.runner.call(self.controller.start, True)

    def toggle(self) -> None:
        self.runner.call(self.controller.toggle)

    def stop_belt(self) -> None:
        self.runner.call(self.controller.stop)

    def nudge(self, step: float) -> None:
        self.runner.call(self.controller.nudge, step)

    def nudge_incline(self, step: int) -> None:
        self.runner.call(self.controller.nudge_incline, step)

    def set_auto_incline(self, enabled: bool) -> None:
        self.runner.call(self.controller.set_auto_incline, enabled)

    def set_off_belt_pause(self, enabled: bool) -> None:
        self.runner.call(self.controller.set_off_belt_pause, enabled)

    def set_off_belt_seconds(self, seconds: float) -> None:
        self.runner.call(self.controller.set_off_belt_seconds, seconds)

    def set_rest(self, enabled=None, after_minutes=None, rest_minutes=None) -> None:
        self.runner.call(self.controller.set_rest, enabled, after_minutes, rest_minutes)

    def go_preset(self, n: int) -> None:
        self.runner.call(self.controller.go_preset, n)

    def set_speed(self, speed: float) -> None:
        self.runner.call(self.controller.set_speed, speed)

    def save_preset(self, n: int) -> None:
        self.runner.call(self.controller.save_preset, n)

    def save_plans(self, plans) -> None:
        self.runner.call(self.controller.set_plans, tuple(plans))

    def run_plan(self, plan) -> None:
        self.runner.call(self.controller.start_plan, plan)

    def skip_point(self) -> None:
        self.runner.call(self.controller.skip_point)

    def end_plan(self) -> None:
        self.runner.call(self.controller.end_plan)

    def connect_to(self, address: str) -> None:
        self.runner.call(self.controller.connect, address)

    def cancel_connect(self) -> None:
        self.runner.call(self.controller.cancel_connect)

    def scan(self) -> None:
        self.runner.submit(self._scan())

    async def _scan(self) -> None:
        from .. import ble  # On this thread, so bleak sets up COM here and not in Qt's.

        try:
            devices, error = await ble.discover(), ""
        except Exception as e:
            log.exception("Scan failed")
            devices, error = [], str(e) or type(e).__name__
        self.scan_finished.emit(devices, error)

    def shutdown(self) -> None:
        """Pauses a moving belt and disconnects, then emits shutdown_finished."""
        future = self.runner.submit(self.controller.shutdown())
        future.add_done_callback(lambda _: self.shutdown_finished.emit())
