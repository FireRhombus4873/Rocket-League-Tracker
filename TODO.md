- [ ] Change history.json to be a database file (use sqlite), for faster retrieval of information, etc.

## Configuration / setup
- [ ] Move `LOCAL_USERNAME` out of `main.py` into a settings file (or auto-detect from `UpdateState` so renames don't break it).
- [ ] Persist window size/position between launches.
- [ ] Add a "first run" prompt to set username, instead of editing `main.py`.

## Data / correctness
- [ ] Backfill `id` on existing history entries via a one-shot migration on app start (currently relies on the name-fallback match indefinitely).
- [ ] Allow editing or deleting a single match (not just whole sessions) from the history table.
- [ ] Export history to CSV.

## UI features
- [ ] Render the teammate side of the Past Encounters card (data already computed in `get_current_encounters()`).
- [ ] Filter / search box on the Opponent History table.
- [ ] Highlight in the Past Encounters card when an opponent has a notable record vs you (e.g. lost 3+ in a row, or 0–5 all-time).
- [ ] Optional desktop notification when a previously-faced opponent is detected at match start.
- [ ] Let the user name sessions (label like "Friday duos") instead of just a number.
- [ ] Per-team totals row in `MatchStatsDialog`.

## Polish
- [ ] Strip the lingering SOS-era comments noted in `CLAUDE.md`.
