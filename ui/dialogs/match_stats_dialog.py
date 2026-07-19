"""
MatchStatsDialog — opened by clicking a row in the match-history table.

Shows per-player stats for that match. Each player's name cell is a clickable
link that opens their Rocket League Tracker profile in the default browser.
"""
import webbrowser
from urllib.parse import quote

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
)
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtCore import Qt

from ..theme import (
    BG_DARK, BG_CARD, BG_TABLE, BG_ALT, BG_HOVER, TEXT, SUBTEXT, FAINT,
    ACCENT, ACCENT2, WIN_CLR, LOSS_CLR, BORDER, BORDER_SOFT, SELECT_BG, FONT_UI,
)
from ..widgets import platform_icon, NameColumnCursor


# Our internal platform strings -> rocketleague.tracker.network profile slugs.
# Module-scoped (rather than nested in the dialog) so it can be unit-tested.
_TRACKER_PLATFORM_SLUGS = {
    "epic":        "epic",
    "steam":       "steam",
    "psn":         "psn",
    "ps4":         "psn",
    "ps5":         "psn",
    "playstation": "psn",
    "xbl":         "xbl",
    "xbox":        "xbl",
    "xboxone":     "xbl",
    "switch":      "switch",
}


def tracker_platform_slug(platform: str) -> str:
    """Return the tracker.network profile slug for one of our platform strings,
    falling back to the lower-cased input for anything unmapped."""
    return _TRACKER_PLATFORM_SLUGS.get(platform.lower(), platform.lower())


class MatchStatsDialog(QDialog):
    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match Stats")
        self.setMinimumSize(1022, 428)
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {BG_DARK};
                color: {TEXT};
                font-family: {FONT_UI};
                font-size: 13px;
            }}
            QTableWidget {{
                background-color: {BG_TABLE};
                border: 1px solid {BORDER_SOFT};
                border-radius: 12px;
                gridline-color: transparent;
                color: {TEXT};
            }}
            QTableWidget::item {{ padding: 9px 12px; border: none; }}
            QTableWidget::item:selected {{ background-color: {SELECT_BG}; }}
            QHeaderView::section {{
                background-color: transparent;
                color: {FAINT};
                border: none;
                border-bottom: 1px solid {BORDER_SOFT};
                padding: 10px 12px;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }}
            QPushButton {{
                background-color: {BG_CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {SUBTEXT}; }}
        """)

        result  = entry.get("result", "?").upper()
        date    = entry.get("date", "")[:10]
        session = entry.get("sessionNum", "?")
        result_colour = WIN_CLR if result == "WIN" else LOSS_CLR

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(18)

        # ── Header ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)
        title_lbl = QLabel(f"Session {session}")
        title_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.DemiBold))
        title_lbl.setStyleSheet(f"color: {TEXT};")
        date_lbl = QLabel(date)
        date_lbl.setFont(QFont("Segoe UI", 12))
        date_lbl.setStyleSheet(f"color: {SUBTEXT};")
        result_lbl = QLabel(result)
        result_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        result_lbl.setStyleSheet(
            f"color: {result_colour}; background-color: {result_colour}22; "
            f"border-radius: 11px; padding: 5px 16px; letter-spacing: 1px;"
        )
        header.addWidget(title_lbl)
        header.addWidget(date_lbl)
        header.addStretch()
        header.addWidget(result_lbl)
        layout.addLayout(header)

        # ── Players table ────────────────────────────────────────────────
        cols = ["Name", "Platform", "Side", "Score", "Goals", "Shots",
                "Assists", "Saves", "Touches", "Car Touches", "Demos"]
        table = QTableWidget(0, len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setShowGrid(False)
        table.setStyleSheet(table.styleSheet() + f"QTableWidget {{ alternate-background-color: {BG_ALT}; }}")

        def tracker_url(platform: str, name: str) -> str:
            slug = tracker_platform_slug(platform)
            encoded_name = quote(name)
            return f"https://rocketleague.tracker.network/rocket-league/profile/{slug}/{encoded_name}/overview"

        def add_player(player: dict, side: str, side_colour: str):
            row = table.rowCount()
            table.insertRow(row)
            plat = player.get("platform", "Unknown")
            values = [
                (player.get("name", "?"),              TEXT),
                (f"{platform_icon(plat)}  {plat.capitalize()}", SUBTEXT),
                (side,                                  side_colour),
                (str(player.get("score",      0)),     TEXT),
                (str(player.get("goals",      0)),     TEXT),
                (str(player.get("shots",      0)),     TEXT),
                (str(player.get("assists",    0)),     TEXT),
                (str(player.get("saves",      0)),     TEXT),
                (str(player.get("touches",    0)),     TEXT),
                (str(player.get("carTouches", 0)),     TEXT),
                (str(player.get("demos",      0)),     TEXT),
            ]
            for col, (text, colour) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(colour))
                if col == 0:
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                    item.setForeground(QColor(side_colour))
                    item.setData(Qt.ItemDataRole.UserRole, player)
                    item.setToolTip("Click to open Rocket League Tracker profile")
                table.setItem(row, col, item)

        for p in entry.get("teammates", []):
            add_player(p, "Teammate", ACCENT2)
        for p in entry.get("opponents", []):
            add_player(p, "Opponent", ACCENT)

        def on_cell_clicked(row, column):
            if column != 0:
                return
            item = table.item(row, 0)
            player = item.data(Qt.ItemDataRole.UserRole)
            if not player:
                return
            platform = player.get("platform", "")
            name = player.get("name", "")
            if not platform or not name:
                return
            if platform.lower() == "steam":
                name = player.get("id").split("|")[1]  # use Steam ID for tracker URL
            url = tracker_url(platform, name)
            webbrowser.open(url)

        table.cellClicked.connect(on_cell_clicked)
        cursor_filter = NameColumnCursor(table)
        table.viewport().installEventFilter(cursor_filter)
        table.setMouseTracking(True)
        table.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(table)

        # ── Close button ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
