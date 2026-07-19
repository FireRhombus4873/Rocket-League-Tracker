"""
Builders for the message shapes the app consumes, so tests read at the level of
"a match where I scored 3 and they scored 1" instead of raw API JSON.

The Stats API sends stats in PascalCase (`Goals`, `CarTouches`, ...); these
helpers let tests pass our friendlier lower-case names (`goals=`, `car_touches=`).
"""

# our-name -> the PascalCase key the Stats API actually sends
_STAT_KEYS = {
    "score":       "Score",
    "goals":       "Goals",
    "shots":       "Shots",
    "assists":     "Assists",
    "saves":       "Saves",
    "touches":     "Touches",
    "car_touches": "CarTouches",
    "demos":       "Demos",
}


def player(name: str, primary_id: str, team: int, **stats) -> dict:
    """Build one `Players[]` entry as it appears inside an UpdateState message.

    >>> player("Me", "Steam|1|0", team=0, goals=3)
    {'Name': 'Me', 'PrimaryId': 'Steam|1|0', 'TeamNum': 0, 'Goals': 3}
    """
    entry = {"Name": name, "PrimaryId": primary_id, "TeamNum": team}
    for key, value in stats.items():
        entry[_STAT_KEYS.get(key, key)] = value
    return entry


def update_state(guid: str, players: list, *, time_secs=300.0, overtime=False,
                 team_colors=("3f8fff", "ff6a00")) -> dict:
    """Build an UpdateState `Data` payload (already decoded from the outer
    envelope) with two teams and the given players."""
    teams = [
        {"TeamNum": 0, "ColorPrimary": team_colors[0], "Name": "Blue"},
        {"TeamNum": 1, "ColorPrimary": team_colors[1], "Name": "Orange"},
    ]
    return {
        "MatchGuid": guid,
        "Players": players,
        "Game": {"Teams": teams, "TimeSeconds": time_secs, "bOvertime": overtime},
    }
