## Configuration / setup
- [x] Move `LOCAL_USERNAME` out of `main.py` into a settings file (or auto-detect from `UpdateState` so renames don't break it).
- [ ] Persist window size/position between launches.
- [x] Add a "first run" prompt to set username, instead of editing `main.py`.
- [x] Allow user to also add common teammates names as a comma seperated list.

## Data / correctness
- [ ] Allow editing or deleting a single match (not just whole sessions) from the history table.
- [x] Change history.json to be a database file (use sqlite), for faster retrieval of information, etc.
- [ ] Export history to CSV.

## UI features
- [ ] Render the teammate side of the Past Encounters card (data already computed in `get_current_encounters()`).
    - [ ] Filter teammates so that common teammates are not mentioned
- [ ] Filter / search box on the Opponent History table.
- [ ] Highlight in the Past Encounters card when an opponent has a notable record vs you (e.g. lost 3+ in a row, or 0–5 all-time).
- [ ] Let the user name sessions (label like "Friday duos") instead of just a number. Maybe?
- [ ] Per-team totals row in `MatchStatsDialog`.
