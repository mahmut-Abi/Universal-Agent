from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import URL, Engine, create_engine, event


def create_configured_sqlite_engine(path: str | Path) -> Engine:
    """Create a SQLite engine with production runtime concurrency PRAGMAs."""

    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"timeout": 30.0},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(
        dbapi_connection: Any,
        connection_record: Any,
    ) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine
