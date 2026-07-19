"""
Tier 2 — the small platform-string parsers that the UI and persistence rely on.

`_parse_platform` turns a PrimaryId prefix into our display platform; the
`tracker_platform_slug` (lifted out of the dialog) maps that to the URL slug used
for tracker.network profile links. Importing the dialog module pulls in PyQt, but
only at import time — no widget is constructed, so this stays a pure-logic tier.
"""
import pytest

from sessionStore import _parse_platform
from ui.dialogs.match_stats_dialog import tracker_platform_slug


@pytest.mark.parametrize("primary_id, expected", [
    ("steam|76561|0", "Steam"),
    ("epic|abc|0",    "Epic"),
    ("ps4|foo|0",     "PlayStation"),
    ("psn|foo|0",     "PlayStation"),
    ("xbl|foo|0",     "Xbox"),
    ("xboxone|foo|0", "Xbox"),
    ("switch|foo|0",  "Switch"),
    ("mystery|foo|0", "Mystery"),  # unknown prefix -> capitalised
    ("",              "Unknown"),  # empty id -> Unknown, never crashes
])
def test_parse_platform(primary_id, expected):
    assert _parse_platform(primary_id) == expected


@pytest.mark.parametrize("platform, slug", [
    ("Steam",       "steam"),
    ("Epic",        "epic"),
    ("PlayStation", "psn"),
    ("ps4",         "psn"),
    ("ps5",         "psn"),
    ("Xbox",        "xbl"),
    ("xbl",         "xbl"),
    ("xboxone",     "xbl"),
    ("Switch",      "switch"),
    ("Weird",       "weird"),  # unmapped -> lower-cased passthrough
])
def test_tracker_platform_slug(platform, slug):
    assert tracker_platform_slug(platform) == slug
