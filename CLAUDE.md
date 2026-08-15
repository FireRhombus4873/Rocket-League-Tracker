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
- `ANALYTICS_SCOPE` is a third module global — what the Analytics tab is currently scoped to, read by `_refresh_analytics` and written by `on_analytics_scope_changed`. The socket thread calls `_refresh_analytics` after every recorded match, so it is always **rebound, never mutated in place**. `on_session_delete_requested` resets it to all-time when the deleted session is the scoped one, rather than leaving the tab pinned to an empty view the user has no obvious way to explain.
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

**Analytics aggregation**: `get_analytics(scope=None)` is the single public entry point — one nested dict, one signal, one slot. It holds `_db_lock` across the whole build and does its Python derivation *inside* the lock, the deliberate exception to the "derive after releasing the lock" idiom used everywhere else: otherwise a match recorded mid-refresh could leave the cards and the charts disagreeing. Cost is three queries (`_analytics_from_matches`, `_analytics_self_stats`, `_analytics_leaderboards`) plus a reuse of `get_session_summaries()`. Every value in the payload is non-None — `{}` for absent objects, `0.0` plus a companion count (e.g. `timedMatches`) for unknown scalars — so the Qt slot needs no None-guards. Tuning constants (`ROLLING_WINDOW`, `MIN_OPPONENT_GAMES`, `MIN_TEAMMATE_GAMES`, `LEADERBOARD_LIMIT`, `TOD_BUCKET_HOURS`) live at module scope so the UI never invents thresholds. Time-of-day / day-of-week bucketing is done in **Python, not `strftime()`**, because legacy migrated rows can carry `played_at = ''` and a Python guard is harder to forget than a `WHERE` clause; both bucket lists are fixed-length (6 and 7) so the charts always have a full axis. `_analytics_leaderboards` is the one aggregation grouped in SQL (bounded top-N out of ~4 rows per match, and nothing order-dependent), and it post-processes through `_fold_legacy_player_ids` — see below.

**Analytics scope**: `scope` narrows every figure to one session. Three types (`SCOPE_ALL` / `SCOPE_CURRENT` / `SCOPE_SESSION` at module scope): `current` is resolved against `session_num` **at query time**, so that view follows along when a new session starts, while `session` is pinned to the number it was handed and never moves. `_resolve_scope` normalises the caller's dict and returns `(scope, sql, params)`; the SQL fragment always starts with `AND` and always aliases the matches table as **`m`**, which is why `_analytics_from_matches` carries a `WHERE 1=1` it wouldn't otherwise need. Keep both conventions in any new scoped query, and bind the session number — nothing from the caller is interpolated into the string. An unusable scope (`None`, unknown type, non-numeric `sessionNum`) **degrades to all-time rather than raising**: this runs on the socket thread's refresh chain, where a throw takes the socket listener down with it. The resolved scope is echoed back under `"scope"` so the tab renders its chips from what was actually queried instead of tracking its own copy.

Two things follow from a scope that the UI has to stay in step with:
- **`trend` / `trendMode`** (the key formerly called `winRateBySession`). All-time gives `trendMode == "sessions"` — one point per session, `rollingWinPct` being the `ROLLING_WINDOW` rolling average. A session scope gives `"matches"` from `_analytics_match_progression`: one point per match, `winPct` being that match's 0/1 result and `rollingWinPct` the *cumulative* record. Both feed the same `WinRateTrendChart` because the series maths is identical; only the wording differs, which is why the labels are `set_data` keywords rather than a mode flag.
- **`_analytics_leaderboards` is skipped entirely under a scope** (returns empty lists) and the tab hides both that card and the time-of-day/day-of-week one. `MIN_OPPONENT_GAMES` / `MIN_TEAMMATE_GAMES` almost never trigger inside one session, and a single sitting fills one or two hour buckets — so scoping them would produce empty tables and a greyed-out chart rather than a narrower answer. If you ever un-hide either card under a scope, wire the query up first.

