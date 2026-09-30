"""Asynchronous task engine.

A task wraps an async coroutine factory plus metadata. The engine persists task
state to the DB, runs tasks concurrently up to a limit, supports cancellation
and retries, reports progress, and emits live ``task_update`` events to the UI.

Long-running or scheduled work (backups, research, monitoring jobs) is modelled
as tasks so it is observable and controllable from the console.
"""
from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from jarvis.db.repositories import TaskRepo
from jarvis.security.audit import audit, get_logger

log = get_logger("tasks")

EmitFn = Callable[[dict], Awaitable[None]]


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskContext:
    """Handed to a task coroutine so it can report progress / check cancellation."""
    task_id: int
    _engine: "TaskEngine"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    async def progress(self, value: float, note: str = "") -> None:
        await self._engine._on_progress(self.task_id, value, note)


TaskCoro = Callable[[TaskContext], Awaitable[Any]]


class TaskEngine:
    def __init__(self, emit: EmitFn, max_concurrent: int = 3) -> None:
        self.emit = emit
        self.repo = TaskRepo()
        self._sema = asyncio.Semaphore(max_concurrent)
        self._running: dict[int, TaskContext] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------ #
    async def submit(self, title: str, coro: TaskCoro, *, description: str = "",
                     retries: int = 0) -> int:
        task_id = self.repo.create(title, description)
        ctx = TaskContext(task_id=task_id, _engine=self)
        self._running[task_id] = ctx
        self._tasks[task_id] = asyncio.create_task(self._run(task_id, coro, ctx, retries))
        await self._emit_update(task_id)
        return task_id

    async def _run(self, task_id: int, coro: TaskCoro, ctx: TaskContext,
                   retries: int) -> None:
        async with self._sema:
            attempt = 0
            while True:
                if ctx.cancelled:
                    await self._finish(task_id, TaskStatus.CANCELLED)
                    return
                attempt += 1
                self.repo.update(task_id, status=TaskStatus.RUNNING.value)
                await self._emit_update(task_id)
                audit("task_start", task_id=task_id, attempt=attempt)
                try:
                    result = await coro(ctx)
                    self.repo.update(task_id, result=result, progress=1.0)
                    await self._finish(task_id, TaskStatus.COMPLETED)
                    return
                except asyncio.CancelledError:
                    await self._finish(task_id, TaskStatus.CANCELLED)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.exception("task %s failed", task_id)
                    if attempt <= retries and not ctx.cancelled:
                        await asyncio.sleep(min(2 ** attempt, 10))
                        continue
                    self.repo.update(task_id, error=str(exc))
                    await self._finish(task_id, TaskStatus.FAILED, error=str(exc))
                    return

    async def _finish(self, task_id: int, status: TaskStatus,
                      error: Optional[str] = None) -> None:
        self.repo.update(task_id, status=status.value)
        self._running.pop(task_id, None)
        self._tasks.pop(task_id, None)
        audit("task_finish", task_id=task_id, status=status.value, error=error)
        await self._emit_update(task_id)
        await self.emit({"type": "notification", "level":
                         "error" if status == TaskStatus.FAILED else "info",
                         "source": "tasks",
                         "message": f"Task '{self._title(task_id)}' {status.value}."})

    # ------------------------------------------------------------------ #
    async def _on_progress(self, task_id: int, value: float, note: str) -> None:
        self.repo.update(task_id, progress=max(0.0, min(1.0, value)))
        await self._emit_update(task_id, note=note)

    def cancel(self, task_id: int) -> bool:
        ctx = self._running.get(task_id)
        if not ctx:
            return False
        ctx.cancel_event.set()
        t = self._tasks.get(task_id)
        if t:
            t.cancel()
        return True

    def cancel_all(self) -> int:
        ids = list(self._running.keys())
        for tid in ids:
            self.cancel(tid)
        return len(ids)

    # ------------------------------------------------------------------ #
    def _title(self, task_id: int) -> str:
        row = self.repo.get(task_id)
        return row["title"] if row else f"#{task_id}"

    async def _emit_update(self, task_id: int, note: str = "") -> None:
        row = self.repo.get(task_id)
        if row:
            await self.emit({"type": "task_update", "task": row, "note": note})

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repo.list(limit)

    def active(self) -> list[dict[str, Any]]:
        return self.repo.active()
