"""
MainWindow — the app's top-level window: the shell only.

A persistent header (status pill + gear button) sits above a three-tab
QTabWidget (Tracker / Sessions / Analytics), plus system-tray integration for
minimise-to-tray / autostart use.

The tabs own their own widgets and slots (see ui.tabs). `self.signals` is
built here first, then handed to each tab so it can connect itself; the only
slots left on this class are the status bar and the tray.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton,
    QSystemTrayIcon, QMenu, QTabWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QAction

from .theme import (
    APP_STYLESHEET,
    BG_CARD, BG_TABLE, BG_HOVER,
    TEXT, SUBTEXT,
    ACCENT, WIN_CLR, BORDER_SOFT,
)
from .signals import UISignals
from .tabs.tracker_tab import TrackerTab
from .tabs.sessions_tab import SessionsTab
from .tabs.analytics_tab import AnalyticsTab

# Assets are bundled at the project (dev) / _MEIPASS (frozen) root. This module
# lives one level down in the `ui` package, so climb two parents to reach them.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


# --------------------------------------------------------------------------
# Main Window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, close_to_tray: bool = True):
        super().__init__()
        # When False, closing the window quits the app outright instead of
        # hiding to the tray. main.py owns this decision (see
        # _should_close_to_tray) — dev runs quit, the shipped build stays
        # resident so it can reappear when Rocket League starts.
        self._close_to_tray = close_to_tray

        # Public signal bus – wired up by main.py. Built *before* the UI so the
        # tab widgets can be handed the bus and connect their own slots in
        # their constructors.
        self.signals = UISignals()

        self.setWindowTitle("Rocket League Tracker")
        self.setMinimumSize(1000, 750)
        self.setWindowIcon(QIcon(str(ASSETS_DIR / "RocketLeagueTracker.ico")))
        # Styles first: widgets built afterwards get polished once, on
        # parenting, rather than re-polished when the sheet arrives.
        self._apply_styles()
        self._build_ui()

        # Everything else is connected by the tab that owns it.
        self.signals.status_changed.connect(self._on_status_changed)
        self.signals.game_started.connect(self._on_game_started)

        self._setup_tray()

    def is_tracking_paused(self) -> bool:
        """Public — main.py's MatchEnded handler discards the match when true."""
        return self._tracker_tab.is_tracking_paused()

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
        if not self._close_to_tray:
            # Take the tray icon down first — Windows leaves a ghost icon
            # behind if the process dies while it's still registered.
            self._tray.hide()
            event.accept()
            QApplication.quit()
            return

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
        # Tabs are built parentless and handed to addTab, which takes
        # ownership — passing parent=self first would force a second reparent.
        self._tracker_tab   = TrackerTab(self.signals)
        self._sessions_tab  = SessionsTab(self.signals)
        self._analytics_tab = AnalyticsTab(self.signals)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._tracker_tab,   "TRACKER")
        self._tabs.addTab(self._sessions_tab,  "SESSIONS")
        self._tabs.addTab(self._analytics_tab, "ANALYTICS")
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

    def _on_status_changed(self, message: str):
        connected = "connected" in message.lower()
        colour = WIN_CLR if connected else SUBTEXT
        self._status_dot.setStyleSheet(
            f"color: {colour}; background: transparent; font-size: 13px;"
        )
        self._status_label.setText(message)
        self._status_label.setStyleSheet(f"color: {colour}; background: transparent;")
        self.statusBar().showMessage(message)
