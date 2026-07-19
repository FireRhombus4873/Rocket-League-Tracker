"""
GUI package for Rocket League Tracker.

Re-exports the two symbols main.py wires up so callers can keep a single
import site: `from ui import MainWindow, SettingsDialog`.
"""
from .main_window import MainWindow
from .dialogs.settings_dialog import SettingsDialog

__all__ = ["MainWindow", "SettingsDialog"]
