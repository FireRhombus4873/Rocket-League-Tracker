"""
Shared fixtures.

The `store` fixture points SessionStore at a throwaway SQLite DB inside pytest's
tmp dir, so the data-layer tests exercise the *real* persistence code without
touching the user's %LOCALAPPDATA% history.

SessionStore binds its paths at import time (`from config import HISTORY_DB_FILE,
HISTORY_FILE`), so the names live in the `sessionStore` module namespace — that's
what we patch, not `config`.
"""
import pytest


@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    """Redirect SessionStore's DB + legacy-JSON paths into tmp_path.

    Returns the (db_file, json_file) paths so tests that need to stage a file
    *before* the store is constructed (e.g. the JSON-migration test) can build
    their own SessionStore afterwards."""
    import sessionStore
    db_file   = tmp_path / "history.db"
    json_file = tmp_path / "match_history.json"
    monkeypatch.setattr(sessionStore, "HISTORY_DB_FILE", db_file)
    monkeypatch.setattr(sessionStore, "HISTORY_FILE", json_file)
    return db_file, json_file


@pytest.fixture
def store(db_paths):
    """A fresh SessionStore backed by an empty temp DB. Connection is closed on
    teardown so Windows can delete the tmp file."""
    import sessionStore
    s = sessionStore.SessionStore()
    yield s
    s.conn.close()