**The `legacy:` split-identity problem**: someone you played both before and after the JSON migration has *two* rows in `players` — their real `PrimaryId` and a synthetic `legacy:<name>` — which splits their leaderboard record in half (a real DB had `123rdswagcity` at 382 and 43 instead of 425). `_fold_legacy_player_ids` merges a synthetic group into the real one **only when exactly one real id shares that name and role**; display names aren't unique (`.` is a real one), so anything ambiguous stands on its own. Same "don't guess when it's ambiguous" rule as the `set_local_username` backfill. Any *new* per-player aggregation needs this fold too, or it will double-count long-standing players.

### `statsApiConfig.py` — install detection + Stats API enablement

The Stats API is disabled by default and enabled per *install*, not per user, in `<install>\TAGame\Config\DefaultStatsAPI.ini` (`[TAGame.MatchStatsExporter_TA]`, `PacketSendRate=0` disables it). So a refused connection on 49123 has two causes — game not running, or exporter off — and only this file distinguishes them.

- `find_installs()` returns `(path, source)` for each install, most trustworthy first: **running process** (`psutil` → `Path(exe).parents[2]`; exact, but needs the game open), then **Epic** (`%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\` — both `LauncherInstalled.dat` and `Manifests\*.item`), then **Steam** (registry `SteamPath` → `steamapps\libraryfolders.vdf` → `appmanifest_252950.acf`). Every candidate is confirmed by finding `DefaultStatsAPI.ini` on disk, so a stale manifest or wrong AppID is discarded rather than reported — that's why the Epic lookup doesn't match on RL's undocumented AppName (`Sugar`).
- `stats_api_status()` is the single entry point; `enabled` is already collapsed to a bool (False whenever anything is unknown), so callers need no None-guards. Check `found` to tell "disabled" from "couldn't locate your install".
- ⚠️ `set_packet_send_rate` works on **bytes, not str**. This is a file we don't own: decoding with `errors="ignore"` would silently drop any non-UTF-8 byte and re-encoding would normalise the BOM and CRLF. Substituting into raw bytes changes the digits and nothing else — verified byte-identical. It writes via a sibling `.rlt-tmp` then `os.replace`, so a permissions failure hits the temp file before the real config is touched.
- Both stores install under a UAC-protected directory, so `PermissionError` is an expected outcome, not an edge case. `main.py` surfaces it as an inline error in the dialog (which stays open for a retry after elevating) rather than a message box.
- Rate bounds (`MIN_PACKET_RATE` / `MAX_PACKET_RATE` / `RECOMMENDED_PACKET_RATE`) live here and are **passed into `StatsApiDialog`** — nothing under `ui/` imports a root module, and the dialog deliberately has no defaults of its own so the two can't drift.

### `processHandler.py` — process detection

- `wait_for_game()` polls `psutil` every 2s for `RocketLeague.exe`
- `wait_for_game_to_close()` polls the same way and returns as soon as `psutil` reports the process gone
- ⚠️ Earlier revisions had a `CLOSE_CONFIRMATIONS = 3` debounce wrapped in try/except for `NoSuchProcess` / `AccessDenied` (psutil is flaky during system boot, and one missed check kills the socket listener). That safety net is **not currently in the code** — restoring it is worth doing before declaring autostart "fixed"

### `ui/` package — GUI

The GUI was split out of a single ~1,390-line `mainWindow.py` into a small package. Behaviour is unchanged — only the layout:

- `ui/main_window.py` — `MainWindow`: the shell only — the persistent header, the tray, the `QTabWidget`, and the status slot. **No tab internals.**
- `ui/tabs/` — one module per top-level tab: `tracker_tab.py` (`TrackerTab`), `sessions_tab.py` (`SessionsTab`), `analytics_tab.py` (`AnalyticsTab`). Each is a `QWidget` subclass owning its own widgets *and* its own slots. See *Tab composition* below.
- `ui/theme.py` — dark-theme design tokens (`BG_DARK`, `ACCENT`, …), the app-wide `APP_STYLESHEET` string, and `tint(colour, alpha)`. ⚠️ **Use `tint()` for a translucent fill in a stylesheet, never `f"{ACCENT2}1e"`.** Qt reads an 8-digit stylesheet hex as `#AARRGGBB`, so the alpha belongs at the *front*: `#4f9dea1e` parses as a lime `#9dea1e` at `0x4f` alpha, not a translucent blue. It renders as a plausible colour, which is exactly why it went unnoticed for so long — `sessions_tab.py`'s delete-button hover was an olive slab, and `tracker_tab.py:402` (`{ACCENT2}1c`) still is. `charts.py`'s `_alpha()` is the `QColor` equivalent for the painted widgets.
- `ui/widgets.py` — reusable pieces shared by the window, the tabs and the dialogs: `Card`, `soft_shadow`, `platform_icon`, `NameColumnCursor`, plus the three factories `stat_card` / `make_table` / `make_placeholder`. ⚠️ `stat_card` attaches `_title_label`, `_value_label` and `_caption_label` to the frame it returns — a **cross-module contract** the tab slots rely on to update text in place. `_caption_label` is `None` when no caption is passed, so any caller that writes to it must pass one; `_title_label` is always present (the Analytics tab rewrites it, since "ALL-TIME RECORD" is false once that tab is scoped to one session).
- `ui/charts.py` — hand-rolled `QPainter` chart widgets for the Analytics tab (`WinRateTrendChart`, `WinRateBarChart`, `ComparisonBars`, plus the `_ChartBase` they share). Data in via `set_data(...)`, pixels out — no DB access, no signals, and no unit formatting (anything needing `m:ss` arrives pre-formatted from the slot, so numbers are formatted in exactly one place). Every colour comes from `theme.py`. Deliberately no charting dependency: runtime deps stay PyQt6 + psutil and `RocketLeagueTracker.spec`'s `hiddenimports` stays empty.
  - `WinRateTrendChart` renders two different series shapes (per-session and per-match — see *Analytics scope*) with **identical maths**, so the wording is `set_data` keywords (`point_legend` / `trend_legend` / `empty_text`) rather than a mode flag, and their defaults reproduce the all-time view so a caller only speaks up when it has something else to say. A point's `xLabel` overrides the axis text and its `tooltip` overrides the hover text — the per-match tooltip is built in the analytics slot, because "2W 1L · 100%" straight off a match point would read as that match's record rather than the running one. ⚠️ `PAD_T` deliberately leaves a `LEGEND_DROP` band above the plot: the legend has to clear the 100% gridline by more than a dot radius, since in per-match mode every win *is* a 100% point and the legend sits on the right, where it used to overlap real data.
- `ui/signals.py` — `UISignals`.
- `ui/dialogs/` — `match_stats_dialog.py` (`MatchStatsDialog`) and `settings_dialog.py` (`SettingsDialog`).
- `ui/__init__.py` re-exports `MainWindow` and `SettingsDialog`, so `main.py` imports both via `from ui import MainWindow, SettingsDialog`. Only `main.py` imports from the GUI layer. The tab classes are deliberately **not** re-exported — nothing outside `ui/` needs them.

⚠️ Assets are bundled at the project / `_MEIPASS` root, but `ui/main_window.py` sits one level down, so it resolves them via `ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"`. Any new module under `ui/` that loads an asset must account for its own depth — don't copy the old `Path(__file__).parent / "assets"`, which now points one level too shallow. **`ui/tabs/` is two levels down**, so it would need a third `.parent`; none of the three tabs loads an asset and none should need to (same posture as `charts.py`, which deliberately has no `ASSETS_DIR` — don't add one). If a tab ever does, hoist `ASSETS_DIR` to a shared module rather than writing a second, differently-deep copy.

