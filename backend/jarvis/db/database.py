"""SQLite database wrapper.

SQLite is synchronous; we guard access with a re-entrant lock and enable WAL so
the single shared connection is safe to use from FastAPI's threadpool and the
background task workers. The API is intentionally tiny (execute / query / one)
and the higher-level repositories build on top of it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from jarvis.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT 'New conversation',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,              -- user | assistant | tool | system
    content         TEXT NOT NULL,
    meta            TEXT,                       -- JSON: tool calls, provider, mode
    created_at      REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    steps       TEXT,                           -- JSON list of step dicts
    result      TEXT,                           -- JSON
    error       TEXT,
    progress    REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                  -- preference | fact | episodic | semantic
    key         TEXT,
    value       TEXT NOT NULL,
    importance  INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                          -- JSON-encoded
);

CREATE TABLE IF NOT EXISTS permissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT '*',       -- optional argument signature
    decision    TEXT NOT NULL,                   -- allow | deny
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_perm_tool ON permissions(tool);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT NOT NULL,
    event       TEXT NOT NULL,
    data        TEXT,                            -- JSON (redacted)
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS tool_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool        TEXT NOT NULL,
    args        TEXT,
    status      TEXT NOT NULL,                   -- ok | error | denied
    output      TEXT,
    duration_ms INTEGER,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toolhist_created ON tool_history(created_at);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    title       TEXT,
    hash        TEXT,
    chunks      INTEGER NOT NULL DEFAULT 0,
    indexed_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON doc_chunks(document_id);

CREATE TABLE IF NOT EXISTS automations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    trigger     TEXT NOT NULL,                   -- JSON
    actions     TEXT NOT NULL,                   -- JSON
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run    REAL,
    created_at  REAL NOT NULL
);
"""


class Database:
    """Thread-safe SQLite wrapper with a minimal query surface."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- core operations ------------------------------------------------ #
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a write statement; returns lastrowid."""
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid or 0

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, [tuple(s) for s in seq])
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- convenience ---------------------------------------------------- #
    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def dumps(obj: Any) -> str:
        return json.dumps(obj, default=str, ensure_ascii=False)

    @staticmethod
    def loads(text: Optional[str], default: Any = None) -> Any:
        if not text:
            return default
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return default


_db_singleton: Optional[Database] = None
_db_lock = threading.Lock()


def get_db() -> Database:
    """Return the process-wide database instance (lazily created)."""
    global _db_singleton
    if _db_singleton is None:
        with _db_lock:
            if _db_singleton is None:
                _db_singleton = Database()
    return _db_singleton
