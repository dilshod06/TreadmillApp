"""The main window: live readout and the same controls as the terminal UI."""
from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QPushButton,
    QSizePolicy, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..controller import (
    CONNECTED, CONNECTING, FAILED, RECONNECTING, STATE_LABELS, View, detail_rows, fmt_duration,
)
from ..plans import DISTANCE, fmt_amount
from ..protocol import State
from .backend import Backend
from .countdown import CountdownPage
from .icons import COLORS, make_icon, make_view_icon
from .settings import SettingsPage

STATE_COLORS = {
    State.RUNNING: COLORS["running"],
    State.STARTING: COLORS["starting"],
    State.PAUSED: COLORS["paused"],
    State.STOPPED: COLORS["stopped"],
}
CONNECTION_DOT = {
    CONNECTED: COLORS["running"],
    CONNECTING: COLORS["connecting"],
    RECONNECTING: COLORS["connecting"],
    FAILED: "#dc2626",
}

STYLE = """
QPushButton[preset="true"] { padding: 8px 4px; }
QPushButton[active="true"] { background: #2e9d4f; color: white; font-weight: 700; border: 1px solid #2e9d4f; }
QPushButton[tooFast="true"] { color: palette(mid); }
QPushButton[big="true"] { font-size: 16px; padding: 10px 18px; }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }
"""


class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.caption = QLabel(title)
        self.caption.setStyleSheet("color: palette(mid);")
        self.value = QLabel("--")
        self.value.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)


