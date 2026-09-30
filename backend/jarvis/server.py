"""FastAPI application: WebSocket realtime channel + REST API + static UI.

This is a thin shell over :data:`jarvis.core.core`. All heavy lifting lives in
the subsystem modules; here we just translate HTTP/WS into core calls.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis.config import settings
from jarvis.core import core
from jarvis.db.repositories import (
    AuditRepo, ConversationRepo, MessageRepo, SettingsRepo, ToolHistoryRepo,
)
from jarvis.memory.store import MemoryStore
from jarvis.security.audit import audit, get_logger
from jarvis.tools.base import registry

log = get_logger("server")

# settings the UI is allowed to change at runtime (secrets excluded on purpose)
_EDITABLE = {
    "ai_provider", "permission_mode", "enable_shell", "tts_enabled", "tts_rate",
    "tts_volume", "tts_voice", "wake_word", "memory_enabled", "proactive_enabled",
    "server_tts", "boot_greeting",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await core.startup()
    try:
        yield
    finally:
        await core.shutdown()


app = FastAPI(title="J.A.R.V.I.S.", version="1.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# WebSocket — the realtime channel
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    core.register(ws)
    try:
        # greet with a snapshot so the UI can hydrate immediately
        await ws.send_json({"type": "hello", "diagnostics": core.diagnostics(),
                            "settings": settings.public_dict()})
        await core.on_client_ready()
        while True:
            data = await ws.receive_json()
            await _handle_ws_message(data)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("ws error", exc_info=True)
    finally:
        core.unregister(ws)


async def _handle_ws_message(data: dict[str, Any]) -> None:
    mtype = data.get("type")
    if mtype == "chat":
        text = (data.get("text") or "").strip()
        if not text:
            return
        # lightweight built-in interrupt
        if text.lower() in {"stop", "jarvis stop", "jarvis, stop", "cancel", "ruko"}:
            core.stop()
            await core.broadcast({"type": "notification", "level": "info",
                                  "source": "core", "message": "Stopped."})
            return
        asyncio.create_task(core.handle_user_text(data.get("conversation_id"), text))
    elif mtype == "approve":
        core.resolve_approval(data.get("approval_id", ""),
                              bool(data.get("approved")),
                              bool(data.get("remember")))
    elif mtype == "stop":
        core.stop()
    elif mtype == "cancel_task":
        if core.tasks:
            core.tasks.cancel(int(data.get("task_id", 0)))


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/api/diagnostics")
async def diagnostics() -> dict:
    return core.diagnostics()


@app.get("/api/settings")
async def get_settings() -> dict:
    return settings.public_dict()


@app.post("/api/settings")
async def update_settings(payload: dict) -> dict:
    repo = SettingsRepo()
    applied = {}
    for key, value in payload.items():
        if key in _EDITABLE:
            repo.set(key, value)
            applied[key] = value
    if "ai_provider" in applied:
        core.reload_provider()
    await core.broadcast({"type": "settings_changed", "settings": settings.public_dict()})
    return {"applied": applied, "settings": settings.public_dict()}


@app.get("/api/tools")
async def list_tools() -> dict:
    return {"tools": registry.describe_all(),
            "by_category": {k: [t.name for t in v]
                            for k, v in registry.by_category().items()}}


@app.get("/api/skills")
async def list_skills() -> dict:
    from jarvis.skills import get_skill_manager
    mgr = get_skill_manager()
    return {"skills": mgr.describe_all(), "loaded": mgr.loaded_count,
            "directories": [str(d) for d in mgr.directories()]}


@app.get("/api/conversations")
async def conversations() -> dict:
    return {"conversations": ConversationRepo().list()}


@app.get("/api/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: int) -> dict:
    return {"messages": MessageRepo().history(conversation_id)}


@app.get("/api/memory")
async def get_memory(kind: Optional[str] = None) -> dict:
    return {"memory": MemoryStore().list(kind=kind),
            "enabled": settings.get("memory_enabled", True)}


@app.post("/api/memory")
async def add_memory(payload: dict) -> dict:
    mid = MemoryStore().remember(
        payload.get("kind", "fact"), payload.get("value", ""),
        key=payload.get("key"), importance=int(payload.get("importance", 2)))
    return {"id": mid}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    MemoryStore().delete(memory_id)
    return {"deleted": memory_id}


@app.delete("/api/memory")
async def clear_memory(kind: Optional[str] = None) -> dict:
    MemoryStore().clear(kind)
    return {"cleared": kind or "all"}


# -- standing approvals ----------------------------------------------------- #
# A remembered "allow" is a permanent grant. The user must be able to SEE what
# they have standing-approved and take it back; a grant you cannot inspect or
# revoke is not meaningful consent.
@app.get("/api/permissions")
async def get_permissions() -> dict:
    from jarvis.permissions.engine import PermissionEngine
    rows = PermissionEngine().remembered()
    for r in rows:
        t = registry.get(r["tool"])
        r["description"] = t.description if t else "(tool no longer installed)"
        r["risk"] = t.risk_level.value if t else "unknown"
    return {"permissions": rows, "mode": settings.get("permission_mode", "balanced")}


@app.delete("/api/permissions/{tool}")
async def revoke_permission(tool: str) -> dict:
    from jarvis.permissions.engine import PermissionEngine
    PermissionEngine().forget(tool)
    audit("permission_revoked", tool=tool)
    return {"revoked": tool}


@app.delete("/api/permissions")
async def revoke_all_permissions() -> dict:
    from jarvis.db.repositories import PermissionRepo
    PermissionRepo().revoke_all()
    audit("permissions_cleared")
    return {"cleared": True}


@app.get("/api/tasks")
async def tasks() -> dict:
    return {"tasks": core.tasks.list() if core.tasks else []}


@app.get("/api/history")
async def tool_history() -> dict:
    return {"history": ToolHistoryRepo().recent()}


@app.get("/api/audit")
async def audit_log() -> dict:
    return {"audit": AuditRepo().recent()}


@app.get("/api/voices")
async def voices() -> dict:
    from jarvis.voice import get_tts
    return {"voices": get_tts().voices()}


@app.post("/api/speak")
async def speak(payload: dict) -> dict:
    from jarvis.voice import get_tts
    text = payload.get("text", "")
    ok = await asyncio.to_thread(get_tts().speak, text)
    return {"spoken": ok}


# --------------------------------------------------------------------------- #
# Static UI
# --------------------------------------------------------------------------- #
_frontend = settings.frontend_dir
if (_frontend / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend / "assets")), name="assets")
    app.mount("/css", StaticFiles(directory=str(_frontend / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(_frontend / "js")), name="js")

    @app.get("/")
    async def index() -> Any:
        return FileResponse(str(_frontend / "index.html"))
else:
    @app.get("/")
    async def index_missing() -> Any:  # pragma: no cover
        return JSONResponse({"error": "frontend not built", "path": str(_frontend)},
                            status_code=500)
