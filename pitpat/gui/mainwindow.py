"""The main window: a top bar (connection, Normal / Countdown, settings), warnings
that stay put on every page, the page itself, and a "now running" bar."""
import math
from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QPixmap, QPainter, QColor, QIcon, QShortcut
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMenu, QProgressBar, QPushButton,
    QScrollArea, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from ..controller import (
    CONNECTED, CONNECTING, FAILED, RECONNECTING, STATE_LABELS, View, fmt_duration,
)
from ..plans import DISTANCE, fmt_amount
from ..protocol import State
from . import theme
from .backend import Backend
from .countdown import CountdownPage
from .history_page import HistoryPage
from .icons import COLORS, make_icon, make_view_icon
from .settings import SettingsPage
from .widgets import ProfileChart, Switch, Toasts, card, label, make_button, round_button, set_prop, set_text

CONNECTION_DOT = {
    CONNECTED: COLORS["running"],
    CONNECTING: COLORS["connecting"],
    RECONNECTING: COLORS["connecting"],
    FAILED: "#dc2626",
}
NORMAL, COUNTDOWN, HISTORY, SETTINGS = 0, 1, 2, 3


def _dot(color: str) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(3, 3, 8, 8)
    painter.end()
    return QIcon(pixmap)


class Stat(QFrame):
    """One figure in the strip under the controls: a caption and a value."""

    def __init__(self, title: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(0)
        self.caption = label(title, muted=True)
        self.caption.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.value = QLabel("--")
        self.value.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)


def _banner(tone: str, heading: str, detail: str) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("banner")
    frame.setProperty("tone", tone)
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(12)
    words = QVBoxLayout()
    words.setSpacing(0)
    top = QLabel(heading)
    top.setStyleSheet("font-weight: 700;")
    sub = QLabel(detail)
    sub.setWordWrap(True)
    words.addWidget(top)
    words.addWidget(sub)
    big = QLabel()
    big.setStyleSheet("font-size: 20px; font-weight: 700;")
    row.addLayout(words, 1)
    row.addWidget(big)
    frame.hide()
    return frame, big


def _scrolled(widget: QWidget) -> QScrollArea:
    """`widget` on a scrolling page with a comfortable margin, so a short window still reaches everything."""
    holder = QWidget()
    holder.setObjectName("page")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(widget)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(holder)
    return area


