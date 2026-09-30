"""Repository layer — typed helpers over the raw :class:`Database`.

Each repository owns one concern. They are cheap to construct (they just hold a
reference to the shared DB), so callers create them on demand.
"""
from __future__ import annotations

from typing import Any, Optional

from jarvis.db.database import Database, get_db


class _Repo:
    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or get_db()


# --------------------------------------------------------------------------- #
class ConversationRepo(_Repo):
    def create(self, title: str = "New conversation") -> int:
        now = self.db.now()
        return self.db.execute(
            "INSERT INTO conversations(title, created_at, updated_at) VALUES(?,?,?)",
            (title, now, now),
        )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        )

    def touch(self, conversation_id: int) -> None:
        self.db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (self.db.now(), conversation_id),
        )

    def rename(self, conversation_id: int, title: str) -> None:
        self.db.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title, self.db.now(), conversation_id),
        )

    def delete(self, conversation_id: int) -> None:
        self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))


class MessageRepo(_Repo):
    def add(self, conversation_id: int, role: str, content: str,
            meta: Optional[dict] = None) -> int:
        mid = self.db.execute(
            "INSERT INTO messages(conversation_id, role, content, meta, created_at)"
            " VALUES(?,?,?,?,?)",
            (conversation_id, role, content, self.db.dumps(meta or {}), self.db.now()),
        )
        ConversationRepo(self.db).touch(conversation_id)
        return mid

    def history(self, conversation_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
            (conversation_id, limit),
        )
        for r in rows:
            r["meta"] = self.db.loads(r.get("meta"), {})
        return rows


class TaskRepo(_Repo):
    def create(self, title: str, description: str = "",
               steps: Optional[list] = None) -> int:
        now = self.db.now()
        return self.db.execute(
            "INSERT INTO tasks(title, description, status, steps, progress, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (title, description, "pending", self.db.dumps(steps or []), 0.0, now, now),
        )

    def update(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        for json_field in ("steps", "result"):
            if json_field in fields:
                fields[json_field] = self.db.dumps(fields[json_field])
        fields["updated_at"] = self.db.now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id=?",
                        (*fields.values(), task_id))

    def get(self, task_id: int) -> Optional[dict[str, Any]]:
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            row["steps"] = self.db.loads(row.get("steps"), [])
            row["result"] = self.db.loads(row.get("result"), None)
        return row

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["steps"] = self.db.loads(r.get("steps"), [])
            r["result"] = self.db.loads(r.get("result"), None)
        return rows

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status IN ('pending','running','waiting_approval','paused')"
            " ORDER BY id DESC"
        )
        for r in rows:
            r["steps"] = self.db.loads(r.get("steps"), [])
        return rows


class MemoryRepo(_Repo):
    VALID_KINDS = {"preference", "fact", "episodic", "semantic"}

    def add(self, kind: str, value: str, key: Optional[str] = None,
            importance: int = 1) -> int:
        kind = kind if kind in self.VALID_KINDS else "fact"
        now = self.db.now()
        # de-dupe on (kind, key) when a key is supplied
        if key:
            existing = self.db.one(
                "SELECT id FROM memory WHERE kind=? AND key=?", (kind, key))
            if existing:
                self.db.execute(
                    "UPDATE memory SET value=?, importance=?, updated_at=? WHERE id=?",
                    (value, importance, now, existing["id"]),
                )
                return existing["id"]
        return self.db.execute(
            "INSERT INTO memory(kind, key, value, importance, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (kind, key, value, importance, now, now),
        )

    def list(self, kind: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        if kind:
            return self.db.query(
                "SELECT * FROM memory WHERE kind=? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (kind, limit),
            )
        return self.db.query(
            "SELECT * FROM memory ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (limit,),
        )

    def search(self, term: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{term}%"
        return self.db.query(
            "SELECT * FROM memory WHERE value LIKE ? OR key LIKE ?"
            " ORDER BY importance DESC LIMIT ?",
            (like, like, limit),
        )

    def delete(self, memory_id: int) -> None:
        self.db.execute("DELETE FROM memory WHERE id=?", (memory_id,))

    def clear(self, kind: Optional[str] = None) -> None:
        if kind:
            self.db.execute("DELETE FROM memory WHERE kind=?", (kind,))
        else:
            self.db.execute("DELETE FROM memory")


class SettingsRepo(_Repo):
    def get(self, key: str) -> Any:
        row = self.db.one("SELECT value FROM settings WHERE key=?", (key,))
        return self.db.loads(row["value"], None) if row else None

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, self.db.dumps(value)),
        )

    def all(self) -> dict[str, Any]:
        return {r["key"]: self.db.loads(r["value"], None)
                for r in self.db.query("SELECT key, value FROM settings")}

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM settings WHERE key=?", (key,))


class PermissionRepo(_Repo):
    def remember(self, tool: str, decision: str, scope: str = "*") -> None:
        self.db.execute(
            "INSERT INTO permissions(tool, scope, decision, created_at) VALUES(?,?,?,?)",
            (tool, scope, decision, self.db.now()),
        )

    def lookup(self, tool: str, scope: str = "*") -> Optional[str]:
        row = self.db.one(
            "SELECT decision FROM permissions WHERE tool=? AND scope IN (?, '*')"
            " ORDER BY created_at DESC LIMIT 1",
            (tool, scope),
        )
        return row["decision"] if row else None

    def forget(self, tool: str, scope: str = "*") -> None:
        """Revoke every remembered decision for a tool, so it is asked again.
        A grant the user cannot take back is not really a grant."""
        self.db.execute(
            "DELETE FROM permissions WHERE tool=? AND scope IN (?, '*')", (tool, scope))

    def list(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM permissions ORDER BY created_at DESC")

    def revoke_all(self) -> None:
        self.db.execute("DELETE FROM permissions")


class AuditRepo(_Repo):
    def log(self, level: str, event: str, data: Optional[dict] = None) -> None:
        self.db.execute(
            "INSERT INTO audit_log(level, event, data, created_at) VALUES(?,?,?,?)",
            (level, event, self.db.dumps(data or {}), self.db.now()),
        )

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = self.db.loads(r.get("data"), {})
        return rows

    def clear(self) -> None:
        self.db.execute("DELETE FROM audit_log")


class ToolHistoryRepo(_Repo):
    def record(self, tool: str, args: dict, status: str, output: str,
               duration_ms: int) -> None:
        self.db.execute(
            "INSERT INTO tool_history(tool, args, status, output, duration_ms, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (tool, self.db.dumps(args), status, output[:4000], duration_ms, self.db.now()),
        )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM tool_history ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["args"] = self.db.loads(r.get("args"), {})
        return rows

    def clear(self) -> None:
        self.db.execute("DELETE FROM tool_history")


def bind_settings_override() -> None:
    """Wire live DB settings into the config singleton's :meth:`get`."""
    from jarvis.config import settings

    repo = SettingsRepo()

    def _override(key: str) -> Any:
        return repo.get(key)

    settings.bind_override(_override)
