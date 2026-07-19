"""
Tier 2 — EventHandler dispatch + the extracted get_winner parser. Pure logic,
no Qt, no sockets: feed an event name + Data dict, assert the forwarded result.
"""
import pytest

from eventHandler import EventHandler, get_winner


def _capture():
    """Return (received_list, callback) so tests can see what got forwarded."""
    received = []
    return received, received.append


def test_match_ended_forwards_winner_team():
    received, cb = _capture()
    EventHandler(on_event_callback=cb).dispatch("MatchEnded", {"WinnerTeamNum": 1})
    assert received == [{"MatchEnded": 1}]


def test_goal_scored_forwards_scorer_name():
    received, cb = _capture()
    EventHandler(on_event_callback=cb).dispatch("GoalScored", {"Scorer": {"Name": "Bob"}})
    assert received == [{"GoalScored": "Bob"}]


def test_match_initialized_carries_no_player_data():
    # Stats API quirk: MatchInitialized has no players — just signals a start.
    received, cb = _capture()
    EventHandler(on_event_callback=cb).dispatch("MatchInitialized", {})
    assert received == [{"MatchInitialised": True}]


def test_statfeed_demolition_branch():
    received, cb = _capture()
    EventHandler(on_event_callback=cb).dispatch("StatfeedEvent", {
        "Type": "Demolition",
        "MainTarget": {"Name": "Attacker"},
        "SecondaryTarget": {"Name": "Victim"},
    })
    assert received == [{"StatDemolition": ["Attacker", "Victim", "Demolition"]}]


def test_unknown_event_does_not_call_back():
    received, cb = _capture()
    EventHandler(on_event_callback=cb).dispatch("NoSuchEvent", {})
    assert received == []


@pytest.mark.parametrize("event, expected", [
    ({"MatchEnded": 0},   0),
    ({"MatchEnded": 1},   1),
    ({"MatchEnded": "1"}, 1),   # API may send it as a string
    ({"MatchEnded": "?"}, -1),  # missing-value sentinel
    ({"MatchEnded": None}, -1),
    ({}, -1),                   # key absent entirely (leaver)
])
def test_get_winner(event, expected):
    assert get_winner(event) == expected
