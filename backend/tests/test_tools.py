"""Tool registry + representative built-in tools.

Focus areas:
  * the registry actually loaded the built-ins,
  * the ``run(name, /, **kwargs)`` positional-only fix (a tool whose own argument
    is literally ``name`` must not collide with the registry's ``name`` param),
  * the calculator is a real safe evaluator (no ``eval``),
  * tool execution is journaled to tool_history,
  * the shell tool refuses to run while disabled and blocks destructive commands.
"""
from __future__ import annotations

from jarvis.tools.base import ToolResult, registry, tool
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.db.repositories import SettingsRepo, ToolHistoryRepo


# A throwaway tool whose parameter is literally "name" — this is the exact shape
# (open_application(name=...)) that used to collide with ToolRegistry.run(name).
@tool("echo_name", "test helper", parameters={
    "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
def _echo_name(name: str) -> ToolResult:
    return ToolResult.success({"echoed": name}, summary=f"echo:{name}")


# --------------------------------------------------------------------------- #
def test_builtin_tools_registered():
    names = registry.names()
    for expected in ("get_time", "calculate", "system_status", "open_application",
                     "run_shell", "web_search", "remember", "recall"):
        assert expected in names, expected


def test_run_with_name_argument_does_not_collide():
    result = registry.run("echo_name", name="chrome")
    assert result.ok
    assert result.output == {"echoed": "chrome"}


def test_unknown_tool_returns_failure():
    result = registry.run("does_not_exist")
    assert not result.ok
    assert "Unknown tool" in (result.error or "")


def test_calculate_evaluates_arithmetic():
    result = registry.run("calculate", expression="3 * (4 + 5) / 2")
    assert result.ok
    assert result.output["result"] == 13.5


def test_calculate_rejects_code_injection():
    # not arithmetic — must be refused by the AST allowlist, never executed
    result = registry.run("calculate", expression="__import__('os').getcwd()")
    assert not result.ok


def test_get_time_has_speakable_summary():
    result = registry.run("get_time")
    assert result.ok
    assert result.summary
    assert "iso" in result.output


def test_tool_run_is_recorded_in_history():
    before = len(ToolHistoryRepo().recent())
    registry.run("calculate", expression="1+1")
    after = ToolHistoryRepo().recent()
    assert len(after) == before + 1
    assert after[0]["tool"] == "calculate"
    assert after[0]["status"] == "ok"


# --------------------------------------------------------------------------- #
# Shell safety
# --------------------------------------------------------------------------- #
def test_shell_disabled_by_default():
    result = registry.run("run_shell", command="echo hi")
    assert not result.ok
    assert "disabled" in (result.error or "").lower()


def test_shell_blocks_dangerous_command_even_when_enabled():
    SettingsRepo().set("enable_shell", True)
    try:
        result = registry.run("run_shell", command="rm -rf /")
        assert not result.ok
        assert "refused" in (result.error or "").lower() or "block" in (result.error or "").lower()
    finally:
        SettingsRepo().set("enable_shell", False)


def test_shell_dynamic_permission_escalates_for_dangerous():
    shell = registry.get("run_shell")
    safe_perm, safe_risk = shell.effective_levels(command="Get-Process")
    danger_perm, danger_risk = shell.effective_levels(command="format C:")
    assert safe_perm is PermissionLevel.CONFIRM and safe_risk is RiskLevel.HIGH
    assert danger_perm is PermissionLevel.ALWAYS_CONFIRM and danger_risk is RiskLevel.CRITICAL
