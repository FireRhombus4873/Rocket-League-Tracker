from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy, QDialog, QPushButton, QCheckBox,
    QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QIcon, QAction

# --------------------------------------------------------------------------
# Signal bridge: lets background threads safely update the Qt UI
# --------------------------------------------------------------------------
class UISignals(QObject):
    players_updated      = pyqtSignal(list, dict)  # list of player dicts, team_info dict
    record_updated       = pyqtSignal(int, int)    # wins, losses
    history_updated      = pyqtSignal(list, int)   # list of match history dicts, current session num
    status_changed       = pyqtSignal(str)         # status bar text
    session_prompt       = pyqtSignal(int)         # last session number, triggers dialog
    new_session_requested = pyqtSignal()           # user clicked "New Session"
    game_started         = pyqtSignal()            # Rocket League process detected

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
# Match Stats Dialog
# --------------------------------------------------------------------------
class MatchStatsDialog(QDialog):
    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match Stats")
        self.setMinimumSize(1000, 420)
        self.setStyleSheet(f"""
            QDialog, QWidget {{
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
            QTableWidget::item:selected {{ background-color: #264f78; }}
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
            QPushButton {{
                background-color: {BG_CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: #21262d; }}
        """)

        result  = entry.get("result", "?").upper()
        date    = entry.get("date", "")[:10]
        session = entry.get("sessionNum", "?")
        result_colour = WIN_CLR if result == "WIN" else LOSS_CLR

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # ── Header ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        title_lbl = QLabel(f"SESSION {session}  ·  {date}")
        title_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {ACCENT2};")
        result_lbl = QLabel(result)
        result_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        result_lbl.setStyleSheet(f"color: {result_colour};")
        header.addWidget(title_lbl)
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
        table.setStyleSheet(table.styleSheet() + f"QTableWidget {{ alternate-background-color: #1a2030; }}")

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
                table.setItem(row, col, item)

        for p in entry.get("teammates", []):
            add_player(p, "Teammate", ACCENT2)
        for p in entry.get("opponents", []):
            add_player(p, "Opponent", ACCENT)

        layout.addWidget(table)

        # ── Close button ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


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
        self.signals.game_started.connect(self._on_game_started)

        self._history_entries: list = []
        self._history_table.itemClicked.connect(self._on_history_row_clicked)

        self._setup_tray()

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------
    def _setup_tray(self):
        icon = QIcon(str(Path(__file__).parent / "assets" / "RocketLeagueTracker.ico"))
        self._tray = QSystemTrayIcon(icon, parent=self)
        self._tray.setToolTip("Rocket League Tracker")

        menu = QMenu()
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self._show_from_tray)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _show_from_tray(self):
        self.showNormal()
        self.activateWindow()

    def _quit_app(self):
        self._tray.hide()
        import sys
        sys.exit(0)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_from_tray()

    def _on_game_started(self):
        self._show_from_tray()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Rocket League Tracker",
            "Still running in the background. Will reappear when Rocket League starts.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

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

        self._new_session_btn = QPushButton("＋  NEW SESSION")
        self._new_session_btn.setFixedHeight(40)
        self._new_session_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                color: {ACCENT2};
                border: 1px solid {ACCENT2};
                border-radius: 6px;
                padding: 0 16px;
                font-family: "Courier New";
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: #0d2030;
                border-color: {TEXT};
                color: {TEXT};
            }}
            QPushButton:pressed {{ background-color: #0a1820; }}
        """)
        self._new_session_btn.clicked.connect(
            lambda: self.signals.new_session_requested.emit()
        )
        record_row.addWidget(self._new_session_btn)

        self._pause_tracking_cb = QCheckBox("PAUSE TRACKING")
        self._pause_tracking_cb.setFixedHeight(40)
        self._pause_tracking_cb.setToolTip(
            "When enabled, finished matches are not saved to history.\n"
            "Players still appear in the Current Match panel."
        )
        self._pause_tracking_cb.setStyleSheet(f"""
            QCheckBox {{
                color: {SUBTEXT};
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 0 14px;
                font-family: "Courier New";
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                spacing: 8px;
            }}
            QCheckBox:hover {{
                border-color: {ACCENT};
                color: {TEXT};
            }}
            QCheckBox:checked {{
                color: {ACCENT};
                border-color: {ACCENT};
                background-color: #2a1014;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {BORDER};
                border-radius: 3px;
                background-color: {BG_DARK};
            }}
            QCheckBox::indicator:hover {{
                border-color: {ACCENT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT};
                border-color: {ACCENT};
            }}
        """)
        record_row.addWidget(self._pause_tracking_cb)
        root.addLayout(record_row)

        # ── Middle split: current players | history ──────────────────────
        middle = QHBoxLayout()
        middle.setSpacing(16)

        # Current match players
        players_card = Card("Current Match — Players")
        self._players_container = QWidget()
        self._players_container.setStyleSheet(f"background-color:{BG_CARD}")
        self._players_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._players_layout = QVBoxLayout(self._players_container)
        self._players_layout.setContentsMargins(0, 0, 0, 0)
        self._players_layout.setSpacing(12)
        self._players_layout.addStretch()
        players_card.content_layout.addWidget(self._players_container, stretch=1)
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
        # Clear existing widgets in the players layout
        while self._players_layout.count():
            item = self._players_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        # Group players by team
        teams: dict = {}
        for p in players:
            teams.setdefault(p.get("team", -1), []).append(p)

        for team_num in sorted(teams.keys()):
            info        = team_info.get(team_num, {})
            team_colour = info.get("color") or (ACCENT2 if team_num == 0 else ACCENT)
            team_label  = info.get("name") or (f"Team {team_num + 1}" if team_num >= 0 else "Unassigned")

            section = self._build_team_section(team_label, team_colour, teams[team_num])
            self._players_layout.addWidget(section)

        self._players_layout.addStretch()
        self._players_container.update()

    def _build_team_section(self, team_label: str, team_colour: str, players: list) -> QWidget:
        section = QFrame()
        section.setStyleSheet("QFrame { background: transparent; border: none; }")
        sec_layout = QVBoxLayout(section)
        sec_layout.setContentsMargins(0, 0, 0, 0)
        sec_layout.setSpacing(0)

        header = QLabel(team_label.upper())
        header.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        header.setStyleSheet(
            f"color: {team_colour}; "
            f"background: transparent; "
            f"border: none; "
            f"border-bottom: 2px solid {team_colour}; "
            f"padding: 4px 8px; "
            f"letter-spacing: 2px;"
        )
        sec_layout.addWidget(header)

        for idx, p in enumerate(players):
            row = QFrame()
            row.setObjectName("playerRow")
            row.setStyleSheet(
                f"QFrame#playerRow {{ "
                f"background-color: {BG_TABLE if idx % 2 == 0 else '#1a2030'}; "
                f"border: none; }}"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 6, 12, 6)
            row_layout.setSpacing(8)

            name_lbl = QLabel(p.get("name", "Unknown"))
            name_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
            name_lbl.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")

            plat = p.get("platform", "Unknown")
            plat_lbl = QLabel(f"{platform_icon(plat)}  {plat.capitalize()}")
            plat_lbl.setStyleSheet(f"color: {SUBTEXT}; background: transparent; border: none;")

            row_layout.addWidget(name_lbl)
            row_layout.addStretch()
            row_layout.addWidget(plat_lbl)

            sec_layout.addWidget(row)

        return section

    def _on_record_updated(self, wins: int, losses: int):
        self._wins_card._value_label.setText(str(wins))
        self._losses_card._value_label.setText(str(losses))
        total = wins + losses
        ratio = f"{wins/total:.0%}" if total > 0 else "—"
        self._ratio_card._value_label.setText(ratio)

    def _on_history_row_clicked(self, item):
        row = item.row()
        if row < len(self._history_entries):
            dlg = MatchStatsDialog(self._history_entries[row], parent=self)
            dlg.exec()

    def _on_history_updated(self, history: list, session_num: int):
        """history is a list of match entry dicts, most recent first."""
        self._history_entries = list(history)
        self._update_streak(history, session_num)
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

    def _update_streak(self, history: list, session_num: int):
        streak = 0
        session_history = [e for e in history if e.get("sessionNum") == session_num]
        if session_history:
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

    def is_tracking_paused(self) -> bool:
        return self._pause_tracking_cb.isChecked()

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