### Tab composition

`MainWindow` builds `self.signals = UISignals()` **first**, before `_apply_styles()` / `_build_ui()`, then constructs each tab with the bus (`TrackerTab(self.signals)`) and hands it to `addTab`. Each tab connects its own slots in its constructor; `MainWindow` keeps only `status_changed` and `game_started`.

The old rule that *"builders must not touch `self.signals`"* is **gone** — the bus now exists before any widget does. Don't reinstate the deferred-lookup lambda workaround it required.

Four rules for anything under `ui/tabs/`:
- **Never import `main_window`.** `ui/__init__.py` → `main_window` → `tabs.*` → `main_window` is circular and fails at import of `ui`, i.e. at app startup, not in tests. Use `self.window()` and no typed parent reference.
- **Connect bound methods, never lambdas capturing widgets.** PyQt auto-disconnects bound methods when the receiver `QObject` dies; a lambda is held by the still-alive `UISignals` and raises `RuntimeError: wrapped C/C++ object ... has been deleted` on the next emit. This bites here specifically because `main.py`'s `_refresh_*` chain emits from the **socket thread** — closing the window mid-match would otherwise fire a stale connection during teardown.
- **Dialogs parent to `self.window()`**, not the tab.
- **Never set an object name or stylesheet on a tab root** — that's the only way to break `APP_STYLESHEET`'s inheritance onto the tab's children.

