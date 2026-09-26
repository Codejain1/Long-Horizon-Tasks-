"""Thin database layer over SQLite (local dev) and Postgres + pgvector.

SQL is written with ``%s`` placeholders and translated for SQLite. JSON goes in
TEXT (SQLite) or JSONB (Postgres) columns; vectors in BLOB or ``vector(n)``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np


class Database:
    kind: str  # "sqlite" | "postgres"

    def execute(self, sql: str, params: tuple = ()) -> None:
        raise NotImplementedError

    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        raise NotImplementedError

    def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def json(self, value: Any) -> Any:
        raise NotImplementedError

    def vector(self, vec: np.ndarray) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def load_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class SQLiteDatabase(Database):
    kind = "sqlite"

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("%s", "?")

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(self._sql(sql), params)

    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(self._sql(sql), params).fetchall()

    def json(self, value: Any) -> Any:
        return json.dumps(value, default=_json_default)

    def vector(self, vec: np.ndarray) -> Any:
        return np.asarray(vec, dtype=np.float32).tobytes()

    def close(self) -> None:
        self._conn.close()


class PostgresDatabase(Database):
    kind = "postgres"

    def __init__(self, url: str):
        import psycopg
        from pgvector.psycopg import register_vector

        self._conn = psycopg.connect(url, autocommit=True)
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)
        self._lock = threading.Lock()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def json(self, value: Any) -> Any:
        from psycopg.types.json import Jsonb

        return Jsonb(json.loads(json.dumps(value, default=_json_default)))

    def vector(self, vec: np.ndarray) -> Any:
        return np.asarray(vec, dtype=np.float32)

    def close(self) -> None:
        self._conn.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def connect(url: str) -> Database:
    if url == "sqlite://":
        return SQLiteDatabase(":memory:")
    if url.startswith("sqlite:///"):
        return SQLiteDatabase(url.removeprefix("sqlite:///"))
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDatabase(url)
    raise ValueError(f"unsupported database URL: {url!r}")


@contextmanager
def connected(url: str) -> Iterator[Database]:
    db = connect(url)
    try:
        yield db
    finally:
        db.close()
