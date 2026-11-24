from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from local_video_site.core import config


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.ensure_data_directories()
    connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
    connection.row_factory = sqlite3.Row
    # Referential cleanup is deliberately handled in application code.
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
