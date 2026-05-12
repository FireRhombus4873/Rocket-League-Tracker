# Rocket League Tracker

A desktop application that monitors Rocket League gameplay in real-time, tracking match results and opponent history across sessions.

## Features

- **Live match tracking** — displays current players with their platform
- **Past encounters** — for each current opponent, see your prior W/L record and how long since you last faced them
- **Opponent history** — persistent record of everyone you've played against with per-match details
- **Session stats** — win/loss/ratio cards with streak tracking per session
- **Session summary** — per-session aggregates (best win streak, worst loss streak, win %) with the ability to delete an entire session
- **Match details** — click any match in history to see full player statistics
- **Pause tracking** — toggle to ignore the current match's result (still shows players live)
- **Settings** — set your in-game name and a list of common teammates from inside the app
- **Platform support** — identifies Steam, Epic, PSN, Xbox, and Switch players

## Running

Edit `C:\Program Files\Epic Games\rocketleague\TAGame\Config\DefaultStatsAPI.ini` so that `PacketSendRate=1`

Download the latest release

Run the RocketLeagueTracker.exe

On first launch, the app prompts you for your in-game name (used to detect which team is yours). You can change it any time via the gear button in the top-right.

## Building

```bat
python -m venv .venv
.venv\Scripts\activate
pip install PyQt6 psutil pyinstaller
./build
```

## Project Structure

```
Rocket League Tracker/
├── main.py             # Entry point — wires up all components
├── mainWindow.py       # PyQt6 GUI (main window + match/session/settings dialogs)
├── sessionStore.py     # SQLite-backed match history and session management
├── eventHandler.py     # Processes Stats API events
├── socketHandler.py    # TCP connection to Stats API (localhost:49123)
├── processHandler.py   # Monitors RocketLeague.exe process
├── settingsManager.py  # Loads/saves user settings (username, teammates)
├── config.py           # Filesystem paths for the DB, settings, legacy JSON
├── assets/
│   ├── RocketLeagueTracker.ico
│   └── settings.png
└── build.bat           # Builds the standalone .exe via PyInstaller
```

## Data

Match history is stored in a SQLite database at:
```
%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\history.db
```

Tables: `sessions`, `matches`, `players`, `match_players` — sessions own matches, matches reference players via `match_players` (role = opponent/teammate, with the full stat block per player per match). Players are identified by their `PrimaryId` (e.g. `Steam|123|0`) so duplicates of common display names like `.` are tracked correctly.

User settings live alongside the DB at `settings.json` (current values: `localUsername`, `commonTeammates`).

If you have an older `match_history.json` from a previous version, it's imported into the database automatically on first launch and renamed to `match_history.json.bak`.

## How It Works

1. **ProcessHandler** watches for `RocketLeague.exe` to start
2. **SocketHandler** connects to the Stats API at `localhost:49123`
3. **EventHandler** routes incoming game events to the appropriate handlers
4. **SessionStore** maintains live match state and persists results to SQLite
5. **MainWindow** renders real-time updates via PyQt6 signals
