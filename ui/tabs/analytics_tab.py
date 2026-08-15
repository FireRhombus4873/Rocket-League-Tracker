"""
AnalyticsTab — six stacked cards inside the repo's only QScrollArea, rendered
from one SessionStore.get_analytics() snapshot via `analytics_updated`.

In order: overview stat cards → win rate over time → your averages → overtime
& match length → win rate by time of day / day of week → leaderboards.
Everything is all-time; there is no scope selector.

Read-only: this tab emits nothing and never reaches into another tab.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem,
    QHeaderView, QFrame, QScrollArea,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from ..theme import (
    BG_TABLE, TEXT, SUBTEXT, FAINT,
    ACCENT, ACCENT2, WIN_CLR, LOSS_CLR, BORDER_SOFT,
)
from ..widgets import Card, stat_card, make_table
from ..charts import ComparisonBars, WinRateBarChart, WinRateTrendChart
from ..signals import UISignals


# Module-level so they're reachable from the Tier 2 tests without a Qt window,
# same rationale as get_winner / tracker_platform_slug.
def _fmt_mmss(seconds: float) -> str:
    """4:52 from 292.4. "—" for 0/negative, i.e. nothing was timed."""
    if not seconds or seconds <= 0:
        return "—"
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _fmt_pct(value: float, total: int) -> str:
    """"63%", or "—" when there's nothing to divide."""
    return f"{value:.0%}" if total else "—"


