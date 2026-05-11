from config import HISTORY_FILE, HISTORY_DB_FILE
from datetime import datetime
import sqlite3
import json
import os

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
    """Map an UpdateState player's PascalCase stat keys to our camelCase shape."""
    return {
        "score":      p.get("Score",      0),
        "goals":      p.get("Goals",      0),
        "shots":      p.get("Shots",      0),
        "assists":    p.get("Assists",    0),
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
        # PrimaryId -> player dict; leavers are kept so their last-known
        # stats are still in the saved match entry.
        self._player_registry   = {}

        # `match_history` is a bounded cache of the most recent matches
        # (oldest-first) used only to feed the history table. All aggregations
        # (encounters, session summaries, active-session tally) hit the DB
        # directly so the cache size never affects correctness.
        self._cache_size = 20

        HISTORY_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(HISTORY_DB_FILE))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()

        self._initDatabase()
        self._maybe_migrate_from_json()

        self._session_finalised = False

        self.match_history = self._load_history()
        self.session_num   = self._load_last_session_num()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def new_session(self):
        self.session_num       += 1
        self._session_finalised = True
        self.wins   = 0
        self.losses = 0

    def continue_session(self):
        """Re-tally wins/losses for the current session from the database."""
        self._session_finalised = True
        self._retally_active_session()

    def _retally_active_session(self):
        """Pull the active session's win/loss counts directly from the DB."""
        self.cursor.execute(
            "SELECT result, COUNT(*) FROM matches WHERE session_id = ? GROUP BY result",
            (self.session_num,),
        )
        counts = {r: c for r, c in self.cursor.fetchall()}
        self.wins   = counts.get("win", 0)
        self.losses = counts.get("loss", 0)

    def _load_last_session_num(self) -> int:
        self.cursor.execute("SELECT COALESCE(MAX(id), 0) FROM sessions")
        return self.cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Match lifecycle
    # ------------------------------------------------------------------

    def try_set_players_from_update(self, data: dict, local_username: str) -> bool:
        """Called on every UpdateState tick.

        Stats for every currently-present player are refreshed on each tick so
        record_result() always has end-of-match values. Players who leave
        mid-match are KEPT in the registry with their last-known stats — that's
        why this is an upsert into `_player_registry`, never a rebuild.

        Returns True only when the UI roster should be refreshed (new match
        GUID, or a new player joined). Stat-only ticks return False to avoid
        flickering the player list."""
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
            # We never matched the local username against any UpdateState
            # player — best-effort fall back so we still record the match.
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

        played_at  = datetime.now().isoformat(timespec="seconds")
        result_str = "win" if won else "loss"
        opponents  = [_player_entry(p) for p in self.current_opponents
                      if p.get("platform") != "Unknown"]
        teammates  = [_player_entry(p) for p in self.current_teammates
                      if p.get("platform") != "Unknown"]

        # Ensure the session row exists; bump its ended_at to this match.
        self.cursor.execute("""
            INSERT INTO sessions (id, name, started_at, ended_at)
            VALUES (?, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET ended_at = excluded.ended_at
        """, (self.session_num, played_at, played_at))

        self.cursor.execute("""
            INSERT INTO matches (session_id, played_at, result, winner_team)
            VALUES (?, ?, ?, ?)
        """, (self.session_num, played_at, result_str, winner_team))
        match_id = self.cursor.lastrowid

        for role, plist in (("opponent", opponents), ("teammate", teammates)):
            for p in plist:
                # An empty PrimaryId at match end is unexpected; synthesise an
                # id so the FK to `players` still holds. Distinct from the
                # `legacy:` prefix used by the one-shot JSON migration.
                pid = p["id"] or f"unknown:{p['name']}"
                self.cursor.execute("""
                    INSERT INTO players (id, name, platform, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name      = excluded.name,
                        platform  = excluded.platform,
                        last_seen = excluded.last_seen
                """, (pid, p["name"], p["platform"], played_at, played_at))
                self.cursor.execute("""
                    INSERT OR IGNORE INTO match_players
                        (match_id, player_id, role, name_at_match, team_num,
                         score, goals, shots, assists, saves, touches, car_touches, demos)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (match_id, pid, role, p["name"], None,
                      p["score"], p["goals"], p["shots"], p["assists"], p["saves"],
                      p["touches"], p["carTouches"], p["demos"]))

        self.conn.commit()

        # Mirror the new row into the bounded recent-matches cache so the
        # history table picks it up without re-querying. Trim oldest if needed.
        self.match_history.append({
            "date":       played_at,
            "result":     result_str,
            "sessionNum": self.session_num,
            "opponents":  opponents,
            "teammates":  teammates,
        })
        if len(self.match_history) > self._cache_size:
            self.match_history = self.match_history[-self._cache_size:]

        self.current_opponents  = []
        self.current_teammates  = []
        self.current_players    = []
        self._player_registry   = {}
        self._seen_player_count = 0

    def delete_session(self, session_num: int):
        """Remove all matches for the given session from the database and re-tally
        the active session's wins/losses (in case the deleted session was the
        active one). FK ON DELETE CASCADE handles `matches` and `match_players`."""
        self.cursor.execute("DELETE FROM sessions WHERE id = ?", (session_num,))
        self.conn.commit()
        self.match_history = self._load_history()
        self._retally_active_session()

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

    def _initDatabase(self):
        """Create tables/indexes if they don't yet exist. Idempotent — safe to
        run on every launch."""
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY,
                name       TEXT,
                started_at TEXT NOT NULL,
                ended_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS matches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                played_at   TEXT NOT NULL,
                result      TEXT NOT NULL CHECK(result IN ('win','loss')),
                winner_team INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_matches_session   ON matches(session_id);
            CREATE INDEX IF NOT EXISTS idx_matches_played_at ON matches(played_at DESC);

            CREATE TABLE IF NOT EXISTS players (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                platform   TEXT NOT NULL,
                first_seen TEXT,
                last_seen  TEXT
            );

            CREATE TABLE IF NOT EXISTS match_players (
                match_id      INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                player_id     TEXT    NOT NULL REFERENCES players(id),
                role          TEXT    NOT NULL CHECK(role IN ('opponent','teammate')),
                name_at_match TEXT    NOT NULL,
                team_num      INTEGER,
                score         INTEGER NOT NULL DEFAULT 0,
                goals         INTEGER NOT NULL DEFAULT 0,
                shots         INTEGER NOT NULL DEFAULT 0,
                assists       INTEGER NOT NULL DEFAULT 0,
                saves         INTEGER NOT NULL DEFAULT 0,
                touches       INTEGER NOT NULL DEFAULT 0,
                car_touches   INTEGER NOT NULL DEFAULT 0,
                demos         INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (match_id, player_id)
            );
            CREATE INDEX IF NOT EXISTS idx_match_players_player_role
                ON match_players(player_id, role);

            PRAGMA user_version = 1;
        """)
        self.conn.commit()

    def _maybe_migrate_from_json(self):
        """One-shot import: if a legacy match_history.json exists and the DB has
        no matches yet, port its contents over and rename the JSON to .bak so
        we don't re-import on the next launch."""
        self.cursor.execute("SELECT COUNT(*) FROM matches")
        if self.cursor.fetchone()[0] > 0:
            return
        if not os.path.exists(HISTORY_FILE):
            return
        try:
            with open(HISTORY_FILE, "r") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, IOError):
            return
        if not entries:
            return

        # Build session rows first using each session's date range.
        by_session: dict = {}
        for e in entries:
            num = e.get("sessionNum")
            if isinstance(num, int):
                by_session.setdefault(num, []).append(e)
        for num, ses_entries in by_session.items():
            dates = [e.get("date", "") for e in ses_entries if e.get("date")]
            first = min(dates) if dates else ""
            last  = max(dates) if dates else ""
            self.cursor.execute(
                "INSERT OR IGNORE INTO sessions (id, name, started_at, ended_at) VALUES (?, NULL, ?, ?)",
                (num, first, last),
            )

        for e in entries:
            session_id = e.get("sessionNum")
            result     = e.get("result")
            played_at  = e.get("date", "")
            if not isinstance(session_id, int) or result not in ("win", "loss"):
                continue
            self.cursor.execute(
                "INSERT INTO matches (session_id, played_at, result, winner_team) VALUES (?, ?, ?, NULL)",
                (session_id, played_at, result),
            )
            match_id = self.cursor.lastrowid
            for role, plist in (("opponent", e.get("opponents", [])),
                                ("teammate", e.get("teammates", []))):
                for p in plist:
                    pid      = p.get("id") or f"legacy:{p.get('name', 'Unknown')}"
                    name     = p.get("name", "Unknown")
                    platform = p.get("platform", "Unknown")
                    self.cursor.execute("""
                        INSERT INTO players (id, name, platform, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name,
                            last_seen = excluded.last_seen
                    """, (pid, name, platform, played_at, played_at))
                    self.cursor.execute("""
                        INSERT OR IGNORE INTO match_players
                            (match_id, player_id, role, name_at_match, team_num,
                             score, goals, shots, assists, saves, touches, car_touches, demos)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (match_id, pid, role, name, None,
                          p.get("score", 0), p.get("goals", 0), p.get("shots", 0),
                          p.get("assists", 0), p.get("saves", 0), p.get("touches", 0),
                          p.get("carTouches", 0), p.get("demos", 0)))

        self.conn.commit()

        try:
            os.rename(HISTORY_FILE, str(HISTORY_FILE) + ".bak")
        except OSError as err:
            print(f"Migrated history.json to DB but could not rename original: {err}")

    def _load_history(self) -> list:
        """Read the most recent N matches from the DB into the legacy JSON dict
        shape, oldest-first. Bounded by `self._cache_size` — full history lives
        in the DB and is reached via direct queries (`_encounter_for`,
        `get_session_summaries`, `_retally_active_session`)."""
        self.cursor.execute(
            "SELECT id FROM matches ORDER BY id DESC LIMIT ?",
            (self._cache_size,),
        )
        recent_ids = [r[0] for r in self.cursor.fetchall()]
        if not recent_ids:
            return []
        placeholders = ",".join("?" * len(recent_ids))
        self.cursor.execute(f"""
            SELECT m.id, m.session_id, m.played_at, m.result,
                   mp.player_id, mp.role, mp.name_at_match,
                   mp.score, mp.goals, mp.shots, mp.assists, mp.saves,
                   mp.touches, mp.car_touches, mp.demos,
                   p.platform
            FROM matches m
            LEFT JOIN match_players mp ON mp.match_id = m.id
            LEFT JOIN players p ON p.id = mp.player_id
            WHERE m.id IN ({placeholders})
            ORDER BY m.id ASC
        """, recent_ids)
        rows = self.cursor.fetchall()

        entries: dict = {}
        for (mid, sid, played_at, result,
             pid, role, name_at_match,
             score, goals, shots, assists, saves,
             touches, car_touches, demos,
             platform) in rows:
            entry = entries.setdefault(mid, {
                "date":       played_at,
                "result":     result,
                "sessionNum": sid,
                "opponents":  [],
                "teammates":  [],
            })
            if pid is None:
                continue  # match with no recorded players (shouldn't happen, but defensive)
            player = {
                "id":         pid,
                "name":       name_at_match,
                "platform":   platform or "Unknown",
                "score":      score,
                "goals":      goals,
                "shots":      shots,
                "assists":    assists,
                "saves":      saves,
                "touches":    touches,
                "carTouches": car_touches,
                "demos":      demos,
            }
            if role == "opponent":
                entry["opponents"].append(player)
            else:
                entry["teammates"].append(player)

        return list(entries.values())

    # ------------------------------------------------------------------
    # Helpers for the UI
    # ------------------------------------------------------------------

    def get_recent_opponents(self, n: int = 20) -> list:
        return list(reversed(self.match_history[-n:]))

    def get_current_encounters(self) -> dict:
        """Look up prior history for every player currently in the match,
        split by role. The teammates list is computed for completeness — only
        opponents are rendered today; see `_encounter_for` for the row shape.

        `matchesAgo` is None when the last meeting was in a different session;
        UI falls back to displaying `lastDate` in that case."""
        return {
            "opponents": [self._encounter_for(p.get("id", ""), p.get("name", ""), "opponents")
                          for p in self.current_opponents if p.get("name")],
            "teammates": [self._encounter_for(p.get("id", ""), p.get("name", ""), "teammates")
                          for p in self.current_teammates if p.get("name")],
        }

    def _encounter_for(self, player_id: str, name: str, list_key: str) -> dict:
        """`list_key` is 'opponents' or 'teammates' - selects which role of each
        match_players row to scan when counting prior meetings.

        Matches by PrimaryId so two players sharing a display name ('.' is common)
        aren't conflated. Legacy entries imported from the JSON have synthetic
        `legacy:<name>` ids, so we OR in a name match against those rows only."""
        role = "opponent" if list_key == "opponents" else "teammate"
        pid  = player_id or ""

        self.cursor.execute("""
            SELECT m.id, m.session_id, m.played_at, m.result
            FROM matches m
            JOIN match_players mp ON mp.match_id = m.id
            WHERE mp.role = ?
              AND (mp.player_id = ?
                   OR (mp.player_id LIKE 'legacy:%' AND mp.name_at_match = ?))
            ORDER BY m.id DESC
        """, (role, pid, name))
        rows = self.cursor.fetchall()

        wins       = sum(1 for r in rows if r[3] == "win")
        losses     = sum(1 for r in rows if r[3] == "loss")
        encounters = len(rows)

        last_date         = ""
        last_session_num  = None
        matches_ago       = None
        if rows:
            last_match_id, last_session_num, last_date, _ = rows[0]
            last_date = last_date or ""
            if last_session_num == self.session_num:
                self.cursor.execute(
                    "SELECT COUNT(*) FROM matches WHERE session_id = ? AND id > ?",
                    (self.session_num, last_match_id),
                )
                matches_ago = self.cursor.fetchone()[0]

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
        """One summary dict per session, most recent session first. Reads from
        the DB so it sees the full history, not just the bounded cache."""
        self.cursor.execute("""
            SELECT m.session_id, m.played_at, m.result
            FROM matches m
            ORDER BY m.session_id ASC, m.id ASC
        """)
        by_session: dict = {}
        for sid, played_at, result in self.cursor.fetchall():
            by_session.setdefault(sid, []).append((played_at, result))

        summaries = []
        for sid, entries in by_session.items():
            wins   = sum(1 for _, r in entries if r == "win")
            losses = sum(1 for _, r in entries if r == "loss")
            total  = wins + losses

            best_win, worst_loss = 0, 0
            cur_win, cur_loss = 0, 0
            for _, r in entries:
                if r == "win":
                    cur_win += 1
                    cur_loss = 0
                    best_win = max(best_win, cur_win)
                elif r == "loss":
                    cur_loss += 1
                    cur_win = 0
                    worst_loss = max(worst_loss, cur_loss)

            dates = [d for d, _ in entries if d]
            summaries.append({
                "sessionNum":      sid,
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