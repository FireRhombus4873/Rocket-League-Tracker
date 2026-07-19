"""
Tier 1 — the data layer (SessionStore against a real, temporary SQLite DB).

This is where a bug means silent data corruption — a win logged as a loss, a
match dropped, a wrong encounter count — so it gets the most coverage. Each test
drives the store the way the live socket thread would: feed UpdateState ticks via
`try_set_players_from_update`, then end the match via `record_result` /
`discard_match`, and assert on the store's counters and the DB itself.

The scenario list mirrors the "Editing sessionStore.py" section of CLAUDE.md.
"""
import json

from factories import player, update_state


# ---------------------------------------------------------------------------
# Recording wins / losses
# ---------------------------------------------------------------------------

def test_win_recorded_when_local_team_wins(store):
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me",    "Steam|1|0", team=0, goals=3),
        player("Rival", "Epic|2|0",  team=1, goals=1),
    ]), local_username="Me")
    store.record_result(winner_team=0)

    assert (store.wins, store.losses) == (1, 0)
    summary = store.get_session_summaries()[0]
    assert summary["wins"] == 1 and summary["matches"] == 1


def test_loss_recorded_when_local_team_loses(store):
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me",    "Steam|1|0", team=0, goals=0),
        player("Rival", "Epic|2|0",  team=1, goals=2),
    ]), local_username="Me")
    store.record_result(winner_team=1)

    assert (store.wins, store.losses) == (0, 1)


def test_leaver_loss_inferred_from_goals(store):
    # User left before MatchEnded -> only MatchDestroyed fires -> no winner arg.
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me",    "Steam|1|0", team=0, goals=1),
        player("Rival", "Epic|2|0",  team=1, goals=4),
    ]), local_username="Me")
    store.record_result()  # winner inferred from goal totals

    assert (store.wins, store.losses) == (0, 1)


def test_leaver_win_inferred_from_goals(store):
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me",    "Steam|1|0", team=0, goals=5),
        player("Rival", "Epic|2|0",  team=1, goals=2),
    ]), local_username="Me")
    store.record_result()

    assert (store.wins, store.losses) == (1, 0)


def test_freeplay_match_is_not_recorded(store):
    # Local username never appears among the players, so _local_team stays -1
    # and record_result must skip entirely (rather than defaulting to team 0).
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("SomeoneElse", "Steam|9|0", team=0, goals=2),
    ]), local_username="Me")
    store.record_result(winner_team=0)

    assert (store.wins, store.losses) == (0, 0)
    assert store.get_session_summaries() == []


# ---------------------------------------------------------------------------
# The "should I refresh the UI?" contract of try_set_players_from_update
# ---------------------------------------------------------------------------

def test_roster_changes_signal_a_refresh_but_stat_ticks_do_not(store):
    first = update_state("m1", [player("Me", "Steam|1|0", team=0)])
    assert store.try_set_players_from_update(first, "Me") is True   # new GUID

    stat_only = update_state("m1", [player("Me", "Steam|1|0", team=0, goals=1)])
    assert store.try_set_players_from_update(stat_only, "Me") is False  # same roster

    joined = update_state("m1", [
        player("Me",    "Steam|1|0", team=0),
        player("Rival", "Epic|2|0",  team=1),
    ])
    assert store.try_set_players_from_update(joined, "Me") is True  # late joiner


def test_leaver_kept_in_registry_with_last_known_stats(store):
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me",    "Steam|1|0", team=0, goals=1),
        player("Rival", "Epic|2|0",  team=1, goals=2, saves=3),
    ]), "Me")
    # Rival disconnects — the next tick no longer lists them.
    store.try_set_players_from_update(update_state("m1", [
        player("Me", "Steam|1|0", team=0, goals=1),
    ]), "Me")

    rivals = [p for p in store.current_opponents if p["name"] == "Rival"]
    assert len(rivals) == 1 and rivals[0]["saves"] == 3  # last-known stats retained

    store.record_result(winner_team=1)
    recorded = [p["name"] for p in store.match_history[-1]["opponents"]]
    assert "Rival" in recorded


def test_duplicate_display_names_do_not_collide(store):
    # Two opponents both named "." but with distinct PrimaryIds must stay distinct.
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me", "Steam|1|0",  team=0, goals=1),
        player(".",  "Epic|aaa|0", team=1, goals=1),
        player(".",  "Epic|bbb|0", team=1, goals=1),
    ]), "Me")
    assert len(store.current_opponents) == 2

    store.record_result(winner_team=0)
    match_id = store.cursor.execute("SELECT MAX(id) FROM matches").fetchone()[0]
    n_opponents = store.cursor.execute(
        "SELECT COUNT(*) FROM match_players WHERE match_id = ? AND role = 'opponent'",
        (match_id,),
    ).fetchone()[0]
    assert n_opponents == 2


# ---------------------------------------------------------------------------
# Double-record guard, pause/discard, duration & overtime
# ---------------------------------------------------------------------------

def test_result_recorded_flag_guards_double_recording(store):
    store.new_session()
    roster = [player("Me", "Steam|1|0", team=0), player("R", "Epic|2|0", team=1)]

    store.try_set_players_from_update(update_state("m1", roster), "Me")
    assert store.result_recorded() is False
    store.record_result(winner_team=0)
    assert store.result_recorded() is True  # MatchDestroyed would now no-op

    # A brand-new match GUID clears the flag again.
    store.try_set_players_from_update(update_state("m2", roster), "Me")
    assert store.result_recorded() is False


