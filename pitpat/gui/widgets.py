"""Small building blocks shared by the pages: the plan chart, a switch, toasts
and a few helpers for making buttons and cards."""
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..plans import Plan
from . import theme

CHART_INCLINE = 12  # The scale of the incline bars; treadmills top out around here


def make_button(text: str, kind: str = "", big: bool = False, name: str = "") -> QPushButton:
    """A button that never takes keyboard focus, so Space and the arrows stay shortcuts."""
    button = QPushButton(text)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if kind:
        button.setProperty("kind", kind)
    if big:
        button.setProperty("big", True)
    if name:
        button.setObjectName(name)
    return button


def round_button(text: str, small: bool = False) -> QPushButton:
    return make_button(text, name="roundSmall" if small else "round")


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def label(text: str = "", **props) -> QLabel:
    widget = QLabel(text)
    for key, value in props.items():
        widget.setProperty(key, value)
    return widget


def set_prop(widget: QWidget, name: str, value) -> None:
    """Sets a style property and restyles the widget if it changed."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def set_text(widget, text: str) -> None:
    """Avoids relayouts from setting the text it already has."""
    if widget.text() != text:
        widget.setText(text)


# --- Switch ------------------------------------------------------------------------------------

class Switch(QAbstractButton):
    """An on/off switch. Checkable like a checkbox, so `toggled` and `setChecked` work the same."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(46, 28)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = theme.color("accent" if on else "line")
        if not self.isEnabled():
            track.setAlphaF(0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 46, 28), 14, 14)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(21 if on else 3, 3, 22, 22))


# --- Plan chart --------------------------------------------------------------------------------

