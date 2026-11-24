from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .connection import connect


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@contextmanager
def write_transaction() -> Iterator:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
