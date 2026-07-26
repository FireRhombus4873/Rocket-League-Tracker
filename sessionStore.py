from config import HISTORY_FILE, HISTORY_DB_FILE
from datetime import datetime
import sqlite3
import threading
import json
import os

PLATFORM_MAP = {
    "steam":     "Steam",
    "epic":      "Epic",
    "ps4":       "PlayStation",
    "psn":       "PlayStation",
    "xboxone":   "Xbox",
    "xbl":       "Xbox",
    "switch":    "Switch",
}

# Analytics tuning. All-time only — the Analytics tab has no scope selector.
ROLLING_WINDOW     = 5   # sessions in the win-rate rolling average
MIN_OPPONENT_GAMES = 3   # encounters before an opponent can rank as "toughest"
MIN_TEAMMATE_GAMES = 2   # matches together before a teammate is listed
LEADERBOARD_LIMIT  = 8   # rows per leaderboard
TOD_BUCKET_HOURS   = 4   # time-of-day bucket width -> 6 buckets

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

def _parse_platform(primary_id: str) -> str:
    prefix = primary_id.split("|")[0].lower()
    return PLATFORM_MAP.get(prefix, prefix.capitalize() or "Unknown")

def _mean(xs) -> float:
    return (sum(xs) / len(xs)) if xs else 0.0

def _bucket_row(label: str, matches: int, wins: int) -> dict:
    """One time-of-day / day-of-week bucket, shaped for the bar charts."""
    return {
        "label":   label,
        "matches": matches,
        "wins":    wins,
        "losses":  matches - wins,
        "winPct":  (wins / matches) if matches else 0.0,
    }

def _tod_label(index: int) -> str:
    return f"{index * TOD_BUCKET_HOURS:02d}–{(index + 1) * TOD_BUCKET_HOURS:02d}"

def _fold_legacy_player_ids(groups: dict) -> dict:
    """Merge `legacy:<name>` leaderboard groups into the real PrimaryId group for
    the same person.

    The one-shot JSON migration gave pre-ID-tracking rows a synthetic
    `legacy:<name>` id (see _maybe_migrate_from_json), so someone you played
    before and after that cutover appears twice — once under their real
    PrimaryId, once under the synthetic one — splitting their record in half.
    _encounter_for already falls back to a name match for exactly this reason.

    A synthetic group is folded only when *one* real id shares its name and role:
    display names aren't unique ("." is a real one), so anything ambiguous is left
    standing on its own rather than guessed at. Keyed by (role, player_id); the
    surviving key is the real id's.
    """
    real_by_name: dict = {}
    for (role, pid), entry in groups.items():
        if not pid.startswith("legacy:"):
            real_by_name.setdefault((role, entry["name"].lower()), []).append(pid)

    folded = {}
    for (role, pid), entry in groups.items():
        target = None
        if pid.startswith("legacy:"):
            candidates = real_by_name.get((role, entry["name"].lower()), [])
            if len(candidates) == 1:
                target = (role, candidates[0])
        if target is None:
            folded.setdefault((role, pid), dict(entry))
            continue
        into = folded.setdefault(target, dict(groups[target]))
        into["played"] += entry["played"]
        into["wins"]   += entry["wins"]
        into["losses"] += entry["losses"]
        into["lastDate"] = max(into["lastDate"], entry["lastDate"])
    return folded