Tabs never reach into each other. Cross-tab refreshes happen because `main.py` re-emits the relevant signals after a delete — that indirection is the design, not an accident.

**`MainWindow` (`ui/main_window.py`)** — a persistent header (status indicator + gear settings button) sits above a `QTabWidget` (`self._tabs`) holding `self._tracker_tab` / `self._sessions_tab` / `self._analytics_tab`; the header is built by `_build_header()`. The status bar spans all tabs. The one piece of tab surface left on the window is `is_tracking_paused()`, a one-line delegate to `TrackerTab` kept because `main.py` calls it.
  - **Tracker** — win/loss/ratio/streak cards, NEW SESSION / PAUSE TRACKING controls, current-match player list, Past Encounters card (per-player W/L vs you, opponents and teammates merged with a red/blue role dot — common teammates from settings are filtered out), and the match history table. The history table's trailing column (`TrackerTab.HISTORY_DELETE_COL`) holds a per-row ✕ built by `_make_match_delete_cell`; `_handle_match_delete` confirms, then emits `match_delete_requested(match_id)`. It's a **cell widget, not a `QTableWidgetItem`**, so clicking it doesn't fire `itemClicked` and open `MatchStatsDialog` on the way past — keep it that way if the column moves. ⚠️ The ✕'s `clicked` lambda resolves `self._handle_match_delete` at *call* time; that late binding is load-bearing, because `test_ui.py` monkeypatches the instance attribute after the buttons are built. Don't "tidy" it into an eager method reference.
    - ⚠️ `APP_STYLESHEET`'s `QTableWidget::item { padding: 9px 12px; }` applies to cell widgets too: a cell widget is laid out in the **padded content rect**, not the full cell (20×22 inside the 44×40 delete column). A child sized larger than that — e.g. via `setFixedSize` — is silently clipped, which shows up as a hover fill with straight edges instead of a rounded square. That's why the ✕ expands to fill its holder rather than taking a fixed size. Any future cell widget should size itself the same way, or account for the padding.
  - **Sessions** — one row per session (dates, matches, W/L, win %, best-win/worst-loss streaks) with a totals header. Selecting a row enables **Delete Session**, which confirms then emits `session_delete_requested`; `main.py` performs the delete and re-emits `sessions_updated` to re-render. This replaced the old modal `SessionSummaryDialog` and the standalone SESSIONS button. Selecting a row also enables **View Analytics** (double-clicking the row does the same), which emits `analytics_scope_changed({"type": "session", ...})`. That control lives here rather than as a picker on the Analytics tab **on purpose**: sessions run to the hundreds, and this table already is the browser for them — dates, counts and win rates — so a dropdown over there would be a worse version of a list that already exists.
  - **Analytics** — a scope bar above six stacked cards inside a `QScrollArea` (the repo's only one), built by `AnalyticsTab._build_ui` plus one `_build_analytics_*` sub-builder each, and rendered by `_on_analytics_updated` from a single `analytics_updated(dict)` payload — one `SessionStore.get_analytics()` snapshot. In order: overview stat cards (record, win rate, avg duration, overtime record) → win rate over time (per session + rolling average) → your averages (per-match stats in wins vs losses, plus best/worst match) → overtime & match length → win rate by time of day and day of week → toughest-opponent / most-played-teammate leaderboards. Head-to-head match comparison, promised by the old placeholder, was **dropped** — it's an interactive dialog, a separate feature. The slot **mutates widgets in place rather than rebuilding them**, so scroll position survives the refresh that fires after every match, and it reads every field with `.get()` because it runs on the socket thread's refresh chain where one `KeyError` would break the lot.
    - The **scope bar** sits *outside* the `QScrollArea` so it can't scroll away from the numbers it labels. Three chips: `All time`, `Current session`, and a third that is only on screen while a specific session is being viewed — it's the active chip whenever it's visible, so clicking it *clears* to all-time rather than re-selecting, and it carries a ✕ to say so. Chips **emit `analytics_scope_changed` and set nothing locally**: `main.py` re-queries and the resolved scope arrives with the snapshot, so the highlight can never claim a view the numbers below it don't match. That also means the Sessions tab's View Analytics button needs no special path — same signal, same round trip. Under a session scope the tab hides the timing and leaderboard cards (see *Analytics scope*) and relabels the record card from `ALL-TIME RECORD` to `SESSION RECORD` via `stat_card`'s `_title_label`.
    - ⚠️ Two traps this tab hit. `APP_STYLESHEET` doesn't style `QScrollArea`, so it needs `setFrameShape(NoFrame)` (else a sunken grey box against `BG_DARK`) and a transparent viewport. And the bigger one: `QMainWindow, QWidget { background-color: BG_DARK }` matches **every** custom widget, and a stylesheet background makes Qt fill the rect before `paintEvent` — so any custom-painted widget inside a `Card` must set `background: transparent` or it paints a dark rectangle over the card. That's why `_ChartBase.__init__` does it unconditionally.
    - The leaderboard tables set `Player` (column 0) to `Stretch` and the rest to `ResizeToContents`, rather than `make_table`'s default `setStretchLastSection(True)` — otherwise `Win %` absorbs the slack and its values drift away from the header. They also disable their own vertical scrollbar and get `setFixedHeight(rows * 40 + 36)` in `_fill_analytics_leaderboard`, since the tab's `QScrollArea` owns scrolling.
- The dark theme lives in `ui/theme.py` as constants (`BG_DARK`, `ACCENT`, etc.) plus the `APP_STYLESHEET` string; `MainWindow._apply_styles` just applies that sheet, and the `QTabWidget`/`QTabBar` styling is part of it.
- All UI updates flow through `UISignals` (`ui/signals.py`, a `QObject` with `pyqtSignal`s) — background threads emit, main thread slots receive. **Never touch widgets from a background thread directly.** The Sessions tab is fed by `sessions_updated(list)` (emitted from `main.py`'s `_refresh_sessions` after startup, record, new-session, and delete) and drives deletes back via `session_delete_requested(int)`. `match_delete_requested(int)` is the single-match equivalent; `main.py` handles both by performing the delete and then re-emitting record + history + sessions, since either delete can move the W/L counters. `analytics_updated(dict)` feeds the whole Analytics tab from `main.py`'s `_refresh_analytics` — the rule is **wherever `_refresh_sessions` goes, `_refresh_analytics` goes, last** (startup, record, new-session, both deletes), plus once more after a settings save, since a corrected username can attribute more history. `analytics_scope_changed(dict)` is the reverse direction and has **two emitters** — the Analytics scope chips and the Sessions tab's View Analytics button — both landing on one `main.py` handler so they can't drift: it stores the scope, re-refreshes, and calls `MainWindow.show_analytics_tab()` (a no-op when the request came from the Analytics tab itself). `stats_api_problem(dict)` carries one `statsApiConfig.stats_api_status()` snapshot from `process_watcher` to `prompt_stats_api`; it's emitted **after `game_started`** so the main window is up before the dialog parents to it, and it deliberately does not gate `socket_handler.start()` — the user may fix the config and relaunch, and the reconnect loop costs nothing.
- Two modal dialogs live in `ui/dialogs/`:
  - `MatchStatsDialog` (`ui/dialogs/match_stats_dialog.py`) — opened by clicking a history row; shows per-player stats for that match. Each player's **name cell is a clickable link** (underlined, coloured by role, pointing-hand cursor via the `NameColumnCursor` event filter) that opens their Rocket League Tracker profile in the default browser (`https://rocketleague.tracker.network/rocket-league/profile/<slug>/<id-or-name>/overview`). `tracker_platform_slug` maps our platform strings to tracker slugs (`ps4`/`ps5`/`playstation` → `psn`, `xbox`/`xbl` → `xbl`, etc.). For Steam the URL uses the numeric account ID pulled from `PrimaryId` (`id.split("|")[1]`); other platforms use the display name (URL-encoded).
  - `SettingsDialog` (`ui/dialogs/settings_dialog.py`) — opened by the gear button or auto-prompted on first run when no username is saved
  - `StatsApiDialog` (`ui/dialogs/stats_api_dialog.py`) — shown when `stats_api_problem` fires, i.e. the Stats API is off and nothing can be recorded. Offers a `QSpinBox` for the send rate and writes the game config on confirm, then `main.py` tells the user to restart Rocket League. Does **no file IO itself**: `write_callback(rate)` comes from `main.py` and returns an error string to show inline (or None), which keeps the write where every other action lives and lets the dialog stay open after a `PermissionError`. ⚠️ It sets no `QSpinBox::up-button`/`::down-button` rules on purpose — styling either sub-control makes Qt stop drawing the native arrows, leaving a blank slab beside the field.
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
- **Tier 1 — `tests/test_analytics.py`**, same fixtures, split out because the file above is scoped to the match-lifecycle scenarios. Covers v3 stamping, the backfill (including the ambiguous-name guard and idempotency), the whole `get_analytics()` payload, the empty-DB shape, the local player's exclusion from the teammate leaderboard by both id and name fallback, and the `legacy:` fold. It also covers **scope** end to end: that a session scope narrows the overview / self-stats / buckets while leaving all-time untouched, that `current` resolves live (and follows a new session while a pinned `session` doesn't), the per-match progression's cumulative `rollingWinPct`, the skipped leaderboards, an empty scope staying empty rather than widening, and — the one that matters most — that every unusable scope shape **degrades to all-time instead of raising**, since this runs on the socket thread. Two arrangement techniques it introduces: **back-dating** via `UPDATE matches SET played_at = ?` after `record_result` (`played_at` comes from `datetime.now()` inside the store, so the clock isn't injectable and rewriting the row afterwards is narrower than monkeypatching it), and `update_state(..., time_secs=None)` to omit `TimeSeconds` and get a NULL `duration_secs`. Multi-session data comes from interleaving `store.new_session()`.
- **Tier 2 — `test_event_handler.py`, `test_socket_handler.py`, `test_parsing.py`**. Pure logic, no Qt: `EventHandler.dispatch`, the `SocketHandler._handle_message` double-decode/routing, `_parse_platform`, and `get_winner` / `tracker_platform_slug`. The socket *reconnect/buffer loop* is not unit-tested (needs a live TCP peer) — only the message parsing that feeds it.
- **Tier 2 — `test_stats_api_config.py`**, covering `statsApiConfig` end to end. Writing is asserted at the **byte** level, not on parsed values: a 0→1 round trip must reproduce the shipped file exactly, and a BOM or a stray non-UTF-8 byte must survive — neither is observable from a decoded comparison, and both are the whole reason the function works on bytes. Detection fakes every source inside `tmp_path`: Epic by pointing `%PROGRAMDATA%` at a staged tree, Steam by stubbing `_steam_root` (the real one reads the registry) with both the current and pre-2021 `libraryfolders.vdf` shapes, and the live-process source by stubbing `psutil.process_iter`. An **autouse fixture redirects `_documents_dir`** — without it, a developer who happens to have a `TAStatsAPI.ini` under their own Documents would get different results from everyone else.
  - ⚠️ Two traps when extending it. Don't assert "a rejected rate didn't reach the file" using rate `0` — the fixture config already reads `PacketSendRate=0`, so a stray write is byte-identical to no write and the test proves nothing (it uses 121 / -5 / `"9"` / `None` instead). And `True` needs rejecting explicitly in the source, since `bool` is an `int` subclass and would otherwise pass the range check and write `PacketSendRate=True`.
