"""The Countdown page: your plans, each one click from running, and a live
readout of the plan in progress."""
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..controller import View
from ..plans import DISTANCE, Plan, RunView, duplicate, fmt_amount
from .backend import Backend
from .icons import COLORS
from .plan_editor import PlanEditor

LIST, RUNNING = 0, 1


def plan_text(plan: Plan, point) -> str:
    text = f"{point.speed:g} {plan.speed_unit}"
    return f"{text}, incline {point.incline}" if plan.uses_incline else text


def next_text(run: RunView) -> str:
    nxt = run.next_point
    if nxt is None:
        return "That's the last stretch."
    when = fmt_amount(run.plan, run.until_next)
    return f"In {when}: {plan_text(run.plan, nxt)}"


class PlanRow(QFrame):
    def __init__(self, plan: Plan, backend: Backend, page: "CountdownPage"):
        super().__init__()
        self.plan = plan
        self.setObjectName("card")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: page.row_menu(plan, self.mapToGlobal(pos)))

        title = QLabel(plan.name)
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        detail = QLabel(plan.summary())
        detail.setWordWrap(True)  # A long name or summary wraps instead of pushing Run off screen
        detail.setStyleSheet("color: palette(mid);")
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(title)
        text.addWidget(detail)

        self.run = QPushButton("▶  Run")
        self.run.setProperty("big", True)
        self.run.setProperty("active", True)
        self.run.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.run.setToolTip("Start this plan now. The belt starts too if it's stopped.")
        self.run.clicked.connect(lambda: backend.run_plan(plan))
        edit = QPushButton("Edit")
        edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit.clicked.connect(lambda: page.edit(plan))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addLayout(text, 1)
        layout.addWidget(edit)
        layout.addWidget(self.run)


class CountdownPage(QStackedWidget):
    def __init__(self, backend: Backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
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
        self.empty = QLabel("No plans yet. Create one and it'll be a single click away.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color: palette(mid); padding: 16px;")
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(8)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(self.empty)
        holder_layout.addLayout(self.list_layout)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(220)
        scroll.setWidget(holder)
        layout.addWidget(scroll)
        new = QPushButton("＋  New plan…")
        new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        new.clicked.connect(lambda: self.edit(None))
        layout.addWidget(new, 0, Qt.AlignmentFlag.AlignLeft)
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
        if dialog.exec() != PlanEditor.DialogCode.Accepted or dialog.plan is None:
            return
        if plan is None:
            plans = (*self.plans, dialog.plan)
        else:
            plans = tuple(dialog.plan if p == plan else p for p in self.plans)
        self.backend.save_plans(plans)

    def row_menu(self, plan: Plan, pos) -> None:
        menu = QMenu(self)
        edit = menu.addAction("Edit…")
        copy = menu.addAction("Duplicate")
        delete = menu.addAction("Delete")
        chosen = menu.exec(pos)
        if chosen is edit:
            self.edit(plan)
        elif chosen is copy:
            names = {p.name for p in self.plans}
            self.backend.save_plans((*self.plans, duplicate(plan, names)))
        elif chosen is delete:
            confirm = QMessageBox.question(self, "Delete plan", f"Delete “{plan.name}”?")
            if confirm == QMessageBox.StandardButton.Yes:
                self.backend.save_plans(tuple(p for p in self.plans if p != plan))

    # --- Plan in progress ---------------------------------------------------------------------

    def _build_run(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.run_name = QLabel()
        self.run_name.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        self.run_left = QLabel()
        self.run_left.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.run_now = QLabel()
        self.run_next = QLabel()
        self.run_next.setStyleSheet("color: palette(mid);")
        self.run_override = QLabel()
        self.run_override.setStyleSheet("color: #d98f00;")
        self.run_incline = QLabel()
        self.skip = QPushButton("Skip to next point")
        self.skip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.skip.clicked.connect(self.backend.skip_point)
        end = QPushButton("End plan")
        end.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        end.setToolTip("Go back to Normal. The belt keeps going at its current speed.")
        end.clicked.connect(self.backend.end_plan)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip)
        buttons.addWidget(end)
        buttons.addStretch(1)
        for w in (self.run_name, self.progress, self.run_left, self.run_now, self.run_override,
                  self.run_next, self.run_incline):
            layout.addWidget(w)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _show_run(self, run: RunView, view: View) -> None:
        plan = run.plan
        self.run_name.setText(plan.name)
        self.progress.setValue(round(run.fraction * 1000))
        unit_left = fmt_amount(plan, run.remaining)
        self.run_left.setText(f"{unit_left} {'to go' if plan.measure == DISTANCE else 'left'}"
                              f"   ·   {round(run.fraction * 100)}% done")
        self.run_now.setText(f"Now: {plan_text(plan, run.point)}   (point {run.index + 1} of {len(plan.points)})")
        self.run_override.setText("You've changed the speed or incline. The plan takes over again at the next point."
                                  if run.overridden else "")
        self.run_next.setText(next_text(run))
        self.skip.setEnabled(run.next_point is not None)

        s = view.status
        if plan.uses_incline:
            reported = s.incline if s else None
            same = reported == run.point.incline
            color = COLORS["running"] if same else "#d98f00"
            hint = "" if same else ("   moving…" if view.auto_incline else "   ← set this on the treadmill")
            shown = "?" if reported is None else str(reported)
            self.run_incline.setText(
                f'<span style="color:{color}">Incline target {run.point.incline} · treadmill reports {shown}{hint}</span>'
            )
        else:
            self.run_incline.setText("")

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
        for row in self.rows:
            row.run.setEnabled(self.connected)
