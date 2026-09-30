"""Plugin / skill system.

Verifies that the example ``notes`` skill was really discovered, imported, and
that its tools registered as first-class tools — and that a skill tool marked
dangerous carries its declared permission/risk so it flows through the same
approval gate as the built-ins. Also exercises the notes tools end-to-end
(real file persistence in the test data dir)."""
from __future__ import annotations

from jarvis.skills import get_skill_manager
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import registry


def test_notes_skill_loaded():
    mgr = get_skill_manager()
    assert mgr.loaded_count >= 1
    names = {s["name"] for s in mgr.describe_all()}
    assert "notes" in names


def test_skill_registered_its_tools():
    tool_names = registry.names()
    assert {"add_note", "list_notes", "clear_notes"} <= tool_names


def test_notes_skill_describe_reports_tools():
    mgr = get_skill_manager()
    notes = next(s for s in mgr.describe_all() if s["name"] == "notes")
    assert notes["status"] == "loaded"
    assert notes["version"] == "1.0.0"
    assert set(notes["tools"]) == {"add_note", "list_notes", "clear_notes"}


def test_dangerous_skill_tool_keeps_permission_metadata():
    clear = registry.get("clear_notes")
    assert clear.permission_level is PermissionLevel.CONFIRM
    assert clear.risk_level is RiskLevel.MEDIUM
    assert clear.dangerous is True


def test_notes_roundtrip_persists():
    # start clean (clear_notes runs directly here — the permission gate lives in
    # the orchestrator, not in registry.run)
    registry.run("clear_notes")
    registry.run("add_note", text="buy milk")
    registry.run("add_note", text="ship JARVIS")
    listed = registry.run("list_notes", limit=10)
    assert listed.ok
    joined = "\n".join(listed.output)
    assert "buy milk" in joined
    assert "ship JARVIS" in joined

    cleared = registry.run("clear_notes")
    assert cleared.ok
    empty = registry.run("list_notes")
    assert empty.output == []
