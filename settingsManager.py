from config import SETTINGS_FILE
import json

DEFAULTS = {
    "localUsername": None,
    "commonTeammates": []
}

class SettingsManager():
    def __init__(self):
        self.settings = DEFAULTS
        self.InitSettings()

    def InitSettings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                self.settings = json.load(f)

    def saveSettings(self):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.settings, f)