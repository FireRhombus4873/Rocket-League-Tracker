import psutil
import time

PROCESS_NAME = "RocketLeague.exe"

class ProcessHandler():
    def __init__(self):
        pass

    def is_rocket_league_running(self):
        return any(p.name() == PROCESS_NAME for p in psutil.process_iter(["name"]))

    def wait_for_game(self):
        print("Waiting for Rocket League to launch...")
        while not self.is_rocket_league_running():
            time.sleep(2)
        print("Rocket League detected!")

    def wait_for_game_to_close(self):
        while self.is_rocket_league_running():
            time.sleep(2)
        print("Rocket League closed. Waiting for relaunch...\n")