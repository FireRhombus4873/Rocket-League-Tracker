from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QIcon

# --------------------------------------------------------------------------
# Signal bridge: lets background threads safely update the Qt UI
# --------------------------------------------------------------------------
class UISignals(QObject):
    players_updated  = pyqtSignal(list, dict)    # list of player dicts, team_info dict
    record_updated   = pyqtSignal(int, int)       # wins, losses
    history_updated  = pyqtSignal(list)           # list of match history dicts
    status_changed   = pyqtSignal(str)            # status bar text
    session_prompt   = pyqtSignal(int)            # last session number, triggers dialog

# --------------------------------------------------------------------------
# Colour palette
# --------------------------------------------------------------------------
BG_DARK   = "#0d1117"
BG_CARD   = "#161b22"
BG_TABLE  = "#1c2128"
ACCENT    = "#ff4655"   # Rocket League-ish red
ACCENT2   = "#00aaff"   # blue for team 1
TEXT      = "#e6edf3"
SUBTEXT   = "#7d8590"
WIN_CLR   = "#3fb950"
LOSS_CLR  = "#f85149"
BORDER    = "#30363d"

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
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        header = QLabel(title.upper())
        header.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {SUBTEXT}; background: transparent; border: none;")
        layout.addWidget(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(6)
        layout.addLayout(self.content_layout)

# --------------------------------------------------------------------------
# Main Window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rocket League Tracker")
        self.setMinimumSize(1000, 750)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "RocketLeagueTracker.ico")))
        self._apply_styles()
        self._build_ui()

        # Public signal bus – wired up by main.py
        self.signals = UISignals()
        self.signals.players_updated.connect(self._on_players_updated)
        self.signals.record_updated.connect(self._on_record_updated)
        self.signals.history_updated.connect(self._on_history_updated)
        self.signals.status_changed.connect(self._on_status_changed)

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------
    def _apply_styles(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {BG_DARK};
                color: {TEXT};
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }}
            QTableWidget {{
                background-color: {BG_TABLE};
                border: 1px solid {BORDER};
                border-radius: 6px;
                gridline-color: {BORDER};
                color: {TEXT};
            }}
            QTableWidget::item {{ padding: 6px 10px; }}
            QTableWidget::item:selected {{
                background-color: #264f78;
            }}
            QHeaderView::section {{
                background-color: {BG_CARD};
                color: {SUBTEXT};
                border: none;
                border-bottom: 1px solid {BORDER};
                padding: 6px 10px;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            QScrollBar:vertical {{
                background: {BG_DARK};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER};
                border-radius: 4px;
            }}
            QLabel {{ background: transparent; }}
            QStatusBar {{ color: {SUBTEXT}; font-size: 11px; }}
        """)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # ── Header ──────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        title = QLabel("ROCKET LEAGUE TRACKER")
        title.setFont(QFont("Courier New", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ACCENT}; letter-spacing: 3px;")
        header_row.addWidget(title)
        header_row.addStretch()

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {SUBTEXT}; font-size: 18px;")
        self._status_label = QLabel("Waiting for game...")
        self._status_label.setStyleSheet(f"color: {SUBTEXT};")
        header_row.addWidget(self._status_dot)
        header_row.addWidget(self._status_label)
        root.addLayout(header_row)

        # ── Win / Loss row ───────────────────────────────────────────────
        record_row = QHBoxLayout()
        record_row.setSpacing(12)

        self._wins_card   = self._stat_card("WINS",   "0", WIN_CLR)
        self._losses_card = self._stat_card("LOSSES", "0", LOSS_CLR)
        self._ratio_card  = self._stat_card("RATIO",  "—", ACCENT2)
        self._streak_card = self._stat_card("STREAK", "0", SUBTEXT)

        record_row.addWidget(self._wins_card)
        record_row.addWidget(self._losses_card)
        record_row.addWidget(self._ratio_card)
        record_row.addWidget(self._streak_card)
        record_row.addStretch()
        root.addLayout(record_row)

        # ── Middle split: current players | history ──────────────────────
        middle = QHBoxLayout()
        middle.setSpacing(16)

        # Current match players
        players_card = Card("Current Match — Players")
        self._players_table = self._make_table(["Name", "Platform", "Team"])
        players_card.content_layout.addWidget(self._players_table)
        players_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        middle.addWidget(players_card, stretch=3)

        # Opponent history
        history_card = Card("Opponent History")
        self._history_table = self._make_table(["Session", "Date", "Result", "Opponents", "Teammates"])
        history_card.content_layout.addWidget(self._history_table)
        history_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        middle.addWidget(history_card, stretch=4)

        root.addLayout(middle, stretch=1)

        # Status bar
        self.statusBar().showMessage("Not connected")

    def _stat_card(self, label: str, value: str, colour: str) -> QFrame:
        frame = QFrame()
        frame.setFixedSize(120, 80)
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        lbl = QLabel(label)
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {SUBTEXT};")
        layout.addWidget(lbl)

        val = QLabel(value)
        val.setFont(QFont("Courier New", 26, QFont.Weight.Bold))
        val.setStyleSheet(f"color: {colour};")
        layout.addWidget(val)

        # store reference to the value label so we can update it
        frame._value_label = val
        return frame

    def _make_table(self, headers: list) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(t.styleSheet() + f"""
            QTableWidget {{ alternate-background-color: #1a2030; }}
        """)
        return t

    # ------------------------------------------------------------------
    # Slot handlers (called on main thread via signals)
    # ------------------------------------------------------------------
    def _on_players_updated(self, players: list, team_info: dict):
        t = self._players_table
        players = sorted(players, key=lambda p: p['team'])
        t.setRowCount(0)
        for p in players:
            row = t.rowCount()
            t.insertRow(row)

            name_item = QTableWidgetItem(p.get("name", "Unknown"))
            name_item.setForeground(QColor(TEXT))

            plat = p.get("platform", "Unknown")
            plat_item = QTableWidgetItem(f"{platform_icon(plat)}  {plat.capitalize()}")
            plat_item.setForeground(QColor(SUBTEXT))

            team_num = p.get("team", -1)
            info     = team_info.get(team_num, {})
            # Use the team's primary colour for the label; fall back to defaults
            team_colour = info.get("color") or (ACCENT2 if team_num == 0 else ACCENT)
            team_label  = info.get("name") or (f"Team {team_num + 1}" if team_num >= 0 else "—")
            team_item   = QTableWidgetItem(team_label)
            team_item.setForeground(QColor(team_colour))

            t.setItem(row, 0, name_item)
            t.setItem(row, 1, plat_item)
            t.setItem(row, 2, team_item)

    def _on_record_updated(self, wins: int, losses: int):
        self._wins_card._value_label.setText(str(wins))
        self._losses_card._value_label.setText(str(losses))
        total = wins + losses
        ratio = f"{wins/total:.0%}" if total > 0 else "—"
        self._ratio_card._value_label.setText(ratio)

    def _on_history_updated(self, history: list):
        """history is a list of match entry dicts, most recent first."""
        self._update_streak(history)
        t = self._history_table
        t.setRowCount(0)
        for entry in history:
            row = t.rowCount()
            t.insertRow(row)

            session_num = str(entry.get("sessionNum", "—"))
            date_str    = entry.get("date", "")[:10]
            result      = entry.get("result", "?").upper()

            def fmt_players(players):
                parts = []
                for p in players:
                    name  = p.get("name", "?")
                    score = p.get("score", 0)
                    goals = p.get("goals", 0)
                    parts.append(f"{name} (Sc:{score} G:{goals})")
                return ",  ".join(parts) if parts else "—"

            opp_str  = fmt_players(entry.get("opponents", []))
            team_str = fmt_players(entry.get("teammates", []))

            session_item = QTableWidgetItem(session_num)
            date_item    = QTableWidgetItem(date_str)
            result_item  = QTableWidgetItem(result)
            opp_item     = QTableWidgetItem(opp_str)
            team_item    = QTableWidgetItem(team_str)

            session_item.setForeground(QColor(ACCENT2))
            date_item.setForeground(QColor(SUBTEXT))
            result_item.setForeground(QColor(WIN_CLR if result == "WIN" else LOSS_CLR))
            opp_item.setForeground(QColor(TEXT))
            team_item.setForeground(QColor(SUBTEXT))

            t.setItem(row, 0, session_item)
            t.setItem(row, 1, date_item)
            t.setItem(row, 2, result_item)
            t.setItem(row, 3, opp_item)
            t.setItem(row, 4, team_item)

    def _update_streak(self, history: list):
        streak = 0
        if history:
            current_session = history[0].get("sessionNum")
            session_history = [e for e in history if e.get("sessionNum") == current_session]
            anchor = session_history[0].get("result", "").upper()
            for entry in session_history:
                result = entry.get("result", "").upper()
                if result != anchor:
                    break
                streak += 1 if anchor == "WIN" else -1

        lbl = self._streak_card._value_label
        lbl.setText(str(streak))
        if streak > 0:
            lbl.setStyleSheet(f"color: {WIN_CLR};")
        elif streak < 0:
            lbl.setStyleSheet(f"color: {LOSS_CLR};")
        else:
            lbl.setStyleSheet(f"color: {SUBTEXT};")

    def _on_status_changed(self, message: str):
        connected = "connected" in message.lower()
        self._status_dot.setStyleSheet(
            f"color: {WIN_CLR if connected else SUBTEXT}; font-size: 18px;"
        )
        self._status_label.setText(message)
        self._status_label.setStyleSheet(
            f"color: {WIN_CLR if connected else SUBTEXT};"
        )
        self.statusBar().showMessage(message)