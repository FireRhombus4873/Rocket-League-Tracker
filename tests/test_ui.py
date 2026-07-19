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
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_record_updated_updates_cards(window):
    window.signals.record_updated.emit(3, 1)
    assert window._wins_card._value_label.text() == "3"
    assert window._losses_card._value_label.text() == "1"
    assert window._ratio_card._value_label.text() == "75%"


def test_record_updated_ratio_dash_when_no_games(window):
    window.signals.record_updated.emit(0, 0)
    assert window._ratio_card._value_label.text() == "—"


def test_status_changed_updates_label(window):
    window.signals.status_changed.emit("Connected — waiting for match")
    assert window._status_label.text() == "Connected — waiting for match"


def test_sessions_updated_updates_totals(window):
    window.signals.sessions_updated.emit([
        {"sessionNum": 2, "firstDate": "2024-01-02", "lastDate": "2024-01-02",
         "matches": 3, "wins": 2, "losses": 1, "winPct": 2 / 3,
         "bestWinStreak": 2, "worstLossStreak": 1},
        {"sessionNum": 1, "firstDate": "2024-01-01", "lastDate": "2024-01-01",
         "matches": 1, "wins": 0, "losses": 1, "winPct": 0.0,
         "bestWinStreak": 0, "worstLossStreak": 1},
    ])
    assert window._sessions_title_lbl.text() == "ALL SESSIONS  ·  2"
    assert window._sessions_totals_lbl.text() == "2W  2L  ·  50%"


def test_history_row_selection_enables_delete_after_session_select(window):
    # A rendered history table should populate rows without error.
    window.signals.history_updated.emit([
        {"date": "2024-01-01T12:00:00", "result": "win", "sessionNum": 1,
         "opponents": [{"name": "Foe", "platform": "Steam", "score": 100, "goals": 1}],
         "teammates": []},
    ], 1)
    assert window._history_table.rowCount() == 1
