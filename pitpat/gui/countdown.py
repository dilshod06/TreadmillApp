"""The Countdown page: your plans, each one click from running, and a live
view of the plan in progress."""
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QMenu, QMessageBox, QProgressBar, QStackedWidget, QVBoxLayout, QWidget,
)

from ..controller import View
from ..plans import DISTANCE, Plan, RunView, duplicate, fmt_amount
from ..protocol import State
from .backend import Backend
from .plan_editor import PlanEditor
from .widgets import ProfileChart, card, label, make_button, round_button, set_text

LIST, RUNNING = 0, 1


def plan_text(plan: Plan, point) -> str:
    text = f"{point.speed:g} {plan.speed_unit}"
    return f"{text} · incline {point.incline}" if plan.uses_incline else text


def next_text(run: RunView) -> tuple[str, str]:
    """What comes next, and when."""
    nxt = run.next_point
    if nxt is None:
        return "Last stretch", f"ends at {run.plan.total:g} {run.plan.total_unit}"
    return plan_text(run.plan, nxt), f"in {fmt_amount(run.plan, run.until_next)}"


def title(text: str) -> QLabel:
    heading = QLabel(text)
    heading.setStyleSheet("font-size: 20px; font-weight: 700;")
    return heading


class PlanRow(QWidget):
    def __init__(self, plan: Plan, backend: Backend, page: "CountdownPage"):
        super().__init__()
        self.plan = plan
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: page.row_menu(plan, self.mapToGlobal(pos)))

        frame = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        name = QLabel(plan.name)
        name.setStyleSheet("font-size: 16px; font-weight: 700;")
        name.setWordWrap(True)  # A long name wraps instead of pushing Run off screen
        detail = label(plan.summary(), muted=True)
        detail.setWordWrap(True)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(name)
        text.addWidget(detail)

        self.run = make_button("▶  Run", kind="go")
        self.run.setToolTip("Start this plan now. The belt starts too if it's stopped.")
        self.run.clicked.connect(lambda: backend.run_plan(plan))
        self.edit = make_button("Edit")
        self.edit.clicked.connect(lambda: page.edit(plan))
        self.chart = ProfileChart(compact=True)
        self.chart.set_plan(plan)

        layout = QGridLayout(frame)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        layout.addLayout(text, 0, 0)
        layout.addWidget(self.edit, 0, 1, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.run, 0, 2, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.chart, 1, 0, 1, 3)
        layout.setColumnStretch(0, 1)


