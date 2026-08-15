"""
TrackerTab — the live view: win/loss/ratio/streak cards, the NEW SESSION and
PAUSE TRACKING controls, the current-match player list, the Past Encounters
card, and the match-history table.

Fed by `record_updated`, `history_updated`, `players_updated` and
`encounters_updated`; drives `new_session_requested` and
`match_delete_requested` back out.
"""
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem,
    QHeaderView, QFrame, QSizePolicy, QPushButton, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from ..theme import (
    BG_CARD, BG_TABLE, BG_ALT, BG_HOVER,
    TEXT, SUBTEXT, FAINT,
    ACCENT, ACCENT2, WIN_CLR, LOSS_CLR, LOSS_CLR_BG, BORDER,
)
from ..widgets import (
    platform_icon, Card, stat_card, make_table, make_placeholder,
)
from ..signals import UISignals
from ..dialogs.match_stats_dialog import MatchStatsDialog


class TrackerTab(QWidget):
    # Trailing column of the match-history table; holds the per-row delete
    # button rather than a QTableWidgetItem.
    HISTORY_DELETE_COL = 6

    def __init__(self, signals: UISignals, parent=None):
        super().__init__(parent)
        self._signals = signals
        # State before any connect — _on_history_row_clicked reads this.
        self._history_entries: list = []

        self._build_ui()
        self._history_table.itemClicked.connect(self._on_history_row_clicked)

        signals.players_updated.connect(self._on_players_updated)
        signals.encounters_updated.connect(self._on_encounters_updated)
        signals.record_updated.connect(self._on_record_updated)
        signals.history_updated.connect(self._on_history_updated)

    def is_tracking_paused(self) -> bool:
        return self._pause_tracking_cb.isChecked()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(16)

        # ── Win / Loss row ───────────────────────────────────────────────
        record_row = QHBoxLayout()
        record_row.setSpacing(14)

        self._wins_card   = stat_card("WINS",     "0", WIN_CLR,  "this session")
        self._losses_card = stat_card("LOSSES",   "0", LOSS_CLR, "this session")
        self._ratio_card  = stat_card("WIN RATE", "—", ACCENT2,  "this session")
        self._streak_card = stat_card("STREAK",   "0", SUBTEXT,  "current run")

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
            lambda: self._signals.new_session_requested.emit()
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
                background-color: {LOSS_CLR_BG};
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
            make_placeholder("No active match", "Players appear here once a match begins")
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
            make_placeholder("No encounters yet", "Your history with these players shows here")
        )
        self._encounters_layout.addStretch()
        encounters_card.content_layout.addWidget(self._encounters_container, stretch=1)
        encounters_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_col.addWidget(encounters_card, stretch=2)

        middle.addLayout(left_col, stretch=3)

        # Match history
        history_card = Card("Match History")
        self._history_table = make_table(
            ["Session", "Date", "Result", "Score", "Opponents", "Teammates", ""]
        )
        # The two name columns are the only ones that grow unbounded, so pin the
        # four narrow ones to their content and let those share what's left.
        # They elide with "…"; the per-cell tooltip carries the full roster.
        # The trailing delete column is fixed so it can't be squeezed away.
        hist_hdr = self._history_table.horizontalHeader()
        hist_hdr.setStretchLastSection(False)
        for col in (0, 1, 2, 3):
            hist_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (4, 5):
            hist_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        hist_hdr.setSectionResizeMode(self.HISTORY_DELETE_COL, QHeaderView.ResizeMode.Fixed)
        self._history_table.setColumnWidth(self.HISTORY_DELETE_COL, 44)
        history_card.content_layout.addWidget(self._history_table)
        history_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        middle.addWidget(history_card, stretch=4)

        outer.addLayout(middle, stretch=1)

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
                make_placeholder("No active match", "Players appear here once a match begins")
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
                make_placeholder("No encounters yet", "Your history with these players shows here")
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
            dlg = MatchStatsDialog(self._history_entries[row], parent=self.window())
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
                    parts.append(name)
                return ",  ".join(parts) if parts else "—"

            def fmt_detail(players):
                """One player per line, with the score/goal detail the visible
                cell text no longer carries. Plain text — names can contain
                characters Qt would otherwise parse as rich text."""
                lines = [
                    f"{p.get('name', '?')}  —  Sc {p.get('score', 0)}, G {p.get('goals', 0)}"
                    for p in players
                ]
                return "\n".join(lines) if lines else "—"

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

            opp_item.setToolTip(fmt_detail(entry.get("opponents", [])))
            team_item.setToolTip(fmt_detail(entry.get("teammates", [])))

            t.setItem(row, 0, session_item)
            t.setItem(row, 1, date_item)
            t.setItem(row, 2, result_item)
            t.setItem(row, 3, score_item)
            t.setItem(row, 4, opp_item)
            t.setItem(row, 5, team_item)
            t.setCellWidget(row, self.HISTORY_DELETE_COL,
                            self._make_match_delete_cell(entry))

    def _make_match_delete_cell(self, entry: dict) -> QWidget:
        """A centred ✕ button for one history row. Lives in a cell *widget*, so
        clicking it doesn't fire `itemClicked` and open the stats dialog."""
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)

        btn = QPushButton("✕")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("Delete this match from history")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # The button fills the cell widget rather than taking a fixed size.
        # `QTableWidget::item { padding: 9px 12px }` in APP_STYLESHEET shrinks a
        # cell widget to the padded content rect (here 20x22, not the 44x40
        # cell), so a fixed size larger than that gets clipped — which showed up
        # as a hover fill with one straight edge instead of a rounded square.
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {FAINT};
                border: none;
                border-radius: 7px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {LOSS_CLR_BG};
                color: {LOSS_CLR};
                border-radius: 7px;
            }}
            QPushButton:pressed {{
                background-color: {LOSS_CLR_BG};
                color: {LOSS_CLR};
                border-radius: 7px;
            }}
        """)
        btn.clicked.connect(lambda _checked=False, e=entry: self._handle_match_delete(e))
        lay.addWidget(btn)
        return holder

    def _handle_match_delete(self, entry: dict):
        """Confirm, then hand the delete off to main.py via
        `match_delete_requested`; it re-emits record/history/sessions to
        re-render everything the removal affects."""
        match_id = entry.get("id")
        if match_id is None:
            return

        result     = entry.get("result", "?").upper()
        date_str   = (entry.get("date") or "")[:10]
        opp_goals  = sum(p.get("goals", 0) for p in entry.get("opponents", []))
        team_goals = sum(p.get("goals", 0) for p in entry.get("teammates", []))
        opponents  = ", ".join(p.get("name", "?") for p in entry.get("opponents", [])) or "—"

        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Delete Match")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"Delete this match?\n\n"
            f"Session {entry.get('sessionNum', '?')}  ·  {date_str}\n"
            f"{result}  {team_goals} – {opp_goals}  vs  {opponents}\n\n"
            "It will be permanently removed from history and your win/loss "
            "record. This cannot be undone."
        )
        delete_btn = confirm.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        confirm.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        confirm.exec()
        if confirm.clickedButton() != delete_btn:
            return

        self._signals.match_delete_requested.emit(match_id)

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
