"""Utility tools: time/date, safe calculator, memory access, and self-describe."""
from __future__ import annotations

import ast
import operator
import platform
from datetime import datetime
from typing import Any

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool


# --------------------------------------------------------------------------- #
@tool(
    name="get_time",
    description="Get the current local date and time. Use for any 'what time/date is it' question.",
    category="utility",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def get_time() -> ToolResult:
    now = datetime.now()
    return ToolResult.success(
        {"iso": now.isoformat(timespec="seconds"),
         "date": now.strftime("%A, %d %B %Y"),
         "time": now.strftime("%I:%M %p")},
        summary=f"It is {now.strftime('%I:%M %p')} on {now.strftime('%A, %d %B %Y')}.",
    )


# --------------------------------------------------------------------------- #
# A genuinely safe arithmetic evaluator (AST allowlist — no eval()).
_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


@tool(
    name="calculate",
    description="Evaluate a basic arithmetic expression (+, -, *, /, //, %, **). "
                "No variables or functions — numbers and operators only.",
    category="utility",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. '3 * (4 + 5) / 2'"}
        },
        "required": ["expression"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def calculate(expression: str) -> ToolResult:
    try:
        tree = ast.parse(expression, mode="eval")
        value = _safe_eval(tree.body)
    except Exception:
        return ToolResult.failure(f"Could not evaluate '{expression}'.")
    return ToolResult.success({"expression": expression, "result": value},
                              summary=f"{expression} = {value}")


# --------------------------------------------------------------------------- #
@tool(
    name="system_info",
    description="Static information about the computer: OS, CPU model, core count, hostname, Python version.",
    category="system",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def system_info() -> ToolResult:
    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "hostname": platform.node(),
        "python": platform.python_version(),
    }
    try:
        import psutil
        info["cpu_cores_physical"] = psutil.cpu_count(logical=False)
        info["cpu_cores_logical"] = psutil.cpu_count(logical=True)
        info["ram_total_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    return ToolResult.success(info, summary=f"{info['os']} on {info['hostname']}.")


# --------------------------------------------------------------------------- #
@tool(
    name="remember",
    description="Store a durable fact or user preference in long-term memory. "
                "Use ONLY for information the user explicitly wants remembered.",
    category="memory",
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["preference", "fact", "semantic"],
                     "description": "preference = how the user likes things; fact/semantic = knowledge"},
            "value": {"type": "string", "description": "The thing to remember."},
            "key": {"type": "string", "description": "Optional short label for de-duplication."},
        },
        "required": ["kind", "value"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def remember(kind: str, value: str, key: str | None = None) -> ToolResult:
    from jarvis.config import settings
    if not settings.get("memory_enabled", True):
        return ToolResult.failure("Memory is disabled in settings.")
    from jarvis.db.repositories import MemoryRepo
    mid = MemoryRepo().add(kind, value, key=key, importance=2)
    return ToolResult.success({"id": mid}, summary=f"Noted: {value}")


@tool(
    name="recall",
    description="Search long-term memory for stored facts and preferences.",
    category="memory",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def recall(query: str) -> ToolResult:
    from jarvis.db.repositories import MemoryRepo
    hits = MemoryRepo().search(query, limit=10)
    if not hits:
        return ToolResult.success([], summary="I have nothing stored about that.")
    lines = [f"- ({h['kind']}) {h['value']}" for h in hits]
    return ToolResult.success(hits, summary="Here is what I remember:\n" + "\n".join(lines))