- **Tier 3 — `test_ui.py`** (marked `@pytest.mark.ui`, needs `pytest-qt` + a display). Deliberately shallow: emit a `UISignals` signal, assert the target widget's text updated. Signals are always emitted on the **`window`** fixture so the full `MainWindow` wiring stays under test; the `tracker` / `sessions` / `analytics` fixtures exist only to *reach* the asserted widget on its owning tab. Don't `qtbot.addWidget` a tab — they're children of an already-registered widget, and double-registration risks a double-delete at teardown. **Do not assert pixels, colours, or stylesheet strings** — those are meant to change when the UI is restyled, and testing them just creates churn. These tests survive a redesign and only fail if the signal→slot→widget wiring breaks. The charts are not asserted on at all — their only observable is pixels. `_on_analytics_updated` reads ~40 keys off one payload, so there's a deliberate **empty-payload test** (`analytics_updated.emit({})`) alongside the populated one. Note these tests never `show()` the window, so `isVisible()` is `False` for every widget regardless of state — use `isHidden()` when asserting an empty-state toggle. The scope tests are the same shape in both directions: a session-scoped payload must hide the two all-time cards and relabel the record card, and the chips / View Analytics button must *emit* the right scope (asserted with `qtbot.waitSignal`) — never that they set their own highlight, which is the whole point of the round trip.

