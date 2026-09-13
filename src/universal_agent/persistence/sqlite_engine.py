from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import URL, Engine, create_engine, event


def create_configured_sqlite_engine(path: str | Path) -> Engine:
    """Create a SQLite engine with production runtime concurrency PRAGMAs.

    Connections may be used from worker threads because async store adapters
    offload blocking SQLite calls through ``asyncio.to_thread``; the engine's
    connection pool serializes checkout per call so pooled connections never
    migrate threads while a transaction is open.
    """

    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"timeout": 30.0, "check_same_thread": False},
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


class LazySqliteEngine:
    """Thread-safe lazy holder that creates the engine on first use.

    Engine creation runs DDL (``create_all``) and a legacy-column migration, so
    it is blocking. When async store adapters offload that work to a worker
    thread, concurrent store calls would otherwise race to build two engines
    for the same path; the holder guarantees a single shared engine.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._engine: Engine | None = None

    def get(self, build: Callable[[str | Path], Engine]) -> Engine:
        engine = self._engine
        if engine is not None:
            return engine
        with self._lock:
            engine = self._engine
            if engine is None:
                engine = build(self._path)
                self._engine = engine
            return engine
