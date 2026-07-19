"""
MainWindow — the app's top-level window.

A persistent header (status pill + gear button) sits above a three-tab
QTabWidget (Tracker / Sessions / Analytics), plus system-tray integration for
minimise-to-tray / autostart use. All UI updates arrive on the main thread
through `self.signals` (see ui.signals.UISignals): background threads emit,
the slots here receive.
"""
from pathlib import Path

import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy, QPushButton, QCheckBox,
    QSystemTrayIcon, QMenu, QMessageBox, QTabWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QAction

from .theme import (
    APP_STYLESHEET,
    BG_CARD, BG_TABLE, BG_ALT, BG_HOVER,
    TEXT, SUBTEXT, FAINT,
    ACCENT, ACCENT2, WIN_CLR, LOSS_CLR,
    BORDER, BORDER_SOFT,
)
from .widgets import soft_shadow, platform_icon, Card
from .signals import UISignals
from .dialogs.match_stats_dialog import MatchStatsDialog

# Assets are bundled at the project (dev) / _MEIPASS (frozen) root. This module
# lives one level down in the `ui` package, so climb two parents to reach them.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


# --------------------------------------------------------------------------
# Main Window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rocket League Tracker")
        self.setMinimumSize(1000, 750)
        self.setWindowIcon(QIcon(str(ASSETS_DIR / "RocketLeagueTracker.ico")))
        self._apply_styles()
        self._build_ui()

        # Public signal bus – wired up by main.py
        self.signals = UISignals()
        self.signals.players_updated.connect(self._on_players_updated)
        self.signals.encounters_updated.connect(self._on_encounters_updated)
        self.signals.record_updated.connect(self._on_record_updated)
        self.signals.history_updated.connect(self._on_history_updated)
        self.signals.sessions_updated.connect(self._on_sessions_updated)
        self.signals.status_changed.connect(self._on_status_changed)
        self.signals.game_started.connect(self._on_game_started)

        self._history_entries: list = []
        self._history_table.itemClicked.connect(self._on_history_row_clicked)

        self._setup_tray()

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------
    def _setup_tray(self):
        icon = QIcon(str(ASSETS_DIR / "RocketLeagueTracker.ico"))
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
        self.setStyleSheet(APP_STYLESHEET)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 20, 26, 16)
        root.setSpacing(14)

        # ── Header (persistent across all tabs) ──────────────────────────
        root.addLayout(self._build_header())

        # ── Tabbed navigation ────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_tracker_tab(),   "TRACKER")
        self._tabs.addTab(self._build_sessions_tab(),  "SESSIONS")
        self._tabs.addTab(self._build_analytics_tab(), "ANALYTICS")
        root.addWidget(self._tabs, stretch=1)

        # Status bar
        self.statusBar().showMessage("Not connected")

    def _build_header(self) -> QHBoxLayout:
        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 2, 2, 6)
        header_row.setSpacing(12)

        # Slim brand accent bar to the left of the title
        accent_bar = QFrame()
        accent_bar.setFixedSize(4, 26)
        accent_bar.setStyleSheet(f"background-color: {ACCENT}; border-radius: 2px;")
        header_row.addWidget(accent_bar)

        title = QLabel("ROCKET LEAGUE TRACKER")
        title.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {TEXT}; letter-spacing: 2px;")
        header_row.addWidget(title)
        header_row.addStretch()

        # Status grouped into a soft pill
        status_pill = QFrame()
        status_pill.setObjectName("statusPill")
        status_pill.setStyleSheet(
            f"QFrame#statusPill {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_SOFT}; border-radius: 15px; }}"
        )
        pill_layout = QHBoxLayout(status_pill)
        pill_layout.setContentsMargins(14, 6, 16, 6)
        pill_layout.setSpacing(8)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {SUBTEXT}; font-size: 13px;")
        self._status_label = QLabel("Waiting for game...")
        self._status_label.setStyleSheet(f"color: {SUBTEXT};")
        pill_layout.addWidget(self._status_dot)
        pill_layout.addWidget(self._status_label)
        header_row.addWidget(status_pill)

        self._settings_btn = QPushButton()
        self._settings_btn.setIcon(QIcon(str(ASSETS_DIR / "settings.png")))
        self._settings_btn.setIconSize(QSize(18, 18))
        self._settings_btn.setFixedSize(34, 34)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SOFT};
                border-radius: 10px;
            }}
            QPushButton:hover {{
                border-color: {SUBTEXT};
                background-color: {BG_HOVER};
            }}
            QPushButton:pressed {{ background-color: {BG_TABLE}; }}
        """)
        self._settings_btn.clicked.connect(
            lambda: self.signals.settings_prompt.emit()
        )
        header_row.addSpacing(4)
        header_row.addWidget(self._settings_btn)
        return header_row

    # ── Tracker tab ──────────────────────────────────────────────────────
    def _build_tracker_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(16)

        # ── Win / Loss row ───────────────────────────────────────────────
        record_row = QHBoxLayout()
        record_row.setSpacing(14)

        self._wins_card   = self._stat_card("WINS",     "0", WIN_CLR,  "this session")
        self._losses_card = self._stat_card("LOSSES",   "0", LOSS_CLR, "this session")
        self._ratio_card  = self._stat_card("WIN RATE", "—", ACCENT2,  "this session")
        self._streak_card = self._stat_card("STREAK",   "0", SUBTEXT,  "current run")

        record_row.addWidget(self._wins_card)
        record_row.addWidget(self._losses_card)
        record_row.addWidget(self._ratio_card)
        record_row.addWidget(self._streak_card)
        record_row.addStretch()

        self._new_session_btn = QPushButton("＋   New Session")
        self._new_session_btn.setFixedHeight(42)
        self._new_session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_session_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                color: {ACCENT2};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 0 20px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background-color: {BG_HOVER};
                border-color: {ACCENT2};
                color: {TEXT};
            }}
            QPushButton:pressed {{ background-color: {BG_TABLE}; }}
        """)
        self._new_session_btn.clicked.connect(
            lambda: self.signals.new_session_requested.emit()
        )
        record_row.addWidget(self._new_session_btn)

        self._pause_tracking_cb = QCheckBox("Pause Tracking")
        self._pause_tracking_cb.setFixedHeight(42)
        self._pause_tracking_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_tracking_cb.setToolTip(
            "When enabled, finished matches are not saved to history.\n"
            "Players still appear in the Current Match panel."
        )
        self._pause_tracking_cb.setStyleSheet(f"""
            QCheckBox {{
                color: {SUBTEXT};
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
                spacing: 9px;
            }}
            QCheckBox:hover {{
                border-color: {SUBTEXT};
                color: {TEXT};
            }}
            QCheckBox:checked {{
                color: {ACCENT};
                border-color: {ACCENT};
                background-color: {ACCENT}1e;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border: 1px solid {BORDER};
                border-radius: 4px;
                background-color: {BG_TABLE};
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
        outer.addLayout(record_row)

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
        self._players_layout.setSpacing(14)
        self._players_layout.addWidget(
            self._make_placeholder("No active match", "Players appear here once a match begins")
        )
        self._players_layout.addStretch()
        players_card.content_layout.addWidget(self._players_container, stretch=1)
        players_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_col.addWidget(players_card, stretch=2)

        encounters_card = Card("Past Encounters")
        self._encounters_container = QWidget()
        self._encounters_container.setStyleSheet(f"background-color:{BG_CARD}")
        self._encounters_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._encounters_layout = QVBoxLayout(self._encounters_container)
        self._encounters_layout.setContentsMargins(0, 0, 0, 0)
        self._encounters_layout.setSpacing(6)
        self._encounters_layout.addWidget(
            self._make_placeholder("No encounters yet", "Your history with these players shows here")
        )
        self._encounters_layout.addStretch()
        encounters_card.content_layout.addWidget(self._encounters_container, stretch=1)
        encounters_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_col.addWidget(encounters_card, stretch=2)

        middle.addLayout(left_col, stretch=3)

        # Match history
        history_card = Card("Match History")
        self._history_table = self._make_table(["Session", "Date", "Result", "Score", "Opponents", "Teammates"])
        history_card.content_layout.addWidget(self._history_table)
        history_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        middle.addWidget(history_card, stretch=4)

        outer.addLayout(middle, stretch=1)
        return tab

    # ── Sessions tab ─────────────────────────────────────────────────────
    def _build_sessions_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(14)

        # ── Header / totals ─────────────────────────────────────────────
        header = QHBoxLayout()
        self._sessions_title_lbl = QLabel("ALL SESSIONS")
        self._sessions_title_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.DemiBold))
        self._sessions_title_lbl.setStyleSheet(f"color: {TEXT}; letter-spacing: 0.5px;")
        self._sessions_totals_lbl = QLabel()
        self._sessions_totals_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._sessions_totals_lbl.setStyleSheet(f"color: {SUBTEXT};")
        header.addWidget(self._sessions_title_lbl)
        header.addStretch()
        header.addWidget(self._sessions_totals_lbl)
        outer.addLayout(header)

        # ── Sessions table (wrapped in a card-like panel) ────────────────
        cols = ["Session", "Dates", "Matches", "Wins", "Losses",
                "Win %", "Best W Streak", "Worst L Streak"]
        self._sessions_table = self._make_table(cols)
        self._sessions_table.itemSelectionChanged.connect(self._update_session_delete_enabled)

        self._sessions_empty_lbl = QLabel("No sessions recorded yet.")
        self._sessions_empty_lbl.setStyleSheet(f"color: {SUBTEXT}; padding: 24px;")
        self._sessions_empty_lbl.setVisible(False)

        table_panel = QFrame()
        table_panel.setObjectName("panel")
        table_panel.setStyleSheet(
            f"QFrame#panel {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_SOFT}; border-radius: 14px; }}"
        )
        soft_shadow(table_panel, blur=32, y_offset=8, alpha=70)
        panel_layout = QVBoxLayout(table_panel)
        panel_layout.setContentsMargins(16, 12, 16, 16)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self._sessions_table)
        panel_layout.addWidget(self._sessions_empty_lbl)
        outer.addWidget(table_panel, stretch=1)

        # ── Delete control ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._session_delete_btn = QPushButton("Delete Session")
        self._session_delete_btn.setFixedWidth(150)
        self._session_delete_btn.setFixedHeight(38)
        self._session_delete_btn.setEnabled(False)
        self._session_delete_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                color: {LOSS_CLR};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 6px 18px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {LOSS_CLR}1e;
                border-color: {LOSS_CLR};
            }}
            QPushButton:disabled {{
                color: {FAINT};
                border-color: {BORDER_SOFT};
            }}
        """)
        self._session_delete_btn.clicked.connect(self._handle_session_delete)
        btn_row.addWidget(self._session_delete_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self._session_summaries: list = []
        return tab

    # ── Analytics tab ────────────────────────────────────────────────────
    def _build_analytics_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(16)

        card = Card("Analytics")

        heading = QLabel("Analytics coming soon")
        heading.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
        heading.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
        card.content_layout.addWidget(heading)

        blurb = QLabel(
            "This tab will turn your recorded matches into trends and comparisons. "
            "Planned views:"
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(
            f"color: {SUBTEXT}; background: transparent; border: none; padding-bottom: 6px;"
        )
        card.content_layout.addWidget(blurb)

        planned = [
            "Win rate over time — per session and rolling average",
            "Goal / shot / save averages, with your best and worst matches",
            "Overtime record and average match duration",
            "Performance by time of day and day of week",
            "Toughest opponents and most-played teammates",
            "Head-to-head comparison between any two matches",
        ]
        for item in planned:
            row = QLabel(f"<span style='color:{ACCENT2}'>•</span>&nbsp;&nbsp;{item}")
            row.setWordWrap(True)
            row.setStyleSheet(
                f"color: {TEXT}; background: transparent; border: none; padding: 5px 0 5px 4px;"
            )
            card.content_layout.addWidget(row)

        card.content_layout.addStretch()
        outer.addWidget(card, stretch=1)
        return tab

    def _stat_card(self, label: str, value: str, colour: str, caption: str = "") -> QFrame:
        frame = QFrame()
        frame.setObjectName("statCard")
        frame.setFixedSize(158, 104)
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
        val.setFont(QFont("Segoe UI", 30, QFont.Weight.DemiBold))
        val.setStyleSheet(f"color: {colour};")
        layout.addWidget(val)

        if caption:
            cap = QLabel(caption)
            cap.setFont(QFont("Segoe UI", 8))
            cap.setStyleSheet(f"color: {FAINT}; letter-spacing: 0.3px;")
            layout.addWidget(cap)

        # store reference to the value label so we can update it
        frame._value_label = val
        return frame

    def _make_table(self, headers: list) -> QTableWidget:
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

    def _make_placeholder(self, text: str, sub: str = "") -> QWidget:
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

        if not teams:
            self._players_layout.addWidget(
                self._make_placeholder("No active match", "Players appear here once a match begins")
            )

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
        sec_layout.setSpacing(6)

        # Team header: colour dot + name + player count
        header_row = QFrame()
        header_row.setStyleSheet("background: transparent; border: none;")
        hl = QHBoxLayout(header_row)
        hl.setContentsMargins(2, 0, 2, 4)
        hl.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {team_colour}; background: transparent; border: none; font-size: 11px;")
        name = QLabel(team_label.upper())
        name.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        name.setStyleSheet(
            f"color: {team_colour}; background: transparent; border: none; letter-spacing: 1.5px;"
        )
        count = QLabel(f"{len(players)}")
        count.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        count.setStyleSheet(f"color: {FAINT}; background: transparent; border: none;")
        hl.addWidget(dot)
        hl.addWidget(name)
        hl.addStretch()
        hl.addWidget(count)
        sec_layout.addWidget(header_row)

        for idx, p in enumerate(players):
            row = QFrame()
            row.setObjectName("playerRow")
            row.setStyleSheet(
                f"QFrame#playerRow {{ "
                f"background-color: {BG_TABLE if idx % 2 == 0 else BG_ALT}; "
                f"border: none; border-radius: 8px; }}"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(14, 9, 14, 9)
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

    def _on_encounters_updated(self, opponents: list, teammates: list, common_teammates: list):
        teammates = [player for player in teammates if player.get("name") not in common_teammates]

        # Tag each entry with its role so _build_encounter_row can colour the
        # badge and pick role-appropriate wording. Opponents render first.
        combined = (
            [{**enc, "role": "opponent"} for enc in opponents]
            + [{**enc, "role": "teammate"} for enc in teammates]
        )

        while self._encounters_layout.count():
            item = self._encounters_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if not combined:
            self._encounters_layout.addWidget(
                self._make_placeholder("No encounters yet", "Your history with these players shows here")
            )
        else:
            for idx, enc in enumerate(combined):
                self._encounters_layout.addWidget(self._build_encounter_row(enc, idx))

        self._encounters_layout.addStretch()
        self._encounters_container.update()

    def _build_encounter_row(self, enc: dict, idx: int) -> QWidget:
        role        = enc.get("role", "opponent")
        is_opponent = role == "opponent"
        role_colour = ACCENT if is_opponent else ACCENT2

        row = QFrame()
        row.setObjectName("encounterRow")
        row.setStyleSheet(
            f"QFrame#encounterRow {{ "
            f"background-color: {BG_TABLE if idx % 2 == 0 else BG_ALT}; "
            f"border: none; border-radius: 8px; }}"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(10)

        role_dot = QLabel("●")
        role_dot.setStyleSheet(
            f"color: {role_colour}; background: transparent; border: none; font-size: 14px;"
        )
        role_dot.setToolTip("Opponent" if is_opponent else "Teammate")
        layout.addWidget(role_dot)

        name_lbl = QLabel(enc.get("name", "?"))
        name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        name_lbl.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
        layout.addWidget(name_lbl)
        layout.addStretch()

        encounters = enc.get("encounters", 0)
        cross      = enc.get("crossEncounters", 0)
        if encounters == 0:
            if is_opponent:
                note_text = (
                    f"First time facing them · {cross} as teammate{'s' if cross != 1 else ''}"
                    if cross else "First time facing them"
                )
            else:
                note_text = (
                    f"First time as teammates · {cross} as opponent{'s' if cross != 1 else ''}"
                    if cross else "First time playing with them"
                )
            note = QLabel(note_text)
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
                f"color: {ACCENT2}; background-color: {ACCENT2}1c; border: none; "
                f"border-radius: 8px; padding: 3px 9px; font-size: 10px;"
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

            opp_goals  = sum(p.get("goals", 0) for p in entry.get("opponents", []))
            team_goals = sum(p.get("goals", 0) for p in entry.get("teammates", []))

            session_item = QTableWidgetItem(session_num)
            date_item    = QTableWidgetItem(datetime.date.fromisoformat(date_str).strftime("%d, %B %Y"))
            result_item  = QTableWidgetItem(result)
            score_item   = QTableWidgetItem(str(team_goals) + " – " + str(opp_goals))
            opp_item     = QTableWidgetItem(opp_str)
            team_item    = QTableWidgetItem(team_str)

            session_item.setForeground(QColor(ACCENT2))
            date_item.setForeground(QColor(SUBTEXT))
            result_item.setForeground(QColor(WIN_CLR if result == "WIN" else LOSS_CLR))
            score_item.setForeground(QColor(TEXT))
            opp_item.setForeground(QColor(TEXT))
            team_item.setForeground(QColor(SUBTEXT))

            t.setItem(row, 0, session_item)
            t.setItem(row, 1, date_item)
            t.setItem(row, 2, result_item)
            t.setItem(row, 3, score_item)
            t.setItem(row, 4, opp_item)
            t.setItem(row, 5, team_item)

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

    # ------------------------------------------------------------------
    # Sessions tab
    # ------------------------------------------------------------------
    def _on_sessions_updated(self, summaries: list):
        """Render the sessions table. `summaries` is `get_session_summaries()`,
        most-recent session first."""
        self._session_summaries = list(summaries)

        total_w = sum(s.get("wins", 0)   for s in self._session_summaries)
        total_l = sum(s.get("losses", 0) for s in self._session_summaries)
        total_m = total_w + total_l
        total_pct = f"{total_w / total_m:.0%}" if total_m else "—"

        self._sessions_title_lbl.setText(f"ALL SESSIONS  ·  {len(self._session_summaries)}")
        self._sessions_totals_lbl.setText(f"{total_w}W  {total_l}L  ·  {total_pct}")

        t = self._sessions_table
        t.setRowCount(0)
        self._sessions_empty_lbl.setVisible(not self._session_summaries)
        t.setVisible(bool(self._session_summaries))

        for s in self._session_summaries:
            row = t.rowCount()
            t.insertRow(row)
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
                t.setItem(row, col, item)

        self._update_session_delete_enabled()

    def _update_session_delete_enabled(self):
        self._session_delete_btn.setEnabled(
            bool(self._sessions_table.selectionModel().selectedRows())
        )

    def _handle_session_delete(self):
        rows = self._sessions_table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if idx >= len(self._session_summaries):
            return
        summary = self._session_summaries[idx]
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

        # main.py performs the deletion and emits `sessions_updated`, which
        # re-renders this table (plus the record/history on the Tracker tab).
        self.signals.session_delete_requested.emit(num)

    def is_tracking_paused(self) -> bool:
        return self._pause_tracking_cb.isChecked()

    def _on_status_changed(self, message: str):
        connected = "connected" in message.lower()
        colour = WIN_CLR if connected else SUBTEXT
        self._status_dot.setStyleSheet(
            f"color: {colour}; background: transparent; font-size: 13px;"
        )
        self._status_label.setText(message)
        self._status_label.setStyleSheet(f"color: {colour}; background: transparent;")
        self.statusBar().showMessage(message)
