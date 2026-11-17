"""Colours and the stylesheet. One token set per theme, chosen from the system's
light or dark setting, so the window, the cards and the custom-drawn widgets
(the plan chart, the switches) always agree."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

LIGHT = {
    "win": "#f5f7f9", "surface": "#ffffff", "surface2": "#eef1f5", "line": "#d9e0e7",
    "fg": "#111820", "muted": "#566170",
    "accent": "#2f5bea", "accent_soft": "rgba(47,91,234,0.13)", "on_accent": "#ffffff",
    "run": "#168a47", "on_run": "#ffffff", "run_soft": "rgba(22,138,71,0.13)",
    "pause": "#b36500", "on_pause": "#ffffff", "pause_soft": "rgba(179,101,0,0.14)",
    "danger": "#c93a22", "danger_soft": "rgba(201,58,34,0.12)",
}
DARK = {
    "win": "#12171d", "surface": "#181e26", "surface2": "#20272f", "line": "#2b333d",
    "fg": "#e9edf2", "muted": "#98a4b1",
    "accent": "#7d98ff", "accent_soft": "rgba(125,152,255,0.17)", "on_accent": "#0b1233",
    "run": "#38c978", "on_run": "#04210f", "run_soft": "rgba(56,201,120,0.15)",
    "pause": "#f0a830", "on_pause": "#2a1800", "pause_soft": "rgba(240,168,48,0.16)",
    "danger": "#ff7a63", "danger_soft": "rgba(255,122,99,0.16)",
}

_current = dict(LIGHT)


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        value = scheme()
        if value == Qt.ColorScheme.Dark:
            return True
        if value == Qt.ColorScheme.Light:
            return False
    return QGuiApplication.palette().window().color().lightness() < 128


def tokens() -> dict:
    """The colours in use now."""
    return _current


def color(name: str, alpha: float = 1.0) -> QColor:
    """A token as a QColor, for painting. Soft tokens carry their own alpha."""
    value = _current[name]
    if value.startswith("rgba"):
        r, g, b, a = (float(x) for x in value[5:-1].split(","))
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(a * alpha)
        return c
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def use(dark: bool) -> str:
    """Selects a theme and returns its stylesheet."""
    _current.clear()
    _current.update(DARK if dark else LIGHT)
    css = STYLE
    for key, value in _current.items():
        css = css.replace(f"@{key}@", value)
    return css


STYLE = """
QMainWindow, QDialog { background: @win@; }
QWidget { color: @fg@; }
QWidget#page { background: transparent; }
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @line@; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: @fg@; color: @win@; border: 0; padding: 4px 6px; }

QFrame#topbar { background: @surface@; border: none; border-bottom: 1px solid @line@; }
QFrame#mini { background: @surface@; border: none; border-top: 1px solid @line@; }
QFrame#card { background: @surface@; border: 1px solid @line@; border-radius: 14px; }
QFrame#seg { background: @surface2@; border: none; border-radius: 11px; }
QFrame#divider { background: @line@; border: none; }

QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: @muted@; padding: 7px 22px; margin: 3px; border-radius: 9px; font-weight: 600; }
QTabBar::tab:selected { background: @surface@; color: @fg@; }
QTabBar::tab:hover:!selected { color: @fg@; }

QPushButton { background: @surface@; color: @fg@; border: 1px solid @line@; border-radius: 10px; padding: 8px 16px; font-weight: 600; }
QPushButton:hover { background: @surface2@; }
QPushButton:pressed { background: @line@; }
QPushButton:disabled { color: @muted@; background: @win@; border-color: @line@; }
QPushButton[big="true"] { font-size: 18px; padding: 16px 20px; border-radius: 14px; }
QPushButton[kind="go"] { background: @run@; border-color: @run@; color: @on_run@; }
QPushButton[kind="go"]:hover { background: @run@; }
QPushButton[kind="hold"] { background: @pause@; border-color: @pause@; color: @on_pause@; }
QPushButton[kind="hold"]:hover { background: @pause@; }
QPushButton[kind="accent"] { background: @accent@; border-color: @accent@; color: @on_accent@; }
QPushButton[kind="accent"]:hover { background: @accent@; }
QPushButton[kind="go"]:disabled, QPushButton[kind="hold"]:disabled, QPushButton[kind="accent"]:disabled {
    background: @surface2@; border-color: @line@; color: @muted@; }
