"""
Tier 3 — thin UI wiring tests (pytest-qt).

Deliberately shallow: we assert that the signal a background thread emits lands
in the right widget, NOT pixels, colours, or stylesheet strings (those churn on
every restyle and testing them just creates busywork). These survive any visual
redesign and only fail if the signal -> slot -> widget path actually breaks.

Marked `ui` so a headless box can skip them with `-m "not ui"`. Skipped cleanly
if pytest-qt isn't installed.
"""
import pytest

pytest.importorskip("pytestqt")  # provides the `qtbot` fixture + a QApplication

pytestmark = pytest.mark.ui


@pytest.fixture
def window(qtbot):
    from ui import MainWindow
    # close_to_tray=False is what a source run uses, and it matters here:
    # qtbot closes every registered widget at teardown, so the tray branch of
    # closeEvent would pop the real "still running in the background" balloon
    # after each test. This branch takes the tray icon down instead.
    w = MainWindow(close_to_tray=False)
    qtbot.addWidget(w)
    return w


# The tabs are children of the registered window — don't qtbot.addWidget them,
# double-registration risks a double-delete at teardown. Signals are still
# emitted on `window`, so these tests keep exercising the full wiring.
@pytest.fixture
def tracker(window):
    return window._tracker_tab


@pytest.fixture
def sessions(window):
    return window._sessions_tab


@pytest.fixture
def analytics(window):
    return window._analytics_tab


def test_record_updated_updates_cards(window, tracker):
    window.signals.record_updated.emit(3, 1)
    assert tracker._wins_card._value_label.text() == "3"
    assert tracker._losses_card._value_label.text() == "1"
    assert tracker._ratio_card._value_label.text() == "75%"


def test_record_updated_ratio_dash_when_no_games(window, tracker):
    window.signals.record_updated.emit(0, 0)
    assert tracker._ratio_card._value_label.text() == "—"


def test_status_changed_updates_label(window):
    window.signals.status_changed.emit("Connected — waiting for match")
    assert window._status_label.text() == "Connected — waiting for match"


def test_sessions_updated_updates_totals(window, sessions):
    window.signals.sessions_updated.emit([
        {"sessionNum": 2, "firstDate": "2024-01-02", "lastDate": "2024-01-02",
         "matches": 3, "wins": 2, "losses": 1, "winPct": 2 / 3,
         "bestWinStreak": 2, "worstLossStreak": 1},
        {"sessionNum": 1, "firstDate": "2024-01-01", "lastDate": "2024-01-01",
         "matches": 1, "wins": 0, "losses": 1, "winPct": 0.0,
         "bestWinStreak": 0, "worstLossStreak": 1},
    ])
    assert sessions._sessions_title_lbl.text() == "ALL SESSIONS  ·  2"
    assert sessions._sessions_totals_lbl.text() == "2W  2L  ·  50%"


def test_history_row_selection_enables_delete_after_session_select(window, tracker):
    # A rendered history table should populate rows without error.
    window.signals.history_updated.emit([
        {"date": "2024-01-01T12:00:00", "result": "win", "sessionNum": 1,
         "opponents": [{"name": "Foe", "platform": "Steam", "score": 100, "goals": 1}],
         "teammates": []},
    ], 1)
    assert tracker._history_table.rowCount() == 1


def test_history_delete_button_targets_the_right_match(window, tracker, qtbot, monkeypatch):
    """Each row's ✕ must carry that row's match id through to the signal —
    the confirmation dialog itself is modal, so it's stubbed out here."""
    from PyQt6.QtWidgets import QPushButton

    def entry(mid, result):
        return {"id": mid, "date": "2024-01-0%dT12:00:00" % mid, "result": result,
                "sessionNum": 1, "opponents": [], "teammates": []}

    window.signals.history_updated.emit([entry(1, "win"), entry(2, "loss")], 1)

    # The click handler resolves self._handle_match_delete at call time, so an
    # instance-level stub applies to the already-built buttons.
    monkeypatch.setattr(
        tracker, "_handle_match_delete",
        lambda e: window.signals.match_delete_requested.emit(e["id"]),
    )

    cell = tracker._history_table.cellWidget(1, tracker.HISTORY_DELETE_COL)
    with qtbot.waitSignal(window.signals.match_delete_requested) as blocker:
        cell.findChild(QPushButton).click()
    assert blocker.args == [2]  # second row, not the first


