"""
GUI package for Rocket League Tracker.

Re-exports the symbols main.py wires up so callers can keep a single
import site: `from ui import MainWindow, SettingsDialog, StatsApiDialog`.
"""
from .main_window import MainWindow
from .dialogs.settings_dialog import SettingsDialog
from .dialogs.stats_api_dialog import StatsApiDialog

__all__ = ["MainWindow", "SettingsDialog", "StatsApiDialog"]