class MainWindow(QMainWindow):
    quit_requested = Signal()
    choose_requested = Signal()

    def __init__(self, backend: Backend, view: View):
        super().__init__()
        self.backend = backend
        self.view = view
        self.had_run = False
        self.last_mode = NORMAL
        self.hide_on_close = True  # Closing goes to the tray, unless there isn't one
        self.setWindowTitle("PitPat")
        self.setWindowIcon(make_icon("offline", "PP"))
        self.setMinimumSize(600, 560)
        self.resize(740, 840)

        central = QWidget()
        central.setObjectName("page")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Top bar: connection, mode switch, settings ---
        bar = QFrame()
        bar.setObjectName("topbar")
        grid = QGridLayout(bar)
        grid.setContentsMargins(16, 10, 16, 10)
        self.connection = QPushButton()
        self.connection.setObjectName("conn")
        self.connection.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.connection.setToolTip("Choose another treadmill")
        self.connection.clicked.connect(self.choose_requested)
        seg = QFrame()
        seg.setObjectName("seg")
        seg_layout = QHBoxLayout(seg)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        self.modes = QTabBar()
        self.modes.addTab("Normal")
        self.modes.addTab("Countdown")
        self.modes.addTab("History")
        self.modes.addTab("Settings")
        self.modes.setTabVisible(SETTINGS, False)  # Reached from the gear; it only keeps the index
        self.modes.setExpanding(False)
        self.modes.setDrawBase(False)
        self.modes.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.modes.setToolTip("Normal: you set the speed. Countdown: follow a plan. The tabs only change what "
                              "you see; a plan carries on until it ends or you end it.")
        self.modes.currentChanged.connect(self._mode_changed)
        seg_layout.addWidget(self.modes)
        self.gear = QToolButton()
        self.gear.setObjectName("gear")
        self.gear.setText("⚙")
        self.gear.setToolTip("Settings")
        self.gear.setCheckable(True)
        self.gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gear.toggled.connect(lambda on: self.modes.setCurrentIndex(SETTINGS if on else self.last_mode))
        grid.addWidget(self.connection, 0, 0, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(seg, 0, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.gear, 0, 2, Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        root.addWidget(bar)

        # --- Warnings that stay put whatever page is showing ---
        alerts = QWidget()
        alerts.setObjectName("page")
        alerts_layout = QVBoxLayout(alerts)
        alerts_layout.setContentsMargins(16, 12, 16, 0)
        alerts_layout.setSpacing(8)
        self.cool_banner, self.cool_time = _banner(
            "cool", "The treadmill is cooling down", "Starting is locked until the rest is over.")
        self.away_banner, self.away_time = _banner(
            "away", "You're off the belt", "Pausing if you don't step back on.")
        alerts_layout.addWidget(self.cool_banner)
        alerts_layout.addWidget(self.away_banner)
        root.addWidget(alerts)

        # --- Pages ---
        self.pages = QStackedWidget()
        self.countdown = CountdownPage(backend)
        self.history = HistoryPage()
        self.settings = SettingsPage(backend)
        self.settings.choose_requested.connect(self.choose_requested)
        self.settings.message.connect(self.show_message)
        self.pages.addWidget(_scrolled(self._build_normal()))
        self.pages.addWidget(_scrolled(self.countdown))
        self.pages.addWidget(_scrolled(self.history))
        self.pages.addWidget(_scrolled(self.settings))
        root.addWidget(self.pages, 1)

        # --- Now running ---
        self.mini = QFrame()
        self.mini.setObjectName("mini")
        mini_layout = QVBoxLayout(self.mini)
        mini_layout.setContentsMargins(0, 0, 0, 0)
        mini_layout.setSpacing(0)
        self.mini_bar = QProgressBar()
        self.mini_bar.setRange(0, 1000)
        self.mini_bar.setTextVisible(False)
        self.mini_bar.setStyleSheet(
            "QProgressBar { min-height: 3px; max-height: 3px; border-radius: 0; }"
            "QProgressBar::chunk { border-radius: 0; }"
        )
        mini_layout.addWidget(self.mini_bar)
        row = QHBoxLayout()
        row.setContentsMargins(16, 8, 16, 10)
        row.setSpacing(12)
        self.mini_dot = QLabel("●")
        self.mini_dot.setObjectName("stateDot")
        self.mini_open = QPushButton()
        self.mini_open.setObjectName("miniText")
        self.mini_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mini_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mini_open.clicked.connect(self._open_running)
        self.mini_toggle = make_button("Pause", kind="hold")
        self.mini_toggle.clicked.connect(backend.toggle)
        row.addWidget(self.mini_dot)
        row.addWidget(self.mini_open, 1)
        row.addWidget(self.mini_toggle)
        mini_layout.addLayout(row)
        self.mini.hide()
        root.addWidget(self.mini)

        self.toasts = Toasts(central)

        for key, handler in (
            (Qt.Key.Key_Space, backend.toggle),
            (Qt.Key.Key_Up, lambda: backend.nudge(0.1)),
            (Qt.Key.Key_Plus, lambda: backend.nudge(0.1)),
            (Qt.Key.Key_Down, lambda: backend.nudge(-0.1)),
            (Qt.Key.Key_Minus, lambda: backend.nudge(-0.1)),
            *((getattr(Qt.Key, f"Key_{n}"), lambda n=n: backend.go_preset(n)) for n in range(1, 10)),
        ):
            QShortcut(QKeySequence(key), self, activated=handler)

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.apply_theme())
        self.apply_theme()
        self.update_view(view)

    # --- Normal page ------------------------------------------------------------

    def _build_normal(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        # A plan in progress stays one click away from here.
        self.plan_banner = QPushButton()
        self.plan_banner.setObjectName("planstrip")
        self.plan_banner.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plan_banner.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plan_banner.clicked.connect(lambda: self.modes.setCurrentIndex(COUNTDOWN))
        self.plan_banner.hide()
        grid.addWidget(self.plan_banner, 0, 0, 1, 2)

        hero = card()
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 14, 18, 16)
        hero_layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.state_dot = QLabel("●")
        self.state_dot.setObjectName("stateDot")
        self.state = QLabel()
        self.state.setObjectName("state")
        self.presence = QLabel()
        self.presence.setObjectName("chip")
        self.rest = QLabel()
        self.rest.setObjectName("chip")
        top.addWidget(self.state_dot)
        top.addWidget(self.state)
        top.addStretch(1)
        top.addWidget(self.presence)
        top.addWidget(self.rest)
        hero_layout.addLayout(top)

        speed_row = QHBoxLayout()
        self.slower = round_button("−")
        self.faster = round_button("+")
        self.slower.setToolTip("Slower (Down)")
        self.faster.setToolTip("Faster (Up)")
        self.slower.clicked.connect(lambda: self.backend.nudge(-0.1))
        self.faster.clicked.connect(lambda: self.backend.nudge(0.1))
        readout = QVBoxLayout()
        readout.setSpacing(0)
        self.speed = QLabel("--")
        self.speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed.setStyleSheet("font-size: 80px; font-weight: 700;")
        self.unit = label("", muted=True)
        self.unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit.setStyleSheet("font-weight: 600;")
        readout.addWidget(self.speed)
        readout.addWidget(self.unit)
        speed_row.addWidget(self.slower)
        speed_row.addLayout(readout, 1)
        speed_row.addWidget(self.faster)
        hero_layout.addLayout(speed_row)
        self.target = label("", muted=True)
        self.target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.target)

        presets = QGridLayout()
        presets.setHorizontalSpacing(6)
        presets.setVerticalSpacing(6)
        self.presets: list[QPushButton] = []
        for i in range(9):
            button = make_button("")
            button.setProperty("pill", True)
            button.setToolTip("Right-click to save the current speed here")
            button.clicked.connect(lambda _=False, n=i + 1: self.backend.go_preset(n))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, n=i + 1, b=button: self._preset_menu(n, b, pos))
            presets.addWidget(button, i // 5, i % 5)
            self.presets.append(button)
        hero_layout.addSpacing(8)
        hero_layout.addLayout(presets)

        exact = QHBoxLayout()
        exact.addStretch(1)
        exact.addWidget(label("Exact speed", muted=True))
        self.exact = QDoubleSpinBox()
        self.exact.setDecimals(1)
        self.exact.setSingleStep(0.1)
        self.exact.setRange(0.1, 30.0)
        self.exact.setValue(2.5)
        self.go = make_button("Go")
        self.go.clicked.connect(self._go)
        self.exact.lineEdit().returnPressed.connect(self._go)
        exact.addWidget(self.exact)
        exact.addWidget(self.go)
        exact.addStretch(1)
        hero_layout.addSpacing(4)
        hero_layout.addLayout(exact)
        grid.addWidget(hero, 1, 0)

        side = QVBoxLayout()
        side.setSpacing(14)
        incline = card()
        incline_layout = QVBoxLayout(incline)
        incline_layout.setContentsMargins(16, 12, 16, 12)
        incline_layout.setSpacing(8)
        incline_layout.addWidget(label("INCLINE", caption=True))
        step = QHBoxLayout()
        self.incline_down = round_button("−", small=True)
        self.incline_up = round_button("+", small=True)
        self.incline_down.clicked.connect(lambda: self.backend.nudge_incline(-1))
        self.incline_up.clicked.connect(lambda: self.backend.nudge_incline(1))
        value = QVBoxLayout()
        value.setSpacing(0)
        self.incline_label = QLabel("--")
        self.incline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.incline_label.setStyleSheet("font-size: 34px; font-weight: 700;")
        self.incline_sub = label("", muted=True)
        self.incline_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.addWidget(self.incline_label)
        value.addWidget(self.incline_sub)
        step.addWidget(self.incline_down)
        step.addLayout(value, 1)
        step.addWidget(self.incline_up)
        incline_layout.addLayout(step)
        self.incline_note = label("", muted=True)
        self.incline_note.setWordWrap(True)
        self.incline_note.setStyleSheet("font-size: 12px;")
        self.incline_link = make_button("Turn on in Settings", kind="link")
        self.incline_link.clicked.connect(lambda: self.modes.setCurrentIndex(SETTINGS))
        incline_layout.addWidget(self.incline_note)
        incline_layout.addWidget(self.incline_link, 0, Qt.AlignmentFlag.AlignLeft)
        side.addWidget(incline)

        self.toggle = make_button("Start", kind="go", big=True)
        self.toggle.setToolTip("Start or pause (Space)")
        self.stop = make_button("Stop")
        self.toggle.clicked.connect(self.backend.toggle)
        self.stop.clicked.connect(self.backend.stop_belt)
        side.addWidget(self.toggle)
        side.addWidget(self.stop)
        hint = QLabel("Space starts or pauses · ↑ ↓ change the speed")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(hint)
        side.addStretch(1)
        grid.addLayout(side, 1, 1)

        strip = card()
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 4, 0, 4)
        strip_layout.setSpacing(0)
        self.cards: dict[str, Stat] = {}
        for n, (key, title) in enumerate((("time", "Time"), ("distance", "Distance"), ("floors", "Floors climbed"), ("steps", "Steps"), ("calories", "Calories"))):
            if n:
                line = QFrame()
                line.setObjectName("divider")
                line.setFixedWidth(1)
                strip_layout.addWidget(line)
            self.cards[key] = Stat(title)
            if key == "floors":
                self.cards[key].value.setProperty("tone", "floors")
            strip_layout.addWidget(self.cards[key], 1)
        grid.addWidget(strip, 2, 0, 1, 2)
        grid.setRowStretch(3, 1)
        return page

    # --- Look -------------------------------------------------------------------

    def apply_theme(self) -> None:
        self.setStyleSheet(theme.use(theme.is_dark()))
        for kind in (ProfileChart, Switch):
            for widget in self.findChildren(kind):
                widget.update()

    # --- User actions -----------------------------------------------------------

    def _mode_changed(self, index: int) -> None:
        """Only the view changes; a plan in progress carries on."""
        self.pages.setCurrentIndex(index)
        if index != SETTINGS:
            self.last_mode = index
        if self.gear.isChecked() != (index == SETTINGS):
            self.gear.blockSignals(True)
            self.gear.setChecked(index == SETTINGS)
            self.gear.blockSignals(False)
        self._update_mini(self.view)  # The bar shows on every page but the one that already shows the run

    def show_settings(self) -> None:
        self.modes.setCurrentIndex(SETTINGS)

    def _open_running(self) -> None:
        self.modes.setCurrentIndex(COUNTDOWN if self.view.run is not None else NORMAL)

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

    def show_message(self, text: str) -> None:
        self.toasts.show_message(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.toasts.place()

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
        self.connection.setIcon(_dot(dot))
        set_text(self.connection, text)

        run = view.run
        if s is None:
            idle = view.conn_state in ("idle", FAILED)
            set_text(self.state, "NOT CONNECTED" if idle else "WAITING FOR TREADMILL")
            kind = ""
            set_text(self.speed, "--")
            set_text(self.unit, "")
            set_text(self.target, "Choose a treadmill to get started" if idle else "")
            for card_ in self.cards.values():
                card_.value.setText("--")
            set_text(self.toggle, "Start")
        else:
            set_text(self.state, STATE_LABELS[s.state])
            kind = {State.RUNNING: "running", State.STARTING: "paused", State.PAUSED: "paused"}.get(s.state, "")
            set_text(self.speed, f"{s.to_display(s.speed):.1f}")
            set_text(self.unit, s.speed_unit)
            if s.state == State.RUNNING:
                line = f"Target {s.to_display(s.target_speed):.1f}  ·  top speed {s.to_display(s.max_speed):.1f}"
                if isinstance(view.goal, float):
                    line += f"  ·  moving to {view.goal:.1f}"
                elif view.goal == "stop":
                    line += "  ·  stopping…"
            elif s.state == State.PAUSED:
                line = "Paused. Press Resume to carry on."
            else:
                line = "Pick a speed below or press Start"
            set_text(self.target, line)
            set_text(self.cards["time"].value, fmt_duration(s.duration_s))
            set_text(self.cards["distance"].value, f"{s.distance:.2f} {s.distance_unit}")
            set_text(self.cards["floors"].value, f"{view.floors:.1f}")
            set_text(self.cards["steps"].value, "--" if s.steps is None else f"{s.steps:,}")
            set_text(self.cards["calories"].value, f"{s.calories} kcal")
            moving = s.state in (State.RUNNING, State.STARTING)
            set_text(self.toggle, "Pause" if moving else "Resume" if s.state == State.PAUSED else "Start")
            max_speed = s.to_display(s.max_speed) if s.max_speed else 30.0
            self.exact.setMaximum(max(0.1, round(max_speed, 1)))
            self.exact.setSuffix(f" {s.speed_unit}")
        set_prop(self.state, "kind", kind)
        set_prop(self.state_dot, "kind", kind)
        self._update_cards(view)

        connected = s is not None
        running = connected and s.state == State.RUNNING
        moving = connected and s.state in (State.RUNNING, State.STARTING)
        set_prop(self.toggle, "kind", "hold" if moving else "go")
        cooling = view.rest_left is not None
        self.toggle.setEnabled(connected and (moving or not cooling))
        for w in (self.stop, self.go, self.exact, *self.presets):
            w.setEnabled(connected)
        for w in (self.slower, self.faster):
            w.setEnabled(running)
        for button in self.presets:
            button.setEnabled(connected and not (cooling and not moving))

        goal = view.goal if isinstance(view.goal, float) else None
        max_speed = s.to_display(s.max_speed) if s and s.max_speed else float("inf")
        for n, (button, speed) in enumerate(zip(self.presets, view.presets), 1):
            set_text(button, f"{speed:.1f}")
            set_prop(button, "active", bool(connected and speed == (goal if goal is not None else view.current_speed)))
            button.setToolTip("Right-click to save the current speed here" if speed <= max_speed + 0.05
                              else "Faster than this treadmill goes")

        self._update_incline(view)
        self._update_presence(view)
        self._update_rest(view)
        self._update_alerts(view)
        self._update_plan(view)
        self.countdown.update_view(view)
        self.history.update_view(view)
        self.settings.update_view(view)
        if run is not None and not self.had_run and self.modes.currentIndex() != COUNTDOWN:
            self.modes.setCurrentIndex(COUNTDOWN)  # A plan just started, maybe from the tray: show it
        self.had_run = run is not None
        self._update_mini(view)

    def _update_cards(self, view: View) -> None:
        """Time and distance count up, except in a plan, where the one it is measured in counts down to 0."""
        run, s = view.run, view.status
        for key, title, measure in (("time", "Time", "time"), ("distance", "Distance", DISTANCE)):
            card_ = self.cards[key]
            counting_down = run is not None and run.plan.measure == measure and s is not None
            set_text(card_.caption, f"{title} left" if counting_down else title)
            set_prop(card_.caption, "accent", counting_down)
            set_prop(card_.value, "accent", counting_down)
            if counting_down:
                card_.value.setText(fmt_amount(run.plan, run.remaining))

    def _update_plan(self, view: View) -> None:
        run = view.run
        self.plan_banner.setVisible(run is not None)
        self.modes.setTabText(COUNTDOWN, "Countdown" if run is None else f"Countdown  ·  {round(run.fraction * 100)}%")
        if run is not None:
            self.plan_banner.setText(
                f"▶   {run.plan.name} is running · {fmt_amount(run.plan, run.remaining)} left      View ›"
            )

    def _update_incline(self, view: View) -> None:
        s = view.status
        if s is None:
            value, sub = "--", ""
        else:
            value = str(s.incline)
            sub = f"of {s.max_incline}"
            if view.incline_goal is not None:
                sub += f"  → {view.incline_goal}"
        set_text(self.incline_label, value)
        set_text(self.incline_sub, sub)
        steerable = s is not None and view.auto_incline and s.max_incline > 0 and s.state == State.RUNNING
        self.incline_down.setEnabled(steerable)
        self.incline_up.setEnabled(steerable)
        if not view.auto_incline:
            note = "The app doesn't change it. A plan only reminds you."
        elif s is not None and s.state != State.RUNNING:
            note = "The incline only moves while the belt runs."
        else:
            note = ""
        set_text(self.incline_note, note)
        self.incline_note.setVisible(bool(note))
        self.incline_link.setVisible(not view.auto_incline)

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
        set_text(self.presence, text)
        self.presence.setVisible(bool(text))
        set_prop(self.presence, "tone", "ok" if view.on_belt and text else "warn" if view.on_belt is False else "")

    def _update_rest(self, view: View) -> None:
        if view.rest_left is not None:
            text, tone = f"Cooling down · {fmt_duration(view.rest_left)}", "warn"
        elif view.rest_limit and view.status is not None:
            text, tone = f"Rest in {fmt_duration(max(0, view.rest_after_seconds - view.run_seconds))}", ""
        else:
            text, tone = "", ""
        set_text(self.rest, text)
        self.rest.setVisible(bool(text))
        set_prop(self.rest, "tone", tone)

    def _update_alerts(self, view: View) -> None:
        s = view.status
        running = s is not None and s.state == State.RUNNING
        cooling = view.rest_left is not None and not running
        self.cool_banner.setVisible(cooling)
        if cooling:
            set_text(self.cool_time, fmt_duration(view.rest_left))
        away = running and view.off_belt_pause and view.on_belt is False and view.off_belt_for is not None
        self.away_banner.setVisible(bool(away))
        if away:
            set_text(self.away_time, f"{max(0, math.ceil(view.off_belt_seconds - view.off_belt_for))} s")

    def _update_mini(self, view: View) -> None:
        """The bar at the bottom: what is running, when this page doesn't already show it."""
        s, run = view.status, view.run
        page = self.modes.currentIndex()
        here = page == NORMAL or (page == COUNTDOWN and run is not None)
        active = s is not None and (s.state != State.STOPPED or run is not None)
        self.mini.setVisible(not here and active)
        if here or not active:
            return
        moving = s.state in (State.RUNNING, State.STARTING)
        kind = "running" if s.state == State.RUNNING else "paused"
        set_prop(self.mini_dot, "kind", kind)
        speed = f"{s.to_display(s.speed):.1f} {s.speed_unit}"
        if run is not None:
            title = run.plan.name
            detail = f"{fmt_amount(run.plan, run.remaining)} left · {speed}"
            if view.auto_incline:
                detail += f" · incline {s.incline}"
            self.mini_bar.setValue(round(run.fraction * 1000))
        else:
            title = speed
            detail = f"{fmt_duration(s.duration_s)} · {s.distance:.2f} {s.distance_unit}"
            self.mini_bar.setValue(0)
        self.mini_open.setText(f"{title}\n{detail}")
        set_text(self.mini_toggle, "Pause" if moving else "Resume")
        set_prop(self.mini_toggle, "kind", "hold" if moving else "go")
        self.mini_toggle.setEnabled(moving or view.rest_left is None)

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
