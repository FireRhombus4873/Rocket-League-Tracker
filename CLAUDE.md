# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rocket League Tracker is a Windows desktop app written in Python + PyQt6 that connects to the in-game Rocket League Stats API (TCP, `localhost:49123`) to track match results, player stats, and opponent history across sessions.

The app runs in the background, optionally autostarting with Windows, and ships as a single PyInstaller-built executable.

## Architecture

The runtime pipeline is built around five components, each in its own file, plus two support modules (`config.py`, `settingsManager.py`) that own paths and persisted preferences. They communicate through callbacks and PyQt signals — the boundaries between them matter, so changes should respect the existing flow.

```
processHandler ──watches──> RocketLeague.exe
       │
       ▼
socketHandler ──connects──> Stats API (localhost:49123)
       │
       │ raw JSON messages
       ▼
eventHandler / sessionStore (depending on event type)
       │
       │ PyQt signals (thread-safe)
       ▼
mainWindow (PyQt6 GUI)
```

### `main.py` — wiring

- Constructs every component
- Defines `handle_event` (named events) and `handle_update_state` (the high-frequency tick) as nested functions so they can capture `window`, `session`, etc.
- Constructs `SocketHandler` *after* the callbacks are defined to avoid `UnboundLocalError`
- Spawns `process_watcher` on a background thread that loops forever: wait for RL → emit `settings_prompt` if no username is saved → start socket → wait for RL to close → stop socket → repeat
- `LOCAL_USERNAME` / `COMMON_TEAMMATES` are module globals. They're synced from `SettingsManager` **immediately after construction in `main()`** (not just via `prompt_settings`) — without this, `try_set_players_from_update` would be called with `local_username=None` and crash on `.lower()`, killing the socket loop. `prompt_settings` then re-syncs after the dialog so first-run / edits also take effect without a restart.

### `config.py` / `settingsManager.py` — paths + user settings

