"""
Small reusable UI building blocks shared across the window, the tabs and the
dialogs: a soft drop-shadow helper, platform-icon lookup, the elevated Card
frame, the stat-card / table / placeholder factories, and the cursor event
filter used by clickable table name columns.
"""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QGraphicsDropShadowEffect,
    QWidget, QTableWidget, QHeaderView,
)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import QObject, Qt

from .theme import BG_ALT, BG_CARD, BORDER_SOFT, FAINT, SUBTEXT


def soft_shadow(widget, *, blur=28, y_offset=6, alpha=90):
    """Attach a subtle drop shadow so surfaces lift off the background.
    Qt stylesheets can't do box-shadow, so we use a graphics effect."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


PLATFORM_ICONS = {
    "steam":   "🖥",
    "epic":    "🎮",
    "psn":     "🎮",
    "xbox":    "🎮",
    "Unknown": "❓",
}

def platform_icon(platform: str) -> str:
    return PLATFORM_ICONS.get(platform.lower(), "🎮")


# --------------------------------------------------------------------------
# Reusable card widget
# --------------------------------------------------------------------------
class Card(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SOFT};
                border-radius: 14px;
            }}
        """)
        soft_shadow(self, blur=32, y_offset=8, alpha=70)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(14)

        header = QLabel(title.upper())
        header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        header.setStyleSheet(
            f"color: {SUBTEXT}; background: transparent; border: none; "
            f"letter-spacing: 1.5px;"
        )
        layout.addWidget(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(8)
        layout.addLayout(self.content_layout)


# --------------------------------------------------------------------------
# Factories shared by the tab modules
# --------------------------------------------------------------------------
def stat_card(label: str, value: str, colour: str, caption: str = "",
              *, value_pt: int = 30, width: int = 158) -> QFrame:
    """A small elevated number card. `value_pt` / `width` are keyword-only with
    the Tracker tab's values as defaults — the Analytics cards need wider boxes
    and smaller type because "104W / 71L" doesn't fit at 30pt in 158px.

    ⚠️ Cross-module contract: the returned frame carries `_value_label` and
    `_caption_label` so the tab slots can update the text in place. Every
    caller that reads `_caption_label` must pass a `caption` — it is None
    otherwise, and writing to it would raise AttributeError.
    """
    frame = QFrame()
    frame.setObjectName("statCard")
    frame.setFixedSize(width, 104)
    frame.setStyleSheet(f"""
        QFrame#statCard {{
            background-color: {BG_CARD};
            border: 1px solid {BORDER_SOFT};
            border-radius: 12px;
        }}
    """)
    soft_shadow(frame, blur=22, y_offset=5, alpha=55)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(0)

    lbl = QLabel(label)
    lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
    lbl.setStyleSheet(f"color: {SUBTEXT}; letter-spacing: 1.3px;")
    layout.addWidget(lbl)

    layout.addStretch()

    val = QLabel(value)
    val.setFont(QFont("Segoe UI", value_pt, QFont.Weight.DemiBold))
    val.setStyleSheet(f"color: {colour};")
    layout.addWidget(val)

    cap = None
    if caption:
        cap = QLabel(caption)
        cap.setFont(QFont("Segoe UI", 8))
        cap.setStyleSheet(f"color: {FAINT}; letter-spacing: 0.3px;")
        layout.addWidget(cap)

    # store references to the mutable labels so slots can update them
    frame._value_label   = val
    frame._caption_label = cap
    return frame


def make_table(headers: list) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setHighlightSections(False)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(40)
    t.setShowGrid(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    t.setStyleSheet(t.styleSheet() + f"""
        QTableWidget {{ alternate-background-color: {BG_ALT}; }}
    """)
    return t


def make_placeholder(text: str, sub: str = "") -> QWidget:
    """A centred, muted idle/empty state for the live-match panels."""
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 24, 0, 24)
    lay.setSpacing(6)
    lay.addStretch()
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFont(QFont("Segoe UI", 12))
    lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent;")
    lay.addWidget(lbl)
    if sub:
        s = QLabel(sub)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setFont(QFont("Segoe UI", 9))
        s.setStyleSheet(f"color: {FAINT}; background: transparent;")
        lay.addWidget(s)
    lay.addStretch()
    return w


# --------------------------------------------------------------------------
# Mouse cursor change for a table's clickable name column
# --------------------------------------------------------------------------
class NameColumnCursor(QObject):
    def __init__(self, table, name_col=0):
        super().__init__(table)
        self.table = table
        self.name_col = name_col

    def eventFilter(self, obj, event):
        if event.type() == event.Type.MouseMove:
            index = self.table.indexAt(event.pos())
            if index.isValid() and index.column() == self.name_col:
                self.table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        return False
