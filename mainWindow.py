from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy, QDialog, QPushButton, QCheckBox,
    QSystemTrayIcon, QMenu, QMessageBox
)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QAction

# --------------------------------------------------------------------------
# Signal bridge: lets background threads safely update the Qt UI
# --------------------------------------------------------------------------
class UISignals(QObject):
    players_updated       = pyqtSignal(list, dict)  # list of player dicts, team_info dict
    encounters_updated    = pyqtSignal(list, list)  # opponents encounters, teammates encounters
    record_updated        = pyqtSignal(int, int)    # wins, losses
    history_updated       = pyqtSignal(list, int)   # list of match history dicts, current session num
    status_changed        = pyqtSignal(str)         # status bar text
    session_prompt        = pyqtSignal(int)         # last session number, triggers dialog
    settings_prompt       = pyqtSignal()            # user doesn't have localUsername set
    new_session_requested = pyqtSignal()            # user clicked "New Session"
    sessions_requested    = pyqtSignal()            # user clicked "Sessions"
    game_started          = pyqtSignal()            # Rocket League process detected

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
# Settings Dialog
# --------------------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, username: str = "", teammates=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {BG_DARK};
                color: {TEXT};
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }}
            QLineEdit {{
                background-color: {BG_TABLE};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px 8px;
                selection-background-color: #264f78;
            }}
            QLineEdit:focus {{ border-color: {ACCENT2}; }}
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # ── Header ──────────────────────────────────────────────────────
        title_lbl = QLabel("SETTINGS")
        title_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {ACCENT2}; letter-spacing: 2px;")
        layout.addWidget(title_lbl)

        # ── Form ────────────────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 4)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def field_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {SUBTEXT};")
            return lbl

        self._username_edit = QLineEdit(username)
        self._username_edit.setPlaceholderText("e.g. FireRhombus4873")
        form.addRow(field_label("In-game name"), self._username_edit)

        teammates_text = ", ".join(teammates or [])
        self._teammates_edit = QLineEdit(teammates_text)
        self._teammates_edit.setPlaceholderText("Optional, comma-separated")
        form.addRow(field_label("Common teammates"), self._teammates_edit)

        layout.addLayout(form)

        # ── Help text ───────────────────────────────────────────────────
        help_lbl = QLabel(
            "Your in-game name is used to detect which team is yours. "
            "Common teammates may be used in future features (e.g. flagging "
            "frequent teammates in match history)."
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(f"color: {SUBTEXT}; font-size: 11px;")
        layout.addWidget(help_lbl)

        # ── Inline error ────────────────────────────────────────────────
        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {LOSS_CLR}; font-size: 11px;")
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(100)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        if not self._username_edit.text().strip():
            self._error_lbl.setText("In-game name is required.")
            self._error_lbl.setVisible(True)
            self._username_edit.setStyleSheet(f"border: 1px solid {LOSS_CLR};")
            self._username_edit.setFocus()
            return
        self.accept()

    def username(self) -> str:
        return self._username_edit.text().strip()

    def teammates(self) -> list:
        return [t.strip() for t in self._teammates_edit.text().split(",") if t.strip()]


# --------------------------------------------------------------------------
# Session Summary Dialog
# --------------------------------------------------------------------------
class SessionSummaryDialog(QDialog):
    def __init__(self, summaries: list, parent=None, on_delete=None):
        """
        on_delete: optional callable(session_num) -> list[summary]. When provided,
        a Delete button is shown; calling it after confirmation should remove the
        session and return fresh summaries for the dialog to re-render.
        """
        super().__init__(parent)
        self.setWindowTitle("Session Summary")
        self.setMinimumSize(900, 480)
        self._on_delete = on_delete
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # ── Header / totals ─────────────────────────────────────────────
        self._title_lbl = QLabel()
        self._title_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {ACCENT2};")
        self._totals_lbl = QLabel()
        self._totals_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        self._totals_lbl.setStyleSheet(f"color: {TEXT};")

        header = QHBoxLayout()
        header.addWidget(self._title_lbl)
        header.addStretch()
        header.addWidget(self._totals_lbl)
        layout.addLayout(header)

        # ── Sessions table ──────────────────────────────────────────────
        cols = ["Session", "Dates", "Matches", "Wins", "Losses",
                "Win %", "Best W Streak", "Worst L Streak"]
        self._table = QTableWidget(0, len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(self._table.styleSheet() +
                                  f"QTableWidget {{ alternate-background-color: #1a2030; }}")
        layout.addWidget(self._table)

        self._empty_lbl = QLabel("No sessions recorded yet.")
        self._empty_lbl.setStyleSheet(f"color: {SUBTEXT}; padding: 20px;")
        self._empty_lbl.setVisible(False)
        layout.addWidget(self._empty_lbl)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        if self._on_delete is not None:
            self._delete_btn = QPushButton("Delete Session")
            self._delete_btn.setFixedWidth(140)
            self._delete_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {BG_CARD};
                    color: {LOSS_CLR};
                    border: 1px solid {BORDER};
                    border-radius: 6px;
                    padding: 6px 18px;
                }}
                QPushButton:hover {{
                    background-color: #2a1014;
                    border-color: {LOSS_CLR};
                }}
                QPushButton:disabled {{
                    color: {SUBTEXT};
                    border-color: {BORDER};
                }}
            """)
            self._delete_btn.clicked.connect(self._handle_delete)
            self._delete_btn.setEnabled(False)
            self._table.itemSelectionChanged.connect(self._update_delete_enabled)
            btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._render(summaries)

    def _render(self, summaries: list):
        self._summaries = list(summaries)
        total_w = sum(s.get("wins", 0)   for s in self._summaries)
        total_l = sum(s.get("losses", 0) for s in self._summaries)
        total_m = total_w + total_l
        total_pct = f"{total_w/total_m:.0%}" if total_m else "—"

        self._title_lbl.setText(f"ALL SESSIONS  ·  {len(self._summaries)}")
        self._totals_lbl.setText(f"{total_w}W  {total_l}L  ·  {total_pct}")

        self._table.setRowCount(0)
        self._empty_lbl.setVisible(not self._summaries)
        self._table.setVisible(bool(self._summaries))

        for s in self._summaries:
            row = self._table.rowCount()
            self._table.insertRow(row)
            first = s.get("firstDate", "")[:10]
            last  = s.get("lastDate", "")[:10]
            date_range = first if first == last else f"{first} → {last}"
            pct = f"{s.get('winPct', 0):.0%}" if s.get("matches") else "—"

            values = [
                (str(s.get("sessionNum", "?")),     ACCENT2),
                (date_range,                        SUBTEXT),
                (str(s.get("matches", 0)),          TEXT),
                (str(s.get("wins", 0)),             WIN_CLR),
                (str(s.get("losses", 0)),           LOSS_CLR),
                (pct,                               TEXT),
                (str(s.get("bestWinStreak", 0)),    WIN_CLR),
                (str(s.get("worstLossStreak", 0)),  LOSS_CLR),
            ]
            for col, (text, colour) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(colour))
                self._table.setItem(row, col, item)

        if hasattr(self, "_delete_btn"):
            self._update_delete_enabled()

    def _update_delete_enabled(self):
        self._delete_btn.setEnabled(bool(self._table.selectionModel().selectedRows()))

    def _handle_delete(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if idx >= len(self._summaries):
            return
        summary = self._summaries[idx]
        num = summary.get("sessionNum")

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete Session")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"Delete session {num}?\n\n"
            f"{summary.get('matches', 0)} match(es) — "
            f"{summary.get('wins', 0)}W / {summary.get('losses', 0)}L — "
            f"will be permanently removed from history. This cannot be undone."
        )
        delete_btn = confirm.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        confirm.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        confirm.exec()
        if confirm.clickedButton() != delete_btn:
            return

        new_summaries = self._on_delete(num)
        self._render(new_summaries or [])


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
        self.signals.encounters_updated.connect(self._on_encounters_updated)
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

        self._settings_btn = QPushButton()
        self._settings_btn.setIcon(QIcon(str(Path(__file__).parent / "assets" / "settings.png")))
        self._settings_btn.setIconSize(QSize(18, 18))
        self._settings_btn.setFixedSize(32, 32)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            QPushButton:hover {{
                border-color: {TEXT};
                background-color: #1a2030;
            }}
            QPushButton:pressed {{ background-color: #0a1820; }}
        """)
        self._settings_btn.clicked.connect(
            lambda: self.signals.settings_prompt.emit()
        )
        header_row.addSpacing(8)
        header_row.addWidget(self._settings_btn)
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

        self._sessions_btn = QPushButton("☰  SESSIONS")
        self._sessions_btn.setFixedHeight(40)
        self._sessions_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                color: {SUBTEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 0 16px;
                font-family: "Courier New";
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: #1a2030;
                border-color: {TEXT};
                color: {TEXT};
            }}
            QPushButton:pressed {{ background-color: #0a1820; }}
        """)
        self._sessions_btn.clicked.connect(
            lambda: self.signals.sessions_requested.emit()
        )
        record_row.addWidget(self._sessions_btn)

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

        # Current match players + past encounters (stacked in the left column)
        left_col = QVBoxLayout()
        left_col.setSpacing(16)

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
        left_col.addWidget(players_card, stretch=2)

        encounters_card = Card("Past Encounters — Opponents")
        self._encounters_container = QWidget()
        self._encounters_container.setStyleSheet(f"background-color:{BG_CARD}")
        self._encounters_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._encounters_layout = QVBoxLayout(self._encounters_container)
        self._encounters_layout.setContentsMargins(0, 0, 0, 0)
        self._encounters_layout.setSpacing(4)
        self._encounters_layout.addStretch()
        encounters_card.content_layout.addWidget(self._encounters_container, stretch=1)
        encounters_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_col.addWidget(encounters_card, stretch=1)

        middle.addLayout(left_col, stretch=3)

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

    def _on_encounters_updated(self, opponents: list, teammates: list):
        # Teammates data is computed by sessionStore for future use; rendering
        # for them isn't wired up yet — the card only shows opponents today.
        _ = teammates

        while self._encounters_layout.count():
            item = self._encounters_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if not opponents:
            empty = QLabel("Waiting for match...")
            empty.setStyleSheet(f"color: {SUBTEXT}; background: transparent; padding: 8px;")
            self._encounters_layout.addWidget(empty)
        else:
            for idx, enc in enumerate(opponents):
                self._encounters_layout.addWidget(self._build_encounter_row(enc, idx))

        self._encounters_layout.addStretch()
        self._encounters_container.update()

    def _build_encounter_row(self, enc: dict, idx: int) -> QWidget:
        row = QFrame()
        row.setObjectName("encounterRow")
        row.setStyleSheet(
            f"QFrame#encounterRow {{ "
            f"background-color: {BG_TABLE if idx % 2 == 0 else '#1a2030'}; "
            f"border: none; border-radius: 4px; }}"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        name_lbl = QLabel(enc.get("name", "?"))
        name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        name_lbl.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
        layout.addWidget(name_lbl)
        layout.addStretch()

        encounters = enc.get("encounters", 0)
        if encounters == 0:
            note = QLabel("First time facing them")
            note.setStyleSheet(
                f"color: {SUBTEXT}; background: transparent; border: none; font-style: italic;"
            )
            layout.addWidget(note)
        else:
            wins   = enc.get("wins", 0)
            losses = enc.get("losses", 0)
            record = QLabel(
                f"<span style='color:{WIN_CLR}'>{wins}W</span>  "
                f"<span style='color:{LOSS_CLR}'>{losses}L</span>  "
                f"<span style='color:{SUBTEXT}'>· {encounters} played</span>"
            )
            record.setStyleSheet("background: transparent; border: none;")
            layout.addWidget(record)

            ma = enc.get("matchesAgo")
            if ma is not None:
                if ma == 0:
                    when_text = "Last match"
                elif ma == 1:
                    when_text = "1 match ago"
                else:
                    when_text = f"{ma} matches ago"
            else:
                date = (enc.get("lastDate") or "")[:10]
                when_text = f"On {date}" if date else "Earlier"

            when_lbl = QLabel(when_text)
            when_lbl.setStyleSheet(
                f"color: {ACCENT2}; background: transparent; border: none; "
                f"font-family: 'Courier New'; font-size: 11px;"
            )
            layout.addWidget(when_lbl)

        return row

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