class ProfileChart(QWidget):
    """A plan's speed over its length, with the incline as bars along the bottom.
    With a `position`, the part already done is drawn stronger and a marker shows where you are."""

    def __init__(self, compact: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.compact = compact
        self.plan: Optional[Plan] = None
        self.position: Optional[float] = None
        self.setMinimumHeight(58 if compact else 176)
        if compact:
            self.setMaximumHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed if compact else QSizePolicy.Policy.Preferred)

    def set_plan(self, plan: Optional[Plan], position: Optional[float] = None) -> None:
        if plan != self.plan or position != self.position:
            self.plan, self.position = plan, position
            self.update()

    def paintEvent(self, _event) -> None:
        plan = self.plan
        if plan is None or not plan.points or plan.total <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        compact = self.compact
        left, right = (0, 0) if compact else (32, 6)
        top, bottom = (4, 4) if compact else (16, 22)
        w, h = self.width() - left - right, self.height() - top - bottom
        total = plan.total
        ymax = max(1.5, *(pt.speed for pt in plan.points)) * 1.25

        def x(v: float) -> float:
            return left + max(0.0, min(1.0, v / total)) * w

        def y(v: float) -> float:
            return top + h - (v / ymax) * h

        small = QFont(self.font())
        small.setPixelSize(10)
        bold = QFont(small)
        bold.setPixelSize(11)
        bold.setBold(True)
        muted, line, fg = theme.color("muted"), theme.color("line"), theme.color("fg")

        if not compact:
            p.setFont(small)
            for k in range(4):
                v = ymax / 1.25 * k / 3
                p.setPen(QPen(line, 1))
                p.drawLine(QPointF(left, y(v)), QPointF(left + w, y(v)))
                p.setPen(muted)
                p.drawText(QRectF(0, y(v) - 7, left - 5, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.1f}")

        spans = []
        for i, pt in enumerate(plan.points):
            end = plan.points[i + 1].at if i + 1 < len(plan.points) else total
            spans.append((pt, x(pt.at), x(end)))

        # Incline as bars along the bottom.
        p.setPen(Qt.PenStyle.NoPen)
        for pt, x0, x1 in spans:
            if pt.incline > 0:
                bar = max(3.0, min(1.0, pt.incline / CHART_INCLINE) * (18 if compact else 34))
                p.setBrush(theme.color("pause", 0.3))
                p.drawRect(QRectF(x0, top + h - bar, max(1.0, x1 - x0), bar))
                if not compact and x1 - x0 > 56:
                    p.setFont(small)
                    p.setPen(theme.color("pause"))
                    p.drawText(QRectF(x0, top + h - bar - 15, x1 - x0, 14), Qt.AlignmentFlag.AlignCenter, f"incline {pt.incline}")
                    p.setPen(Qt.PenStyle.NoPen)

        # Speed as a stepped line over a soft fill.
        stroke = QPainterPath()
        area = QPainterPath()
        for i, (pt, x0, x1) in enumerate(spans):
            if i == 0:
                stroke.moveTo(x0, y(pt.speed))
                area.moveTo(x0, top + h)
                area.lineTo(x0, y(pt.speed))
            else:
                stroke.lineTo(x0, y(pt.speed))
                area.lineTo(x0, y(pt.speed))
            stroke.lineTo(x1, y(pt.speed))
            area.lineTo(x1, y(pt.speed))
        area.lineTo(spans[-1][2], top + h)
        area.closeSubpath()
        p.setBrush(theme.color("accent", 0.16))
        p.drawPath(area)
        if self.position is not None:
            done = x(self.position)
            p.save()
            p.setClipRect(QRectF(left, 0, max(0.0, done - left), self.height()))
            p.setBrush(theme.color("accent", 0.5))
            p.drawPath(area)
            p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.color("accent"), 2 if compact else 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(stroke)

        if not compact:
            p.setFont(bold)
            p.setPen(fg)
            for pt, x0, x1 in spans:
                if x1 - x0 > 26:
                    p.drawText(QPointF(x0 + 5, y(pt.speed) - 5), f"{pt.speed:g}")
            p.setFont(small)
            p.setPen(muted)
            p.drawText(QRectF(left, self.height() - 16, 60, 14), Qt.AlignmentFlag.AlignLeft, "0")
            p.drawText(QRectF(self.width() - right - 100, self.height() - 16, 100, 14), Qt.AlignmentFlag.AlignRight, f"{total:g} {plan.total_unit}")

        if self.position is not None:
            mx = x(self.position)
            p.setPen(QPen(fg, 2))
            p.drawLine(QPointF(mx, top - 4), QPointF(mx, top + h))
            p.setBrush(fg)
            p.drawEllipse(QPointF(mx, top - 4), 4, 4)


# --- Toasts ------------------------------------------------------------------------------------

class Toasts(QWidget):
    """Short messages that fade after a few seconds, stacked at the bottom of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)
        self.items: list[QFrame] = []
        self.bottom_margin = 84
        self.hide()

    def show_message(self, text: str, seconds: float = 5.0) -> None:
        frame = QFrame()
        frame.setObjectName("toast")
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 10, 14, 10)
        message = QLabel(text)
        message.setWordWrap(True)
        row.addWidget(message)
        self.layout_.addWidget(frame)
        self.items.append(frame)
        while len(self.items) > 3:
            self._drop(self.items[0])
        self.place()
        self.show()
        self.raise_()
        QTimer.singleShot(int(seconds * 1000), lambda f=frame: self._drop(f))

    def _drop(self, frame: QFrame) -> None:
        if frame in self.items:
            self.items.remove(frame)
            self.layout_.removeWidget(frame)
            frame.setParent(None)
            frame.deleteLater()
            if not self.items:
                self.hide()
            else:
                self.place()

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(460, max(220, parent.width() - 32))
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((parent.width() - width) // 2, parent.height() - self.height() - self.bottom_margin)


# --- Bar chart ---------------------------------------------------------------------------------

class BarChart(QWidget):
    """One bar per day or week, with the value on top of each when there are few of them."""

    def __init__(self, color: str = "accent", decimals: int = 1, minimum_top: float = 1.0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.color_name = color
        self.decimals = decimals
        self.minimum_top = minimum_top  # The scale never shrinks below this, so a tiny week isn't drawn full height
        self.bars: list[tuple[str, float, str]] = []  # (axis label, value, tooltip)
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_bars(self, bars: list[tuple[str, float, str]]) -> None:
        self.bars = bars
        self.update()

    def _layout(self) -> tuple[float, float, float, float]:
        top, bottom = 16.0, 20.0
        return 4.0, 4.0, top, bottom

    def event(self, e) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QToolTip

        if e.type() == QEvent.Type.ToolTip and self.bars:
            left, right, _, _ = self._layout()
            width = (self.width() - left - right) / len(self.bars)
            index = int((e.pos().x() - left) // width)
            if 0 <= index < len(self.bars):
                QToolTip.showText(e.globalPos(), self.bars[index][2], self)
                return True
        return super().event(e)

    def paintEvent(self, _event) -> None:
        if not self.bars:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = self._layout()
        count = len(self.bars)
        slot = (self.width() - left - right) / count
        base = self.height() - bottom
        high = max(self.minimum_top, *(value for _, value, _ in self.bars))
        small = QFont(self.font())
        small.setPixelSize(10)
        bold = QFont(small)
        bold.setBold(True)
        p.setPen(QPen(theme.color("line"), 1))
        p.drawLine(QPointF(left, base), QPointF(self.width() - right, base))
        step = max(1, -(-count // (7 if count <= 7 else 8)))
        fill = theme.color(self.color_name)
        for i, (name, value, _tip) in enumerate(self.bars):
            width = max(2.0, slot * 0.7)
            x = left + i * slot + slot * 0.15
            height = value / high * (base - top)
            if value > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(QRectF(x, base - max(2.0, height), width, max(2.0, height)), 2, 2)
                if count <= 10:
                    p.setFont(bold)
                    p.setPen(theme.color("fg"))
                    p.drawText(QRectF(x - 10, base - height - 15, width + 20, 14), Qt.AlignmentFlag.AlignCenter,
                               f"{value:.{self.decimals}f}")
            if i % step == 0:
                p.setFont(small)
                p.setPen(theme.color("muted"))
                p.drawText(QRectF(x - slot, base + 3, width + 2 * slot, 14), Qt.AlignmentFlag.AlignCenter, name)
