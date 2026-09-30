"""Tool base classes and the global registry.

Every JARVIS capability is a :class:`Tool` with rich metadata so the AI brain,
the permission engine, and the UI can all reason about it uniformly:

    name, description, parameters (JSON schema), permission_level, risk_level,
    category, and an ``execute`` implementation.

Tools are synchronous (they touch the OS, filesystem, subprocesses). The
orchestrator runs them in a worker thread so the event loop is never blocked.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.security.audit import get_logger

log = get_logger("tools")


# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: Optional[str] = None
    # a short, human-facing summary the assistant can speak/quote
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: Any = None, summary: str = "", **meta: Any) -> "ToolResult":
        return cls(ok=True, output=output, summary=summary, meta=meta)

    @classmethod
    def failure(cls, error: str, **meta: Any) -> "ToolResult":
        return cls(ok=False, error=error, summary=error, meta=meta)

    def to_model_string(self) -> str:
        """Compact string handed back to the LLM as the tool result."""
        if not self.ok:
            return f"ERROR: {self.error}"
        if self.summary and self.output is None:
            return self.summary
        import json
        try:
            body = json.dumps(self.output, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            body = str(self.output)
        if len(body) > 6000:
            body = body[:6000] + " …(truncated)"
        return (self.summary + "\n" if self.summary else "") + body


# --------------------------------------------------------------------------- #
class Tool:
    """Base class for all tools. Subclass and set the class attributes, or use
    the :func:`tool` decorator for simple function tools."""

    name: str = ""
    description: str = ""
    category: str = "general"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission_level: PermissionLevel = PermissionLevel.SAFE
    risk_level: RiskLevel = RiskLevel.LOW
    # If True, the UI shows a rich confirmation with the resolved arguments.
    dangerous: bool = False

    def execute(self, **kwargs: Any) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- validation (override for custom checks) ------------------------ #
    def validate(self, **kwargs: Any) -> Optional[str]:
        """Return an error string if arguments are invalid, else None."""
        return None

    # -- argument-aware permission override ----------------------------- #
    def dynamic_permission(self, **kwargs: Any):
        """Optionally return ``(PermissionLevel, RiskLevel)`` computed from the
        actual arguments (e.g. launching an allowlisted app is SAFE, launching
        an unknown one needs confirmation). Return ``None`` to use the static
        class-level levels."""
        fn = getattr(self, "_dyn", None)
        return fn(**kwargs) if fn else None

    def effective_levels(self, **kwargs: Any):
        dyn = None
        try:
            dyn = self.dynamic_permission(**kwargs)
        except Exception:  # never let a permission hook crash the call
            dyn = None
        return dyn if dyn else (self.permission_level, self.risk_level)

    # -- schema for the AI provider ------------------------------------- #
    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "permission_level": self.permission_level.value,
            "risk_level": self.risk_level.value,
            "parameters": self.parameters,
        }


# --------------------------------------------------------------------------- #
class _FunctionTool(Tool):
    def __init__(self, fn: Callable[..., ToolResult], **attrs: Any) -> None:
        self._fn = fn
        for k, v in attrs.items():
            setattr(self, k, v)

    def execute(self, **kwargs: Any) -> ToolResult:
        return self._fn(**kwargs)


def tool(name: str, description: str, *, category: str = "general",
         parameters: Optional[dict] = None,
         permission_level: PermissionLevel = PermissionLevel.SAFE,
         risk_level: RiskLevel = RiskLevel.LOW,
         dangerous: bool = False,
         dynamic_permission: Optional[Callable[..., Any]] = None,
         ) -> Callable[[Callable[..., ToolResult]], _FunctionTool]:
    """Decorator turning a function into a registered Tool instance."""

    def wrap(fn: Callable[..., ToolResult]) -> _FunctionTool:
        t = _FunctionTool(
            fn,
            name=name,
            description=description,
            category=category,
            parameters=parameters or {"type": "object", "properties": {}},
            permission_level=permission_level,
            risk_level=risk_level,
            dangerous=dangerous,
        )
        if dynamic_permission is not None:
            t._dyn = dynamic_permission
        registry.register(t)
        return t

    return wrap


# --------------------------------------------------------------------------- #
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        if not t.name:
            raise ValueError("Tool must have a name")
        if t.name in self._tools:
            log.warning("Tool %s already registered; overwriting", t.name)
        self._tools[t.name] = t
        log.debug("registered tool %s", t.name)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> set[str]:
        return set(self._tools.keys())

    def by_category(self) -> dict[str, list[Tool]]:
        out: dict[str, list[Tool]] = {}
        for t in self._tools.values():
            out.setdefault(t.category, []).append(t)
        return out

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_schema() for t in self._tools.values()]

    def describe_all(self) -> list[dict[str, Any]]:
        return [t.describe() for t in sorted(self._tools.values(), key=lambda x: x.name)]

    # -- execution with history/audit ---------------------------------- #
    def run(self, name: str, /, **kwargs: Any) -> ToolResult:
        # `name` is positional-only so a tool arg literally called "name"
        # (e.g. open_application(name=...)) never collides with it.
        from jarvis.db.repositories import ToolHistoryRepo
        t = self.get(name)
        if not t:
            return ToolResult.failure(f"Unknown tool: {name}")
        err = t.validate(**kwargs)
        if err:
            return ToolResult.failure(err)
        started = time.time()
        try:
            result = t.execute(**kwargs)
        except Exception as exc:  # noqa: BLE001 - convert any tool crash to a result
            log.exception("tool %s crashed", name)
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
        duration = int((time.time() - started) * 1000)
        try:
            ToolHistoryRepo().record(
                name, kwargs,
                "ok" if result.ok else "error",
                result.summary or (result.error or ""),
                duration,
            )
        except Exception:
            log.debug("failed to record tool history", exc_info=True)
        result.meta["duration_ms"] = duration
        return result


# global singleton
registry = ToolRegistry()


def load_builtin_tools() -> None:
    """Import tool modules so their tools self-register. Idempotent."""
    from jarvis.tools import (  # noqa: F401
        util_tools,
        system_tools,
        power_tools,
        screen_tools,
        file_tools,
        app_tools,
        shell_tools,
        web_tools,
    )