- `config.py` exposes the single source of truth for filesystem paths: `BASE_DIR`, `HISTORY_DB_FILE`, `HISTORY_FILE` (legacy JSON, only used by the one-shot migration), `SETTINGS_FILE`.
- `SettingsManager` loads/saves `settings.json`. Two keys today: `localUsername` (used by `sessionStore` to determine which team is "ours") and `commonTeammates` (a list of names that are hidden from the Past Encounters card — `_on_encounters_updated` filters them out so the card highlights *new* people you've teamed with, not your usual crew).

### `socketHandler.py` — TCP listener

- Persistent reconnect loop: tries `(localhost, 49123)`, retries every 2s on `ConnectionRefusedError`
- `start()` is idempotent — it will not spawn a duplicate listener thread if one is already alive (this matters for autostart scenarios)
- `stop()` closes the underlying socket so a blocking `recv` returns immediately rather than waiting for the next packet
- Accumulates bytes into a buffer until they parse as valid JSON, then dispatches
- Stats API messages have shape `{"Event": "...", "Data": "..."}` where **`Data` is a JSON-encoded string**, not an object — it must be decoded twice
- `UpdateState` events go to `on_update_state_callback`; everything else goes to `on_message_callback`

### `eventHandler.py` — named event dispatcher

- Maps event names like `BallHit`, `GoalScored`, `MatchEnded` to handler methods
- Each handler returns a small dict (e.g. `{"GoalScored": "PlayerName"}`) which is forwarded to the main `handle_event` callback
- Stats API event names use PascalCase, no prefix (unlike SOS plugin which uses `game:` prefixes)
- `MatchInitialized` carries no player data in this API — players come from `UpdateState` only

### `sessionStore.py` — state + persistence

This is the most complex file. It handles four intertwined concerns:

**Player roster tracking**: `_player_registry` is a dict keyed by **`PrimaryId`** (e.g. `"Steam|123|0"`) that persists for the entire match. Keying by ID rather than name means players who share a display name like `.` don't collide. Players are *upserted* (never deleted) so leavers retain their last known stats. `current_players` / `current_opponents` / `current_teammates` are derived from the registry.

**Stats vs UI refresh separation**: `try_set_players_from_update` is called on every `UpdateState` tick. It always updates stats but only returns `True` (signalling a UI refresh) when the roster changes (new GUID or new player joined). This keeps stats live without flickering the UI.

**Session management**: `session_num` increments on `new_session()` (Rocket League launched fresh) or stays the same on `continue_session()` (re-tallies wins/losses from the database for that session). The user picks via a dialog when RL is detected.

**Persistence (SQLite)**: history lives in `%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\history.db`. Four tables: `sessions`, `matches`, `players`, `match_players` — see *Data Schema* below. The DB is the source of truth; `self.match_history` is a **bounded in-memory cache of only the most recent `_cache_size` (20) matches**, oldest-first, used to feed the history table in the UI. All aggregations (`_encounter_for`, `get_session_summaries`, `_retally_active_session`) query the DB directly — they never walk the cache — so cache size has zero effect on correctness.

The connection is opened with `check_same_thread=False` and every cursor access is wrapped in `self._db_lock` (a `threading.RLock`). This is required because the socket listener thread calls `record_result` / `_encounter_for` while the Qt main thread calls `delete_session` / `get_session_summaries`. If you add a new method that touches `self.cursor`, wrap its body in `with self._db_lock:` or you'll re-introduce the cross-thread crash.

If a legacy `match_history.json` exists and the DB has no matches, `_maybe_migrate_from_json` ports it over once on startup and renames the file to `.bak`. Entries written before ID-tracking get a synthetic `legacy:<name>` player id; new bad rows (if `PrimaryId` is somehow empty at match end) get `unknown:<name>` — both kept distinct so `_encounter_for` knows which rows to fall back to a name match on.

**Pause tracking**: when `MainWindow.is_tracking_paused()` is true, `main.py`'s `MatchEnded` handler calls `session.discard_match()` instead of `record_result()`. Players still display live during the match (the `UpdateState` flow is untouched), but no DB row is written and `wins`/`losses` stay put. The user toggles this via the checkbox in the win/loss row.

### `processHandler.py` — process detection

- `wait_for_game()` polls `psutil` every 2s for `RocketLeague.exe`
- `wait_for_game_to_close()` polls the same way and returns as soon as `psutil` reports the process gone
- ⚠️ Earlier revisions had a `CLOSE_CONFIRMATIONS = 3` debounce wrapped in try/except for `NoSuchProcess` / `AccessDenied` (psutil is flaky during system boot, and one missed check kills the socket listener). That safety net is **not currently in the code** — restoring it is worth doing before declaring autostart "fixed"

### `mainWindow.py` — GUI

- Single window with: status header (status indicator + gear settings button), win/loss/ratio/streak cards, SESSIONS / NEW SESSION / PAUSE TRACKING controls, current-match player list, Past Encounters card (per-player W/L vs you, opponents and teammates merged with a red/blue role dot — common teammates from settings are filtered out), match history table
- Dark theme defined as constants (BG_DARK, ACCENT, etc.) at the top
- All UI updates flow through `UISignals` (a `QObject` with `pyqtSignal`s) — background threads emit, main thread slots receive. **Never touch widgets from a background thread directly.**
- Three modal dialogs:
  - `MatchStatsDialog` — opened by clicking a history row; shows per-player stats for that match
  - `SessionSummaryDialog` — opened by the SESSIONS button; one row per session with delete + confirm
  - `SettingsDialog` — opened by the gear button or auto-prompted on first run when no username is saved
- System tray icon allows minimise-to-tray behaviour for autostart use

## Critical Conventions

### Threading

- Qt requires all UI work on the main thread. Background threads (`process_watcher`, socket listener) communicate via `window.signals.<name>.emit(...)`.
- When adding a new background → UI interaction, **always add a new `pyqtSignal` to `UISignals`** rather than calling widget methods directly.

### Stats API quirks

- The `Data` field is a JSON-encoded **string**, requiring a second `json.loads`. This trips people up.
- Pre-match `UpdateState` ticks contain players with all-zero stats. Don't snapshot stats early — `record_result` is the only place that should write to history.
- A player's `PrimaryId` looks like `"Epic|abc123|0"` or `"Steam|7656...|0"` — `platform|accountId|splitscreenIndex`. The platform prefix is parsed via `_parse_platform()`. **`PrimaryId` is the canonical player identity**; never compare players by display name (`.` is a common name, names can change). Splitscreen index is included so guest players on the same console are tracked as distinct.
- Team colours come from `Game.Teams[].ColorPrimary` as a hex string with no `#` prefix.

### Editing `sessionStore.py`

The interaction between `_player_registry`, `_seen_player_count`, and the "should I refresh the UI?" return value is subtle. Before changing logic in `try_set_players_from_update`, re-read the docstring and walk through these scenarios:

1. New match starts (new GUID) — should reset everything and refresh UI
2. Late joiner mid-match — should refresh UI
3. Stats updating during play (no roster change) — should NOT refresh UI but MUST update stats
4. Player leaves mid-match — should keep their stats in the registry
5. Match ends — `record_result` writes to the DB and trims the cache; or, when tracking is paused, `discard_match` clears the registry without writing

If a change breaks any of these, stats won't get recorded correctly — and silent data corruption is the worst kind of bug here.

Also: any new aggregation (W/L totals, streaks, per-player records) must hit the DB. **Do not derive values from `self.match_history`** — it's a bounded cache of the most recent 20 matches and is wrong for any session with more games than that. Add a SQL query or extend `_retally_active_session` / `get_session_summaries` instead.

## Building and Running

### Development

```bat
python -m venv .venv
.venv\Scripts\activate
pip install PyQt6 psutil
python main.py
```

### Release build

```bat
.venv\Scripts\activate
pip install pyinstaller
.\build.bat
```

The build uses `RocketLeagueTracker.spec` (a PyInstaller spec file) which bundles everything into a single `.exe` with the icon from `assets/`.

## Things That Aren't What They Look Like

- **The Stats API is not the SOS plugin.** Earlier versions of this code used SOS — different event names (`game:ball_hit` etc.), a different port (49122), and a different message structure. The current codebase no longer references SOS, but if you find any such comments in future, they're stale: Stats API is the source of truth.
- **`MatchInitialized` does not contain player data** in the Stats API. Players are *only* available through `UpdateState`. Don't add player parsing to `on_match_initialized` in `eventHandler.py`.
- **`self.match_history` is not the history** — it's a bounded 20-match cache for the UI table only. The DB is authoritative. See *Editing `sessionStore.py`*.

## Versioning

Project follows semver. Currently in pre-1.0 — schema and core behaviour can still change. Bug fixes that affect data correctness (stats not saved, win/loss wrong, app fails to connect) ship as patch releases as soon as they're found. Cosmetic and additive changes can be batched.

## Data Schema

History is stored in SQLite at `%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\history.db`. The schema is created/maintained by `SessionStore._initDatabase` and stamped with `PRAGMA user_version` (currently `1`):

- **`sessions`** — `id` PK (matches `session_num`), `name` (nullable, reserved for the labelled-sessions TODO), `started_at`, `ended_at`
- **`matches`** — `id` autoincrement PK, `session_id` FK ON DELETE CASCADE, `played_at`, `result` ∈ {`win`,`loss`}, `winner_team`
- **`players`** — `id` PK = full `PrimaryId`, `name` (most recently seen), `platform`, `first_seen`, `last_seen`. Canonical identity.
- **`match_players`** — composite PK `(match_id, player_id)`, `role` ∈ {`opponent`,`teammate`}, `name_at_match` (snapshot — preserved so renames don't rewrite history), `team_num`, stat columns. Indexed on `(player_id, role)` for fast encounter lookups.

`record_result` upserts the session row (bumping `ended_at`), inserts the match, upserts each `players` row, then inserts `match_players`. `delete_session` is a single `DELETE FROM sessions WHERE id = ?` — FK cascades handle the rest.

`_encounter_for` returns a dict per player with `wins`, `losses`, `encounters`, `crossEncounters` (count in the *opposite* role — lets the UI distinguish a true first meeting from "first time as a teammate but we've faced before"), `lastDate`, `lastSessionNum`, `matchesAgo`.

Synthetic player ids:
- `legacy:<name>` — written by `_maybe_migrate_from_json` for entries that predate ID tracking. `_encounter_for` falls back to a name match against these rows only.
- `unknown:<name>` — written by `record_result` if `PrimaryId` is somehow empty at match end. Should never appear under normal play; if it does, treat as a bug signal.

The legacy JSON shape (still readable by `_maybe_migrate_from_json`) was:

```json
{ "date": "...", "result": "win|loss", "sessionNum": 3,
  "opponents": [ { "id?": "Steam|...|0", "name": "...", "platform": "...", "score": 0, "goals": 0, ... } ],
  "teammates": [ /* same shape */ ] }
```

When evolving the schema, bump `user_version` and add an idempotent upgrade step in `_initDatabase` (or a dedicated migration helper). Existing DBs in users' `%LOCALAPPDATA%` won't have the new columns — use `ALTER TABLE … ADD COLUMN` with safe defaults rather than failing hard.