class AnalyticsTab(QWidget):
    def __init__(self, signals: UISignals, parent=None):
        super().__init__(parent)
        self._build_ui()

        signals.analytics_updated.connect(self._on_analytics_updated)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        """Six stacked cards inside a scroll area, fed by one analytics_updated
        snapshot."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # APP_STYLESHEET doesn't cover QScrollArea: without NoFrame it draws a
        # sunken grey box against BG_DARK, and the viewport has to stay transparent
        # so the cards' shadows sit on the tab background. The scrollbar itself is
        # already themed by APP_STYLESHEET.
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        col = QVBoxLayout(body)
        col.setContentsMargins(2, 2, 14, 10)    # right margin clears the scrollbar
        col.setSpacing(16)

        self._analytics_empty_lbl = QLabel(
            "No matches recorded yet — analytics fill in as you play."
        )
        self._analytics_empty_lbl.setStyleSheet(f"color: {SUBTEXT}; padding: 32px 4px;")
        self._analytics_empty_lbl.setVisible(False)
        col.addWidget(self._analytics_empty_lbl)

        # Summary numbers first, so the most-wanted figures need no scrolling.
        self._analytics_cards = [
            self._build_analytics_overview_row(),
            self._build_analytics_trend_card(),
            self._build_analytics_averages_card(),
            self._build_analytics_overtime_card(),
            self._build_analytics_timing_card(),
            self._build_analytics_leaderboards_row(),
        ]
        for widget in self._analytics_cards:
            col.addWidget(widget)
        # No stretch on any card — each is fixed-height and this absorbs the slack,
        # which is what keeps the scroll area well-behaved.
        col.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

    def _build_analytics_overview_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Widths are set by the longest *caption*, not the value — measured at
        # 8pt with room for four-digit match counts.
        self._analytics_record_card = stat_card(
            "ALL-TIME RECORD", "0W / 0L", TEXT, "no matches yet",
            value_pt=21, width=206)
        self._analytics_winrate_card = stat_card(
            "WIN RATE", "—", ACCENT2, "best run — · worst —",
            value_pt=28, width=178)
        self._analytics_duration_card = stat_card(
            "AVG DURATION", "—", TEXT, "no timed matches",
            value_pt=28, width=168)
        self._analytics_overtime_card = stat_card(
            "OVERTIME", "0W / 0L", ACCENT, "— of matches",
            value_pt=21, width=178)

        for card in (self._analytics_record_card, self._analytics_winrate_card,
                     self._analytics_duration_card, self._analytics_overtime_card):
            layout.addWidget(card)
        layout.addStretch()
        return row

    def _build_analytics_trend_card(self) -> QWidget:
        card = Card("Win Rate Over Time")
        self._analytics_trend_note_lbl = QLabel()
        self._analytics_trend_note_lbl.setStyleSheet(
            f"color: {SUBTEXT}; background: transparent; border: none;")
        card.content_layout.addWidget(self._analytics_trend_note_lbl)

        self._analytics_trend_chart = WinRateTrendChart()
        card.content_layout.addWidget(self._analytics_trend_chart)
        return card

    def _build_analytics_averages_card(self) -> QWidget:
        card = Card("Your Averages")

        self._analytics_self_summary_lbl = QLabel()
        self._analytics_self_summary_lbl.setFont(QFont("Segoe UI", 12))
        self._analytics_self_summary_lbl.setWordWrap(True)
        self._analytics_self_summary_lbl.setStyleSheet(
            f"color: {TEXT}; background: transparent; border: none;")
        card.content_layout.addWidget(self._analytics_self_summary_lbl)

        self._analytics_self_note_lbl = QLabel()
        self._analytics_self_note_lbl.setFont(QFont("Segoe UI", 9))
        self._analytics_self_note_lbl.setWordWrap(True)
        self._analytics_self_note_lbl.setStyleSheet(
            f"color: {FAINT}; background: transparent; border: none;")
        self._analytics_self_note_lbl.setVisible(False)
        card.content_layout.addWidget(self._analytics_self_note_lbl)

        self._analytics_self_chart = ComparisonBars(
            left_label="In wins", right_label="In losses")
        card.content_layout.addWidget(self._analytics_self_chart)

        panels = QHBoxLayout()
        panels.setSpacing(12)
        best, self._analytics_best_lbl, self._analytics_best_sub_lbl = \
            self._make_analytics_mini_panel("BEST MATCH")
        worst, self._analytics_worst_lbl, self._analytics_worst_sub_lbl = \
            self._make_analytics_mini_panel("WORST MATCH")
        panels.addWidget(best, stretch=1)
        panels.addWidget(worst, stretch=1)
        card.content_layout.addLayout(panels)
        return card

    def _make_analytics_mini_panel(self, title: str):
        """A recessed sub-panel inside a Card — the Sessions-tab QFrame#panel
        pattern, minus the shadow (it's already on an elevated surface).
        Returns (frame, value_label, sub_label)."""
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setStyleSheet(
            f"QFrame#panel {{ background-color: {BG_TABLE}; "
            f"border: 1px solid {BORDER_SOFT}; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(3)

        heading = QLabel(title)
        heading.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        heading.setStyleSheet(
            f"color: {FAINT}; background: transparent; border: none; "
            f"letter-spacing: 1.1px;")
        layout.addWidget(heading)

        value = QLabel("—")
        value.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        value.setStyleSheet(f"color: {TEXT}; background: transparent; border: none;")
        layout.addWidget(value)

        sub = QLabel()
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet(f"color: {SUBTEXT}; background: transparent; border: none;")
        layout.addWidget(sub)
        return frame, value, sub

    def _build_analytics_overtime_card(self) -> QWidget:
        card = Card("Overtime & Match Length")
        # The detail behind the two overview cards, not a duplicate of them.
        self._analytics_ot_chart = ComparisonBars(
            left_label="Regulation", right_label="Overtime",
            left_colour=ACCENT2, right_colour=ACCENT)
        card.content_layout.addWidget(self._analytics_ot_chart)

        self._analytics_duration_note_lbl = QLabel()
        self._analytics_duration_note_lbl.setFont(QFont("Segoe UI", 9))
        self._analytics_duration_note_lbl.setWordWrap(True)
        self._analytics_duration_note_lbl.setStyleSheet(
            f"color: {SUBTEXT}; background: transparent; border: none;")
        card.content_layout.addWidget(self._analytics_duration_note_lbl)
        return card

    def _build_analytics_timing_card(self) -> QWidget:
        card = Card("When You Play Best")
        columns = QHBoxLayout()
        columns.setSpacing(18)

        self._analytics_tod_chart = WinRateBarChart()
        self._analytics_dow_chart = WinRateBarChart()
        for heading, chart in (("BY TIME OF DAY", self._analytics_tod_chart),
                               ("BY DAY OF WEEK", self._analytics_dow_chart)):
            column = QVBoxLayout()
            column.setSpacing(6)
            label = QLabel(heading)
            label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            label.setStyleSheet(
                f"color: {SUBTEXT}; background: transparent; border: none; "
                f"letter-spacing: 1.1px;")
            column.addWidget(label)
            column.addWidget(chart)
            columns.addLayout(column, stretch=1)
        card.content_layout.addLayout(columns)

        caption = QLabel(
            "Win rate per bucket; the number under each bar is matches played. "
            "Buckets with fewer than 3 matches are greyed out."
        )
        caption.setFont(QFont("Segoe UI", 9))
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {FAINT}; background: transparent; border: none;")
        card.content_layout.addWidget(caption)
        return card

    def _build_analytics_leaderboards_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        specs = [
            ("Toughest Opponents", "Your record against them, min. 3 matches",
             "No opponent faced 3 times yet."),
            ("Most-Played Teammates", "Your record together, min. 2 matches",
             "No repeat teammates yet."),
        ]
        built = []
        for title, note_text, empty_text in specs:
            card = Card(title)
            note = QLabel(note_text)
            note.setFont(QFont("Segoe UI", 9))
            note.setStyleSheet(f"color: {FAINT}; background: transparent; border: none;")
            card.content_layout.addWidget(note)

            table = make_table(["Player", "Played", "W", "L", "Win %"])
            # Let the name column absorb the slack (as the history table does) so
            # the short numeric columns stay next to their headers.
            header = table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in (1, 2, 3, 4):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            # The tab's QScrollArea owns scrolling; these grow to fit their rows.
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            card.content_layout.addWidget(table)

            empty = QLabel(empty_text)
            empty.setStyleSheet(f"color: {SUBTEXT}; padding: 24px;")
            empty.setVisible(False)
            card.content_layout.addWidget(empty)

            layout.addWidget(card, stretch=1)
            built.append((table, empty))

        (self._analytics_opponents_table, self._analytics_opponents_empty_lbl), \
            (self._analytics_teammates_table, self._analytics_teammates_empty_lbl) = built
        return row

    # ------------------------------------------------------------------
    # Slot handlers (called on main thread via signals)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _on_analytics_updated(self, data: dict):
        """Render every Analytics view from one SessionStore.get_analytics()
        snapshot. Widgets are mutated in place, never rebuilt, so the scroll
        position survives a refresh (which fires after every match).

        Every field is read with .get(): this runs off a signal emitted on the
        socket thread's refresh chain, and one KeyError here would break it."""
        overview = data.get("overview") or {}
        total    = overview.get("matches", 0)

        self._analytics_empty_lbl.setVisible(not total)
        for widget in self._analytics_cards:
            widget.setVisible(bool(total))

        self._fill_analytics_overview(overview)
        self._fill_analytics_trend(data)
        self._fill_analytics_self(data.get("selfStats") or {}, total)
        self._fill_analytics_overtime(overview)
        self._analytics_tod_chart.set_data(data.get("timeOfDay") or [])
        self._analytics_dow_chart.set_data(data.get("dayOfWeek") or [])
        self._fill_analytics_leaderboard(
            self._analytics_opponents_table, self._analytics_opponents_empty_lbl,
            data.get("opponents") or [])
        self._fill_analytics_leaderboard(
            self._analytics_teammates_table, self._analytics_teammates_empty_lbl,
            data.get("teammates") or [])

    def _fill_analytics_overview(self, overview: dict):
        wins   = overview.get("wins", 0)
        losses = overview.get("losses", 0)
        total  = overview.get("matches", 0)

        self._analytics_record_card._value_label.setText(f"{wins}W / {losses}L")
        sessions = overview.get("sessions", 0)
        self._analytics_record_card._caption_label.setText(
            f"{total} matches · {sessions} session{'' if sessions == 1 else 's'}"
            if total else "no matches yet"
        )

        self._analytics_winrate_card._value_label.setText(
            _fmt_pct(overview.get("winPct", 0.0), total))
        self._analytics_winrate_card._caption_label.setText(
            f"best run {overview.get('bestWinStreak', 0)}W · "
            f"worst {overview.get('worstLossStreak', 0)}L"
        )

        timed = overview.get("timedMatches", 0)
        self._analytics_duration_card._value_label.setText(
            _fmt_mmss(overview.get("avgDurationSecs", 0.0)))
        self._analytics_duration_card._caption_label.setText(
            f"{timed} timed match{'' if timed == 1 else 'es'}"
            if timed else "no timed matches"
        )

        ot = overview.get("overtimeMatches", 0)
        self._analytics_overtime_card._value_label.setText(
            f"{overview.get('overtimeWins', 0)}W / {overview.get('overtimeLosses', 0)}L")
        self._analytics_overtime_card._caption_label.setText(
            f"{_fmt_pct(overview.get('overtimeShare', 0.0), total)} of matches"
            if ot else "no overtime yet"
        )

    def _fill_analytics_trend(self, data: dict):
        window = data.get("rollingWindow", 5)
        points = data.get("winRateBySession") or []
        self._analytics_trend_note_lbl.setText(
            f"Each point is one session · line is a {window}-session rolling average"
            if len(points) > 1 else
            "One session so far — the trend appears once you've played a few more."
        )
        self._analytics_trend_chart.set_data(points, window)

    def _fill_analytics_self(self, self_stats: dict, total_matches: int):
        avg      = self_stats.get("avg") or {}
        attribed = self_stats.get("matches", 0)

        def number(value: str, unit: str) -> str:
            return (f"<span style='color:{TEXT}'>{value}</span> "
                    f"<span style='color:{SUBTEXT}'>{unit}</span>")

        if attribed:
            parts = [
                number(f"{avg.get('goals', 0):.2f}",   "goals"),
                number(f"{avg.get('shots', 0):.2f}",   "shots"),
                number(f"{avg.get('saves', 0):.2f}",   "saves"),
                number(f"{avg.get('assists', 0):.2f}", "assists"),
                number(f"{avg.get('score', 0):.0f}",   "score per match"),
            ]
            summary = "  ·  ".join(parts)
            accuracy = self_stats.get("shotAccuracy", 0.0)
            summary += (f"<br><span style='color:{TEXT}'>{accuracy:.0%}</span> "
                        f"<span style='color:{SUBTEXT}'>shot accuracy</span>")
        else:
            summary = f"<span style='color:{SUBTEXT}'>No matches attributed to you yet.</span>"
        self._analytics_self_summary_lbl.setText(summary)

        # Be honest about any gap rather than quietly averaging fewer matches.
        if total_matches and not attribed:
            note = ("Set your in-game name in Settings so your own stats can be "
                    "identified.")
        elif attribed < total_matches:
            note = (f"From {attribed} of {total_matches} matches — older matches "
                    f"can't be attributed to you.")
        else:
            note = ""
        self._analytics_self_note_lbl.setText(note)
        self._analytics_self_note_lbl.setVisible(bool(note))

        in_wins   = self_stats.get("avgInWins") or {}
        in_losses = self_stats.get("avgInLosses") or {}
        rows = []
        for label, key, fmt in (("GOALS",   "goals",   "{:.2f}"),
                                ("SHOTS",   "shots",   "{:.2f}"),
                                ("SAVES",   "saves",   "{:.2f}"),
                                ("ASSISTS", "assists", "{:.2f}"),
                                ("SCORE",   "score",   "{:.0f}")):
            left  = in_wins.get(key, 0) or 0
            right = in_losses.get(key, 0) or 0
            rows.append({
                "label":      label,
                "left":       left,
                "right":      right,
                "left_text":  fmt.format(left),
                "right_text": fmt.format(right),
                "max":        None,
            })
        self._analytics_self_chart.set_data(rows)

        for panel_key, value_lbl, sub_lbl in (
            ("best",  self._analytics_best_lbl,  self._analytics_best_sub_lbl),
            ("worst", self._analytics_worst_lbl, self._analytics_worst_sub_lbl),
        ):
            match = self_stats.get(panel_key) or {}
            if not match:
                value_lbl.setText("—")
                sub_lbl.setText("")
                continue
            value_lbl.setText(
                f"{match.get('score', 0)} score · {match.get('goals', 0)}G "
                f"{match.get('shots', 0)}S {match.get('saves', 0)}Sv"
            )
            result = match.get("result", "")
            colour = WIN_CLR if result == "win" else LOSS_CLR
            sub_lbl.setText(
                f"<span style='color:{colour}'>{result.upper()}</span>"
                f"<span style='color:{SUBTEXT}'> · {match.get('date', '')[:10]}</span>"
            )

    def _fill_analytics_overtime(self, overview: dict):
        reg_matches = overview.get("regulationMatches", 0)
        ot_matches  = overview.get("overtimeMatches", 0)
        reg_pct     = overview.get("regulationWinPct", 0.0)
        ot_pct      = overview.get("overtimeWinPct", 0.0)
        reg_len     = overview.get("avgDurationReg", 0.0)
        ot_len      = overview.get("avgDurationOT", 0.0)

        self._analytics_ot_chart.set_data([
            {"label": "MATCHES", "left": reg_matches, "right": ot_matches,
             "left_text": str(reg_matches), "right_text": str(ot_matches),
             "max": None},
            # Pinned to 1.0 so a 58% bar really is 58% of the track.
            {"label": "WIN RATE", "left": reg_pct, "right": ot_pct,
             "left_text": _fmt_pct(reg_pct, reg_matches),
             "right_text": _fmt_pct(ot_pct, ot_matches),
             "max": 1.0},
            {"label": "AVG LENGTH", "left": reg_len, "right": ot_len,
             "left_text": _fmt_mmss(reg_len), "right_text": _fmt_mmss(ot_len),
             "max": None},
        ])

        total = overview.get("matches", 0)
        timed = overview.get("timedMatches", 0)
        note = (
            f"Average length {_fmt_mmss(overview.get('avgDurationWins', 0.0))} in wins "
            f"· {_fmt_mmss(overview.get('avgDurationLosses', 0.0))} in losses. "
            f"Duration is the regulation clock, so overtime beyond it isn't counted."
        )
        if total > timed:
            missing = total - timed
            note += f" {missing} match{'' if missing == 1 else 'es'} have no timing data."
        self._analytics_duration_note_lbl.setText(note)

    def _fill_analytics_leaderboard(self, table, empty_lbl, rows: list):
        table.setRowCount(0)
        empty_lbl.setVisible(not rows)
        table.setVisible(bool(rows))
        for entry in rows:
            row = table.rowCount()
            table.insertRow(row)
            values = [
                (entry.get("name", "?"),                            TEXT),
                (str(entry.get("played", 0)),                       SUBTEXT),
                (str(entry.get("wins", 0)),                         WIN_CLR),
                (str(entry.get("losses", 0)),                       LOSS_CLR),
                (_fmt_pct(entry.get("winPct", 0.0),
                          entry.get("played", 0)),                  TEXT),
            ]
            for col, (text, colour) in enumerate(values):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(colour))
                table.setItem(row, col, item)
        # No inner scrolling — the tab's QScrollArea owns that — so the table has
        # to be exactly as tall as its rows plus the header.
        table.setFixedHeight(max(1, table.rowCount()) * 40 + 36)
