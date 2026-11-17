"""Dialog for creating and editing a Countdown plan."""
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QVBoxLayout,
)

from ..plans import DISTANCE, KM, MAX_INCLINE, MAX_SPEED, MILES, TIME, Plan, Point, validate
from .theme import tokens
from .widgets import ProfileChart, card, make_button


def _spin(value: float, maximum: float, decimals: int = 1, step: float = 0.1) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setRange(0.0, maximum)
    box.setValue(value)
    return box


class PlanEditor(QDialog):
    """Edits a copy of a plan; `plan` holds the result after the dialog is accepted."""

    def __init__(self, plan: Optional[Plan] = None, taken_names: frozenset = frozenset(), parent=None):
        super().__init__(parent)
        self.plan: Optional[Plan] = None
        self.action = "save"  # "duplicate" or "delete" when one of those buttons closed the dialog
        self.original_name = plan.name if plan else None
        self.taken = taken_names - ({plan.name} if plan else set())
        plan = plan or Plan("", MILES, DISTANCE, 2.0, (Point(0.0, 2.5), Point(1.0, 3.0)))
        self.setWindowTitle("Edit plan" if self.original_name else "New plan")
        self.setMinimumWidth(560)

        self.name = QLineEdit(plan.name)
        self.name.setPlaceholderText("e.g. Morning hills")
        self.unit = QComboBox()
        self.unit.addItem("Miles (mph)", MILES)
        self.unit.addItem("Kilometres (km/h)", KM)
        self.unit.setCurrentIndex(0 if plan.unit == MILES else 1)
        self.measure = QComboBox()
        self.measure.addItem("Distance", DISTANCE)
        self.measure.addItem("Time", TIME)
        self.measure.setCurrentIndex(0 if plan.measure == DISTANCE else 1)
        self.total = _spin(plan.total, 1000, decimals=2, step=0.25)
        self.stop_at_end = QCheckBox("Stop the belt when the plan finishes")
        self.stop_at_end.setChecked(plan.stop_at_end)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Units", self.unit)
        form.addRow("Plan length is a", self.measure)
        form.addRow("Total", self.total)
        form.addRow("", self.stop_at_end)

        self.table = QTableWidget(0, 3)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(180)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rows: list[tuple[QDoubleSpinBox, QDoubleSpinBox, QSpinBox]] = []

        add = QPushButton("Add point")
        add.clicked.connect(self.add_point)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_point)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(add)
        buttons_row.addWidget(remove)
        buttons_row.addStretch(1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.preview = ProfileChart()
        preview_card = card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(12, 10, 12, 6)
        preview_layout.addWidget(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("kind", "accent")
        # Only for a plan that already exists.
        self.duplicate_button = make_button("Duplicate")
        self.delete_button = make_button("Delete", kind="danger")
        self.duplicate_button.clicked.connect(lambda: self._finish("duplicate"))
        self.delete_button.clicked.connect(lambda: self._finish("delete"))
        for extra in (self.duplicate_button, self.delete_button):
            extra.setVisible(self.original_name is not None)
            self.buttons.addButton(extra, QDialogButtonBox.ButtonRole.ActionRole)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Points: where the speed and incline change"))
        layout.addWidget(self.table)
        layout.addLayout(buttons_row)
        layout.addWidget(preview_card)
        layout.addWidget(self.summary)
        layout.addWidget(self.buttons)

        for p in plan.points:
            self._append_row(p)
        self._relabel()
        for signal in (self.unit.currentIndexChanged, self.measure.currentIndexChanged):
            signal.connect(self._relabel)
        self.total.valueChanged.connect(self._refresh)
        self.name.textChanged.connect(self._refresh)
        self._refresh()

    # --- Points ---------------------------------------------------------------------

    def _append_row(self, point: Point) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        at = _spin(point.at, 100000, decimals=2, step=0.25)
        speed = _spin(point.speed, MAX_SPEED)
        incline = QSpinBox()
        incline.setRange(0, MAX_INCLINE)
        incline.setValue(point.incline)
        if row == 0:
            at.setEnabled(False)  # The start is always at zero
        for column, box in enumerate((at, speed, incline)):
            self.table.setCellWidget(row, column, box)
            for signal in (box.valueChanged,):
                signal.connect(self._refresh)
        self.rows.append((at, speed, incline))

    def add_point(self) -> None:
        """Adds a point halfway between the last one and the end, copying its speed and incline."""
        points = self._points()
        last = points[-1]
        at = round(last.at + max(0.01, (self.total.value() - last.at) / 2), 2)
        self._append_row(Point(at, last.speed, last.incline))
        self._refresh()

    def remove_point(self) -> None:
        row = self.table.currentRow()
        if row <= 0:  # Nothing selected, or the start, which every plan needs
            return
        self.table.removeRow(row)
        del self.rows[row]
        self._refresh()

    def _points(self) -> tuple[Point, ...]:
        return tuple(Point(at.value(), speed.value(), incline.value()) for at, speed, incline in self.rows)

    def _relabel(self) -> None:
        unit = self.unit.currentData()
        by_distance = self.measure.currentData() == DISTANCE
        at_label = f"At ({unit})" if by_distance else "At (min)"
        speed_label = "Speed (mph)" if unit == MILES else "Speed (km/h)"
        self.table.setHorizontalHeaderLabels([at_label, speed_label, "Incline"])
        self.total.setSuffix(f" {unit}" if by_distance else " min")
        self._refresh()

    # --- Result -------------------------------------------------------------------------

    def current(self) -> Plan:
        points = tuple(sorted(self._points(), key=lambda p: p.at))
        return Plan(
            name=self.name.text().strip(),
            unit=self.unit.currentData(),
            measure=self.measure.currentData(),
            total=self.total.value(),
            points=points,
            stop_at_end=self.stop_at_end.isChecked(),
        )

    def problems(self) -> list[str]:
        plan = self.current()
        errors = validate(plan)
        if plan.name in self.taken:
            errors.insert(0, "You already have a plan with that name.")
        return errors

    def _refresh(self) -> None:
        errors = self.problems()
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(not errors)
        if errors:
            self.summary.setText('<span style="color:%s">%s</span>' % (tokens()["danger"], errors[0]))
        else:
            self.summary.setText(self.current().summary())
        plan = self.current()
        self.preview.set_plan(plan if plan.points and plan.total > 0 else None)

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def save(self) -> None:
        if not self.problems():
            self.plan = self.current()
            self.accept()
