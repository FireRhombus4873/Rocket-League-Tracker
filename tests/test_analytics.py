"""
Tier 1 — the analytics data layer (SessionStore.get_analytics against a real,
temporary SQLite DB).

Split out from test_session_store.py because that file is scoped to the match
lifecycle scenarios in CLAUDE.md's "Editing sessionStore.py", while these tests
need their own arrangement tricks: back-dating `played_at` after the fact (the
clock isn't injectable) and `update_state(time_secs=None)` for a NULL duration.

Same `store` fixture, same rule: drive the real recording pipeline, then assert
on the returned payload and on the DB itself.
"""
from factories import player, update_state


def _record(store, guid, winner, *, time_secs=300.0, end_secs=None,
            overtime=False, when=None, extra=(), **you):
    """Record one finished match, optionally back-dated. Returns its match id.

    `played_at` comes from datetime.now() inside record_result, so there's no way
    to drive a specific timestamp through the public API. Rewriting the row
    afterwards is narrower than monkeypatching the clock and leaves the recording
    path itself under test.

    Goals are non-zero and consistent with `winner` — record_result skips a roster
    where nobody scored at all. `**you` sets extra stats on the local player;
    `extra` is a list of additional Players[] entries.
    """
    roster = [
        player("Me", "Steam|1|0", team=0, goals=(3 if winner == 0 else 1), **you),
        player("R",  "Epic|2|0",  team=1, goals=(1 if winner == 0 else 3)),
        *extra,
    ]
    store.try_set_players_from_update(
        update_state(guid, roster, time_secs=time_secs, overtime=overtime), "Me")
    if end_secs is not None:
        # A second tick moves the clock, so duration = start - end.
        store.try_set_players_from_update(
            update_state(guid, roster, time_secs=end_secs, overtime=overtime), "Me")
    store.record_result(winner_team=winner)

    match_id = store.match_history[-1]["id"]
    if when is not None:
        store.cursor.execute(
            "UPDATE matches SET played_at = ? WHERE id = ?", (when, match_id))
        store.conn.commit()
    return match_id


def _local_ids(store):
    return [r[0] for r in store.cursor.execute(
        "SELECT local_player_id FROM matches ORDER BY id ASC").fetchall()]


# ---------------------------------------------------------------------------
# Schema v3 + local-player attribution
# ---------------------------------------------------------------------------

def test_schema_is_v3_with_local_player_id(store):
    assert store.cursor.execute("PRAGMA user_version").fetchone()[0] == 3
    cols = [r[1] for r in store.cursor.execute("PRAGMA table_info(matches)").fetchall()]
    assert "local_player_id" in cols


def test_record_result_stamps_the_local_player_id(store):
    store.new_session()
    _record(store, "m1", winner=0)

    assert _local_ids(store) == ["Steam|1|0"]


def test_backfill_attributes_rows_with_a_null_local_player_id(store):
    store.new_session()
    _record(store, "m1", winner=0)
    _record(store, "m2", winner=1)
    # Simulate rows written before schema v3.
    store.cursor.execute("UPDATE matches SET local_player_id = NULL")
    store.conn.commit()

    store.set_local_username("me")  # lower-case: matching is case-insensitive

    assert _local_ids(store) == ["Steam|1|0", "Steam|1|0"]


def test_backfill_leaves_ambiguous_matches_alone(store):
    store.new_session()
    # A real teammate who happens to share the local display name. Guessing here
    # would attribute the match to the wrong player, so it must stay NULL.
    _record(store, "m1", winner=0,
            extra=[player("Me", "Epic|7|0", team=0, goals=1)])
    store.cursor.execute("UPDATE matches SET local_player_id = NULL")
    store.conn.commit()

    store.set_local_username("Me")

    assert _local_ids(store) == [None]


def test_backfill_does_not_overwrite_existing_attribution(store):
    store.new_session()
    _record(store, "m1", winner=0)
    store.cursor.execute("UPDATE matches SET local_player_id = 'sentinel'")
    store.conn.commit()

    store.set_local_username("Me")

    assert _local_ids(store) == ["sentinel"]


# ---------------------------------------------------------------------------
# Overview, duration and overtime
# ---------------------------------------------------------------------------

def test_overview_totals_and_streaks(store):
    store.new_session()
    for i, winner in enumerate([0, 1, 0, 0]):   # W, L, W, W
        _record(store, f"m{i}", winner=winner)

    ov = store.get_analytics()["overview"]
    assert (ov["matches"], ov["wins"], ov["losses"]) == (4, 3, 1)
    assert ov["winPct"] == 0.75
    assert ov["bestWinStreak"] == 2
    assert ov["worstLossStreak"] == 1
    assert ov["sessions"] == 1


def test_avg_duration_ignores_untimed_matches(store):
    store.new_session()
    _record(store, "m1", winner=0, time_secs=300.0, end_secs=45.0)    # 255s
    _record(store, "m2", winner=0, time_secs=300.0, end_secs=155.0)   # 145s
    _record(store, "m3", winner=0, time_secs=None)                    # NULL

    ov = store.get_analytics()["overview"]
    assert ov["matches"] == 3            # the untimed match still counts as a match
    assert ov["timedMatches"] == 2
    assert ov["avgDurationSecs"] == 200