def _button(text: str, big: bool = False, preset: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Keeps Space and the arrows for the shortcuts
    if big:
        button.setProperty("big", True)
    if preset:
        button.setProperty("preset", True)
    return button


def _set_flag(widget: QWidget, name: str, on: bool) -> None:
    if bool(widget.property(name)) != on:
        widget.setProperty(name, on)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class MainWindow(QMainWindow):
    quit_requested = Signal()
    choose_requested = Signal()

    def __init__(self, backend: Backend, view: View):
        super().__init__()
        self.backend = backend
        self.view = view
        self.had_run = False
        self.hide_on_close = True  # Closing goes to the tray, unless there isn't one
        self.setWindowTitle("PitPat")
        self.setWindowIcon(make_icon("offline", "PP"))
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(12)

        top = QHBoxLayout()
        self.connection = QLabel()
        self.connection.setTextFormat(Qt.TextFormat.RichText)
        self.choose_button = QPushButton("Choose treadmill…")
        self.choose_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choose_button.clicked.connect(self.choose_requested)
        top.addWidget(self.connection, 1)
        top.addWidget(self.choose_button)
        root.addLayout(top)

        self.modes = QTabBar()
        self.modes.addTab("Normal")
        self.modes.addTab("Countdown")
        self.modes.addTab("Settings")
        self.modes.setExpanding(True)
        self.modes.setDrawBase(False)
        self.modes.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.modes.setToolTip("Normal: you set the speed. Countdown: follow a plan. The tabs only change what "
                              "you see; a plan carries on until it ends or you end it.")
        self.modes.currentChanged.connect(self._mode_changed)
        root.addWidget(self.modes)

        self.state = QLabel()
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.state)

        self.speed = QLabel("--")
        self.speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed.setStyleSheet("font-size: 84px; font-weight: 700;")
        root.addWidget(self.speed)
        self.target = QLabel()
        self.target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.target.setStyleSheet("color: palette(mid);")
        root.addWidget(self.target)

        stats = QHBoxLayout()
        self.cards = {}
        for key, title in (("time", "Time"), ("distance", "Distance"), ("steps", "Steps"), ("calories", "Calories")):
            self.cards[key] = StatCard(title)
            stats.addWidget(self.cards[key])
        root.addLayout(stats)

        controls = QHBoxLayout()
        self.slower = _button("−", big=True)
        self.toggle = _button("Start", big=True)
        self.faster = _button("+", big=True)
        self.stop = _button("Stop", big=True)
        self.slower.clicked.connect(lambda: backend.nudge(-0.1))
        self.faster.clicked.connect(lambda: backend.nudge(0.1))
        self.toggle.clicked.connect(backend.toggle)
        self.stop.clicked.connect(backend.stop_belt)
        self.toggle.setMinimumWidth(120)
        for w in (self.slower, self.toggle, self.faster, self.stop):
            controls.addWidget(w)
        root.addLayout(controls)

        incline = QHBoxLayout()
        self.incline_down = _button("−")
        self.incline_up = _button("+")
        self.incline_down.clicked.connect(lambda: backend.nudge_incline(-1))
        self.incline_up.clicked.connect(lambda: backend.nudge_incline(1))
        self.incline_label = QLabel("Incline --")
        self.incline_label.setMinimumWidth(150)
        self.incline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.presence = QLabel()  # Live status; the settings that drive it are on the Settings tab
        self.presence.setStyleSheet("color: palette(mid);")
        self.rest = QLabel()
        self.rest.setStyleSheet("color: palette(mid);")
        incline.addWidget(self.presence)
        incline.addStretch(1)
        incline.addWidget(self.rest)
        incline.addSpacing(12)
        for w in (self.incline_down, self.incline_label, self.incline_up):
            incline.addWidget(w)
        root.addLayout(incline)

        self.pages = QStackedWidget()
        normal_page = QWidget()
        normal = QVBoxLayout(normal_page)
        normal.setContentsMargins(0, 0, 0, 0)
        normal.setSpacing(12)

        # While a plan runs, Normal still shows it, and a click goes back to its page.
        self.plan_banner = QPushButton()
        self.plan_banner.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plan_banner.setProperty("active", True)
        self.plan_banner.clicked.connect(lambda: self.modes.setCurrentIndex(1))
        self.plan_banner.hide()
        normal.addWidget(self.plan_banner)

        exact = QHBoxLayout()
        exact.addStretch(1)
        exact.addWidget(QLabel("Exact speed"))
        self.exact = QDoubleSpinBox()
        self.exact.setDecimals(1)
        self.exact.setSingleStep(0.1)
        self.exact.setRange(0.1, 30.0)
        self.exact.setValue(2.5)
        self.go = QPushButton("Go")
        self.go.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.go.clicked.connect(self._go)
        self.exact.lineEdit().returnPressed.connect(self._go)
        exact.addWidget(self.exact)
        exact.addWidget(self.go)
        exact.addStretch(1)
        normal.addLayout(exact)

        normal.addWidget(QLabel("Presets  (right-click one to save the current speed there)"))
        grid = QGridLayout()
        self.presets = []
        for i in range(9):
            button = _button("", preset=True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _=False, n=i + 1: backend.go_preset(n))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, n=i + 1, b=button: self._preset_menu(n, b, pos))
            grid.addWidget(button, i // 3, i % 3)
            self.presets.append(button)
        normal.addLayout(grid)
        normal.addStretch(1)

        self.countdown = CountdownPage(backend)
        self.settings = SettingsPage(backend)
        self.pages.addWidget(normal_page)
        self.pages.addWidget(self.countdown)
        self.pages.addWidget(self.settings)
        root.addWidget(self.pages)

        self.details_toggle = QToolButton()
        self.details_toggle.setText("Details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.details_toggle.toggled.connect(self._toggle_details)
        root.addWidget(self.details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels(["Field", "Value"])
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.details.verticalHeader().hide()
        self.details.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.details.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.details.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.details.setMinimumHeight(260)
        self.details.hide()
        root.addWidget(self.details)

        for key, handler in (
            (Qt.Key.Key_Space, backend.toggle),
            (Qt.Key.Key_Up, lambda: backend.nudge(0.1)),
            (Qt.Key.Key_Plus, lambda: backend.nudge(0.1)),
            (Qt.Key.Key_Down, lambda: backend.nudge(-0.1)),
            (Qt.Key.Key_Minus, lambda: backend.nudge(-0.1)),
            *((getattr(Qt.Key, f"Key_{n}"), lambda n=n: backend.go_preset(n)) for n in range(1, 10)),
        ):
            QShortcut(QKeySequence(key), self, activated=handler)

        self.update_view(view)

    # --- User actions -----------------------------------------------------------

    def _mode_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)  # Only the view changes; a plan in progress carries on

    def show_settings(self) -> None:
        self.modes.setCurrentIndex(2)

    def _go(self) -> None:
        self.backend.set_speed(self.exact.value())

    def _preset_menu(self, n: int, button: QPushButton, pos: QPoint) -> None:
        speed = self.view.current_speed
        menu = QMenu(self)
        action = menu.addAction(
            f"Save current speed ({speed:.1f}) as preset {n}" if speed else "Start the belt to save its speed here"
        )
        action.setEnabled(speed is not None)
        if menu.exec(button.mapToGlobal(pos)) is action:
            self.backend.save_preset(n)

    def _toggle_details(self, shown: bool) -> None:
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.details.setVisible(shown)
        if shown:
            self.update_view(self.view)
        else:
            self.adjustSize()

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)

    # --- Rendering --------------------------------------------------------------

    def update_view(self, view: View) -> None:
        self.view = view
        s = view.status
        self.setWindowIcon(make_view_icon(view))

        dot = CONNECTION_DOT.get(view.conn_state, "#6b7280")
        text = {
            CONNECTING: f"Connecting to {view.address}…",
            RECONNECTING: f"Reconnecting to {view.address}…",
            CONNECTED: f"Connected to {view.address}",
            FAILED: f"Couldn't connect to {view.address}",
        }.get(view.conn_state, "Not connected")
        self.connection.setText(f'<span style="color:{dot}">●</span> {text}')

        if s is None:
            idle = view.conn_state in ("idle", FAILED)
            self.state.setText("NOT CONNECTED" if idle else "WAITING FOR TREADMILL")
            color = COLORS["offline"]
            self.speed.setText("--")
            self.target.setText("Choose a treadmill to get started" if idle else "")
            for card in self.cards.values():
                card.value.setText("--")
            self.toggle.setText("Start")
        else:
            self.state.setText(STATE_LABELS[s.state])
            color = STATE_COLORS[s.state]
            self.speed.setText(f"{s.to_display(s.speed):.1f}")
            line = f"{s.speed_unit}  ·  target {s.to_display(s.target_speed):.1f}  ·  max {s.to_display(s.max_speed):.1f}"
            if isinstance(view.goal, float):
                line += f"  ·  → {view.goal:.1f}"
            elif view.goal == "stop":
                line += "  ·  stopping…"
            self.target.setText(line)
            self.cards["time"].value.setText(fmt_duration(s.duration_s))
            self.cards["distance"].value.setText(f"{s.distance:.2f} {s.distance_unit}")
            self.cards["steps"].value.setText("--" if s.steps is None else f"{s.steps:,}")
            self.cards["calories"].value.setText(f"{s.calories} kcal")
            self.toggle.setText("Pause" if s.state in (State.RUNNING, State.STARTING) else "Start")
            max_speed = s.to_display(s.max_speed) if s.max_speed else 30.0
            self.exact.setMaximum(max(0.1, round(max_speed, 1)))
            self.exact.setSuffix(f" {s.speed_unit}")
        self.state.setStyleSheet(f"color: {color}; font-weight: 700; letter-spacing: 2px;")

        connected = s is not None
        for w in (self.slower, self.toggle, self.faster, self.stop, self.go, self.exact, *self.presets):
            w.setEnabled(connected)

        goal = view.goal if isinstance(view.goal, float) else None
        max_speed = s.to_display(s.max_speed) if s and s.max_speed else float("inf")
        for n, (button, speed) in enumerate(zip(self.presets, view.presets), 1):
            button.setText(f"{n}   ·   {speed:.1f}")
            _set_flag(button, "active", connected and speed == (goal if goal is not None else view.current_speed))
            _set_flag(button, "tooFast", speed > max_speed + 0.05)

        self._update_cards(view)
        self._update_incline(view)
        self._update_presence(view)
        self._update_rest(view)
        self._update_plan_banner(view)
        self.countdown.update_view(view)
        self.settings.update_view(view)
        if view.run is not None and not self.had_run and self.modes.currentIndex() != 1:
            self.modes.blockSignals(True)  # A plan just started, maybe from the tray: show it
            self.modes.setCurrentIndex(1)
            self.modes.blockSignals(False)
            self.pages.setCurrentIndex(1)
        self.had_run = view.run is not None

        if self.details_toggle.isChecked():
            rows = detail_rows(view)
            self.details.setRowCount(len(rows))
            for r, (field, value) in enumerate(rows):
                self.details.setItem(r, 0, QTableWidgetItem(field))
                self.details.setItem(r, 1, QTableWidgetItem(value))

    def _update_cards(self, view: View) -> None:
        """Time and distance count up, except in a plan, where the one it is measured in counts down to 0."""
        run, s = view.run, view.status
        for key, title, measure in (("time", "Time", "time"), ("distance", "Distance", DISTANCE)):
            card = self.cards[key]
            counting_down = run is not None and run.plan.measure == measure and s is not None
            card.caption.setText(f"{title} left" if counting_down else title)
            if counting_down:
                card.value.setText(fmt_amount(run.plan, run.remaining))

    def _update_plan_banner(self, view: View) -> None:
        run = view.run
        self.plan_banner.setVisible(run is not None)
        self.modes.setTabText(1, "Countdown" if run is None else f"Countdown  ·  {round(run.fraction * 100)}%")
        if run is not None:
            self.plan_banner.setText(
                f"▶  {run.plan.name} is running: {fmt_amount(run.plan, run.remaining)} left. Tap to see it."
            )

    def _update_rest(self, view: View) -> None:
        if view.rest_left is not None:
            self.rest.setText(f"Cooling down · {fmt_duration(view.rest_left)}")
        elif view.rest_limit and view.status is not None:
            self.rest.setText(f"Rest in {fmt_duration(max(0, view.rest_after_seconds - view.run_seconds))}")
        else:
            self.rest.setText("")

    def _update_incline(self, view: View) -> None:
        s = view.status
        if s is None:
            text = "Incline --"
        else:
            text = f"Incline {s.incline} of {s.max_incline}"
            if view.incline_goal is not None:
                text += f"  → {view.incline_goal}"
        self.incline_label.setText(text)
        steerable = s is not None and view.auto_incline and s.max_incline > 0 and s.state == State.RUNNING
        self.incline_down.setEnabled(steerable)
        self.incline_up.setEnabled(steerable)

    def _update_presence(self, view: View) -> None:
        s = view.status
        if s is None or s.state != State.RUNNING:
            text = ""
        elif s.on_belt is None and view.on_belt is None:
            text = "This treadmill doesn't report it"
        elif view.on_belt is False:
            left = None if view.off_belt_for is None else view.off_belt_seconds - view.off_belt_for
            text = "Off the belt"
            if view.off_belt_pause and left is not None and left > 0:
                text += f" · pausing in {left:.0f} s"
        else:
            text = "On the belt" if view.on_belt else ""
        self.presence.setText(text)

    # --- Window -----------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hide_on_close:
            event.ignore()
            self.hide()
        else:
            event.accept()
            self.quit_requested.emit()

    def raise_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