class CountdownPage(QStackedWidget):
    def __init__(self, backend: Backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("page")
        self.backend = backend
        self.plans: tuple[Plan, ...] = ()
        self.rows: list[PlanRow] = []
        self.connected = False
        self.addWidget(self._build_list())
        self.addWidget(self._build_run())

    # --- Plan list ----------------------------------------------------------------------

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addWidget(title("Plans"))
        words.addWidget(label("Pick one and press Run. It starts the belt for you.", muted=True))
        self.new_button = make_button("＋  New plan…", kind="accent")
        self.new_button.clicked.connect(lambda: self.edit(None))
        head.addLayout(words, 1)
        head.addWidget(self.new_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.empty = label("No plans yet. Create one and it'll be a single click away.", muted=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("padding: 28px;")
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(12)
        layout.addWidget(self.empty)
        layout.addLayout(self.list_layout)
        layout.addStretch(1)
        return page

    def _show_plans(self, plans: tuple[Plan, ...]) -> None:
        self.plans = plans
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = [PlanRow(p, self.backend, self) for p in plans]
        for row in self.rows:
            self.list_layout.addWidget(row)
        self.empty.setVisible(not plans)

    def edit(self, plan: Optional[Plan]) -> None:
        dialog = PlanEditor(plan, frozenset(p.name for p in self.plans), self)
        accepted = dialog.exec() == PlanEditor.DialogCode.Accepted
        if accepted and dialog.action == "duplicate" and plan is not None:
            self._duplicate(plan)
        elif accepted and dialog.action == "delete" and plan is not None:
            self._delete(plan)
        elif accepted and dialog.plan is not None:
            if plan is None:
                plans = (*self.plans, dialog.plan)
            else:
                plans = tuple(dialog.plan if p == plan else p for p in self.plans)
            self.backend.save_plans(plans)

    def _duplicate(self, plan: Plan) -> None:
        self.backend.save_plans((*self.plans, duplicate(plan, {p.name for p in self.plans})))

    def _delete(self, plan: Plan) -> None:
        confirm = QMessageBox.question(self, "Delete plan", f"Delete “{plan.name}”?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.backend.save_plans(tuple(p for p in self.plans if p != plan))

    def row_menu(self, plan: Plan, pos) -> None:
        menu = QMenu(self)
        edit = menu.addAction("Edit…")
        copy = menu.addAction("Duplicate")
        delete = menu.addAction("Delete")
        chosen = menu.exec(pos)
        if chosen is edit:
            self.edit(plan)
        elif chosen is copy:
            self._duplicate(plan)
        elif chosen is delete:
            self._delete(plan)

    # --- Plan in progress ---------------------------------------------------------------------

    def _stepper(self, caption: str) -> tuple[QWidget, QLabel, QWidget, QWidget]:
        frame = card()
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)
        down, up = round_button("−", small=True), round_button("+", small=True)
        value = QLabel("--")
        value.setStyleSheet("font-size: 18px; font-weight: 700;")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setMinimumWidth(46)
        row.addWidget(label(caption, muted=True))
        row.addWidget(down)
        row.addWidget(value)
        row.addWidget(up)
        return frame, value, down, up

    def _fact(self, caption: str) -> tuple[QWidget, QLabel, QLabel]:
        frame = card()
        column = QVBoxLayout(frame)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(0)
        value = QLabel()
        value.setStyleSheet("font-size: 18px; font-weight: 700;")
        note = label("", muted=True)
        column.addWidget(label(caption.upper(), caption=True))
        column.addWidget(value)
        column.addWidget(note)
        return frame, value, note

    def _build_run(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(label("RUNNING PLAN", caption=True, accent=True))
        self.run_name = title("")
        words.addWidget(self.run_name)
        self.end_button = make_button("End plan")
        self.end_button.setToolTip("Go back to Normal. The belt keeps going at its current speed.")
        self.end_button.clicked.connect(self.backend.end_plan)
        head.addLayout(words, 1)
        head.addWidget(self.end_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        big = QHBoxLayout()
        big.setSpacing(10)
        self.run_left = QLabel()
        self.run_left.setStyleSheet("font-size: 64px; font-weight: 700;")
        self.run_unit = label("", muted=True)
        self.run_unit.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.run_pct = label("", accent=True)
        self.run_pct.setStyleSheet("font-size: 17px;")
        self.run_pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.run_floors = QLabel()
        self.run_floors.setProperty("tone", "floors")
        self.run_floors.setAlignment(Qt.AlignmentFlag.AlignRight)
        done = QVBoxLayout()
        done.setSpacing(0)
        done.addStretch(1)
        done.addWidget(self.run_pct)
        done.addWidget(self.run_floors)
        big.addWidget(self.run_left)
        big.addWidget(self.run_unit, 0, Qt.AlignmentFlag.AlignBottom)
        big.addStretch(1)
        big.addLayout(done)
        layout.addLayout(big)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        chart_card = card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(12, 10, 12, 6)
        self.chart = ProfileChart()
        chart_layout.addWidget(self.chart)
        layout.addWidget(chart_card)

        facts = QHBoxLayout()
        facts.setSpacing(12)
        now, self.run_now, self.run_now_note = self._fact("Now")
        nxt, self.run_next, self.run_next_note = self._fact("Next")
        facts.addWidget(now)
        facts.addWidget(nxt)
        layout.addLayout(facts)

        self.run_override = QLabel("You've changed the speed or incline. The plan takes over again at the next point.")
        self.run_override.setObjectName("note")
        self.run_override.setWordWrap(True)
        self.run_incline = QLabel()
        self.run_incline.setObjectName("note")
        self.run_incline.setWordWrap(True)
        layout.addWidget(self.run_override)
        layout.addWidget(self.run_incline)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        speed, self.speed_value, self.speed_down, self.speed_up = self._stepper("Speed")
        incline, self.incline_value, self.incline_down, self.incline_up = self._stepper("Incline")
        self.speed_down.clicked.connect(lambda: self.backend.nudge(-0.1))
        self.speed_up.clicked.connect(lambda: self.backend.nudge(0.1))
        self.incline_down.clicked.connect(lambda: self.backend.nudge_incline(-1))
        self.incline_up.clicked.connect(lambda: self.backend.nudge_incline(1))
        self.skip = make_button("Skip to next point  ›")
        self.skip.clicked.connect(self.backend.skip_point)
        self.toggle = make_button("Pause", kind="hold")
        self.toggle.setMinimumWidth(110)
        self.toggle.clicked.connect(self.backend.toggle)
        controls.addWidget(speed)
        controls.addWidget(incline)
        controls.addStretch(1)
        controls.addWidget(self.skip)
        controls.addWidget(self.toggle)
        layout.addLayout(controls)
        layout.addStretch(1)
        return page

    def _show_run(self, run: RunView, view: View) -> None:
        plan = run.plan
        s = view.status
        set_text(self.run_name, plan.name)
        self.progress.setValue(round(run.fraction * 1000))
        if plan.measure == DISTANCE:
            amount = f"{run.remaining / (plan.raw(1.0)):.2f}"
            unit = f"{plan.unit} to go"
        else:
            amount, unit = fmt_amount(plan, run.remaining), "left"
        set_text(self.run_left, amount)
        set_text(self.run_unit, unit)
        set_text(self.run_pct, f"{round(run.fraction * 100)}% done")
        set_text(self.run_floors, f"{view.floors:.1f} floors climbed")
        self.chart.set_plan(plan, run.position / plan.raw(1.0))
        set_text(self.run_now, plan_text(plan, run.point))
        set_text(self.run_now_note, f"Point {run.index + 1} of {len(plan.points)}")
        value, note = next_text(run)
        set_text(self.run_next, value)
        set_text(self.run_next_note, note)
        self.run_override.setVisible(run.overridden)
        self.skip.setEnabled(run.next_point is not None)

        if plan.uses_incline:
            reported = s.incline if s else None
            same = reported == run.point.incline
            hint = "" if same else ("   moving…" if view.auto_incline else "   ← set this on the treadmill")
            shown = "?" if reported is None else str(reported)
            self.run_incline.setText(f"Incline target {run.point.incline} · treadmill reports {shown}{hint}")
            self.run_incline.setVisible(not same)
        else:
            self.run_incline.setText("")
            self.run_incline.setVisible(False)

        moving = s is not None and s.state in (State.RUNNING, State.STARTING)
        set_text(self.speed_value, "--" if s is None else f"{s.to_display(s.speed):.1f}")
        set_text(self.incline_value, "--" if s is None else str(s.incline))
        for button in (self.speed_down, self.speed_up):
            button.setEnabled(s is not None and s.state == State.RUNNING)
        steerable = s is not None and view.auto_incline and s.max_incline > 0 and s.state == State.RUNNING
        for button in (self.incline_down, self.incline_up):
            button.setEnabled(steerable)
        set_text(self.toggle, "Pause" if moving else "Resume")
        self.toggle.setProperty("kind", "hold" if moving else "go")
        self.toggle.style().unpolish(self.toggle)
        self.toggle.style().polish(self.toggle)
        self.toggle.setEnabled(s is not None and (moving or view.rest_left is None))

    # --- Updates ------------------------------------------------------------------------------

    def update_view(self, view: View) -> None:
        self.connected = view.status is not None
        if view.run is not None:
            self.setCurrentIndex(RUNNING)
            self._show_run(view.run, view)
        else:
            self.setCurrentIndex(LIST)
        if view.plans != self.plans:
            self._show_plans(view.plans)
        cooling = view.rest_left is not None
        for row in self.rows:
            row.run.setEnabled(self.connected and not cooling)
