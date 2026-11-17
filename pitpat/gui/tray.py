"""The system tray icon: live speed on the icon, status in the tooltip, and a
menu with the usual controls."""
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..controller import STATE_LABELS, View, fmt_duration
from ..plans import fmt_amount
from ..protocol import State
from .backend import Backend
from .icons import make_icon, make_view_icon

log = logging.getLogger(__name__)

SEVERITY_ICONS = {
    "information": QSystemTrayIcon.MessageIcon.Information,
    "warning": QSystemTrayIcon.MessageIcon.Warning,
    "error": QSystemTrayIcon.MessageIcon.Critical,
}


def tooltip(view: View) -> str:
    """Kept short: Windows cuts tooltips off at 127 characters."""
    s = view.status
    if s is None:
        text = {
            "connecting": f"Connecting to {view.address}…",
            "reconnecting": f"Reconnecting to {view.address}…",
        }.get(view.conn_state, "Not connected")
        return f"PitPat\n{text}"
    steps = f" · {s.steps:,} steps" if s.steps is not None else ""
    third = f"{fmt_duration(s.duration_s)} · {s.distance:.2f} {s.distance_unit}{steps}"
    if view.run:
        run = view.run
        third = f"{run.plan.name}: {round(run.fraction * 100)}% · {fmt_amount(run.plan, run.remaining)} left"
    return (
        f"PitPat – {STATE_LABELS[s.state].capitalize()}\n"
        f"{s.to_display(s.speed):.1f} {s.speed_unit} (target {s.to_display(s.target_speed):.1f})\n"
        f"{third}"
    )


class Tray(QObject):
    show_requested = Signal()
    choose_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, backend: Backend, view: View):
        super().__init__()
        self.backend = backend
        self.view = view
        self.tray = QSystemTrayIcon(make_icon("offline", "PP"))
        self.tray.activated.connect(self._activated)

        menu = QMenu()
        self.menu = menu  # The tray doesn't take ownership
        self.header = menu.addAction("PitPat")
        self.header.setEnabled(False)
        menu.addSeparator()
        self.open = menu.addAction("Open PitPat")
        self.open.triggered.connect(self.show_requested)
        menu.addSeparator()
        self.toggle = menu.addAction("Start")
        self.toggle.triggered.connect(backend.toggle)
        self.faster = menu.addAction("Speed +0.1")
        self.faster.triggered.connect(lambda: backend.nudge(0.1))
        self.slower = menu.addAction("Speed −0.1")
        self.slower.triggered.connect(lambda: backend.nudge(-0.1))
        # Only shown once the person lets the app set the incline.
        self.incline_up = menu.addAction("Incline +1")
        self.incline_up.triggered.connect(lambda: backend.nudge_incline(1))
        self.incline_down = menu.addAction("Incline −1")
        self.incline_down.triggered.connect(lambda: backend.nudge_incline(-1))
        # Built once and updated in place: replacing items would close the menu under the pointer.
        self.preset_menu = menu.addMenu("Presets")
        self.preset_actions: list[QAction] = []
        for n in range(1, 10):
            action = self.preset_menu.addAction(str(n))
            action.triggered.connect(lambda _=False, n=n: backend.go_preset(n))
            self.preset_actions.append(action)
        # Rebuilt only when the saved plans change, for the same reason.
        self.plans_menu = menu.addMenu("Plans")
        self.shown_plans: tuple = ()
        self.skip_point = menu.addAction("Skip to next point")
        self.skip_point.triggered.connect(backend.skip_point)
        self.end_plan = menu.addAction("End plan")
        self.end_plan.triggered.connect(backend.end_plan)
        self.stop = menu.addAction("Stop")
        self.stop.triggered.connect(backend.stop_belt)
        menu.addSeparator()
        self.choose = menu.addAction("Choose treadmill…")
        self.choose.triggered.connect(self.choose_requested)
        self.settings = menu.addAction("Settings…")
        self.settings.triggered.connect(self.settings_requested)
        menu.addSeparator()
        self.quit = menu.addAction("Quit")
        self.quit.triggered.connect(self.quit_requested)
        self.tray.setContextMenu(menu)

        self._shown_icon = None
        self.update_view(view)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def message(self, title: str, text: str, severity: str = "information") -> None:
        self.tray.showMessage(title, text, SEVERITY_ICONS.get(severity, SEVERITY_ICONS["information"]), 6000)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_requested.emit()

    def update_view(self, view: View) -> None:
        self.view = view
        s = view.status
        key = (view.conn_state, s.state if s else None, round(s.to_display(s.speed), 1) if s else None)
        if key != self._shown_icon:  # The status arrives every second; the icon rarely changes
            self._shown_icon = key
            self.tray.setIcon(make_view_icon(view))
        self.tray.setToolTip(tooltip(view))
        self.header.setText(tooltip(view).splitlines()[1] if s is None else tooltip(view).splitlines()[0])

        connected = s is not None
        moving = connected and s.state in (State.RUNNING, State.STARTING)
        self.toggle.setText("Pause" if moving else "Start")
        for action in (self.toggle, self.faster, self.slower, self.stop, self.preset_menu.menuAction()):
            action.setEnabled(connected)
        self._update_plans(view, connected)
        steerable = connected and s.max_incline > 0 and s.state == State.RUNNING
        for action in (self.incline_up, self.incline_down):
            action.setVisible(view.auto_incline)
            action.setEnabled(steerable)
        for n, (action, speed) in enumerate(zip(self.preset_actions, view.presets), 1):
            unit = f" {s.speed_unit}" if s else ""
            action.setText(f"{n}   {speed:.1f}{unit}")
            action.setCheckable(True)
            action.setChecked(connected and speed == view.current_speed)

    def _update_plans(self, view: View, connected: bool) -> None:
        if view.plans != self.shown_plans:
            self.shown_plans = view.plans
            self.plans_menu.clear()
            for plan in view.plans:
                action = self.plans_menu.addAction(f"{plan.name}   ({plan.summary()})")
                action.triggered.connect(lambda _=False, p=plan: self.backend.run_plan(p))
            if not view.plans:
                self.plans_menu.addAction("No plans yet. Make one in the window.").setEnabled(False)
        self.plans_menu.menuAction().setEnabled(connected and bool(view.plans))
        self.skip_point.setVisible(view.run is not None)
        self.skip_point.setEnabled(view.run is not None and view.run.next_point is not None)
        self.end_plan.setVisible(view.run is not None)
