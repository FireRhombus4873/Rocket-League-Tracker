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

from ui             import MainWindow, SettingsDialog, StatsApiDialog
from settingsManager import SettingsManager
from eventHandler    import EventHandler, get_winner
from socketHandler   import SocketHandler, PORT as STATS_API_PORT
from processHandler  import ProcessHandler
from sessionStore    import SessionStore
import statsApiConfig

LOCAL_USERNAME = None
COMMON_TEAMMATES = []
# What the Analytics tab is scoped to — see SessionStore.get_analytics. Read by
# _refresh_analytics, which the socket thread calls after every recorded match,
# so it is always *rebound* and never mutated in place.
ANALYTICS_SCOPE = {"type": "all"}

def main():
    global LOCAL_USERNAME, COMMON_TEAMMATES

    app = QApplication(sys.argv)

    settings = SettingsManager()
    # Sync the globals from disk before any signal handlers run. Without this,
    # try_set_players_from_update would be called with local_username=None and
    # crash on .lower() — killing the socket loop and freezing the UI.
    LOCAL_USERNAME   = settings.settings.get("localUsername") or ""
    COMMON_TEAMMATES = list(settings.settings.get("commonTeammates") or [])

    window          = MainWindow(close_to_tray=_should_close_to_tray(sys.argv))
    session         = SessionStore()
    # Rows recorded before schema v3 have no matches.local_player_id; this
    # attributes them to you by name so per-you analytics aren't limited to
    # matches recorded since the upgrade. Must run before the first refresh.
    session.set_local_username(LOCAL_USERNAME)
    event_handler   = EventHandler(on_event_callback=lambda evt: handle_event(evt))
    process_handler = ProcessHandler()

    _refresh_history(window, session)
    _refresh_record(window, session)
    _refresh_sessions(window, session)
    _refresh_analytics(window, session)

    def handle_update_state(data: dict):
        fresh = session.try_set_players_from_update(data, local_username=LOCAL_USERNAME)
        if fresh:
            window.signals.players_updated.emit(
                session.current_players, session.team_info
            )
            encs = session.get_current_encounters()
            window.signals.encounters_updated.emit(encs["opponents"], encs["teammates"], COMMON_TEAMMATES)
            window.signals.status_changed.emit("Match in progress")

    def handle_event(event: dict):
        def record_result(winner: int = None):
            session.record_result(winner_team=winner)
            _refresh_record(window, session)
            _refresh_history(window, session)
            _refresh_sessions(window, session)
            _refresh_analytics(window, session)

        if "MatchInitialised" in event:
            window.signals.status_changed.emit("Match initialising...")

        elif "MatchEnded" in event:
            if window.is_tracking_paused():
                session.discard_match()
                window.signals.players_updated.emit([], {})
                window.signals.encounters_updated.emit([], [], [])
                window.signals.status_changed.emit("Match ended — tracking paused (not saved)")
            else:
                # Winner is easily determined from the MatchEnded event
                winner = get_winner(event)
                record_result(winner)
                window.signals.players_updated.emit([], {})
                window.signals.encounters_updated.emit([], [], [])
                window.signals.status_changed.emit("Match ended — waiting for next match")

        elif "MatchDestroyed" in event:
            # If the user leaves the match before the MatchEnded event is called, we can't provide a definitive winner
            if not session.result_recorded():
                record_result()
            window.signals.players_updated.emit([], {})
            window.signals.encounters_updated.emit([], [], [])
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

    def prompt_settings():
        """
        Open the SettingsDialog. Used both by the gear button in the main window
        and as a first-run prompt when no username is configured.

        Runs on the main thread via a signal so it's safe to show a dialog.
        Always syncs the in-memory globals from the settings file afterwards
        so a cancelled dialog doesn't lose previously-saved values.
        """
        global LOCAL_USERNAME, COMMON_TEAMMATES

        saved_username  = settings.settings.get("localUsername") or ""
        saved_teammates = list(settings.settings.get("commonTeammates") or [])

        dlg = SettingsDialog(
            username=saved_username,
            teammates=saved_teammates,
            parent=window,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            saved_username  = dlg.username()
            saved_teammates = dlg.teammates()
            settings.settings["localUsername"]   = saved_username
            settings.settings["commonTeammates"] = saved_teammates
            settings.saveSettings()

        LOCAL_USERNAME   = saved_username
        COMMON_TEAMMATES = saved_teammates
        # A newly-set (or corrected) username can attribute more history to you.
        session.set_local_username(LOCAL_USERNAME)
        _refresh_analytics(window, session)

    def prompt_stats_api(status: dict):
        """
        Tell the user the Stats API is off, and offer to switch it on.

        Runs on the main thread via a signal so it's safe to show a dialog.
        """
        if not status.get("configPath"):
            # No install located, so there's no file we can offer to edit —
            # all we can do is say where to look.
            QMessageBox.warning(
                window,
                "Stats API Disabled",
                "The tracker can't reach Rocket League's Stats API, and couldn't "
                "find your game install to check why.\n\n"
                "Open this file in your Rocket League install folder:\n"
                r"    ...\rocketleague\TAGame\Config\DefaultStatsAPI.ini" "\n\n"
                "and set PacketSendRate=1, then restart Rocket League.",
            )
            return

        def write_rate(rate: int):
            """Returns an error string for the dialog to show inline, or None."""
            try:
                statsApiConfig.set_packet_send_rate(status["configPath"], rate)
            except PermissionError:
                return ("Windows blocked the write — the game config lives in a "
                        "protected folder. Close this, restart the tracker as "
                        "administrator, and try again (or edit the file by hand).")
            except OSError as exc:
                return f"Couldn't update the config file: {exc}"
            return None

        # The socket connects on a fixed port, so a config pointing elsewhere
        # won't work even at a valid send rate. Say so rather than having the
        # user set the rate and hit the same wall again.
        port = status.get("port")
        note = ""
        if port is not None and port != STATS_API_PORT:
            note = (
                f"Note: this config listens on port {port}, but the tracker connects "
                f"on {STATS_API_PORT}. Set Port={STATS_API_PORT} in the same file too."
            )

        dlg = StatsApiDialog(
            config_path=status["configPath"],
            min_rate=statsApiConfig.MIN_PACKET_RATE,
            max_rate=statsApiConfig.MAX_PACKET_RATE,
            recommended=statsApiConfig.RECOMMENDED_PACKET_RATE,
            current_rate=status.get("packetSendRate"),
            note=note,
            write_callback=write_rate,
            parent=window,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            QMessageBox.information(
                window,
                "Restart Rocket League",
                f"Stats API updated — PacketSendRate is now {dlg.rate()}.\n\n"
                "Restart Rocket League for the change to take effect. The tracker "
                "will connect automatically once it's back up.",
            )

    def process_watcher():
        while True:
            window.signals.status_changed.emit("Waiting for Rocket League...")
            process_handler.wait_for_game()

            window.signals.game_started.emit()

            # Checked with the game running: statsApiConfig can then read the
            # install path straight off the live process, which beats guessing
            # from the Epic/Steam metadata. Emitted after game_started so the
            # main window is up before the dialog parents to it. Deliberately
            # does *not* gate socket_handler.start() — the user may fix the
            # config and relaunch, and the reconnect loop costs nothing.
            stats_api = statsApiConfig.stats_api_status()
            if not stats_api["enabled"]:
                window.signals.stats_api_problem.emit(stats_api)

            # Ask on main thread (Qt requires UI calls on main thread).
            # Only auto-prompt for settings on first run — once a username is
            # saved, the user opens the dialog manually via the gear button.
            if not settings.settings.get("localUsername"):
                window.signals.settings_prompt.emit()
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
            _refresh_sessions(window, session)
            _refresh_analytics(window, session)

    def on_analytics_scope_changed(scope: dict):
        """Both the Analytics scope chips and the Sessions tab's View Analytics
        button land here — one path, so the two can't drift."""
        global ANALYTICS_SCOPE
        ANALYTICS_SCOPE = dict(scope or {}) or {"type": "all"}
        _refresh_analytics(window, session)
        # No-op when the request came from the Analytics tab's own chips.
        window.show_analytics_tab()

    def on_session_delete_requested(num: int):
        global ANALYTICS_SCOPE
        session.delete_session(num)
        if ANALYTICS_SCOPE.get("sessionNum") == num:
            # The scoped session is gone; fall back rather than leaving the tab
            # pinned to an empty view the user has no obvious way to explain.
            ANALYTICS_SCOPE = {"type": "all"}
        _refresh_record(window, session)
        _refresh_history(window, session)
        _refresh_sessions(window, session)
        _refresh_analytics(window, session)

    def on_match_delete_requested(match_id: int):
        # Same refresh set as a session delete: removing a match can change the
        # active session's W/L (and therefore the ratio and streak cards) as
        # well as the history table and the session summaries.
        session.delete_match(match_id)
        _refresh_record(window, session)
        _refresh_history(window, session)
        _refresh_sessions(window, session)
        _refresh_analytics(window, session)

    # Wire the session prompt signal to our handler
    window.signals.session_prompt.connect(prompt_session)
    window.signals.settings_prompt.connect(prompt_settings)
    window.signals.new_session_requested.connect(on_new_session_requested)
    window.signals.session_delete_requested.connect(on_session_delete_requested)
    window.signals.match_delete_requested.connect(on_match_delete_requested)
    window.signals.analytics_scope_changed.connect(on_analytics_scope_changed)
    window.signals.stats_api_problem.connect(prompt_stats_api)

    if _should_show_on_start(sys.argv):
        window.show()

    threading.Thread(target=process_watcher, daemon=True).start()
    sys.exit(app.exec())


def _should_show_on_start(argv: list) -> bool:
    """
    Decide whether the main window is visible at launch.

    The frozen build starts in the tray: it autostarts with Windows and
    shouldn't steal focus at boot — the window pops up on its own when
    Rocket League is detected (`game_started` -> `_show_from_tray`).
    Running from source is almost always a dev/test run, so the window is
    shown immediately rather than making you dig through the tray.

    `--tray` and `--show` force either behaviour in both cases.
    """
    if "--show" in argv:
        return True
    if "--tray" in argv:
        return False
    return not getattr(sys, "frozen", False)


def _should_close_to_tray(argv: list) -> bool:
    """
    Decide what closing the window does: hide to the tray, or quit outright.

    The shipped build hides — it has to stay resident to notice the next
    Rocket League launch. From source that's a nuisance (the process
    survives the window and has to be hunted down in the tray), so a dev run
    quits properly.

    `--tray` forces the shipped behaviour. `--show` deliberately does *not*
    affect this — it only controls launch visibility.
    """
    if "--tray" in argv:
        return True
    return getattr(sys, "frozen", False)


def _refresh_record(window: MainWindow, session: SessionStore):
    window.signals.record_updated.emit(session.wins, session.losses)

def _refresh_history(window: MainWindow, session: SessionStore):
    window.signals.history_updated.emit(session.get_recent_opponents(20), session.session_num)

def _refresh_sessions(window: MainWindow, session: SessionStore):
    window.signals.sessions_updated.emit(session.get_session_summaries())

def _refresh_analytics(window: MainWindow, session: SessionStore):
    # Goes wherever _refresh_sessions goes, and last: the whole Analytics tab
    # renders from this one snapshot, scope chips included.
    window.signals.analytics_updated.emit(session.get_analytics(ANALYTICS_SCOPE))


if __name__ == "__main__":
    main()