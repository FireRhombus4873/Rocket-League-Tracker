"""
The three top-level tabs of the main window, one module each.

Each tab is a self-contained QWidget subclass that owns its widgets and its
slots. It receives the `UISignals` bus in its constructor and connects its own
slots there — `MainWindow` builds the bus first, then composes the tabs, and
keeps only the header/status and tray wiring for itself.

Tabs never import `main_window` (that would be circular via `ui/__init__.py`)
and never reach into each other; cross-tab refreshes happen because `main.py`
re-emits the relevant signals.
"""
