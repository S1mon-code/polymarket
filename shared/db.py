"""SQLite utility for all bots."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from shared.config import DB_PATH


def get_db_path(bot_name: str) -> Path:
    """Get per-bot database path."""
    base = Path(DB_PATH).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{bot_name}.db"


@contextmanager
def get_connection(bot_name: str):
    """Context manager for SQLite connection with WAL mode."""
    db_path = get_db_path(bot_name)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_table(bot_name: str, schema: str) -> None:
    """Initialize a table with the given schema SQL."""
    with get_connection(bot_name) as conn:
        conn.executescript(schema)