def _stats(**overrides):
    base = {"score": 400.0, "goals": 1.5, "shots": 3.0, "assists": 0.5,
            "saves": 1.0, "touches": 30.0, "demos": 0.2}
    base.update(overrides)
    return base


def test_analytics_updated_populates_cards_and_leaderboards(window, analytics):
    window.signals.analytics_updated.emit({
        "overview": {"matches": 3, "wins": 2, "losses": 1, "winPct": 2 / 3,
                     "sessions": 1, "bestWinStreak": 2, "worstLossStreak": 1,
                     "timedMatches": 2, "avgDurationSecs": 292.0,
                     "avgDurationWins": 280.0, "avgDurationLosses": 300.0,
                     "avgDurationReg": 290.0, "avgDurationOT": 300.0,
                     "overtimeMatches": 1, "overtimeWins": 0, "overtimeLosses": 1,
                     "overtimeWinPct": 0.0, "overtimeShare": 1 / 3,
                     "regulationMatches": 2, "regulationWinPct": 1.0,
                     "firstDate": "2026-07-01T10:00:00",
                     "lastDate": "2026-07-02T10:00:00"},
        "winRateBySession": [{"sessionNum": 1, "matches": 3, "wins": 2, "losses": 1,
                              "winPct": 2 / 3, "rollingWinPct": 2 / 3}],
        "rollingWindow": 5,
        "selfStats": {"matches": 3, "avg": _stats(), "avgInWins": _stats(score=500.0),
                      "avgInLosses": _stats(score=200.0), "shotAccuracy": 0.5,
                      "best": {"matchId": 3, "date": "2026-07-02T10:00:00",
                               "result": "win", "score": 812, "goals": 4,
                               "shots": 6, "assists": 1, "saves": 3},
                      "worst": {"matchId": 1, "date": "2026-07-01T10:00:00",
                                "result": "loss", "score": 120, "goals": 0,
                                "shots": 1, "assists": 0, "saves": 0}},
        "timeOfDay": [{"label": "00–04", "matches": 3, "wins": 2, "losses": 1,
                       "winPct": 2 / 3}],
        "dayOfWeek": [{"label": "Mon", "matches": 3, "wins": 2, "losses": 1,
                       "winPct": 2 / 3}],
        "opponents": [{"playerId": "Epic|2|0", "name": "R", "played": 3, "wins": 2,
                       "losses": 1, "winPct": 2 / 3, "lastDate": ""}],
        "teammates": [],
    })
    assert analytics._analytics_record_card._value_label.text() == "2W / 1L"
    assert analytics._analytics_winrate_card._value_label.text() == "67%"
    assert analytics._analytics_duration_card._value_label.text() == "4:52"
    assert analytics._analytics_overtime_card._value_label.text() == "0W / 1L"
    assert analytics._analytics_opponents_table.rowCount() == 1
    assert analytics._analytics_teammates_table.rowCount() == 0
    # isHidden(), not isVisible(): the window is never shown in these tests, so
    # isVisible() is False for every widget regardless of the empty-state toggle.
    assert analytics._analytics_teammates_empty_lbl.isHidden() is False
    assert analytics._analytics_opponents_empty_lbl.isHidden() is True


def test_analytics_updated_survives_an_empty_payload(window, analytics):
    """The slot reads ~40 keys off the payload; a single missing one would break
    the whole refresh chain, which runs on the socket thread."""
    window.signals.analytics_updated.emit({})
    assert analytics._analytics_record_card._value_label.text() == "0W / 0L"
    assert analytics._analytics_winrate_card._value_label.text() == "—"
    assert analytics._analytics_duration_card._value_label.text() == "—"
