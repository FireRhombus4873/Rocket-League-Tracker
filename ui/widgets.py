"""
Small reusable UI building blocks shared across the window and dialogs:
a soft drop-shadow helper, platform-icon lookup, the elevated Card frame,
and the cursor event filter used by clickable table name columns.
"""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QGraphicsDropShadowEffect
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import QObject, Qt

from .theme import BG_CARD, BORDER_SOFT, SUBTEXT


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
