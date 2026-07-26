# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rocket League Tracker is a Windows desktop app written in Python + PyQt6 that connects to the in-game Rocket League Stats API (TCP, `localhost:49123`) to track match results, player stats, and opponent history across sessions.

The app runs in the background, optionally autostarting with Windows, and ships as a single PyInstaller-built executable.

## Architecture

The runtime pipeline is built around five components — four single-file modules (`processHandler.py`, `socketHandler.py`, `eventHandler.py`, `sessionStore.py`) plus the GUI, which lives in the `ui/` package — with two support modules (`config.py`, `settingsManager.py`) that own paths and persisted preferences. They communicate through callbacks and PyQt signals — the boundaries between them matter, so changes should respect the existing flow.

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
ui.main_window (PyQt6 GUI)
```

### `main.py` — wiring

- Constructs every component
- Defines `handle_event` (named events) and `handle_update_state` (the high-frequency tick) as nested functions so they can capture `window`, `session`, etc.
- Constructs `SocketHandler` *after* the callbacks are defined to avoid `UnboundLocalError`
- Spawns `process_watcher` on a background thread that loops forever: wait for RL → emit `settings_prompt` if no username is saved → start socket → wait for RL to close → stop socket → repeat
- `LOCAL_USERNAME` / `COMMON_TEAMMATES` are module globals. They're synced from `SettingsManager` **immediately after construction in `main()`** (not just via `prompt_settings`) — without this, `try_set_players_from_update` would be called with `local_username=None` and crash on `.lower()`, killing the socket loop. `prompt_settings` then re-syncs after the dialog so first-run / edits also take effect without a restart. Both places also call `session.set_local_username(...)`, which is what runs the `matches.local_player_id` backfill — skip it and per-you analytics only cover matches recorded since schema v3.
- Owns **startup visibility** via `_should_show_on_start(sys.argv)`, called just before `app.exec()`. `MainWindow` never shows itself — constructing it only puts the tray icon on screen — so whether the window is visible at launch is decided here and nowhere else. Frozen builds (`sys.frozen`, set by the PyInstaller bootloader) start tray-only so autostart doesn't steal focus at boot; running from source starts visible so UI changes are testable without digging through the tray. `--show` / `--tray` force either behaviour. See *Development* below.
- Owns **close behaviour** the same way, via `_should_close_to_tray(sys.argv)` → `MainWindow(close_to_tray=...)`. The build hides to tray on close (it must stay resident to notice the next RL launch); a source run quits outright, so closing the window ends the process instead of orphaning it in the tray. Note the two helpers read the flags differently: `--tray` forces build behaviour in both, but **`--show` only affects launch visibility** — a build passed `--show` still closes to tray.

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

**Persistence (SQLite)**: history lives in `%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\history.db`. Four tables: `sessions`, `matches`, `players`, `match_players` — see *Data Schema* below. The DB is the source of truth; `self.match_history` is a **bounded in-memory cache of only the most recent `_cache_size` (20) matches**, oldest-first, used to feed the history table in the UI. All aggregations (`_encounter_for`, `get_session_summaries`, `get_analytics`, `_retally_active_session`) query the DB directly — they never walk the cache — so cache size has zero effect on correctness. Each cache entry carries its `matches.id` under the `"id"` key (set by both `_load_history` and `record_result`) so the UI can name a specific match when deleting one.

**Deleting history**: `delete_session(session_num)` drops a whole session; `delete_match(match_id)` drops a single match. Both re-read the cache and call `_retally_active_session()` afterwards so `wins`/`losses` — and therefore the win-rate and streak cards — reflect the DB again. `delete_match` deliberately **leaves the owning `sessions` row in place** even when it empties the session: `_load_last_session_num` reads `MAX(sessions.id)`, so removing the row would let a future session reuse a retired number. An empty session just stops appearing in `get_session_summaries`, which builds from `matches`.

The connection is opened with `check_same_thread=False` and every cursor access is wrapped in `self._db_lock` (a `threading.RLock`). This is required because the socket listener thread calls `record_result` / `_encounter_for` while the Qt main thread calls `delete_session` / `get_session_summaries`. If you add a new method that touches `self.cursor`, wrap its body in `with self._db_lock:` or you'll re-introduce the cross-thread crash.

If a legacy `match_history.json` exists and the DB has no matches, `_maybe_migrate_from_json` ports it over once on startup and renames the file to `.bak`. Entries written before ID-tracking get a synthetic `legacy:<name>` player id; new bad rows (if `PrimaryId` is somehow empty at match end) get `unknown:<name>` — both kept distinct so `_encounter_for` knows which rows to fall back to a name match on.

**Pause tracking**: when `MainWindow.is_tracking_paused()` is true, `main.py`'s `MatchEnded` handler calls `session.discard_match()` instead of `record_result()`. Players still display live during the match (the `UpdateState` flow is untouched), but no DB row is written and `wins`/`losses` stay put. The user toggles this via the checkbox in the win/loss row.

**Winner determination & the leaver problem**: two events can end a match. `MatchEnded` carries the authoritative winning team number, so `main.py` parses it (`get_winner`, defaulting to `-1` on a bad/missing value) and passes it as `record_result(winner_team=...)`. But if the user *leaves before* `MatchEnded` fires, only `MatchDestroyed` arrives — with no winner — so that handler calls `record_result()` with no argument. When `winner_team` is `None`, `record_result` falls back to `calculate_winner_team()`, which infers the winner by summing each team's goals from the last known player stats. A `_match_recorded` flag (exposed via `result_recorded()`) guards against double-recording: `MatchDestroyed` only records if `MatchEnded` hasn't already. `record_result` also **skips recording entirely when `_local_team == -1`** (we never matched the local username against any player — typically freeplay with no roster), rather than the old behaviour of defaulting to team 0.

**Match duration & overtime**: every `UpdateState` tick reads `Game.TimeSeconds` (the clock, which counts *down* from ~300) into `_match_start_secs` (first tick) and `_match_last_secs` (latest tick); `Game.bOvertime` latches `_match_overtime` to `True`. At record time, `duration_secs = max(start - last, 0)` and the overtime flag are written to the `matches` row. All of this per-match state is reset on a new GUID, on `record_result`, and on `discard_match`.

**Local-player identity**: `_local_player_id` holds the local user's `PrimaryId`, latched from `UpdateState` by the same loop that resolves `_local_team`, and written to `matches.local_player_id` (schema v3) by `record_result`. It has **exactly `_local_team`'s lifecycle — reset on a new GUID only, *not* in `record_result` / `discard_match`** (unlike the clock/overtime latches above). That keeps "team known ⟹ id known" true; resetting only one of the pair would write a NULL id on a match that *is* attributable. This exists because you are stored in `match_players` as an ordinary `role='teammate'` row with nothing marking it as you (`record_result` derives teammates from `current_teammates`, which includes you) — so without it, per-you stats are uncomputable and you rank as your own most-played teammate. `set_local_username(name)` stores the name and runs a one-time idempotent backfill of `local_player_id` for pre-v3 rows by matching `lower(name_at_match)` on teammate rows; it only touches rows where **exactly one** teammate matches, so a teammate who shares your display name leaves the row NULL rather than being guessed at. `main.py` calls it right after `SessionStore()` and again in `prompt_settings`.

**Analytics aggregation**: `get_analytics()` is the single public entry point — one nested dict, one signal, one slot. It holds `_db_lock` across the whole build and does its Python derivation *inside* the lock, the deliberate exception to the "derive after releasing the lock" idiom used everywhere else: otherwise a match recorded mid-refresh could leave the cards and the charts disagreeing. Cost is three queries (`_analytics_from_matches`, `_analytics_self_stats`, `_analytics_leaderboards`) plus a reuse of `get_session_summaries()`. Every value in the payload is non-None — `{}` for absent objects, `0.0` plus a companion count (e.g. `timedMatches`) for unknown scalars — so the Qt slot needs no None-guards. Tuning constants (`ROLLING_WINDOW`, `MIN_OPPONENT_GAMES`, `MIN_TEAMMATE_GAMES`, `LEADERBOARD_LIMIT`, `TOD_BUCKET_HOURS`) live at module scope so the UI never invents thresholds. Time-of-day / day-of-week bucketing is done in **Python, not `strftime()`**, because legacy migrated rows can carry `played_at = ''` and a Python guard is harder to forget than a `WHERE` clause; both bucket lists are fixed-length (6 and 7) so the charts always have a full axis. `_analytics_leaderboards` is the one aggregation grouped in SQL (bounded top-N out of ~4 rows per match, and nothing order-dependent), and it post-processes through `_fold_legacy_player_ids` — see below.

**The `legacy:` split-identity problem**: someone you played both before and after the JSON migration has *two* rows in `players` — their real `PrimaryId` and a synthetic `legacy:<name>` — which splits their leaderboard record in half (a real DB had `123rdswagcity` at 382 and 43 instead of 425). `_fold_legacy_player_ids` merges a synthetic group into the real one **only when exactly one real id shares that name and role**; display names aren't unique (`.` is a real one), so anything ambiguous stands on its own. Same "don't guess when it's ambiguous" rule as the `set_local_username` backfill. Any *new* per-player aggregation needs this fold too, or it will double-count long-standing players.

### `processHandler.py` — process detection

- `wait_for_game()` polls `psutil` every 2s for `RocketLeague.exe`
- `wait_for_game_to_close()` polls the same way and returns as soon as `psutil` reports the process gone
- ⚠️ Earlier revisions had a `CLOSE_CONFIRMATIONS = 3` debounce wrapped in try/except for `NoSuchProcess` / `AccessDenied` (psutil is flaky during system boot, and one missed check kills the socket listener). That safety net is **not currently in the code** — restoring it is worth doing before declaring autostart "fixed"

### `ui/` package — GUI

The GUI was split out of a single ~1,390-line `mainWindow.py` into a small package. Behaviour is unchanged — only the layout:

- `ui/main_window.py` — `MainWindow`: the persistent header, the `QTabWidget`, and every signal slot.
- `ui/theme.py` — dark-theme design tokens (`BG_DARK`, `ACCENT`, …) **and** the app-wide `APP_STYLESHEET` string.
- `ui/widgets.py` — reusable pieces shared by the window and dialogs: `Card`, `soft_shadow`, `platform_icon`, `NameColumnCursor`.
- `ui/charts.py` — hand-rolled `QPainter` chart widgets for the Analytics tab (`WinRateTrendChart`, `WinRateBarChart`, `ComparisonBars`, plus the `_ChartBase` they share). Data in via `set_data(...)`, pixels out — no DB access, no signals, and no unit formatting (anything needing `m:ss` arrives pre-formatted from the slot, so numbers are formatted in exactly one place). Every colour comes from `theme.py`. Deliberately no charting dependency: runtime deps stay PyQt6 + psutil and `RocketLeagueTracker.spec`'s `hiddenimports` stays empty.
- `ui/signals.py` — `UISignals`.
- `ui/dialogs/` — `match_stats_dialog.py` (`MatchStatsDialog`) and `settings_dialog.py` (`SettingsDialog`).
- `ui/__init__.py` re-exports `MainWindow` and `SettingsDialog`, so `main.py` imports both via `from ui import MainWindow, SettingsDialog`. Only `main.py` imports from the GUI layer.

⚠️ Assets are bundled at the project / `_MEIPASS` root, but `ui/main_window.py` sits one level down, so it resolves them via `ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"`. Any new module under `ui/` that loads an asset must account for its own depth — don't copy the old `Path(__file__).parent / "assets"`, which now points one level too shallow. (`charts.py` loads no assets and deliberately has no `ASSETS_DIR` — don't add one.)

