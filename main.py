"""
Rocket League Tracker
---------------------
Connects to the Rocket League Stats API, tracks player info per match,
records wins/losses, and stores opponent history across sessions.

Requirements:
    pip install PyQt6 psutil

Stats API runs on localhost:49123 when Rocket League is open.
"""

import sys
import threading
from PyQt6.QtWidgets import QApplication, QMessageBox

from mainWindow     import MainWindow, SessionSummaryDialog
from eventHandler   import EventHandler
from socketHandler  import SocketHandler
from processHandler import ProcessHandler
from sessionStore   import SessionStore

LOCAL_USERNAME = "FireRhombus4873"


def main():
    app = QApplication(sys.argv)

    window          = MainWindow()
    session         = SessionStore()
    event_handler   = EventHandler(on_event_callback=lambda evt: handle_event(evt))
    process_handler = ProcessHandler()

    _refresh_history(window, session)
    _refresh_record(window, session)

    def handle_update_state(data: dict):
        fresh = session.try_set_players_from_update(data, local_username=LOCAL_USERNAME)
        if fresh:
            window.signals.players_updated.emit(
                session.current_players, session.team_info
            )
            encs = session.get_current_encounters()
            window.signals.encounters_updated.emit(encs["opponents"], encs["teammates"])
            window.signals.status_changed.emit("Match in progress")

    def handle_event(event: dict):
        if "MatchInitialised" in event:
            window.signals.status_changed.emit("Match initialising...")

        elif "MatchEnded" in event:
            winner = event.get("MatchEnded")
            try:
                winner_int = int(winner)
            except (ValueError, TypeError):
                winner_int = -1
            if window.is_tracking_paused():
                session.discard_match()
                window.signals.players_updated.emit([], {})
                window.signals.encounters_updated.emit([], [])
                window.signals.status_changed.emit("Match ended — tracking paused (not saved)")
            else:
                session.record_result(winner_int)
                _refresh_record(window, session)
                _refresh_history(window, session)
                window.signals.players_updated.emit([], {})
                window.signals.encounters_updated.emit([], [])
                window.signals.status_changed.emit("Match ended — waiting for next match")

        elif "MatchDestroyed" in event:
            window.signals.players_updated.emit([], {})
            window.signals.encounters_updated.emit([], [])
            window.signals.status_changed.emit("Connected — waiting for match")

    socket_handler = SocketHandler(
        on_message_callback=event_handler.dispatch,
        on_update_state_callback=handle_update_state,
    )

    def prompt_session(last_num: int):
        """
        Ask the user whether to continue the previous session or start a new one.
        Runs on the main thread via a signal so it's safe to show a dialog.
        """
        if last_num == 0:
            # First ever launch — no choice needed
            session.new_session()
            return

        msg = QMessageBox()
        msg.setWindowTitle("Session")
        msg.setText(
            f"Continue session {last_num}?\n\n"
            "Choose 'Continue' if Rocket League crashed or you closed it by mistake.\n"
            "Choose 'New Session' to start fresh."
        )
        msg.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        new_btn = msg.addButton("New Session", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() == new_btn:
            session.new_session()
        else:
            session.continue_session()
        _refresh_record(window, session)

    def process_watcher():
        while True:
            window.signals.status_changed.emit("Waiting for Rocket League...")
            process_handler.wait_for_game()

            window.signals.game_started.emit()
            # Ask on main thread (Qt requires UI calls on main thread)
            window.signals.session_prompt.emit(session.session_num)

            window.signals.status_changed.emit("Rocket League detected — connecting...")
            socket_handler.start()

            process_handler.wait_for_game_to_close()
            socket_handler.stop()
            window.signals.status_changed.emit("Rocket League closed")

    def on_new_session_requested():
        msg = QMessageBox()
        msg.setWindowTitle("New Session")
        msg.setText("Start a new session?\n\nThis will reset your wins, losses, and streak counter.")
        msg.addButton("Start New Session", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() != cancel_btn:
            session.new_session()
            _refresh_record(window, session)
            _refresh_history(window, session)

    def on_sessions_requested():
        def delete_and_refresh(num: int) -> list:
            session.delete_session(num)
            _refresh_record(window, session)
            _refresh_history(window, session)
            return session.get_session_summaries()

        dlg = SessionSummaryDialog(
            session.get_session_summaries(),
            parent=window,
            on_delete=delete_and_refresh,
        )
        dlg.exec()

    # Wire the session prompt signal to our handler
    window.signals.session_prompt.connect(prompt_session)
    window.signals.new_session_requested.connect(on_new_session_requested)
    window.signals.sessions_requested.connect(on_sessions_requested)

    threading.Thread(target=process_watcher, daemon=True).start()
    sys.exit(app.exec())


def _refresh_record(window: MainWindow, session: SessionStore):
    window.signals.record_updated.emit(session.wins, session.losses)

def _refresh_history(window: MainWindow, session: SessionStore):
    window.signals.history_updated.emit(session.get_recent_opponents(20), session.session_num)


if __name__ == "__main__":
    main()