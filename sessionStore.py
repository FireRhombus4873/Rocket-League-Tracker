import json
import os
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(os.getenv("LOCALAPPDATA")) / "FireRhombus" / "RocketLeagueTracker" / "match_history.json"

PLATFORM_MAP = {
    "Steam":     "Steam",
    "Epic":      "Epic",
    "ps4":       "PlayStation",
    "psn":       "PlayStation",
    "Xboxone":   "Xbox",
    "xbl":       "Xbox",
    "switch":    "Switch",
}

def _parse_platform(primary_id: str) -> str:
    prefix = primary_id.split("|")[0].lower()
    return PLATFORM_MAP.get(prefix, prefix.capitalize() or "Unknown")

def _extract_stats(p: dict) -> dict:
    """Pull all tracked stats from a raw player dict from UpdateState."""
    return {
        "score":      p.get("Score",      0),
        "goals":      p.get("Goals",      0),
        "shots":      p.get("Shots",      0),
        "assists":    p.get("Assists",     0),
        "saves":      p.get("Saves",      0),
        "touches":    p.get("Touches",    0),
        "carTouches": p.get("CarTouches", 0),
        "demos":      p.get("Demos",      0),
    }


class SessionStore():
    def __init__(self):
        self.wins   = 0
        self.losses = 0

        self.current_opponents  = []
        self.current_teammates  = []
        self.current_players    = []
        self._seen_guid         = None
        self._seen_player_count = 0
        self._local_team        = -1
        self._local_username    = ""
        self.team_info          = {}
        self._player_registry   = {}  # name -> player dict, survives leavers

        self.session_num        = self._load_last_session_num()
        self._session_finalised = False

        self.match_history = self._load_history()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def new_session(self):
        self.session_num       += 1
        self._session_finalised = True
        # Wins/losses start fresh for a new session
        self.wins   = 0
        self.losses = 0

    def continue_session(self):
        """Re-tally wins/losses for the current session from saved history."""
        self._session_finalised = True
        self.wins   = sum(1 for e in self.match_history
                          if e.get("sessionNum") == self.session_num
                          and e.get("result") == "win")
        self.losses = sum(1 for e in self.match_history
                          if e.get("sessionNum") == self.session_num
                          and e.get("result") == "loss")

    def _load_last_session_num(self) -> int:
        history = self._load_history()
        if not history:
            return 0
        nums = [e.get("sessionNum", 0) for e in history if isinstance(e.get("sessionNum"), int)]
        return max(nums, default=0)

    # ------------------------------------------------------------------
    # Match lifecycle
    # ------------------------------------------------------------------

    def try_set_players_from_update(self, data: dict, local_username: str) -> bool:
        """
        Called on every UpdateState tick.

        - On a new MatchGuid: resets state, triggers a UI refresh.
        - On the same guid with new players: merges them in, triggers a UI refresh.
        - On every tick: updates stats for all currently present players so that
          record_result() always has end-of-match values.
        - Players who leave mid-match are KEPT in _player_registry with their last
          known stats so they still appear in the saved history entry.

        Returns True when the UI player list should be refreshed.
        """
        guid = data.get("MatchGuid")
        if not guid:
            return False

        players_raw = data.get("Players", [])
        new_count   = len(players_raw)
        new_guid    = guid != self._seen_guid

        if new_guid:
            self._seen_guid         = guid
            self._seen_player_count = 0
            self._local_team        = -1
            self.team_info          = {}
            self._player_registry   = {}

        # Extract team colours/names
        game = data.get("Game", {})
        for t in game.get("Teams", []):
            num   = t.get("TeamNum", -1)
            color = t.get("ColorPrimary", "")
            if color and not color.startswith("#"):
                color = f"#{color}"
            self.team_info[num] = {
                "name":  t.get("Name", f"Team {num}"),
                "color": color or "#ffffff",
            }

        # Upsert each currently present player into the registry, keyed by
        # PrimaryId (e.g. "Steam|123|0") so duplicate names — like "." — don't
        # collide. Falls back to name only if PrimaryId is missing entirely.
        for p in players_raw:
            name      = p.get("Name", "Unknown")
            primary   = p.get("PrimaryId", "")
            registry_key = primary or name
            self._player_registry[registry_key] = {
                "id":       primary,
                "name":     name,
                "platform": _parse_platform(primary),
                "team":     p.get("TeamNum", -1),
                **_extract_stats(p),
            }

        # Resolve local team from username
        local_team = self._local_team
        for entry in self._player_registry.values():
            if entry["name"].lower() == local_username.lower():
                local_team = entry["team"]
                break

        self._local_team     = local_team
        self._local_username = local_username

        # Derive current_players from the full registry (includes leavers)
        self.set_players(list(self._player_registry.values()), local_team)

        # Only tell the caller to refresh the UI when new players have joined
        roster_changed = new_guid or (new_count > self._seen_player_count)
        if roster_changed:
            self._seen_player_count = new_count
            return True

        return False

    def set_players(self, players: list, local_team: int = -1):
        self.current_players   = players
        self.current_opponents = [p for p in players if p.get("team") != local_team]
        self.current_teammates = [p for p in players if p.get("team") == local_team]

    def record_result(self, winner_team: int):
        """
        Snapshot current_players at match end — by this point UpdateState
        will have been ticking throughout the match so stats are fully populated.
        """
        if self._local_team == -1:
            print("Warning: could not determine local team; defaulting to team 0")
            local_team = 0
        else:
            local_team = self._local_team

        won = (winner_team == local_team)
        if won:
            self.wins += 1
        else:
            self.losses += 1

        def _player_entry(p: dict) -> dict:
            return {
                "id":         p.get("id",         ""),
                "name":       p.get("name",       "Unknown"),
                "platform":   p.get("platform",   "Unknown"),
                "score":      p.get("score",      0),
                "goals":      p.get("goals",      0),
                "shots":      p.get("shots",      0),
                "assists":    p.get("assists",     0),
                "saves":      p.get("saves",      0),
                "touches":    p.get("touches",    0),
                "carTouches": p.get("carTouches", 0),
                "demos":      p.get("demos",      0),
            }

        entry = {
            "date":       datetime.now().isoformat(timespec="seconds"),
            "result":     "win" if won else "loss",
            "sessionNum": self.session_num,
            "opponents":  [_player_entry(p) for p in self.current_opponents
                           if p.get("platform") != "Unknown"],
            "teammates":  [_player_entry(p) for p in self.current_teammates
                           if p.get("platform") != "Unknown"],
        }
        self.match_history.append(entry)
        self._save_history()

        self.current_opponents  = []
        self.current_teammates  = []
        self.current_players    = []
        self._player_registry   = {}
        self._seen_player_count = 0

    def delete_session(self, session_num: int):
        """Remove all matches for the given session from history and re-tally the
        active session's wins/losses (in case the deleted session was the active one)."""
        self.match_history = [e for e in self.match_history
                              if e.get("sessionNum") != session_num]
        self._save_history()
        self.wins   = sum(1 for e in self.match_history
                          if e.get("sessionNum") == self.session_num
                          and e.get("result") == "win")
        self.losses = sum(1 for e in self.match_history
                          if e.get("sessionNum") == self.session_num
                          and e.get("result") == "loss")

    def discard_match(self):
        """Reset per-match state without writing to history or touching wins/losses."""
        self.current_opponents  = []
        self.current_teammates  = []
        self.current_players    = []
        self._player_registry   = {}
        self._seen_player_count = 0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> list:
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save_history(self):
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.match_history, f)
        except IOError as e:
            print(f"Could not save match history: {e}")

    # ------------------------------------------------------------------
    # Helpers for the UI
    # ------------------------------------------------------------------

    def get_recent_opponents(self, n: int = 20) -> list:
        return list(reversed(self.match_history[-n:]))

    def get_current_encounters(self) -> dict:
        """For every player currently in the match, look up our prior history with
        them (separately as opponents and as teammates).

        Returns {"opponents": [...], "teammates": [...]} where each list contains
        one entry per current player, with this shape:

            {
              "name":           str,
              "wins":           int,    # our wins in past matches where they appeared in this role
              "losses":         int,
              "encounters":     int,    # total prior matches in this role
              "lastDate":       str,    # ISO date of most recent prior match in this role ("" if none)
              "lastSessionNum": int | None,
              "matchesAgo":     int | None,  # current-session matches between then and now;
                                              # None when the last meeting was in a different session
            }

        The teammates list is populated for completeness — UI rendering of it is not yet wired up.
        """
        return {
            "opponents": [self._encounter_for(p.get("id", ""), p.get("name", ""), "opponents")
                          for p in self.current_opponents if p.get("name")],
            "teammates": [self._encounter_for(p.get("id", ""), p.get("name", ""), "teammates")
                          for p in self.current_teammates if p.get("name")],
        }

    def _encounter_for(self, player_id: str, name: str, list_key: str) -> dict:
        """`list_key` is 'opponents' or 'teammates' — selects which slot of each
        history entry to scan when counting prior meetings.

        Matches by PrimaryId when available so two players sharing a display name
        ('.' is common) aren't conflated. Falls back to name match for legacy
        history entries written before IDs were tracked."""
        wins = losses = encounters = 0
        last_date = ""
        last_session_num = None
        matches_ago = None
        seen_in_current_session = 0  # current-session matches walked past (newest-first)

        def _entry_contains(entry_players: list) -> bool:
            for p in entry_players:
                p_id = p.get("id")
                if p_id and player_id:
                    if p_id == player_id:
                        return True
                else:
                    # Either side lacks an id — fall back to name match.
                    if p.get("name") == name:
                        return True
            return False

        for entry in reversed(self.match_history):
            is_current = entry.get("sessionNum") == self.session_num
            if _entry_contains(entry.get(list_key, [])):
                encounters += 1
                r = entry.get("result")
                if r == "win":
                    wins += 1
                elif r == "loss":
                    losses += 1
                if not last_date:
                    last_date = entry.get("date", "")
                    last_session_num = entry.get("sessionNum")
                    if is_current:
                        matches_ago = seen_in_current_session
            if is_current:
                seen_in_current_session += 1

        return {
            "name":           name,
            "wins":           wins,
            "losses":         losses,
            "encounters":     encounters,
            "lastDate":       last_date,
            "lastSessionNum": last_session_num,
            "matchesAgo":     matches_ago,
        }

    def get_session_summaries(self) -> list:
        """One summary dict per session, most recent session first."""
        by_session: dict = {}
        for entry in self.match_history:
            num = entry.get("sessionNum")
            if not isinstance(num, int):
                continue
            by_session.setdefault(num, []).append(entry)

        summaries = []
        for num, entries in by_session.items():
            wins   = sum(1 for e in entries if e.get("result") == "win")
            losses = sum(1 for e in entries if e.get("result") == "loss")
            total  = wins + losses

            best_win, worst_loss = 0, 0
            cur_win, cur_loss = 0, 0
            for e in entries:
                if e.get("result") == "win":
                    cur_win += 1
                    cur_loss = 0
                    best_win = max(best_win, cur_win)
                elif e.get("result") == "loss":
                    cur_loss += 1
                    cur_win = 0
                    worst_loss = max(worst_loss, cur_loss)

            dates = [e.get("date", "") for e in entries if e.get("date")]
            summaries.append({
                "sessionNum":      num,
                "firstDate":       min(dates) if dates else "",
                "lastDate":        max(dates) if dates else "",
                "matches":         total,
                "wins":            wins,
                "losses":          losses,
                "winPct":          (wins / total) if total else 0.0,
                "bestWinStreak":   best_win,
                "worstLossStreak": worst_loss,
            })

        summaries.sort(key=lambda s: s["sessionNum"], reverse=True)
        return summaries

    def session_record(self) -> str:
        return f"{self.wins}W / {self.losses}L"