**`MainWindow` (`ui/main_window.py`)** — a persistent header (status indicator + gear settings button) sits above a `QTabWidget` (`self._tabs`) with three top-level tabs. Each tab is built by its own `_build_*_tab()` method returning a `QWidget`; the header is built by `_build_header()`. The status bar spans all tabs.
  - **Tracker** — win/loss/ratio/streak cards, NEW SESSION / PAUSE TRACKING controls, current-match player list, Past Encounters card (per-player W/L vs you, opponents and teammates merged with a red/blue role dot — common teammates from settings are filtered out), and the match history table. The history table's trailing column (`MainWindow.HISTORY_DELETE_COL`) holds a per-row ✕ built by `_make_match_delete_cell`; `_handle_match_delete` confirms, then emits `match_delete_requested(match_id)`. It's a **cell widget, not a `QTableWidgetItem`**, so clicking it doesn't fire `itemClicked` and open `MatchStatsDialog` on the way past — keep it that way if the column moves.
    - ⚠️ `APP_STYLESHEET`'s `QTableWidget::item { padding: 9px 12px; }` applies to cell widgets too: a cell widget is laid out in the **padded content rect**, not the full cell (20×22 inside the 44×40 delete column). A child sized larger than that — e.g. via `setFixedSize` — is silently clipped, which shows up as a hover fill with straight edges instead of a rounded square. That's why the ✕ expands to fill its holder rather than taking a fixed size. Any future cell widget should size itself the same way, or account for the padding.
  - **Sessions** — one row per session (dates, matches, W/L, win %, best-win/worst-loss streaks) with a totals header. Selecting a row enables **Delete Session**, which confirms then emits `session_delete_requested`; `main.py` performs the delete and re-emits `sessions_updated` to re-render. This replaced the old modal `SessionSummaryDialog` and the standalone SESSIONS button.
  - **Analytics** — six stacked cards inside a `QScrollArea` (the repo's only one), built by `_build_analytics_tab` plus one `_build_analytics_*` sub-builder each, and rendered by `_on_analytics_updated` from a single `analytics_updated(dict)` payload — one `SessionStore.get_analytics()` snapshot. In order: overview stat cards (all-time record, win rate, avg duration, overtime record) → win rate over time (per session + rolling average) → your averages (per-match stats in wins vs losses, plus best/worst match) → overtime & match length → win rate by time of day and day of week → toughest-opponent / most-played-teammate leaderboards. Everything is **all-time; there is no scope selector** (the win-rate view is inherently per-session, and the Sessions tab already answers "how did session N go"). Head-to-head match comparison, promised by the old placeholder, was **dropped** — it's an interactive dialog, a separate feature. The slot **mutates widgets in place rather than rebuilding them**, so scroll position survives the refresh that fires after every match, and it reads every field with `.get()` because it runs on the socket thread's refresh chain where one `KeyError` would break the lot.
    - ⚠️ Two traps this tab hit. `APP_STYLESHEET` doesn't style `QScrollArea`, so it needs `setFrameShape(NoFrame)` (else a sunken grey box against `BG_DARK`) and a transparent viewport. And the bigger one: `QMainWindow, QWidget { background-color: BG_DARK }` matches **every** custom widget, and a stylesheet background makes Qt fill the rect before `paintEvent` — so any custom-painted widget inside a `Card` must set `background: transparent` or it paints a dark rectangle over the card. That's why `_ChartBase.__init__` does it unconditionally.
    - The leaderboard tables set `Player` (column 0) to `Stretch` and the rest to `ResizeToContents`, rather than `_make_table`'s default `setStretchLastSection(True)` — otherwise `Win %` absorbs the slack and its values drift away from the header. They also disable their own vertical scrollbar and get `setFixedHeight(rows * 40 + 36)` in `_fill_analytics_leaderboard`, since the tab's `QScrollArea` owns scrolling.
- The dark theme lives in `ui/theme.py` as constants (`BG_DARK`, `ACCENT`, etc.) plus the `APP_STYLESHEET` string; `MainWindow._apply_styles` just applies that sheet, and the `QTabWidget`/`QTabBar` styling is part of it.
- All UI updates flow through `UISignals` (`ui/signals.py`, a `QObject` with `pyqtSignal`s) — background threads emit, main thread slots receive. **Never touch widgets from a background thread directly.** The Sessions tab is fed by `sessions_updated(list)` (emitted from `main.py`'s `_refresh_sessions` after startup, record, new-session, and delete) and drives deletes back via `session_delete_requested(int)`. `match_delete_requested(int)` is the single-match equivalent; `main.py` handles both by performing the delete and then re-emitting record + history + sessions, since either delete can move the W/L counters. `analytics_updated(dict)` feeds the whole Analytics tab from `main.py`'s `_refresh_analytics` — the rule is **wherever `_refresh_sessions` goes, `_refresh_analytics` goes, last** (startup, record, new-session, both deletes), plus once more after a settings save, since a corrected username can attribute more history.
- Two modal dialogs live in `ui/dialogs/`:
  - `MatchStatsDialog` (`ui/dialogs/match_stats_dialog.py`) — opened by clicking a history row; shows per-player stats for that match. Each player's **name cell is a clickable link** (underlined, coloured by role, pointing-hand cursor via the `NameColumnCursor` event filter) that opens their Rocket League Tracker profile in the default browser (`https://rocketleague.tracker.network/rocket-league/profile/<slug>/<id-or-name>/overview`). `tracker_platform_slug` maps our platform strings to tracker slugs (`ps4`/`ps5`/`playstation` → `psn`, `xbox`/`xbl` → `xbl`, etc.). For Steam the URL uses the numeric account ID pulled from `PrimaryId` (`id.split("|")[1]`); other platforms use the display name (URL-encoded).
  - `SettingsDialog` (`ui/dialogs/settings_dialog.py`) — opened by the gear button or auto-prompted on first run when no username is saved
- System tray icon allows minimise-to-tray behaviour for autostart use. `_setup_tray` shows the *tray icon* on construction but never the window — so the window becomes visible only via `_show_from_tray` (tray click, or the `game_started` signal when RL is detected) or an explicit `show()` from `main.py` at launch. `closeEvent` branches on the `close_to_tray` constructor flag: hide + notify (the shipped default), or take the tray icon down and `QApplication.quit()`. Hiding the icon before quitting matters — Windows leaves a ghost tray icon if the process dies while it's still registered.

## Critical Conventions

### Threading

- Qt requires all UI work on the main thread. Background threads (`process_watcher`, socket listener) communicate via `window.signals.<name>.emit(...)`.
- When adding a new background → UI interaction, **always add a new `pyqtSignal` to `UISignals`** (in `ui/signals.py`) rather than calling widget methods directly.

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
5. Match ends — `record_result` writes to the DB and trims the cache (via `MatchEnded` with a known winner, or `MatchDestroyed` with a goal-inferred winner if the user left first); or, when tracking is paused, `discard_match` clears the registry without writing

If a change breaks any of these, stats won't get recorded correctly — and silent data corruption is the worst kind of bug here.

Also: any new aggregation (W/L totals, streaks, per-player records) must hit the DB. **Do not derive values from `self.match_history`** — it's a bounded cache of the most recent 20 matches and is wrong for any session with more games than that. Add a SQL query or extend `_retally_active_session` / `get_session_summaries` / `get_analytics` instead.

## Building and Running

### Development

```bat
python -m venv .venv
.venv\Scripts\activate
pip install PyQt6 psutil
python main.py
```

Run from source the window **opens visible** and **closing it quits the app**; the built `.exe` starts minimised to the tray and closes back to it (see `_should_show_on_start` / `_should_close_to_tray` under *`main.py` — wiring*). To exercise the real autostart path from source, pass `--tray` — it switches *both* behaviours; to force the window open in a build, pass `--show` (launch visibility only — it still closes to tray):

```bat
python main.py --tray      REM behave like the shipped build
RocketLeagueTracker.exe --show
```

### Release build

```bat
.venv\Scripts\activate
pip install pyinstaller
.\build.bat
```

The build uses `RocketLeagueTracker.spec` (a PyInstaller spec file) which bundles everything into a single `.exe` with the icon from `assets/`.

### Testing

Tests live in `tests/` and run with `pytest` (config in `pytest.ini`; dev deps in `requirements-dev.txt`).

```bat
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                 REM whole suite
pytest -m "not ui"     REM skip the Qt tier (headless / no display)
```

`pytest.ini` sets `pythonpath = .` so tests import the top-level modules directly. The suite is organised in three tiers, matched to where the risk actually is (most bugs live in the data layer, even though most *changes* are UI):

- **Tier 1 — `tests/test_session_store.py`** (the bulk). Drives `SessionStore` against a throwaway SQLite DB and asserts on counters + the DB itself. One test per scenario in *Editing `sessionStore.py`* (win/loss/leaver-inferred/freeplay-skipped, the refresh-return contract, duplicate-name non-collision, double-record guard, discard/pause, duration+overtime, encounters incl. `crossEncounters`, session streaks, session + single-match delete cascades and their retally, continue-session retally, legacy-JSON migration). The `store` / `db_paths` fixtures (in `tests/conftest.py`) monkeypatch `sessionStore.HISTORY_DB_FILE` / `HISTORY_FILE` into `tmp_path` — patch them on the **`sessionStore` module**, not `config`, because they're bound at import via `from config import …`. Message shapes are built with the `player()` / `update_state()` helpers in `tests/factories.py`.
- **Tier 1 — `tests/test_analytics.py`**, same fixtures, split out because the file above is scoped to the match-lifecycle scenarios. Covers v3 stamping, the backfill (including the ambiguous-name guard and idempotency), the whole `get_analytics()` payload, the empty-DB shape, the local player's exclusion from the teammate leaderboard by both id and name fallback, and the `legacy:` fold. Two arrangement techniques it introduces: **back-dating** via `UPDATE matches SET played_at = ?` after `record_result` (`played_at` comes from `datetime.now()` inside the store, so the clock isn't injectable and rewriting the row afterwards is narrower than monkeypatching it), and `update_state(..., time_secs=None)` to omit `TimeSeconds` and get a NULL `duration_secs`. Multi-session data comes from interleaving `store.new_session()`.
- **Tier 2 — `test_event_handler.py`, `test_socket_handler.py`, `test_parsing.py`**. Pure logic, no Qt: `EventHandler.dispatch`, the `SocketHandler._handle_message` double-decode/routing, `_parse_platform`, and `get_winner` / `tracker_platform_slug`. The socket *reconnect/buffer loop* is not unit-tested (needs a live TCP peer) — only the message parsing that feeds it.
- **Tier 3 — `test_ui.py`** (marked `@pytest.mark.ui`, needs `pytest-qt` + a display). Deliberately shallow: emit a `UISignals` signal, assert the target widget's text updated. **Do not assert pixels, colours, or stylesheet strings** — those are meant to change when the UI is restyled, and testing them just creates churn. These tests survive a redesign and only fail if the signal→slot→widget wiring breaks. The charts are not asserted on at all — their only observable is pixels. `_on_analytics_updated` reads ~40 keys off one payload, so there's a deliberate **empty-payload test** (`analytics_updated.emit({})`) alongside the populated one. Note these tests never `show()` the window, so `isVisible()` is `False` for every widget regardless of state — use `isHidden()` when asserting an empty-state toggle.

Two pure helpers were lifted to module scope specifically so Tier 2 can reach them: `get_winner` (from a closure in `main.py` → `eventHandler.py`, imported back by `main.py`) and `tracker_platform_slug` (from a method in `ui/dialogs/match_stats_dialog.py` → module level). Both are behaviour-preserving. `ui/main_window.py`'s `_fmt_mmss` / `_fmt_pct` and `sessionStore.py`'s `_with_rolling_win_pct` / `_fold_legacy_player_ids` are module-scope for the same reason.

A bug the suite surfaced and that's since been fixed: `PLATFORM_MAP`'s keys used to be PascalCase (`Steam`/`Epic`/`Xboxone`) while `_parse_platform` lowercases the prefix before the lookup, so an `Xboxone` prefix resolved to `"Xboxone"` instead of `"Xbox"`. The keys are now all lower-case, so the lookup hits them directly. Keep any new `PLATFORM_MAP` keys lower-case for the same reason.

## Things That Aren't What They Look Like

- **The Stats API is not the SOS plugin.** Earlier versions of this code used SOS — different event names (`game:ball_hit` etc.), a different port (49122), and a different message structure. The current codebase no longer references SOS, but if you find any such comments in future, they're stale: Stats API is the source of truth.
- **`MatchInitialized` does not contain player data** in the Stats API. Players are *only* available through `UpdateState`. Don't add player parsing to `on_match_initialized` in `eventHandler.py`.
- **`self.match_history` is not the history** — it's a bounded 20-match cache for the UI table only. The DB is authoritative. See *Editing `sessionStore.py`*.

## Versioning

Project follows semver. Currently in pre-1.0 — schema and core behaviour can still change. Bug fixes that affect data correctness (stats not saved, win/loss wrong, app fails to connect) ship as patch releases as soon as they're found. Cosmetic and additive changes can be batched.

## Data Schema

History is stored in SQLite at `%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\history.db`. The schema is created/maintained by `SessionStore._initDatabase` and stamped with `PRAGMA user_version` (currently `3`):

- **`sessions`** — `id` PK (matches `session_num`), `name` (nullable, reserved for the labelled-sessions TODO), `started_at`, `ended_at`
- **`matches`** — `id` autoincrement PK, `session_id` FK ON DELETE CASCADE, `played_at`, `result` ∈ {`win`,`loss`}, `winner_team`, `overtime` (INTEGER 0/1, added in v2), `duration_secs` (REAL, added in v2; nullable when the clock couldn't be read), `local_player_id` (TEXT, added in v3; the `players.id` of the local user for that match — NULL for pre-v3 rows the backfill couldn't attribute. Deliberately **not** an FK: a local player with an empty `PrimaryId` gets no `players` row, and with `foreign_keys = ON` an FK would reject the whole match instead of just leaving it out of the per-you stats)
- **`players`** — `id` PK = full `PrimaryId`, `name` (most recently seen), `platform`, `first_seen`, `last_seen`. Canonical identity.
- **`match_players`** — composite PK `(match_id, player_id)`, `role` ∈ {`opponent`,`teammate`}, `name_at_match` (snapshot — preserved so renames don't rewrite history), `team_num`, stat columns. Indexed on `(player_id, role)` for fast encounter lookups.
  - ⚠️ **`team_num` is always inserted as `NULL`** — `record_result` passes a literal `None`. It's a dead column; don't build aggregations on it. Use `role`, and `matches.local_player_id` to tell which teammate row is you.

`record_result` upserts the session row (bumping `ended_at`), inserts the match, upserts each `players` row, then inserts `match_players`. `delete_session` is a single `DELETE FROM sessions WHERE id = ?` and `delete_match` a single `DELETE FROM matches WHERE id = ?` — FK cascades handle the rest. Neither prunes now-orphaned `players` rows; they're harmless, since every aggregation joins through `match_players`.

`_encounter_for` returns a dict per player with `wins`, `losses`, `encounters`, `crossEncounters` (count in the *opposite* role — lets the UI distinguish a true first meeting from "first time as a teammate but we've faced before"), `lastDate`, `lastSessionNum`, `matchesAgo`.

`get_analytics` returns one nested dict: `overview` (22 scalars — totals, win %, streaks, session count, first/last date, the duration averages with a `timedMatches` companion, and the overtime/regulation split), `winRateBySession` (chronological `get_session_summaries` rows plus `rollingWinPct`) with `rollingWindow`, `selfStats` (`matches`, `avg` / `avgInWins` / `avgInLosses`, `shotAccuracy`, `best`, `worst`), fixed-length `timeOfDay` (6) and `dayOfWeek` (7) bucket lists, and `opponents` / `teammates` leaderboards.

Synthetic player ids:
- `legacy:<name>` — written by `_maybe_migrate_from_json` for entries that predate ID tracking. `_encounter_for` falls back to a name match against these rows only; the leaderboards fold them into the real id via `_fold_legacy_player_ids` (see *Editing `sessionStore.py`*).
- `unknown:<name>` — written by `record_result` if `PrimaryId` is somehow empty at match end. Should never appear under normal play; if it does, treat as a bug signal.

The legacy JSON shape (still readable by `_maybe_migrate_from_json`) was:

```json
{ "date": "...", "result": "win|loss", "sessionNum": 3,
  "opponents": [ { "id?": "Steam|...|0", "name": "...", "platform": "...", "score": 0, "goals": 0, ... } ],
  "teammates": [ /* same shape */ ] }
```

When evolving the schema, bump `user_version` and add an idempotent upgrade step in `_initDatabase` (or a dedicated migration helper). `_initDatabase` reads the current `user_version` up front, always runs the `CREATE TABLE IF NOT EXISTS` block, then applies version-gated upgrade blocks — e.g. the v2 step is `if version < 2:` → `ALTER TABLE matches ADD COLUMN …` then `PRAGMA user_version = 2`, and v3 does the same for `local_player_id`. Follow that pattern for the next bump. Existing DBs in users' `%LOCALAPPDATA%` won't have the new columns — use `ALTER TABLE … ADD COLUMN` with safe defaults rather than failing hard.

Three things learned doing v3, worth knowing before v4:
- **The ALTER path runs on fresh DBs too.** A brand-new file starts at `user_version = 0`, so it falls through every gate. None of `overtime`, `duration_secs`, or `local_player_id` is in the `CREATE TABLE` — the migrations are what create them, on new and upgraded DBs alike. Keep it that way so both take one code path.
- **Guard the ADD COLUMN with `_column_exists`.** A DB touched by a dev build can already have the column while still stamped at the old version. A duplicate `ADD COLUMN` raises `OperationalError`, and inside an `executescript` that aborts the whole migration — taking `__init__`, and therefore app startup, down with it.
- **Backfills must refuse to guess.** `set_local_username`'s backfill only fills a row when *exactly one* teammate's `name_at_match` matches the configured username (case-insensitively — note SQLite's `lower()` is ASCII-only, so a non-ASCII name simply doesn't backfill), and only where the column is still NULL. Anything ambiguous stays NULL rather than being attributed to the wrong player. `_fold_legacy_player_ids` applies the same rule.

Downgrading is safe: an older build reading a v3 DB skips every gate it knows about and writes NULL `local_player_id` rows, which the newer build's idempotent backfill picks up on the next launch.
