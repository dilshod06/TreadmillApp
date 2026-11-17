"""Tray and window icons, drawn on the fly so the tray can show live state."""
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from ..controller import CONNECTED, CONNECTING, RECONNECTING, View
from ..protocol import State

COLORS = {
    "running": "#2e9d4f",
    "starting": "#d98f00",
    "paused": "#d98f00",
    "stopped": "#6b7280",
    "connecting": "#3b82f6",
    "offline": "#4b5563",
}
SIZES = (16, 20, 24, 32, 48, 64)


def icon_kind(view: Optional[View]) -> str:
    if view is None or view.conn_state == "idle" or view.conn_state == "failed":
        return "offline"
    s = view.status
    if s is None or view.conn_state in (CONNECTING, RECONNECTING):
        return "connecting"
    return {State.RUNNING: "running", State.STARTING: "starting", State.PAUSED: "paused"}.get(s.state, "stopped")


def icon_text(view: Optional[View]) -> str:
    kind = icon_kind(view)
    if kind == "running":
        return f"{view.status.to_display(view.status.speed):.1f}"
    return {"starting": "…", "paused": "II", "stopped": "0", "connecting": "…"}.get(kind, "–")


def make_icon(kind: str = "offline", text: str = "PP") -> QIcon:
    """A rounded square in the state's colour with `text` (usually the speed)."""
    icon = QIcon()
    for size in SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS.get(kind, COLORS["offline"])))
        radius = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(6, round(size * (0.72 if len(text) <= 2 else 0.58 if len(text) == 3 else 0.46))))
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def make_view_icon(view: Optional[View]) -> QIcon:
    return make_icon(icon_kind(view), icon_text(view))
