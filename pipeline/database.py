"""The database: one SQLite file holding the conversation log and the review queue.

Settings (environment variables):
    DATABASE_PATH   the SQLite file, default data/muneer.sqlite3
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import env_text

DEFAULT_PATH = "data/muneer.sqlite3"
WAIT_SECONDS = 10

# The app's threads take turns here rather than in SQLite, whose busy handler
# isn't fair: under load, one writer could wait out the whole timeout and fail.
# Everything that writes the database runs in the app's one process.
_TURN = threading.RLock()


def database_path(env: Mapping[str, str]) -> str:
    return env_text(env, "DATABASE_PATH", DEFAULT_PATH)


def utc_now() -> str:
    """The current time in UTC, as ISO 8601 to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path, schema: Sequence[str]) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success and rolls back on error.

    One short-lived connection per call, so the app's threads never share one.
    The folder and the tables are (re)created every time, so deleting the file,
    or the whole folder, while the app runs simply starts an empty database.
    """
    if not _TURN.acquire(timeout=WAIT_SECONDS):
        raise sqlite3.OperationalError("database is locked")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=WAIT_SECONDS)
        try:
            with db:
                for statement in schema:
                    db.execute(statement)
                yield db
        finally:
            db.close()
    finally:
        _TURN.release()
