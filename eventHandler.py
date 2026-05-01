class EventHandler():
    def __init__(self, on_event_callback=None):
        self.on_event_callback = on_event_callback

        self.EVENT_HANDLERS = {
            "BallHit":             self.on_ball_hit,
            "ClockUpdatedSeconds": self.on_clock_updated,
            "CountdownBegin":      self.on_countdown_begin,
            "CrossbarHit":         self.on_crossbar_hit,
            "GoalReplayStart":     self.on_goal_replay_start,
            "GoalReplayWillEnd":   self.on_goal_replay_will_end,
            "GoalReplayEnd":       self.on_goal_replay_end,
            "GoalScored":          self.on_goal_scored,
            "MatchCreated":        self.on_match_created,
            "MatchInitialized":    self.on_match_initialized,
            "MatchDestroyed":      self.on_match_destroyed,
            "MatchEnded":          self.on_match_ended,
            "MatchPaused":         self.on_match_paused,
            "MatchUnpaused":       self.on_match_unpaused,
            "PodiumStart":         self.on_podium_start,
            "ReplayCreated":       self.on_replay_created,
            "RoundStarted":        self.on_round_started,
            "StatfeedEvent":       self.on_statfeed_event,
        }

    def dispatch(self, event_type, data):
        handler = self.EVENT_HANDLERS.get(event_type)
        if handler:
            result = handler(data)
            if result and self.on_event_callback:
                self.on_event_callback(result)

    def on_ball_hit(self, data):
        players = data.get("Players", [])
        if players:
            player = players[0]
            return {"BallHit": player.get('Name', 'Unknown')}

    def on_clock_updated(self, data):
        if data.get('bOvertime') == False:
            return {"ClockUpdatedSeconds": data.get('TimeSeconds', '?')}
        else:
            return {"OvertimeClockUpdatedSeconds": data.get('TimeSeconds', '?')}

    def on_countdown_begin(self, data):
        return {"CountdownBegin": True}

    def on_crossbar_hit(self, data):
        last_touch = data.get("BallLastTouch", {})
        player = last_touch.get("Player", {})
        return {"CrossbarHit": player.get('Name', 'Unknown')}

    def on_goal_replay_start(self, data):
        return {"ReplayStart": True}

    def on_goal_replay_will_end(self, data):
        return {"ReplayWillEnd": True}

    def on_goal_replay_end(self, data):
        return {"ReplayEnded": True}

    def on_goal_scored(self, data):
        scorer = data.get("Scorer", {})
        return {"GoalScored": scorer.get('Name', 'Unknown')}

    def on_match_created(self, data):
        return {"MatchCreated": True}

    def on_match_initialized(self, data):
        """
        In the Stats API, MatchInitialized carries no player data — the data
        field is empty. Players and team names are extracted from the first
        UpdateState message after the match GUID changes (handled in main.py).
        We just signal that the match has started.
        """
        return {"MatchInitialised": True}

    def on_match_destroyed(self, data):
        return {"MatchDestroyed": True}

    def on_match_ended(self, data):
        winner = data.get("WinnerTeamNum", "?")
        return {"MatchEnded": winner}

    def on_match_paused(self, data):
        return {"MatchPaused": True}

    def on_match_unpaused(self, data):
        return {"MatchUnpaused": True}

    def on_podium_start(self, data):
        return {"Podium": True}

    def on_replay_created(self, data):
        return {"ReplayCreated": True}

    def on_round_started(self, data):
        return {"RoundStarted": True}

    def on_statfeed_event(self, data):
        player = data.get("MainTarget", {})
        stat = data.get("Type", "Unknown")
        if stat == "Demolition":
            secondary = data.get("SecondaryTarget") or {}
            return {"StatDemolition": [player.get('Name', 'Unknown'), secondary.get('Name', 'Unknown'), stat]}
        else:
            return {"Stat": [player.get('Name', 'Unknown'), stat]}