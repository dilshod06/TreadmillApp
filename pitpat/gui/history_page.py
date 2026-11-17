"""The History tab: what you've done, by day and by week, for any dates."""
from datetime import date
from typing import Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QDateEdit, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import history as h
from ..controller import View
from . import theme
from .widgets import BarChart, card, label, make_button, set_text

PRESETS = (("week", "This week"), ("last", "Last week"), ("four", "Last 4 weeks"), ("custom", "Pick dates"))
TITLES = {"week": "Weekly summary", "last": "Last week", "four": "Last 4 weeks", "custom": "Summary for your dates"}
ROW_HEIGHT = 34


def _to_qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _from_qdate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setShowGrid(False)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for column in range(1, len(headers)):  # Numbers line up on the right, so their headings do too
        table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return table


def _cell(text: str, right: bool = True, bold: bool = False, color: Optional[str] = None, fill: Optional[str] = None):
    item = QTableWidgetItem(text)
    item.setTextAlignment((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
    if bold:
        font = QFont()
        font.setBold(True)
        item.setFont(font)
    if color:
        item.setForeground(theme.color(color))
    if fill:
        item.setBackground(theme.color(fill))
    return item


class HistoryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.preset = "week"
        self.view: Optional[View] = None
        self._shown: Optional[tuple] = None
        self.show_summary = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("Workout history")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(label("Each workout is saved to its day when you press Stop or a plan finishes.", muted=True))

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.presets: dict[str, object] = {}
        for key, text in PRESETS:
            button = make_button(text)
            button.setProperty("pill", True)
            button.setCheckable(True)
            button.setStyleSheet("padding: 6px 14px; border-radius: 15px;")
            button.setChecked(key == self.preset)
            self.group.addButton(button)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            chips.addWidget(button)
            self.presets[key] = button
        chips.addStretch(1)
        layout.addLayout(chips)

        self.dates = card()
        dates_layout = QHBoxLayout(self.dates)
        dates_layout.setContentsMargins(14, 10, 14, 10)
        dates_layout.setSpacing(10)
        today = date.today()
        self.first_edit, self.last_edit = QDateEdit(), QDateEdit()
        for edit, day in ((self.first_edit, today.replace(day=1)), (self.last_edit, today)):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("MMM d, yyyy")
            edit.setMaximumDate(_to_qdate(today))
            edit.setDate(_to_qdate(day))
            edit.dateChanged.connect(self._refresh)
        dates_layout.addWidget(label("From", muted=True))
        dates_layout.addWidget(self.first_edit)
        dates_layout.addWidget(label("To", muted=True))
        dates_layout.addWidget(self.last_edit)
        self.date_message = QLabel()
        self.date_message.setProperty("tone", "danger")
        dates_layout.addWidget(self.date_message, 1)
        self.dates.hide()
        layout.addWidget(self.dates)

        head = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.range_label = label("", muted=True)
        head.addWidget(self.heading, 1)
        head.addWidget(self.range_label)
        layout.addLayout(head)

        self.tile_values: dict[str, QLabel] = {}
        self.tile_titles: dict[str, QLabel] = {}
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        for key, name in (("distance", "Distance"), ("floors", "Floors climbed"), ("workouts", "Workouts"), ("time", "Time")):
            frame = card()
            column = QVBoxLayout(frame)
            column.setContentsMargins(14, 10, 14, 10)
            column.setSpacing(0)
            caption = label(name.upper(), caption=True)
            value = QLabel("--")
            value.setStyleSheet("font-size: 26px; font-weight: 700;")
            if key == "floors":
                value.setProperty("tone", "floors")
            column.addWidget(caption)
            column.addWidget(value)
            self.tile_titles[key], self.tile_values[key] = caption, value
            tiles.addWidget(frame, 1)
        layout.addLayout(tiles)

        self.empty = label("", muted=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("padding: 24px;")
        layout.addWidget(self.empty)

        self.body = QWidget()
        self.body.setObjectName("page")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.note = label("", muted=True)
        body.addWidget(self.note)

        charts = QHBoxLayout()
        charts.setSpacing(10)
        self.chart_distance = BarChart("accent", 1, 1.0)
        self.chart_floors = BarChart("pause", 0, 5.0)
        self.chart_titles = []
        for chart in (self.chart_distance, self.chart_floors):
            frame = card()
            column = QVBoxLayout(frame)
            column.setContentsMargins(12, 10, 12, 6)
            column.setSpacing(2)
            caption = label("", caption=True)
            self.chart_titles.append(caption)
            column.addWidget(caption)
            column.addWidget(chart)
            charts.addWidget(frame, 1)
        body.addLayout(charts)

        self.weeks_card, self.weeks_table = self._table_card("WEEK BY WEEK", ["Week", "Distance", "Floors", "Workouts"])
        self.days_card, self.days_table = self._table_card("DAILY RECORDS", ["Day", "Workouts", "Distance", "Floors", "Time"])
        body.addWidget(self.weeks_card)
        body.addWidget(self.days_card)

        share = card()
        share_layout = QVBoxLayout(share)
        share_layout.setContentsMargins(14, 12, 14, 12)
        share_layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Summary to share</b>"), 1)
        self.summary_button = make_button("Generate summary")
        self.summary_button.clicked.connect(self._toggle_summary)
        self.copy_button = make_button("Copy", kind="accent")
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.summary_button)
        row.addWidget(self.copy_button)
        share_layout.addLayout(row)
        self.summary_hint = label("Makes a short text with the totals for these dates, ready to paste.", muted=True)
        self.summary_text = QLabel()
        self.summary_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_text.setWordWrap(True)
        self.summary_text.setObjectName("info")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.summary_text.setFont(mono)
        share_layout.addWidget(self.summary_hint)
        share_layout.addWidget(self.summary_text)
        body.addWidget(share)
        self.footnote = label("Floors climbed = distance × incline % ÷ 10 ft per floor. The treadmill's incline level "
                              "is taken as a % grade.", muted=True)
        self.footnote.setWordWrap(True)
        self.footnote.setStyleSheet("font-size: 12px;")
        body.addWidget(self.footnote)
        layout.addWidget(self.body)
        layout.addStretch(1)
        self._sync_summary()

    def _table_card(self, title: str, headers: list[str]):
        frame = card()
        column = QVBoxLayout(frame)
        column.setContentsMargins(0, 10, 0, 0)
        column.setSpacing(4)
        caption = label(title, caption=True)
        caption.setContentsMargins(14, 0, 0, 0)
        table = _table(headers)
        column.addWidget(caption)
        column.addWidget(table)
        return frame, table

    # --- Choosing dates ------------------------------------------------------------------

    def _choose(self, key: str) -> None:
        self.preset = key
        self.show_summary = False
        self.dates.setVisible(key == "custom")
        self._refresh()

    def range(self) -> tuple[date, date]:
        if self.preset == "custom":
            return _from_qdate(self.first_edit.date()), _from_qdate(self.last_edit.date())
        return h.preset_range(self.preset, date.today())

    def select(self, key: str) -> None:
        """Picks a range as if its button had been pressed."""
        self.presets[key].setChecked(True)
        self._choose(key)

    # --- Summary text --------------------------------------------------------------------

    def _toggle_summary(self) -> None:
        self.show_summary = not self.show_summary
        self._sync_summary()
        self._refresh(force=True)

    def _sync_summary(self) -> None:
        self.summary_button.setText("Hide" if self.show_summary else "Generate summary")
        self.copy_button.setVisible(self.show_summary)
        self.summary_text.setVisible(self.show_summary)
        self.summary_hint.setVisible(not self.show_summary)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.summary_text.text())
        self.copy_button.setText("Copied")

    # --- Showing it ----------------------------------------------------------------------

    def update_view(self, view: View) -> None:
        self.view = view
        self._refresh()

    def _refresh(self, *_args, force: bool = False) -> None:
        view = self.view
        if view is None:
            return
        first, last = self.range()
        key = (view.workouts, view.imperial, first, last, self.preset, self.show_summary, date.today())
        if not force and self._shown is not None and all(a is b or a == b for a, b in zip(self._shown, key)):
            return
        self._shown = key
        self.copy_button.setText("Copy")
        imperial, unit = view.imperial, h.distance_unit(view.imperial)
        self.heading.setText(TITLES[self.preset])
        bad = first > last
        self.date_message.setText("The first date must be on or before the last date." if bad else "")
        self.range_label.setText("" if bad else f"{h.fmt_range(first, last)} · {h.days_in(first, last)} days")
        self.tile_titles["distance"].setText(f"DISTANCE ({unit.upper()})")
        items = [] if bad else h.within(view.workouts, first, last)
        t = h.totals(items)
        self.tile_values["distance"].setText(f"{h.distance(t.metres, imperial):.1f}")
        self.tile_values["floors"].setText(str(round(t.floors)))
        self.tile_values["workouts"].setText(str(t.workouts))
        self.tile_values["time"].setText(h.fmt_minutes(t.seconds))

        self.body.setVisible(bool(items))
        self.empty.setVisible(not items)
        if bad:
            self.empty.setText("Choose a start date that comes before the end date.")
        elif not items:
            self.empty.setText("No workouts in these dates. Press Start on the Normal tab and one will show up here "
                               "when you stop.")
        if not items:
            return

        span = h.days_in(first, last)
        self.note.setText(
            f"Active on {t.days} of {span} days · {h.distance(t.metres / t.days, imperial):.1f} {unit} and "
            f"{h.fmt_floors(t.floors / t.days)} on an active day"
        )
        per = "day" if span <= h.SHORT_RANGE_DAYS else "week"
        self.chart_titles[0].setText(f"{unit.upper()} PER {per.upper()}")
        self.chart_titles[1].setText(f"FLOORS PER {per.upper()}")
        bks = h.buckets(view.workouts, first, last)

        def tip(b: h.Bucket, text: str) -> str:
            return f"{h.fmt_range(b.first, b.last) if b.first != b.last else b.first.strftime('%a %b %d')}: {text}"

        self.chart_distance.set_bars([
            (b.label, h.distance(b.totals.metres, imperial), tip(b, f"{h.distance(b.totals.metres, imperial):.2f} {unit}"))
            for b in bks
        ])
        self.chart_floors.set_bars([
            (b.label, b.totals.floors, tip(b, f"{b.totals.floors:.1f} floors")) for b in bks
        ])

        self.weeks_card.setVisible(span > 7)
        if span > 7:
            rows = h.weeks(view.workouts, first, last)
            self.weeks_table.setRowCount(len(rows) + 1)
            for r, week in enumerate(rows):
                start = h.week_start(week.first)
                self.weeks_table.setItem(r, 0, _cell(f"of {start:%b} {start.day}", right=False))
                self.weeks_table.setItem(r, 1, _cell(f"{h.distance(week.totals.metres, imperial):.1f}"))
                self.weeks_table.setItem(r, 2, _cell(str(round(week.totals.floors)), color="pause"))
                self.weeks_table.setItem(r, 3, _cell(str(week.totals.workouts)))
            n = len(rows)
            self.weeks_table.setItem(n, 0, _cell("Total", right=False, bold=True))
            self.weeks_table.setItem(n, 1, _cell(f"{h.distance(t.metres, imperial):.1f}", bold=True))
            self.weeks_table.setItem(n, 2, _cell(str(round(t.floors)), bold=True, color="pause"))
            self.weeks_table.setItem(n, 3, _cell(str(t.workouts), bold=True))
            self._fit(self.weeks_table, n + 1)

        records = h.daily_records(view.workouts, first, last)
        self.days_table.setRowCount(len(records))
        today = date.today()
        for r, rec in enumerate(records):
            fill = "accent_soft" if rec.day == today else None
            when = f"{rec.day:%a, %b} {rec.day.day}" + (" · today" if rec.day == today else "")
            for c, (text, right, color) in enumerate((
                (when, False, None), (str(rec.totals.workouts), True, None),
                (f"{h.distance(rec.totals.metres, imperial):.2f} {unit}", True, None),
                (f"{rec.totals.floors:.1f}", True, "pause"), (h.fmt_minutes(rec.totals.seconds), True, None),
            )):
                self.days_table.setItem(r, c, _cell(text, right=right, color=color, fill=fill))
        self._fit(self.days_table, len(records), cap=8)

        if self.show_summary:
            self.summary_text.setText(h.summary_text(view.workouts, first, last, imperial))
        self._sync_summary()

    @staticmethod
    def _fit(table: QTableWidget, rows: int, cap: Optional[int] = None) -> None:
        """Sizes a table to its rows, up to `cap` of them, then it scrolls."""
        shown = min(rows, cap) if cap else rows
        header = table.horizontalHeader().height() or ROW_HEIGHT
        table.setFixedHeight(header + shown * ROW_HEIGHT + 4)
        table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if cap and rows > cap else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
