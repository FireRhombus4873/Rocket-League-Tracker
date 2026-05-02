# Rocket League Tracker

A desktop application that monitors Rocket League gameplay in real-time, tracking match results and opponent history across sessions.

## Features

- **Live match tracking** — displays current players with their platform
- **Opponent history** — persistent record of everyone you've played against with per-match details
- **Session stats** — win/loss/ratio cards with streak tracking per session
- **Match details** — click any match in history to see full player statistics
- **Platform support** — identifies Steam, Epic, PSN, Xbox, and Switch players

## Running

Edit `C:\Program Files\Epic Games\rocketleague\TAGame\Config\DefaultStatsAPI.ini` so that `PacketSendRate=1`

Download the latest release

Run the RocketLeagueTracker.exe

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
├── mainWindow.py       # PyQt6 GUI
├── sessionStore.py     # Data persistence and session management
├── eventHandler.py     # Processes Stats API events
├── socketHandler.py    # TCP connection to Stats API (localhost:49123)
├── processHandler.py   # Monitors RocketLeague.exe process
├── assets/
│   └── RocketLeagueTracker.ico
└── build.bat           # Builds the standalone .exe via PyInstaller
```

## Data

Match history is stored at:
```
%LOCALAPPDATA%\FireRhombus\RocketLeagueTracker\match_history.json
```

Each record includes player stats, match result, timestamp, and session number.

## How It Works

1. **ProcessHandler** watches for `RocketLeague.exe` to start
2. **SocketHandler** connects to the Stats API at `localhost:49123`
3. **EventHandler** routes incoming game events to the appropriate handlers
4. **SessionStore** maintains game state and persists match data to JSON
5. **MainWindow** renders real-time updates via PyQt6 signals