QPushButton[kind="danger"] { color: @danger@; }
QPushButton[kind="link"] { border: none; background: transparent; color: @accent@; padding: 2px 4px; }
QPushButton#round { border-radius: 28px; min-width: 56px; max-width: 56px; min-height: 56px; max-height: 56px;
    padding: 0; font-size: 26px; font-weight: 400; }
QPushButton#roundSmall { border-radius: 20px; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px;
    padding: 0; font-size: 20px; font-weight: 400; }
QPushButton[pill="true"] { border-radius: 14px; padding: 4px 4px; font-size: 13px; min-width: 40px; }
QPushButton[pill="true"][active="true"] { background: @accent@; border-color: @accent@; color: @on_accent@; }
QPushButton#planstrip { background: @accent_soft@; border: 1px solid @accent@; border-radius: 12px; text-align: left;
    padding: 10px 14px; font-weight: 500; }
QPushButton#miniText { border: none; background: transparent; text-align: left; padding: 0; }
QToolButton#gear { border: none; border-radius: 10px; padding: 6px 9px; font-size: 18px; color: @muted@; background: transparent; }
QToolButton#gear:hover { background: @surface2@; }
QToolButton#gear:checked { background: @accent_soft@; color: @accent@; }
QPushButton#conn { border: none; background: transparent; color: @muted@; text-align: left; padding: 6px 8px; font-weight: 500; }
QPushButton#conn:hover { background: @surface2@; }

QLabel { background: transparent; }
QLabel:disabled { color: @muted@; }
QLabel[muted="true"] { color: @muted@; }
QLabel[caption="true"] { color: @muted@; font-size: 11px; font-weight: 700; }
QLabel[accent="true"] { color: @accent@; font-weight: 700; }
QLabel[tone="floors"] { color: @pause@; }
QLabel[tone="danger"] { color: @danger@; }
QLabel#chip { padding: 2px 10px; border-radius: 10px; background: @surface2@; color: @muted@; font-weight: 600; font-size: 12px; }
QLabel#chip[tone="ok"] { background: @run_soft@; color: @run@; }
QLabel#chip[tone="warn"] { background: @danger_soft@; color: @danger@; }
QLabel#state { font-weight: 700; font-size: 12px; color: @muted@; }
QLabel#state[kind="running"], QLabel#stateDot[kind="running"] { color: @run@; }
QLabel#state[kind="paused"], QLabel#stateDot[kind="paused"] { color: @pause@; }
QLabel#stateDot { color: @muted@; font-size: 12px; }
QLabel#note { color: @pause@; background: @pause_soft@; border-radius: 10px; padding: 8px 12px; }
QLabel#info { background: @surface2@; border-radius: 10px; padding: 8px 12px; }
QLabel#hint { color: @muted@; font-size: 12px; }

QFrame#banner { border-radius: 10px; }
QFrame#banner[tone="cool"] { background: @pause_soft@; border: 1px solid @pause@; }
QFrame#banner[tone="away"] { background: @danger_soft@; border: 1px solid @danger@; }
QFrame#banner[tone="cool"] QLabel { color: @pause@; }
QFrame#banner[tone="away"] QLabel { color: @danger@; }
QFrame#toast { background: @fg@; border-radius: 12px; }
QFrame#toast QLabel { color: @win@; }

QProgressBar { background: @surface2@; border: none; border-radius: 5px; min-height: 10px; max-height: 10px; text-align: center; }
QProgressBar::chunk { background: @accent@; border-radius: 5px; }

QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox { background: @surface@; color: @fg@; border: 1px solid @line@;
    border-radius: 9px; padding: 6px 8px; selection-background-color: @accent@; selection-color: @on_accent@; }
QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled { color: @muted@; background: @surface2@; }
QComboBox QAbstractItemView { background: @surface@; color: @fg@; border: 1px solid @line@; selection-background-color: @accent@; selection-color: @on_accent@; }
QCheckBox { spacing: 8px; }
QTableWidget { background: @surface@; border: 1px solid @line@; border-radius: 10px; gridline-color: @line@; }
QHeaderView::section { background: @surface2@; color: @muted@; border: none; padding: 5px 6px; font-weight: 600; }
QListWidget { background: @surface@; border: 1px solid @line@; border-radius: 10px; }
QListWidget::item:selected { background: @accent@; color: @on_accent@; }
QMenu { background: @surface@; color: @fg@; border: 1px solid @line@; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: @accent_soft@; }
QMessageBox { background: @win@; }
"""
