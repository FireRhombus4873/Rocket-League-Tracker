"""
Tier 2 — SocketHandler message routing.

Covers `_handle_message`, which owns the Stats API's biggest trip-hazard: the
`Data` field is a JSON-encoded *string*, so it must be decoded a second time.
UpdateState is routed to one callback; every other event to the other.

The reconnect/buffer-draining loop in `listen()` blocks on a live TCP socket and
is left to manual/integration testing — the parsing that feeds it lives here.
"""
import json

from socketHandler import SocketHandler


def test_update_state_is_double_decoded_and_routed():
    got = []
    h = SocketHandler(on_update_state_callback=got.append)
    h._handle_message({"Event": "UpdateState", "Data": json.dumps({"MatchGuid": "g1"})})
    assert got == [{"MatchGuid": "g1"}]  # decoded from the JSON string, not left as text


def test_named_event_routed_to_message_callback():
    got = []
    h = SocketHandler(on_message_callback=lambda event, data: got.append((event, data)))
    h._handle_message({"Event": "GoalScored", "Data": json.dumps({"Scorer": {"Name": "X"}})})
    assert got == [("GoalScored", {"Scorer": {"Name": "X"}})]


def test_data_already_object_passes_through():
    # Defensive branch: if Data is already a dict, don't try to json.loads it.
    got = []
    h = SocketHandler(on_message_callback=lambda event, data: got.append((event, data)))
    h._handle_message({"Event": "GoalScored", "Data": {"Scorer": {"Name": "Y"}}})
    assert got == [("GoalScored", {"Scorer": {"Name": "Y"}})]


def test_missing_data_defaults_to_empty_object():
    got = []
    h = SocketHandler(on_message_callback=lambda event, data: got.append((event, data)))
    h._handle_message({"Event": "CountdownBegin"})
    assert got == [("CountdownBegin", {})]