Two pure helpers were lifted to module scope specifically so Tier 2 can reach them: `get_winner` (from a closure in `main.py` → `eventHandler.py`, imported back by `main.py`) and `tracker_platform_slug` (from a method in `ui/dialogs/match_stats_dialog.py` → module level). Both are behaviour-preserving. `ui/tabs/analytics_tab.py`'s `_fmt_mmss` / `_fmt_pct` and `sessionStore.py`'s `_with_rolling_win_pct` / `_fold_legacy_player_ids` are module-scope for the same reason; the two formatters are covered by `test_parsing.py`. They stay in the analytics module rather than `widgets.py` on purpose — `charts.py` does no unit formatting so that numbers are formatted in exactly one place, and that place is the analytics slot module.

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

`get_analytics` returns one nested dict: `scope` (the resolved `{"type", "sessionNum"}`), `overview` (22 scalars — totals, win %, streaks, session count, first/last date, the duration averages with a `timedMatches` companion, and the overtime/regulation split), `trend` + `trendMode` (chronological; per-session rows plus `rollingWinPct` all-time, per-match progression when scoped — see *Analytics scope*) with `rollingWindow`, `selfStats` (`matches`, `avg` / `avgInWins` / `avgInLosses`, `shotAccuracy`, `best`, `worst`), fixed-length `timeOfDay` (6) and `dayOfWeek` (7) bucket lists, and `opponents` / `teammates` leaderboards. Everything except `trendMode`, `rollingWindow` and the leaderboards honours `scope`.

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