def test_discard_match_writes_nothing(store):
    store.new_session()
    store.try_set_players_from_update(update_state("m1", [
        player("Me", "Steam|1|0", team=0, goals=3),
        player("R",  "Epic|2|0",  team=1),
    ]), "Me")
    store.discard_match()  # tracking paused

    assert (store.wins, store.losses) == (0, 0)
    assert store.cursor.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert store.current_players == []
    assert store.result_recorded() is False


def test_duration_and_overtime_persisted(store):
    store.new_session()
    roster = [player("Me", "Steam|1|0", team=0, goals=2),
              player("R",  "Epic|2|0",  team=1, goals=1)]
    # Clock counts DOWN from 300; overtime latches once seen.
    store.try_set_players_from_update(update_state("m1", roster, time_secs=300), "Me")
    store.try_set_players_from_update(
        update_state("m1", roster, time_secs=250, overtime=True), "Me")
    store.record_result(winner_team=0)

    duration, overtime = store.cursor.execute(
        "SELECT duration_secs, overtime FROM matches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert duration == 50
    assert overtime == 1


# ---------------------------------------------------------------------------
# Encounter aggregation (queries the DB, not the bounded cache)
# ---------------------------------------------------------------------------

def _face(store, guid, winner):
    store.try_set_players_from_update(update_state(guid, [
        player("Me",      "Steam|1|0", team=0, goals=(3 if winner == 0 else 0)),
        player("Nemesis", "Epic|9|0",  team=1, goals=(0 if winner == 0 else 3)),
    ]), "Me")
    store.record_result(winner_team=winner)


def test_encounter_counts_across_matches(store):
    store.new_session()
    _face(store, "m1", winner=0)  # win vs Nemesis
    _face(store, "m2", winner=1)  # loss vs Nemesis

    # Nemesis shows up a third time — the encounter card should know the history.
    store.try_set_players_from_update(update_state("m3", [
        player("Me",      "Steam|1|0", team=0),
        player("Nemesis", "Epic|9|0",  team=1),
    ]), "Me")
    nem = next(e for e in store.get_current_encounters()["opponents"]
               if e["name"] == "Nemesis")
    assert nem["encounters"] == 2
    assert (nem["wins"], nem["losses"]) == (1, 1)


def test_cross_encounter_counts_opposite_role(store):
    store.new_session()
    # Faced Nemesis as an OPPONENT once.
    store.try_set_players_from_update(update_state("m1", [
        player("Me",      "Steam|1|0", team=0, goals=3),
        player("Nemesis", "Epic|9|0",  team=1, goals=0),
    ]), "Me")
    store.record_result(winner_team=0)

    # Now Nemesis is a TEAMMATE — first time on this side, but we've met before.
    store.try_set_players_from_update(update_state("m2", [
        player("Me",      "Steam|1|0", team=0),
        player("Nemesis", "Epic|9|0",  team=0),
        player("Other",   "Steam|5|0", team=1),
    ]), "Me")
    nem = next(e for e in store.get_current_encounters()["teammates"]
               if e["name"] == "Nemesis")
    assert nem["encounters"] == 0       # never teamed before
    assert nem["crossEncounters"] == 1  # but faced once as opponent


# ---------------------------------------------------------------------------
# Sessions: summaries, streaks, delete, continue
# ---------------------------------------------------------------------------

def _record_results(store, winners):
    for i, winner in enumerate(winners):
        store.try_set_players_from_update(update_state(f"m{i}", [
            player("Me", "Steam|1|0", team=0),
            player("R",  "Epic|2|0",  team=1),
        ]), "Me")
        store.record_result(winner_team=winner)


def test_session_summary_streaks(store):
    store.new_session()
    _record_results(store, [0, 0, 1, 0])  # W, W, L, W  (Me is team 0)

    s = store.get_session_summaries()[0]
    assert (s["wins"], s["losses"]) == (3, 1)
    assert s["bestWinStreak"] == 2
    assert s["worstLossStreak"] == 1
    assert s["winPct"] == 0.75


def test_delete_session_cascades(store):
    store.new_session()
    _record_results(store, [0, 1])
    sid = store.session_num

    store.delete_session(sid)

    assert store.cursor.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert store.cursor.execute("SELECT COUNT(*) FROM match_players").fetchone()[0] == 0
    assert store.get_session_summaries() == []
    assert (store.wins, store.losses) == (0, 0)


def test_continue_session_retallies_from_db(store):
    store.new_session()
    _record_results(store, [0, 1])  # one win, one loss in session 1

    # Simulate a fresh launch reconnecting to the same session.
    store.wins = store.losses = 0
    store.continue_session()
    assert (store.wins, store.losses) == (1, 1)


# ---------------------------------------------------------------------------
# One-shot legacy JSON migration
# ---------------------------------------------------------------------------

def test_migration_from_legacy_json(db_paths):
    import sessionStore
    _, json_file = db_paths
    json_file.write_text(json.dumps([
        {
            "date": "2024-01-01T10:00:00", "result": "win", "sessionNum": 1,
            "opponents": [{"id": "Steam|42|0", "name": "OldFoe",
                           "platform": "Steam", "score": 200, "goals": 1}],
            "teammates": [],
        },
    ]))

    s = sessionStore.SessionStore()
    try:
        assert s.cursor.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
        assert s.cursor.execute("SELECT result FROM matches").fetchone()[0] == "win"
        # Original is renamed to .bak so it never re-imports.
        assert not json_file.exists()
        assert (json_file.parent / (json_file.name + ".bak")).exists()
    finally:
        s.conn.close()
