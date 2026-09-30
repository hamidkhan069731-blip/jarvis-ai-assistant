"""Example JARVIS skill: personal notes.

Demonstrates the full skill-authoring pattern:
  * registering multiple tools via the ``@tool`` decorator
  * returning rich :class:`ToolResult` objects with a spoken summary
  * mixing permission levels — ``add_note``/``list_notes`` are SAFE (auto), while
    ``clear_notes`` requires confirmation, so it flows through the same approval
    UI as the built-in destructive tools.

Notes are stored as a simple text file in the JARVIS data directory. Copy this
folder to build your own skill; see ``docs/PLUGIN_DEVELOPMENT.md``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, registry, tool


def _notes_file() -> Path:
    return settings.data_dir / "notes.txt"


@tool(
    "add_note",
    "Save a short personal note for later. Use when the user says 'note that…', "
    "'add a note', or 'remind me to…' (as a note, not a scheduled alarm).",
    category="notes",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The note text."}},
        "required": ["text"],
    },
)
def add_note(text: str) -> ToolResult:
    text = (text or "").strip()
    if not text:
        return ToolResult.failure("Cannot save an empty note.")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _notes_file().open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {text}\n")
    return ToolResult.success(summary=f"Noted: “{text}”.")


@tool(
    "list_notes",
    "List the user's most recent saved notes.",
    category="notes",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "How many recent notes.", "default": 10}},
    },
)
def list_notes(limit: int = 10) -> ToolResult:
    path = _notes_file()
    if not path.exists():
        return ToolResult.success(output=[], summary="You have no notes yet.")
    lines = [ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    recent = lines[-max(1, int(limit)):]
    if not recent:
        return ToolResult.success(output=[], summary="You have no notes yet.")
    body = "\n".join(recent)
    return ToolResult.success(output=recent,
                              summary=f"Your {len(recent)} most recent notes:\n{body}")


@tool(
    "clear_notes",
    "Delete ALL saved notes. This is irreversible.",
    category="notes",
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
    parameters={"type": "object", "properties": {}},
)
def clear_notes() -> ToolResult:
    path = _notes_file()
    count = 0
    if path.exists():
        count = len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])
        path.unlink()
    return ToolResult.success(summary=f"Cleared {count} note(s).")


def register(_registry=registry) -> None:
    """Optional explicit hook. The @tool decorator already registers the tools on
    import, so this is a no-op kept for documentation/clarity."""
    return None
