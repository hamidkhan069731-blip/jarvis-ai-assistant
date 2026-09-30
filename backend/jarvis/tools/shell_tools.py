"""Controlled command / code execution.

Safety model (defence in depth):
  * DISABLED unless ``JARVIS_ENABLE_SHELL=true`` — validate() refuses otherwise.
  * dangerous-pattern detection escalates to CRITICAL / always-confirm.
  * every invocation is at least CONFIRM (never silently auto-run).
  * hard timeout, output capture, working-directory restricted to file roots.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.security.guard import PathAccessError, is_dangerous_command, resolve_in_roots
from jarvis.tools.base import ToolResult, tool

_MAX_TIMEOUT = 120


def _shell_disabled_reason() -> Optional[str]:
    if not settings.get("enable_shell", settings.enable_shell):
        return ("Shell/code execution is disabled. Enable it in Settings "
                "(or set JARVIS_ENABLE_SHELL=true) if you accept the risk.")
    return None


def _command_permission(command: str = "", **_: Any):
    if is_dangerous_command(command):
        return (PermissionLevel.ALWAYS_CONFIRM, RiskLevel.CRITICAL)
    return (PermissionLevel.CONFIRM, RiskLevel.HIGH)


def _resolve_cwd(cwd: str) -> Path:
    if cwd:
        return resolve_in_roots(cwd)
    roots = settings.file_roots
    return roots[0] if roots else Path.home()


@tool(
    name="run_shell",
    description="Execute a shell command (PowerShell or CMD) and capture its output. "
                "Disabled by default; always requires confirmation; destructive commands are blocked/escalated.",
    category="developer",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "shell": {"type": "string", "enum": ["powershell", "cmd"], "default": "powershell"},
            "cwd": {"type": "string", "description": "Working directory (must be within file roots)."},
            "timeout": {"type": "integer", "default": 30, "maximum": _MAX_TIMEOUT},
        },
        "required": ["command"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
    dynamic_permission=_command_permission,
)
def run_shell(command: str, shell: str = "powershell", cwd: str = "",
              timeout: int = 30) -> ToolResult:
    reason = _shell_disabled_reason()
    if reason:
        return ToolResult.failure(reason)
    danger = is_dangerous_command(command)
    if danger:
        return ToolResult.failure(
            f"Refused: command matches a destructive pattern ({danger!r}). "
            "This class of command is blocked for safety.")
    try:
        workdir = _resolve_cwd(cwd)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))

    if shell == "cmd":
        argv = ["cmd", "/c", command]
    else:
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=min(timeout, _MAX_TIMEOUT), cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(f"Command timed out after {timeout}s.")
    except (OSError, FileNotFoundError) as exc:
        return ToolResult.failure(f"Execution error: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    payload = {"returncode": proc.returncode, "stdout": out[:6000], "stderr": err[:2000]}
    if proc.returncode == 0:
        return ToolResult.success(payload, summary=out[:500] or "Command completed (no output).")
    return ToolResult(ok=False, output=payload,
                      error=err[:500] or f"Exited with code {proc.returncode}.",
                      summary=err[:500] or f"Exited with code {proc.returncode}.")


@tool(
    name="run_python",
    description="Run a short Python snippet in a separate process and capture its output. "
                "Disabled by default; requires confirmation. Use for quick computation or scripting.",
    category="developer",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "timeout": {"type": "integer", "default": 30, "maximum": _MAX_TIMEOUT},
        },
        "required": ["code"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
)
def run_python(code: str, timeout: int = 30) -> ToolResult:
    reason = _shell_disabled_reason()
    if reason:
        return ToolResult.failure(reason)
    tmp = Path(tempfile.gettempdir()) / f"jarvis_snippet_{abs(hash(code)) % 10**8}.py"
    try:
        tmp.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True, text=True, timeout=min(timeout, _MAX_TIMEOUT),
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(f"Script timed out after {timeout}s.")
    except OSError as exc:
        return ToolResult.failure(f"Execution error: {exc}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    payload = {"returncode": proc.returncode, "stdout": out[:6000], "stderr": err[:2000]}
    if proc.returncode == 0:
        return ToolResult.success(payload, summary=out[:500] or "Script completed (no output).")
    return ToolResult(ok=False, output=payload, error=err[:500] or "Script failed.",
                      summary=err[:500] or "Script failed.")
