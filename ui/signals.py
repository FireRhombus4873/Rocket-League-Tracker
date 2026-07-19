"""
Signal bridge: lets background threads safely update the Qt UI.

Background threads (the socket listener, the process watcher) never touch
widgets directly — they emit one of these signals and the connected slot runs
on the Qt main thread. Add a new signal here rather than calling widget
methods from a worker thread.
"""
from PyQt6.QtCore import pyqtSignal, QObject


class UISignals(QObject):
    players_updated       = pyqtSignal(list, dict)        # list of player dicts, team_info dict
    encounters_updated    = pyqtSignal(list, list, list)  # opponents encounters, teammates encounters
    record_updated        = pyqtSignal(int, int)          # wins, losses
    history_updated       = pyqtSignal(list, int)         # list of match history dicts, current session num
    status_changed        = pyqtSignal(str)               # status bar text
    session_prompt        = pyqtSignal(int)               # last session number, triggers dialog
    settings_prompt       = pyqtSignal()                  # user doesn't have localUsername set
    new_session_requested    = pyqtSignal()               # user clicked "New Session"
    sessions_updated         = pyqtSignal(list)            # list of session summary dicts
    session_delete_requested = pyqtSignal(int)             # user confirmed deleting a session
    game_started             = pyqtSignal()                # Rocket League process detected