def test_overtime_record_split(store):
    store.new_session()
    _record(store, "m1", winner=1, overtime=True)   # OT loss
    _record(store, "m2", winner=0)                  # regulation win
    _record(store, "m3", winner=0)                  # regulation win

    ov = store.get_analytics()["overview"]
    assert (ov["overtimeMatches"], ov["overtimeWins"], ov["overtimeLosses"]) == (1, 0, 1)
    assert ov["overtimeWinPct"] == 0.0
    assert ov["regulationMatches"] == 2
    assert ov["regulationWinPct"] == 1.0
    assert ov["overtimeShare"] == 1 / 3


# ---------------------------------------------------------------------------
# Win rate over time, time-of-day / day-of-week buckets
# ---------------------------------------------------------------------------

def test_win_rate_by_session_is_chronological_with_rolling_average(store):
    store.new_session()                     # session 1: 2W
    _record(store, "s1a", winner=0)
    _record(store, "s1b", winner=0)
    store.new_session()                     # session 2: 2L
    _record(store, "s2a", winner=1)
    _record(store, "s2b", winner=1)
    store.new_session()                     # session 3: 1W
    _record(store, "s3a", winner=0)

    points = store.get_analytics()["winRateBySession"]
    assert [p["sessionNum"] for p in points] == [1, 2, 3]
    assert [p["winPct"] for p in points] == [1.0, 0.0, 1.0]
    # Match-weighted over the trailing window, not a mean of the percentages:
    # 3 wins from 5 matches, not (1.0 + 0.0 + 1.0) / 3.
    assert points[-1]["rollingWinPct"] == 3 / 5


def test_time_and_day_buckets_skip_rows_with_no_timestamp(store):
    store.new_session()
    _record(store, "m1", winner=0, when="2026-07-20T09:15:00")   # Monday, 08-12
    _record(store, "m2", winner=1, when="2026-07-25T22:40:00")   # Saturday, 20-24
    _record(store, "m3", winner=0, when="")                      # legacy row

    data = store.get_analytics()
    assert data["overview"]["matches"] == 3

    tod = {b["label"]: b for b in data["timeOfDay"]}
    dow = {b["label"]: b for b in data["dayOfWeek"]}
    assert (tod["08–12"]["matches"], tod["08–12"]["wins"]) == (1, 1)
    assert (tod["20–24"]["matches"], tod["20–24"]["wins"]) == (1, 0)
    assert (dow["Mon"]["matches"], dow["Mon"]["winPct"]) == (1, 1.0)
    assert (dow["Sat"]["matches"], dow["Sat"]["winPct"]) == (1, 0.0)

    # The undated match is counted in the overview but can't be bucketed.
    assert sum(b["matches"] for b in data["timeOfDay"]) == 2
    # Buckets are fixed-length so the charts always have a full x axis.
    assert (len(data["timeOfDay"]), len(data["dayOfWeek"])) == (6, 7)


# ---------------------------------------------------------------------------
# Your own stats
# ---------------------------------------------------------------------------

def test_self_stats_averages_and_best_worst(store):
    store.new_session()
    good = _record(store, "m1", winner=0, score=600, saves=4, shots=6)
    bad  = _record(store, "m2", winner=1, score=200, saves=0, shots=2)

    self_stats = store.get_analytics()["selfStats"]
    assert self_stats["matches"] == 2
    assert self_stats["avg"]["score"] == 400
    assert self_stats["avg"]["saves"] == 2
    assert self_stats["avgInWins"]["score"] == 600
    assert self_stats["avgInLosses"]["score"] == 200
    # 3 goals from 6 shots in the win, 1 from 2 in the loss.
    assert self_stats["shotAccuracy"] == 4 / 8
    assert self_stats["best"]["matchId"] == good
    assert self_stats["worst"]["matchId"] == bad
    assert self_stats["best"]["result"] == "win"


def test_self_stats_excludes_unattributed_matches(store):
    store.new_session()
    _record(store, "m1", winner=0, score=500)
    orphan = _record(store, "m2", winner=0, score=100)
    store.cursor.execute(
        "UPDATE matches SET local_player_id = NULL WHERE id = ?", (orphan,))
    store.conn.commit()

    data = store.get_analytics()
    assert data["overview"]["matches"] == 2
    assert data["selfStats"]["matches"] == 1
    assert data["selfStats"]["avg"]["score"] == 500


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

def test_teammate_leaderboard_never_lists_the_local_player(store):
    store.new_session()
    mate = player("Mate", "Steam|4|0", team=0, goals=1)
    for i in range(3):
        _record(store, f"m{i}", winner=0, extra=[mate])

    teammates = store.get_analytics()["teammates"]
    assert "Me" not in [t["name"] for t in teammates]
    assert next(t for t in teammates if t["name"] == "Mate")["played"] == 3


