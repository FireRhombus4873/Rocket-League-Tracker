"""
SessionsTab — one row per session (dates, matches, W/L, win %, streaks) with a
totals header, a View Analytics control and a Delete Session control.

Fed by `sessions_updated`; drives deletes back through
`session_delete_requested`, which main.py performs before re-emitting the
record/history/sessions/analytics chain.

It also drives the Analytics tab's session scope through
`analytics_scope_changed`. That's deliberate: this table already is the session
browser — dates, counts and win rates, scrollable and sortable — so picking one
of several hundred sessions belongs here rather than in a dropdown over there.
main.py brings the Analytics tab forward in response.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem,
    QFrame, QPushButton, QMessageBox,
)
from PyQt6.QtGui import QFont, QColor

from ..theme import (
    BG_CARD, TEXT, SUBTEXT, FAINT,
    ACCENT2, WIN_CLR, LOSS_CLR, BORDER, BORDER_SOFT, tint,
)
from ..widgets import soft_shadow, make_table
from ..signals import UISignals


class SessionsTab(QWidget):
    def __init__(self, signals: UISignals, parent=None):
        super().__init__(parent)
        self._signals = signals
        # State before any connect — _update_session_buttons_enabled fires on
        # the table's selection change and reads this.
        self._session_summaries: list = []

        self._build_ui()

        signals.sessions_updated.connect(self._on_sessions_updated)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
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
        self._sessions_table = make_table(cols)
        self._sessions_table.itemSelectionChanged.connect(self._update_session_buttons_enabled)
        # Double-click is the shortcut for the View Analytics button below. The
        # row is already selected by the time this fires, so both read the same
        # selection.
        self._sessions_table.itemDoubleClicked.connect(self._handle_view_analytics)

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

        # ── Controls ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._session_analytics_btn = QPushButton("View Analytics")
        self._session_analytics_btn.setFixedWidth(150)
        self._session_analytics_btn.setFixedHeight(38)
        self._session_analytics_btn.setEnabled(False)
        self._session_analytics_btn.setToolTip(
            "Show the Analytics tab for the selected session only")
        self._session_analytics_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD};
                color: {ACCENT2};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 6px 18px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {tint(ACCENT2)};
                border-color: {ACCENT2};
            }}
            QPushButton:disabled {{
                color: {FAINT};
                border-color: {BORDER_SOFT};
            }}
        """)
        self._session_analytics_btn.clicked.connect(self._handle_view_analytics)
        btn_row.addWidget(self._session_analytics_btn)

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
                background-color: {tint(LOSS_CLR)};
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

    # ------------------------------------------------------------------
    # Slot handlers (called on main thread via signals)
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

        self._update_session_buttons_enabled()

    def _update_session_buttons_enabled(self):
        selected = bool(self._sessions_table.selectionModel().selectedRows())
        self._session_delete_btn.setEnabled(selected)
        self._session_analytics_btn.setEnabled(selected)

    def _selected_summary(self):
        """The summary dict for the selected row, or None. The row index can
        outrun `_session_summaries` between a delete and its re-render."""
        rows = self._sessions_table.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if idx >= len(self._session_summaries):
            return None
        return self._session_summaries[idx]

    def _handle_view_analytics(self):
        summary = self._selected_summary()
        if summary is None:
            return
        # main.py re-queries with this scope and brings the Analytics tab
        # forward; the chips over there render from the snapshot that comes back.
        self._signals.analytics_scope_changed.emit(
            {"type": "session", "sessionNum": summary.get("sessionNum")})

    def _handle_session_delete(self):
        summary = self._selected_summary()
        if summary is None:
            return
        num = summary.get("sessionNum")

        confirm = QMessageBox(self.window())
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
        self._signals.session_delete_requested.emit(num)
