"""Wires the window, tray and backend together."""
import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from ..config import Config
from ..controller import TreadmillFactory
from .backend import Backend
from .mainwindow import MainWindow
from .picker import PickerDialog
from .runner import AsyncRunner
from .tray import Tray

log = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_MS = 6000


class PitPatGui(QObject):
    def __init__(
        self,
        app: QApplication,
        config: Config,
        runner: AsyncRunner,
        make_treadmill: Optional[TreadmillFactory] = None,
        startup_speed: Optional[float] = None,
    ):
        super().__init__()
        self.app = app
        self.config = config
        self.quitting = False
        self.picker: Optional[PickerDialog] = None
        self.backend = Backend(config, runner, make_treadmill, startup_speed)
        view = self.backend.initial_view()

        self.window = MainWindow(self.backend, view)
        self.has_tray = Tray.available()
        self.tray: Optional[Tray] = Tray(self.backend, view) if self.has_tray else None
        self.window.hide_on_close = self.has_tray

        self.backend.changed.connect(self.on_change)
        self.backend.notified.connect(self.on_notify)
        self.backend.pick_requested.connect(self.pick_device)
        self.window.choose_requested.connect(lambda: self.pick_device(False))
        self.window.quit_requested.connect(self.quit)
        if self.tray:
            self.tray.show_requested.connect(self.toggle_window)
            self.tray.choose_requested.connect(lambda: self.pick_device(False))
            self.tray.settings_requested.connect(self.show_settings)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()

    def start(self, minimized: bool) -> None:
        if not (minimized and self.has_tray):
            self.window.raise_to_front()
        self.backend.start()

    # --- Signals ------------------------------------------------------------------

    def on_change(self, view) -> None:
        self.window.update_view(view)
        if self.tray:
            self.tray.update_view(view)

    def on_notify(self, message: str, severity: str, title: str) -> None:
        self.window.show_message(f"{title}: {message}" if title else message)
        # The status bar is easy to miss, so a rest or off-belt pause is also announced when the window is open.
        pause_notice = title in ("Treadmill needs to cool down", "Treadmill paused")
        if self.tray and (pause_notice or (not self.window.isVisible() and severity != "information")):
            self.tray.message(title or "PitPat", message, severity)

    def show_settings(self) -> None:
        self.window.show_settings()
        self.window.raise_to_front()

    def toggle_window(self) -> None:
        if self.window.isVisible() and not self.window.isMinimized():
            self.window.hide()
        else:
            self.window.raise_to_front()

    def pick_device(self, auto_pick: bool = False) -> None:
        if self.picker is not None or self.quitting:
            return
        self.window.raise_to_front()
        # Connecting scans for the device too, and some stacks allow one scan at a time.
        self.backend.cancel_connect()
        picker = PickerDialog(self.backend, self.config.address, auto_pick, self.window)
        self.picker = picker
        picker.finished.connect(lambda _: self.picker_done(picker))
        picker.open()

    def picker_done(self, picker: PickerDialog) -> None:
        self.picker = None
        picker.deleteLater()
        if picker.address:
            self.backend.connect_to(picker.address)

    def quit(self) -> None:
        """Pauses the belt if it's moving, then exits."""
        if self.quitting:
            return
        self.quitting = True
        self.window.hide()
        self.backend.shutdown_finished.connect(self.app.quit)
        QTimer.singleShot(SHUTDOWN_TIMEOUT_MS, self.app.quit)  # Don't hang on a dead connection
        self.backend.shutdown()

    def close(self) -> None:
        if self.tray:
            self.tray.hide()  # Otherwise Windows leaves a ghost icon until the pointer passes over it
        self.window.hide_on_close = False