def test_teammate_exclusion_falls_back_to_name_when_id_is_null(store):
    store.new_session()
    mate = player("Mate", "Steam|4|0", team=0, goals=1)
    for i in range(3):
        _record(store, f"m{i}", winner=0, extra=[mate])
    # Rows recorded before schema v3 have nothing marking the local player, so
    # the leaderboard has to fall back to the configured username.
    store.cursor.execute("UPDATE matches SET local_player_id = NULL")
    store.conn.commit()
    store.set_local_username("Me")

    assert "Me" not in [t["name"] for t in store.get_analytics()["teammates"]]


def _relabel_as_legacy(store, match_id, real_id, name):
    """Rewrite one match_players row to the `legacy:<name>` synthetic id the JSON
    migration would have produced, so the fold logic has something to merge."""
    legacy = f"legacy:{name}"
    store.cursor.execute(
        "INSERT OR IGNORE INTO players (id, name, platform) VALUES (?, ?, 'Unknown')",
        (legacy, name))
    store.cursor.execute(
        "UPDATE match_players SET player_id = ? WHERE match_id = ? AND player_id = ?",
        (legacy, match_id, real_id))
    store.conn.commit()


def test_leaderboard_folds_legacy_ids_into_the_real_player(store):
    store.new_session()
    first = _record(store, "m1", winner=0)
    _record(store, "m2", winner=0)
    _record(store, "m3", winner=1)
    # The oldest match predates ID tracking, so "R" is stored under legacy:R there.
    _relabel_as_legacy(store, first, "Epic|2|0", "R")

    opponents = store.get_analytics()["opponents"]
    # One row for one human, not a 2-match row plus a 1-match row.
    assert [o["name"] for o in opponents] == ["R"]
    assert opponents[0]["played"] == 3
    assert (opponents[0]["wins"], opponents[0]["losses"]) == (2, 1)
    assert opponents[0]["playerId"] == "Epic|2|0"


def test_legacy_fold_leaves_shared_names_alone(store):
    store.new_session()
    # Two different people both called "." — folding on name would merge them.
    dot_a = player(".", "Epic|11|0", team=1, goals=1)
    dot_b = player(".", "Steam|12|0", team=1, goals=1)
    for i in range(3):
        _record(store, f"a{i}", winner=0, extra=[dot_a])
    for i in range(3):
        _record(store, f"b{i}", winner=0, extra=[dot_b])
    legacy_match = _record(store, "c0", winner=0, extra=[dot_a])
    for i in range(2):
        _record(store, f"c{i + 1}", winner=0, extra=[dot_a])
    _relabel_as_legacy(store, legacy_match, "Epic|11|0", ".")

    dots = {o["playerId"]: o for o in store.get_analytics()["opponents"]
            if o["name"] == "."}
    # Ambiguous, so the legacy match is NOT folded into either "." — Epic|11|0
    # keeps its own 5 (3 + 2), not 6. The orphaned legacy:. group has a single
    # encounter, so the minimum-games threshold drops it from the list entirely.
    assert sorted(dots) == ["Epic|11|0", "Steam|12|0"]
    assert dots["Epic|11|0"]["played"] == 5
    assert dots["Steam|12|0"]["played"] == 3


def test_toughest_opponents_respects_the_minimum_threshold(store):
    store.new_session()
    rare = player("Rare", "Epic|8|0", team=1, goals=1)
    # "R" is in every match via _record; "Rare" only makes two.
    for i, winner in enumerate([0, 1, 0, 0]):
        _record(store, f"m{i}", winner=winner,
                extra=[rare] if i < 2 else ())

    opponents = store.get_analytics()["opponents"]
    names = [o["name"] for o in opponents]
    assert "Rare" not in names          # only 2 encounters, below MIN_OPPONENT_GAMES
    faced = next(o for o in opponents if o["name"] == "R")
    assert (faced["played"], faced["wins"], faced["losses"]) == (4, 3, 1)
    assert faced["winPct"] == 0.75


# ---------------------------------------------------------------------------
# Empty database
# ---------------------------------------------------------------------------

def test_get_analytics_on_an_empty_database(store):
    data = store.get_analytics()
    ov = data["overview"]

    assert (ov["matches"], ov["wins"], ov["losses"]) == (0, 0, 0)
    assert ov["winPct"] == 0.0
    assert ov["avgDurationSecs"] == 0.0
    assert ov["overtimeWinPct"] == 0.0
    assert ov["timedMatches"] == 0
    assert ov["firstDate"] == ""
    assert data["selfStats"]["matches"] == 0
    assert data["selfStats"]["best"] == {}
    assert data["selfStats"]["avg"]["goals"] == 0.0
    assert data["winRateBySession"] == []
    assert data["opponents"] == [] and data["teammates"] == []
    # Buckets stay full-length so the charts render an axis with no data.
    assert (len(data["timeOfDay"]), len(data["dayOfWeek"])) == (6, 7)
