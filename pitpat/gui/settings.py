"""The Settings page: everything the app can be told to do on its own, in one place."""
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..controller import (
    MAX_OFF_BELT_SECONDS, MAX_REST, MAX_REST_AFTER, MIN_OFF_BELT_SECONDS, MIN_REST, MIN_REST_AFTER, View,
    detail_rows, fmt_duration,
)
from . import autostart
from .backend import Backend
from .widgets import Switch, card, label, make_button, set_text

log = logging.getLogger(__name__)


def _spin(low: float, high: float) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(int(low), int(high))
    spin.setKeyboardTracking(False)  # Save when they finish typing, not on each digit
    spin.setFixedWidth(84)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    return spin


def _row(title: str, hint: str, control: QWidget, indent: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(32 if indent else 16, 12, 16, 12)
    layout.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(1)
    heading = QLabel(title)
    heading.setStyleSheet("font-weight: 600;")
    text.addWidget(heading)
    if hint:
        sub = label(hint, muted=True)
        sub.setWordWrap(True)
        text.addWidget(sub)
    layout.addLayout(text, 1)
    layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def _unit_box(spin: QSpinBox, unit: str) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(spin)
    layout.addWidget(label(unit, muted=True))
    return box


class SettingsPage(QWidget):
    choose_requested = Signal()
    message = Signal(str)

    def __init__(self, backend: Backend):
        super().__init__()
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(label("Changes are saved as you make them.", muted=True))

        def group(name: str, *rows: QWidget) -> None:
            layout.addSpacing(10)
            layout.addWidget(label(name.upper(), caption=True))
            frame = card()
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(0)
            for n, row in enumerate(rows):
                if n:
                    line = QFrame()
                    line.setObjectName("divider")
                    line.setFixedHeight(1)
                    inner.addWidget(line)
                inner.addWidget(row)
            layout.addWidget(frame)

        self.auto_incline = Switch()
        self.auto_incline.toggled.connect(backend.set_auto_incline)
        group("Incline", _row(
            "Let the app set the incline",
            "On: plans change it at each point and the incline buttons work. "
            "Off: the app never touches it, and a plan only reminds you.",
            self.auto_incline,
        ))

        self.off_belt = Switch()
        self.off_belt.toggled.connect(backend.set_off_belt_pause)
        self.off_seconds = _spin(MIN_OFF_BELT_SECONDS, MAX_OFF_BELT_SECONDS)
        self.off_seconds.valueChanged.connect(backend.set_off_belt_seconds)
        self.off_row = _row("Pause after", "", _unit_box(self.off_seconds, "seconds"), indent=True)
        group("Stepping off", _row(
            "Pause the belt if I'm off it",
            "Uses the treadmill's own sensor. It starts watching once you've stepped on, "
            "so you can start the belt first.",
            self.off_belt,
        ), self.off_row)

        self.rest_limit = Switch()
        self.rest_limit.toggled.connect(lambda on: backend.set_rest(enabled=on))
        self.rest_after = _spin(MIN_REST_AFTER, MAX_REST_AFTER)
        self.rest_after.valueChanged.connect(lambda n: backend.set_rest(after_minutes=n))
        self.rest_for = _spin(MIN_REST, MAX_REST)
        self.rest_for.valueChanged.connect(lambda n: backend.set_rest(rest_minutes=n))
        self.rest_rows = [
            _row("It can run for", "", _unit_box(self.rest_after, "minutes"), indent=True),
            _row("Then it must rest for", "", _unit_box(self.rest_for, "minutes"), indent=True),
        ]
        meter = QWidget()
        meter_layout = QVBoxLayout(meter)
        meter_layout.setContentsMargins(16, 12, 16, 14)
        meter_layout.setSpacing(8)
        numbers = QHBoxLayout()
        self.rest_status = label("", muted=True)
        self.rest_time = QLabel()
        self.rest_time.setStyleSheet("font-weight: 600;")
        numbers.addWidget(self.rest_status, 1)
        numbers.addWidget(self.rest_time)
        meter_layout.addLayout(numbers)
        self.rest_bar = QProgressBar()
        self.rest_bar.setRange(0, 1000)
        self.rest_bar.setTextVisible(False)
        meter_layout.addWidget(self.rest_bar)
        group("Rest", _row(
            "Make the treadmill rest",
            "The manual asks for a break after a long run. The belt pauses at the limit and the app "
            "won't start it again until the rest is over. Quitting the app resets the count.",
            self.rest_limit,
        ), *self.rest_rows, meter)

        self.autostart = Switch()
        self.autostart.setChecked(self._autostart_enabled())
        self.autostart.toggled.connect(self._set_autostart)
        self.choose_button = make_button("Choose treadmill…")
        self.choose_button.clicked.connect(self.choose_requested)
        self.details_button = make_button("Details", kind="link")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.addWidget(self.details_button)
        buttons_layout.addWidget(self.choose_button)
        treadmill_row = _row("Treadmill", "None chosen yet", buttons)
        self.treadmill_hint = treadmill_row.findChildren(QLabel)[1]
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels(["Field", "Value"])
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.details.verticalHeader().hide()
        self.details.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.details.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.details.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.details.setMinimumHeight(260)
        self.details.hide()
        group("General", _row("Start with Windows", "Open in the tray when you sign in.", self.autostart),
              treadmill_row)
        layout.addWidget(self.details)
        layout.addStretch(1)
        self.view: Optional[View] = None

    # --- Start with Windows ---------------------------------------------------------------

    @staticmethod
    def _autostart_enabled() -> bool:
        try:
            return autostart.is_enabled()
        except Exception:
            log.exception("Couldn't read the autostart setting")
            return False

    def _set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except Exception as e:
            log.exception("Couldn't change the autostart setting")
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not enabled)
            self.autostart.blockSignals(False)
            self.message.emit(f"Couldn't change the startup setting: {e}")

    def _toggle_details(self, shown: bool) -> None:
        self.details_button.setText("Hide details" if shown else "Details")
        self.details.setVisible(shown)
        if shown and self.view is not None:
            self._fill_details(self.view)

    def _fill_details(self, view: View) -> None:
        rows = detail_rows(view)
        self.details.setRowCount(len(rows))
        for r, (field, value) in enumerate(rows):
            self.details.setItem(r, 0, QTableWidgetItem(field))
            self.details.setItem(r, 1, QTableWidgetItem(value))

    # --- Showing the saved values ---------------------------------------------------------------

    @staticmethod
    def _sync(widget, value) -> None:
        """Shows a saved value without sending it back."""
        is_switch = isinstance(widget, QAbstractButton)
        if is_switch and widget.isChecked() == value:
            return
        if not is_switch and (widget.hasFocus() or widget.value() == value):
            return  # Don't fight someone who is typing
        widget.blockSignals(True)
        (widget.setChecked if is_switch else widget.setValue)(value)
        widget.blockSignals(False)

    def update_view(self, view: View) -> None:
        self.view = view
        self._sync(self.auto_incline, view.auto_incline)
        self._sync(self.off_belt, view.off_belt_pause)
        self._sync(self.off_seconds, round(view.off_belt_seconds))
        self.off_row.setEnabled(view.off_belt_pause)
        self._sync(self.rest_limit, view.rest_limit)
        self._sync(self.rest_after, round(view.rest_after_seconds / 60))
        self._sync(self.rest_for, round(view.rest_seconds / 60))
        for row in self.rest_rows:
            row.setEnabled(view.rest_limit)
        if view.rest_left is not None:
            set_text(self.rest_status, "Cooling down")
            set_text(self.rest_time, f"{fmt_duration(view.rest_left)} left")
            self.rest_bar.setValue(round((1 - view.rest_left / view.rest_seconds) * 1000))
        else:
            set_text(self.rest_status, "Run time since its last rest")
            set_text(self.rest_time, f"{fmt_duration(view.run_seconds)} of {fmt_duration(view.rest_after_seconds)}")
            self.rest_bar.setValue(round(min(1.0, view.run_seconds / view.rest_after_seconds) * 1000))
        set_text(self.treadmill_hint, view.address or "None chosen yet")
        if self.details.isVisible():
            self._fill_details(view)
