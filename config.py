from pathlib import Path
import os

APP_NAME = "RocketLeagueTracker"

if os.name == "nt": 
    BASE_DIR = Path(os.getenv("LOCALAPPDATA"))/ "FireRhombus" / APP_NAME
else:
    BASE_DIR = Path.home() / "FireRhombus" / f".{APP_NAME}"

HISTORY_DB_FILE = BASE_DIR / "history.db"
HISTORY_FILE =  BASE_DIR / "match_history.json"
SETTINGS_FILE = BASE_DIR / "settings.json"