def _with_rolling_win_pct(sessions: list) -> list:
    """Copy each chronological session summary with a match-weighted rolling win
    rate over the trailing ROLLING_WINDOW sessions.

    Match-weighted, not a mean of percentages — otherwise a single-match session
    swings the line as hard as a twenty-match one."""
    out = []
    for i, s in enumerate(sessions):
        window = sessions[max(0, i - ROLLING_WINDOW + 1): i + 1]
        w = sum(x["wins"] for x in window)
        m = sum(x["matches"] for x in window)
        out.append({**s, "rollingWinPct": (w / m) if m else 0.0})
    return out

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
        # PrimaryId of the local user, latched from UpdateState alongside
        # _local_team — the two share a lifecycle so "team known => id known".
        self._local_player_id   = ""
        self._local_username    = ""
        self.team_info          = {}
        self._match_recorded    = False
        # PrimaryId -> player dict; leavers are kept so their last-known
        # stats are still in the saved match entry.
        self._player_registry   = {}
        self._match_start_secs  = None
        self._match_last_secs   = None
        self._match_overtime    = False

        # `match_history` is a bounded cache of the most recent matches
        # (oldest-first) used only to feed the history table. All aggregations
        # (encounters, session summaries, active-session tally) hit the DB
        # directly so the cache size never affects correctness.
        self._cache_size = 20

        HISTORY_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        # The socket listener thread calls record_result / get_current_encounters
        # while the Qt main thread calls delete_session / get_session_summaries.
        # `check_same_thread=False` permits cross-thread use; `_db_lock` (RLock so
        # methods that call other locked methods don't deadlock) serialises access
        # to the shared cursor.
        self.conn = sqlite3.connect(str(HISTORY_DB_FILE), check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        self._db_lock = threading.RLock()

        self._initDatabase()
        self._maybe_migrate_from_json()

        self._session_finalised = False

        self.match_history = self._load_history()
        self.session_num   = self._load_last_session_num()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def set_local_username(self, name: str):
        """Remember the configured display name and attribute historic matches to
        the local player.

        `matches.local_player_id` (schema v3) is written live by record_result, but
        every row recorded before v3 — plus anything imported from the legacy JSON
        — has it NULL. Those are still recoverable: you're always stored as a
        `role='teammate'` row, so a case-insensitive match on `name_at_match` finds
        your id.

        Only rows where *exactly one* teammate matches are touched. If a real
        teammate happened to share your display name that match stays NULL rather
        than being attributed to the wrong player.

        Idempotent — only NULL rows are considered — so it's safe to call on every
        launch and after every settings save. Note SQLite's lower() is ASCII-only,
        so a non-ASCII display name won't backfill; new rows are unaffected, they
        carry the real PrimaryId."""
        self._local_username = name or ""
        if not self._local_username:
            return
        lowered = self._local_username.lower()
        with self._db_lock:
            self.cursor.execute("""
                UPDATE matches
                   SET local_player_id = (
                           SELECT mp.player_id FROM match_players mp
                            WHERE mp.match_id = matches.id
                              AND mp.role     = 'teammate'
                              AND lower(mp.name_at_match) = ?)
                 WHERE local_player_id IS NULL
                   AND (SELECT COUNT(*) FROM match_players mp
                         WHERE mp.match_id = matches.id
                           AND mp.role     = 'teammate'
                           AND lower(mp.name_at_match) = ?) = 1
            """, (lowered, lowered))
            self.conn.commit()

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
        with self._db_lock:
            self.cursor.execute(
                "SELECT result, COUNT(*) FROM matches WHERE session_id = ? GROUP BY result",
                (self.session_num,),
            )
            counts = {r: c for r, c in self.cursor.fetchall()}
        self.wins   = counts.get("win", 0)
        self.losses = counts.get("loss", 0)

    def _load_last_session_num(self) -> int:
        with self._db_lock:
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
            self._local_player_id   = ""
            self.team_info          = {}
            self._player_registry   = {}
            self._match_start_secs  = None
            self._match_last_secs   = None
            self._match_overtime    = False
            self._match_recorded    = False

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

        time_secs = game.get("TimeSeconds")
        if time_secs is not None:
            if self._match_start_secs is None:
                self._match_start_secs = time_secs   # first tick — clock near 300
            self._match_last_secs = time_secs        # updated every tick — clock near 0

        if game.get("bOvertime"):
            self._match_overtime = True

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

        # Resolve local team + identity from username
        local_team = self._local_team
        local_id   = self._local_player_id
        for entry in self._player_registry.values():
            if entry["name"].lower() == local_username.lower():
                local_team = entry["team"]
                local_id   = entry["id"]     # PrimaryId — the canonical identity
                break

        self._local_team      = local_team
        self._local_player_id = local_id
        self._local_username  = local_username

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

    def record_result(self, winner_team: int = None):
        """
        Snapshot current_players at match end — by this point UpdateState
        will have been ticking throughout the match so stats are fully populated.
        """
        if self._local_team == -1:
            # We never matched the local username against any UpdateState
            # Probably a freeplay match with no previous data set (first match not recorded), okay to skip recording
            return
        else:
            local_team = self._local_team

        def calculate_winner_team():
            """Calculate the winner team from the current players' goals."""
            team_goals = {}
            for p in self.current_players:
                team = p.get("team")
                goals = p.get("goals", 0)
                team_goals[team] = team_goals.get(team, 0) + goals
            if team_goals.get(0, 0) > team_goals.get(1, 0):
                return 0
            elif team_goals.get(1, 0) > team_goals.get(0, 0):
                return 1
            else:
                return -1

        def empty_match():
            for p in self.current_players:
                if p.get("score", 0) > 0 or p.get("goals", 0) > 0:
                    return False
            return True

        if empty_match():
            # Don't record empty matches (e.g. freeplay, training, or a match that was never started)
            return

        # If winner_team is not provided (When leaving before the MatchEnded event is called), calculate it from the teams goals.
        if winner_team is None:
            winner_team = calculate_winner_team()

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
                "assists":    p.get("assists",    0),
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
        
        duration = None
        if self._match_overtime:
            duration = self._match_last_secs
        else:
            if self._match_start_secs is not None and self._match_last_secs is not None:
                duration = max(round(self._match_start_secs - self._match_last_secs, 1), 0)

        with self._db_lock:
            # Ensure the session row exists; bump its ended_at to this match.
            self.cursor.execute("""
                INSERT INTO sessions (id, name, started_at, ended_at)
                VALUES (?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET ended_at = excluded.ended_at
            """, (self.session_num, played_at, played_at))

            self.cursor.execute("""
                INSERT INTO matches (session_id, played_at, result, winner_team,
                                     overtime, duration_secs, local_player_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.session_num, played_at, result_str, winner_team,
                  self._match_overtime, duration, self._local_player_id or None))
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
        # `id` is carried through so the UI can target this exact match for
        # deletion — see delete_match().
        self.match_history.append({
            "id":         match_id,
            "date":       played_at,
            "result":     result_str,
            "sessionNum": self.session_num,
            "opponents":  opponents,
            "teammates":  teammates,
        })
        if len(self.match_history) > self._cache_size:
            self.match_history = self.match_history[-self._cache_size:]

        self.current_opponents      = []
        self.current_teammates      = []
        self.current_players        = []
        self._player_registry       = {}
        self._seen_player_count     = 0
        self._match_start_secs  = None
        self._match_last_secs   = None
        self._match_overtime    = False
        self._match_recorded    = True

    def result_recorded(self):
        """Return True when the current match has already been recorded."""
        return self._match_recorded

    def delete_session(self, session_num: int):
        """Remove all matches for the given session from the database and re-tally
        the active session's wins/losses (in case the deleted session was the
        active one). FK ON DELETE CASCADE handles `matches` and `match_players`."""
        with self._db_lock:
            self.cursor.execute("DELETE FROM sessions WHERE id = ?", (session_num,))
            self.conn.commit()
        self.match_history = self._load_history()
        self._retally_active_session()

    def delete_match(self, match_id: int):
        """Remove a single match from the database and re-tally the active
        session's wins/losses. FK ON DELETE CASCADE handles `match_players`.

        The owning `sessions` row is deliberately left in place even when it's
        now empty: it keeps `_load_last_session_num` monotonic so a later
        session can't reuse a retired number. An empty session simply drops out
        of `get_session_summaries`, which builds from `matches`."""
        with self._db_lock:
            self.cursor.execute("DELETE FROM matches WHERE id = ?", (match_id,))
            self.conn.commit()
        self.match_history = self._load_history()
        self._retally_active_session()

    def discard_match(self):
        """Reset per-match state without writing to history or touching wins/losses."""
        self.current_opponents      = []
        self.current_teammates      = []
        self.current_players        = []
        self._player_registry       = {}
        self._seen_player_count     = 0
        self._match_start_secs      = None
        self._match_last_secs       = None
        self._match_overtime        = False
        self._match_recorded        = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _initDatabase(self):
        """Create tables/indexes if they don't yet exist. Idempotent — safe to
        run on every launch."""
        with self._db_lock:
            self.cursor.execute("PRAGMA user_version")
            version = self.cursor.fetchone()[0]

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
            """)
            if version < 1:
                self.cursor.execute("PRAGMA user_version = 1")
                
            if version < 2:
                self.cursor.executescript("""
                    ALTER TABLE matches ADD COLUMN overtime INTEGER DEFAULT 0;
                    ALTER TABLE matches ADD COLUMN duration_secs REAL;
                    PRAGMA user_version = 2;
                """)

            if version < 3:
                # Per-you analytics need to know which match_players row was you.
                # ALTER-only (like v2) so fresh and upgraded DBs take one path.
                # Deliberately plain TEXT with no REFERENCES players(id): a local
                # player whose PrimaryId is empty gets no `players` row (record_result
                # filters out platform == "Unknown"), and with foreign_keys = ON an
                # FK here would reject the whole match rather than just leaving that
                # one match out of the per-you stats.
                if not self._column_exists("matches", "local_player_id"):
                    self.cursor.execute(
                        "ALTER TABLE matches ADD COLUMN local_player_id TEXT")
                self.cursor.execute("PRAGMA user_version = 3")

            self.conn.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        """Migrations key off PRAGMA user_version, but a DB touched by a dev build
        can already have a column while still stamped at the old version. A
        duplicate ADD COLUMN raises OperationalError, and inside an executescript
        that aborts the whole migration — which takes __init__, and therefore app
        startup, down with it. Cheap belt-and-braces."""
        with self._db_lock:
            self.cursor.execute(f"PRAGMA table_info({table})")
            return any(row[1] == column for row in self.cursor.fetchall())

    def _maybe_migrate_from_json(self):
        """One-shot import: if a legacy match_history.json exists and the DB has
        no matches yet, port its contents over and rename the JSON to .bak so
        we don't re-import on the next launch."""
        with self._db_lock:
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
        with self._db_lock:
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
                "id":         mid,
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
        role     = "opponent" if list_key == "opponents" else "teammate"
        opposite = "teammate" if role == "opponent" else "opponent"
        pid      = player_id or ""

        with self._db_lock:
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

            # How many prior matches in the *other* role — used by the UI to
            # avoid "first time facing them" when we've actually played them
            # before, just on the opposite side.
            self.cursor.execute("""
                SELECT COUNT(*)
                FROM match_players mp
                WHERE mp.role = ?
                  AND (mp.player_id = ?
                       OR (mp.player_id LIKE 'legacy:%' AND mp.name_at_match = ?))
            """, (opposite, pid, name))
            cross_encounters = self.cursor.fetchone()[0]

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
            "name":             name,
            "wins":             wins,
            "losses":           losses,
            "encounters":       encounters,
            "crossEncounters":  cross_encounters,
            "lastDate":         last_date,
            "lastSessionNum":   last_session_num,
            "matchesAgo":       matches_ago,
        }

    def get_session_summaries(self) -> list:
        """One summary dict per session, most recent session first. Reads from
        the DB so it sees the full history, not just the bounded cache."""
        with self._db_lock:
            self.cursor.execute("""
                SELECT m.session_id, m.played_at, m.result
                FROM matches m
                ORDER BY m.session_id ASC, m.id ASC
            """)
            rows = self.cursor.fetchall()
        by_session: dict = {}
        for sid, played_at, result in rows:
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

    # ------------------------------------------------------------------
    # Analytics aggregation
    # ------------------------------------------------------------------

    def get_analytics(self) -> dict:
        """Everything the Analytics tab renders, in one snapshot.

        One public method rather than one per view: the tab is fed by a single
        `analytics_updated` signal, and holding `_db_lock` across the whole build
        means the cards, charts and leaderboards can't disagree if a match is
        recorded mid-refresh. Unlike the other aggregations this does its Python
        derivation *inside* the lock for that reason — a few ms on a table of at
        most a few thousand rows, and it only runs at the refresh sites in main.py,
        never per UpdateState tick.

        Every value is non-None: scalars that could be "unknown" are 0/0.0 with a
        companion count (e.g. `timedMatches`), absent objects are `{}`. That keeps
        the Qt slot free of None-guards."""
        with self._db_lock:
            from_matches = self._analytics_from_matches()
            self_stats   = self._analytics_self_stats()
            leaders      = self._analytics_leaderboards()
            # get_session_summaries returns newest-first; the trend chart reads
            # left-to-right in time.
            sessions     = list(reversed(self.get_session_summaries()))

        return {
            "overview":         from_matches["overview"],
            "timeOfDay":        from_matches["timeOfDay"],
            "dayOfWeek":        from_matches["dayOfWeek"],
            "winRateBySession": _with_rolling_win_pct(sessions),
            "rollingWindow":    ROLLING_WINDOW,
            "selfStats":        self_stats,
            "opponents":        leaders["opponents"],
            "teammates":        leaders["teammates"],
        }

    def _analytics_from_matches(self) -> dict:
        """Everything derivable from the `matches` table alone: the overview cards,
        the overtime/duration split, and the time-of-day / day-of-week buckets.

        One ordered scan. Ordered by `id`, not `played_at` — `id` is the true
        chronological order and is immune to the empty `played_at` that legacy
        migrated rows can carry. Bucketing is done in Python rather than with
        strftime() for the same reason: a Python guard is harder to forget than a
        WHERE clause."""
        with self._db_lock:
            self.cursor.execute("""
                SELECT session_id, played_at, result, overtime, duration_secs
                FROM matches
                ORDER BY id ASC
            """)
            rows = self.cursor.fetchall()

        wins = losses = 0
        ot_matches = ot_wins = 0
        reg_wins = reg_losses = 0
        dur_all, dur_wins, dur_losses, dur_reg, dur_ot = [], [], [], [], []
        best_win = worst_loss = cur_win = cur_loss = 0
        tod = [[0, 0] for _ in range(24 // TOD_BUCKET_HOURS)]   # [matches, wins]
        dow = [[0, 0] for _ in range(7)]                        # Mon..Sun
        session_ids = set()
        dates = []

        for session_id, played_at, result, overtime, duration in rows:
            won = (result == "win")
            wins   += won
            losses += (not won)
            session_ids.add(session_id)

            if won:
                cur_win += 1
                cur_loss = 0
                best_win = max(best_win, cur_win)
            else:
                cur_loss += 1
                cur_win = 0
                worst_loss = max(worst_loss, cur_loss)

            # `overtime` is 0 and never NULL for legacy rows — the v2 ALTER carried
            # DEFAULT 0 — so anything pre-v2 reads as regulation.
            is_ot = bool(overtime)
            if is_ot:
                ot_matches += 1
                ot_wins    += won
            else:
                reg_wins   += won
                reg_losses += (not won)

            # NULL when Game.TimeSeconds was never seen, and for every legacy row.
            if duration is not None:
                dur_all.append(duration)
                (dur_wins if won else dur_losses).append(duration)
                (dur_ot if is_ot else dur_reg).append(duration)

            if played_at:
                dates.append(played_at)
                try:
                    when = datetime.fromisoformat(played_at)
                except ValueError:
                    continue    # malformed legacy date — counted, but not bucketed
                bucket = when.hour // TOD_BUCKET_HOURS
                tod[bucket][0] += 1
                tod[bucket][1] += won
                dow[when.weekday()][0] += 1
                dow[when.weekday()][1] += won

        total   = wins + losses
        reg_all = reg_wins + reg_losses
        return {
            "overview": {
                "matches":           total,
                "wins":              wins,
                "losses":            losses,
                "winPct":            (wins / total) if total else 0.0,
                "sessions":          len(session_ids),
                "bestWinStreak":     best_win,
                "worstLossStreak":   worst_loss,
                "firstDate":         min(dates) if dates else "",
                "lastDate":          max(dates) if dates else "",
                "timedMatches":      len(dur_all),
                "avgDurationSecs":   _mean(dur_all),
                "avgDurationWins":   _mean(dur_wins),
                "avgDurationLosses": _mean(dur_losses),
                "avgDurationReg":    _mean(dur_reg),
                "avgDurationOT":     _mean(dur_ot),
                "overtimeMatches":   ot_matches,
                "overtimeWins":      ot_wins,
                "overtimeLosses":    ot_matches - ot_wins,
                "overtimeWinPct":    (ot_wins / ot_matches) if ot_matches else 0.0,
                "overtimeShare":     (ot_matches / total) if total else 0.0,
                "regulationMatches": reg_all,
                "regulationWinPct":  (reg_wins / reg_all) if reg_all else 0.0,
            },
            "timeOfDay": [_bucket_row(_tod_label(i), m, w)
                          for i, (m, w) in enumerate(tod)],
            "dayOfWeek": [_bucket_row(label, m, w)
                          for label, (m, w) in zip(WEEKDAY_LABELS, dow)],
        }

    def _analytics_self_stats(self) -> dict:
        """Per-match averages for the local player, plus your best and worst match
        by score.

        Driven off `matches.local_player_id` (schema v3), so matches that predate
        it and couldn't be backfilled are excluded — the returned `matches` is the
        attributable count, which the UI compares against the all-time total to
        explain any gap. No `role = 'teammate'` filter is needed: match_players' PK
        is (match_id, player_id), so the join yields at most one row per match."""
        with self._db_lock:
            self.cursor.execute("""
                SELECT m.id, m.played_at, m.result,
                       mp.score, mp.goals, mp.shots, mp.assists,
                       mp.saves, mp.touches, mp.demos
                FROM matches m
                JOIN match_players mp
                  ON mp.match_id  = m.id
                 AND mp.player_id = m.local_player_id
                WHERE m.local_player_id IS NOT NULL
                ORDER BY m.id ASC
            """)
            rows = self.cursor.fetchall()

        keys = ("score", "goals", "shots", "assists", "saves", "touches", "demos")

        def averages(subset: list) -> dict:
            return {k: _mean([r[3 + i] for r in subset]) for i, k in enumerate(keys)}

        def match_dict(row) -> dict:
            return {
                "matchId": row[0],
                "date":    row[1] or "",
                "result":  row[2],
                "score":   row[3],
                "goals":   row[4],
                "shots":   row[5],
                "assists": row[6],
                "saves":   row[7],
            }

        won  = [r for r in rows if r[2] == "win"]
        lost = [r for r in rows if r[2] != "win"]
        shots_total = sum(r[5] for r in rows)
        goals_total = sum(r[4] for r in rows)

        # Score, tie-broken on goals, then the newest match — the negated id flips
        # that last tiebreak for min().
        best  = max(rows, key=lambda r: (r[3], r[4],  r[0])) if rows else None
        worst = min(rows, key=lambda r: (r[3], r[4], -r[0])) if rows else None

        return {
            "matches":      len(rows),
            "avg":          averages(rows),
            "avgInWins":    averages(won),
            "avgInLosses":  averages(lost),
            "shotAccuracy": (goals_total / shots_total) if shots_total else 0.0,
            "best":         match_dict(best) if best else {},
            "worst":        match_dict(worst) if worst else {},
        }

    def _analytics_leaderboards(self) -> dict:
        """Toughest opponents (lowest win rate against, min MIN_OPPONENT_GAMES) and
        most-played teammates (min MIN_TEAMMATE_GAMES).

        `wins`/`losses` are YOUR record in those matches, not theirs.

        The WHERE clause exists because the local user IS a `role='teammate'` row
        in match_players — record_result derives teammates from `current_teammates`,
        which includes you. Without it you'd top your own most-played-teammates
        list. Rows recorded before schema v3 (or that the backfill couldn't
        attribute) have no local_player_id, so those fall back to a name match.

        This is the one aggregation grouped in SQL rather than Python: its raw row
        count is ~4x the match count while its output is a bounded top-N, and
        nothing about it is order-dependent (which is why get_session_summaries
        groups in Python — streaks are).

        The minimum-games thresholds are applied in Python rather than as a HAVING
        clause, because they have to run *after* _fold_legacy_player_ids: a split
        identity whose synthetic half has only one match would otherwise be pruned
        before it could be merged, quietly undercounting that player."""
        lowered = (self._local_username or "").lower()
        with self._db_lock:
            self.cursor.execute("""
                SELECT mp.role,
                       mp.player_id,
                       COALESCE(p.name, mp.name_at_match)                 AS name,
                       COUNT(*)                                           AS played,
                       SUM(CASE WHEN m.result = 'win'  THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.result = 'loss' THEN 1 ELSE 0 END) AS losses,
                       MAX(m.played_at)                                   AS last_date
                FROM match_players mp
                JOIN matches m      ON m.id = mp.match_id
                LEFT JOIN players p ON p.id = mp.player_id
                WHERE NOT (mp.role = 'teammate'
                           AND (mp.player_id = m.local_player_id
                                OR (m.local_player_id IS NULL
                                    AND lower(mp.name_at_match) = ?)))
                GROUP BY mp.role, mp.player_id
            """, (lowered,))
            rows = self.cursor.fetchall()

        groups = {}
        for role, pid, name, played, wins, losses, last_date in rows:
            groups[(role, pid)] = {
                "playerId": pid,
                "name":     name,
                "played":   played,
                "wins":     wins,
                "losses":   losses,
                "lastDate": last_date or "",
            }
        groups = _fold_legacy_player_ids(groups)

        opponents, teammates = [], []
        for (role, _pid), entry in groups.items():
            entry["winPct"] = (entry["wins"] / entry["played"]) if entry["played"] else 0.0
            if role == "opponent" and entry["played"] >= MIN_OPPONENT_GAMES:
                opponents.append(entry)
            elif role == "teammate" and entry["played"] >= MIN_TEAMMATE_GAMES:
                teammates.append(entry)

        # Toughest = worst record against, most-faced breaking ties.
        opponents.sort(key=lambda r: (r["winPct"], -r["played"], r["name"].lower()))
        teammates.sort(key=lambda r: (-r["played"], r["winPct"], r["name"].lower()))
        return {"opponents": opponents[:LEADERBOARD_LIMIT],
                "teammates": teammates[:LEADERBOARD_LIMIT]}