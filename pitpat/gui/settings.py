"""The Settings page: everything the app can be told to do on its own, in one place."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from ..controller import (
    MAX_OFF_BELT_SECONDS, MAX_REST, MAX_REST_AFTER, MIN_OFF_BELT_SECONDS, MIN_REST, MIN_REST_AFTER, View, fmt_duration,
)
from .backend import Backend


def _check(text: str, tip: str) -> QCheckBox:
    box = QCheckBox(text)
    box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    box.setToolTip(tip)
    return box


def _spin(low: float, high: float, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(int(low), int(high))
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)  # Save when they finish typing, not on each digit
    return spin


class SettingsPage(QWidget):
    def __init__(self, backend: Backend):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        incline = QGroupBox("Incline")
        box = QVBoxLayout(incline)
        self.auto_incline = _check(
            "Let the app set the incline",
            "Off: the app never changes the incline, and a plan only reminds you.\n"
            "On: plans set it at each point, and the incline buttons work.",
        )
        self.auto_incline.toggled.connect(backend.set_auto_incline)
        box.addWidget(self.auto_incline)
        layout.addWidget(incline)

        away = QGroupBox("Stepping off")
        box = QVBoxLayout(away)
        row = QHBoxLayout()
        self.off_belt = _check(
            "Pause the belt if I'm off it for",
            "Uses the treadmill's own sensor. It only counts once you've been on the belt since it started,\n"
            "so you can start the belt and then step on.",
        )
        self.off_belt.toggled.connect(backend.set_off_belt_pause)
        self.off_seconds = _spin(MIN_OFF_BELT_SECONDS, MAX_OFF_BELT_SECONDS, " seconds")
        self.off_seconds.valueChanged.connect(backend.set_off_belt_seconds)
        row.addWidget(self.off_belt)
        row.addWidget(self.off_seconds)
        row.addStretch(1)
        box.addLayout(row)
        layout.addWidget(away)

        rest = QGroupBox("Rest")
        box = QVBoxLayout(rest)
        self.rest_limit = _check(
            "Make the treadmill rest",
            "The manual asks for a rest after a long run. The belt is paused at the limit, and the app\n"
            "won't start it again until the rest is over. A shorter pause doesn't count as a rest.\n"
            "The count starts from zero each time the app starts.",
        )
        self.rest_limit.toggled.connect(lambda on: backend.set_rest(enabled=on))
        self.rest_after = _spin(MIN_REST_AFTER, MAX_REST_AFTER, " minutes")
        self.rest_after.valueChanged.connect(lambda n: backend.set_rest(after_minutes=n))
        self.rest_for = _spin(MIN_REST, MAX_REST, " minutes")
        self.rest_for.valueChanged.connect(lambda n: backend.set_rest(rest_minutes=n))
        box.addWidget(self.rest_limit)
        form = QFormLayout()
        form.addRow("It can run for", self.rest_after)
        form.addRow("Then it must rest for", self.rest_for)
        box.addLayout(form)
        self.rest_status = QLabel()
        self.rest_status.setStyleSheet("color: palette(mid);")
        box.addWidget(self.rest_status)
        layout.addWidget(rest)
        layout.addStretch(1)

    @staticmethod
    def _sync(widget, value) -> None:
        """Shows a saved value without sending it back."""
        box = isinstance(widget, QCheckBox)
        if box and widget.isChecked() == value:
            return
        if not box and (widget.hasFocus() or widget.value() == value):
            return  # Don't fight someone who is typing
        widget.blockSignals(True)
        (widget.setChecked if box else widget.setValue)(value)
        widget.blockSignals(False)

    def update_view(self, view: View) -> None:
        self._sync(self.auto_incline, view.auto_incline)
        self._sync(self.off_belt, view.off_belt_pause)
        self._sync(self.off_seconds, round(view.off_belt_seconds))
        self.off_seconds.setEnabled(view.off_belt_pause)
        self._sync(self.rest_limit, view.rest_limit)
        self._sync(self.rest_after, round(view.rest_after_seconds / 60))
        self._sync(self.rest_for, round(view.rest_seconds / 60))
        self.rest_after.setEnabled(view.rest_limit)
        self.rest_for.setEnabled(view.rest_limit)
        if view.rest_left is not None:
            self.rest_status.setText(f"Cooling down: {fmt_duration(view.rest_left)} left")
        else:
            self.rest_status.setText(
                f"Run time since its last rest: {fmt_duration(view.run_seconds)} "
                f"of {fmt_duration(view.rest_after_seconds)}"
            )
