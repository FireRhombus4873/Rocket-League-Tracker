# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rocket League Tracker is a Windows desktop app written in Python + PyQt6 that connects to the in-game Rocket League Stats API (TCP, `localhost:49123`) to track match results, player stats, and opponent history across sessions.

The app runs in the background, optionally autostarting with Windows, and ships as a single PyInstaller-built executable.

## Architecture

The app is built around five components, each in its own file. They communicate through callbacks and PyQt signals — the boundaries between them matter, so changes should respect the existing flow.

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
- Spawns `process_watcher` on a background thread that loops forever: wait for RL → start socket → wait for RL to close → stop socket → repeat
- `LOCAL_USERNAME` is hardcoded at the top — it's used by `sessionStore` to determine which team is "ours"

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

This is the most complex file. It handles three intertwined concerns:

**Player roster tracking**: `_player_registry` is a dict keyed by player name that persists for the entire match. Players are *upserted* (never deleted) so leavers retain their last known stats. `current_players`/`current_opponents`/`current_teammates` are derived from the registry.

**Stats vs UI refresh separation**: `try_set_players_from_update` is called on every `UpdateState` tick. It always updates stats but only returns `True` (signalling a UI refresh) when the roster changes (new GUID or new player joined). This keeps stats live without flickering the UI.

**Session management**: `session_num` increments on `new_session()` (Rocket League launched fresh) or stays the same on `continue_session()` (re-tallies wins/losses from history for that session). The user picks via a dialog when RL is detected.

History is persisted as JSON to `%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\match_history.json`.

**Pause tracking**: when `MainWindow.is_tracking_paused()` is true, `main.py`'s `MatchEnded` handler calls `session.discard_match()` instead of `record_result()`. Players still display live during the match (the `UpdateState` flow is untouched), but no history entry is written and `wins`/`losses` stay put. The user toggles this via the checkbox next to "New Session".

### `processHandler.py` — process detection

- `wait_for_game()` polls `psutil` every 2s for `RocketLeague.exe`
- `wait_for_game_to_close()` requires **3 consecutive** missed checks before declaring the game closed. This is critical for autostart — `psutil` is flaky during system boot and a single missed check would cause `socket_handler.stop()` to fire, killing the listener thread
- All `psutil` calls are wrapped in a try/except for `NoSuchProcess`/`AccessDenied` errors that can occur during boot

### `mainWindow.py` — GUI

- Single window with: status header, win/loss/ratio/streak cards, current-match player list, match history table
- Dark theme defined as constants (BG_DARK, ACCENT, etc.) at the top
- All UI updates flow through `UISignals` (a `QObject` with `pyqtSignal`s) — background threads emit, main thread slots receive. **Never touch widgets from a background thread directly.**
- Clicking a history row opens a `MatchStatsDialog` showing per-player stats for that match
- System tray icon allows minimise-to-tray behaviour for autostart use

## Critical Conventions

### Threading

- Qt requires all UI work on the main thread. Background threads (`process_watcher`, socket listener) communicate via `window.signals.<name>.emit(...)`.
- When adding a new background → UI interaction, **always add a new `pyqtSignal` to `UISignals`** rather than calling widget methods directly.

### Stats API quirks

- The `Data` field is a JSON-encoded **string**, requiring a second `json.loads`. This trips people up.
- Pre-match `UpdateState` ticks contain players with all-zero stats. Don't snapshot stats early — `record_result` is the only place that should write to history.
- A player's `PrimaryId` looks like `"Epic|abc123|0"` or `"Steam|7656...|0"`. The platform prefix is parsed via `_parse_platform()`.
- Team colours come from `Game.Teams[].ColorPrimary` as a hex string with no `#` prefix.

### Editing `sessionStore.py`

The interaction between `_player_registry`, `_seen_player_count`, and the "should I refresh the UI?" return value is subtle. Before changing logic in `try_set_players_from_update`, re-read the docstring and walk through these scenarios:

1. New match starts (new GUID) — should reset everything and refresh UI
2. Late joiner mid-match — should refresh UI
3. Stats updating during play (no roster change) — should NOT refresh UI but MUST update stats
4. Player leaves mid-match — should keep their stats in the registry
5. Match ends — `record_result` snapshots current state and clears the registry; or, when tracking is paused, `discard_match` clears the registry without writing

If a change breaks any of these, stats won't get recorded correctly — and silent data corruption is the worst kind of bug here.

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

- **The Stats API is not the SOS plugin.** Earlier versions of this code used SOS, which has different event names (`game:ball_hit` etc.), a different port (49122), and a different message structure. Some old comments may still reference SOS — they're stale. Stats API is the source of truth.
- **`MatchInitialized` does not contain player data** in the Stats API. Players are *only* available through `UpdateState`. Don't add player parsing to `on_match_initialized` in `eventHandler.py`.
- **psutil is unreliable during boot.** Don't reduce `CLOSE_CONFIRMATIONS` in `processHandler.py` without understanding why it's there — the value of 3 was set deliberately to fix autostart issues.

## Versioning

Project follows semver. Currently in pre-1.0 — schema and core behaviour can still change. Bug fixes that affect data correctness (stats not saved, win/loss wrong, app fails to connect) ship as patch releases as soon as they're found. Cosmetic and additive changes can be batched.

## Data Schema

Match history entry structure:

```json
{
  "date": "2026-05-01T17:55:49",
  "result": "win" | "loss",
  "sessionNum": 3,
  "opponents": [
    {
      "name": "...",
      "platform": "Epic" | "Steam" | "PlayStation" | "Xbox" | "Switch",
      "score": 0,
      "goals": 0,
      "shots": 0,
      "assists": 0,
      "saves": 0,
      "touches": 0,
      "carTouches": 0,
      "demos": 0
    }
  ],
  "teammates": [ /* same shape as opponents */ ]
}
```

When changing this schema, remember that existing JSON files in users' `%LOCALAPPDATA%` won't have the new fields — `_load_history` should tolerate missing keys via `.get(key, default)` patterns rather than failing hard.
