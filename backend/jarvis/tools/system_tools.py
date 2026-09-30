"""System tools: real-time monitoring and OS control.

Everything here is genuinely functional on Windows using psutil, ctypes
(user32/core), and the win32 clipboard API. Nothing is simulated; where a
capability needs an optional dependency, the tool reports that clearly instead
of pretending to work.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:  # pragma: no cover
    _HAS_PSUTIL = False

_IS_WIN = sys.platform == "win32"


# --------------------------------------------------------------------------- #
def read_system_status() -> dict[str, Any]:
    """Shared snapshot used by both the tool and the live monitor."""
    if not _HAS_PSUTIL:
        return {"available": False, "reason": "psutil is not installed"}
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(Path.home().anchor or "C:\\"))
    status: dict[str, Any] = {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": vm.percent,
        "ram_used_gb": round(vm.used / 1e9, 2),
        "ram_total_gb": round(vm.total / 1e9, 2),
        "disk_percent": du.percent,
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "process_count": len(psutil.pids()),
    }
    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            status["battery_percent"] = round(battery.percent)
            status["battery_plugged"] = battery.power_plugged
    except Exception:
        pass
    try:
        net = psutil.net_io_counters()
        status["net_sent_mb"] = round(net.bytes_sent / 1e6, 1)
        status["net_recv_mb"] = round(net.bytes_recv / 1e6, 1)
    except Exception:
        pass
    return status


@tool(
    name="system_status",
    description="Live system metrics: CPU %, RAM %, disk usage, battery, network, uptime. "
                "Use for 'how is my computer doing', 'what's my CPU', 'is my disk full'.",
    category="system",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def system_status() -> ToolResult:
    s = read_system_status()
    if not s.get("available"):
        return ToolResult.failure(s.get("reason", "monitoring unavailable"))
    summary = (f"CPU {s['cpu_percent']}%, RAM {s['ram_percent']}% "
               f"({s['ram_used_gb']}/{s['ram_total_gb']} GB), "
               f"disk {s['disk_percent']}% used, {s['disk_free_gb']} GB free.")
    if "battery_percent" in s:
        summary += f" Battery {s['battery_percent']}%."
    return ToolResult.success(s, summary=summary)


# --------------------------------------------------------------------------- #
@tool(
    name="list_processes",
    description="List the top processes by CPU or memory usage.",
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "sort_by": {"type": "string", "enum": ["cpu", "memory"], "default": "memory"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
        },
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def list_processes(sort_by: str = "memory", limit: int = 10) -> ToolResult:
    if not _HAS_PSUTIL:
        return ToolResult.failure("psutil is not installed.")
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
        try:
            procs.append({
                "pid": p.info["pid"],
                "name": p.info["name"],
                "cpu": round(p.info.get("cpu_percent") or 0, 1),
                "memory": round(p.info.get("memory_percent") or 0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu" if sort_by == "cpu" else "memory"
    procs.sort(key=lambda x: x[key], reverse=True)
    top = procs[:limit]
    lines = [f"{p['name']} (pid {p['pid']}): {p[key]}% {sort_by}" for p in top]
    return ToolResult.success(top, summary="Top processes:\n" + "\n".join(lines))


@tool(
    name="kill_process",
    description="Terminate a running process by PID. Requires confirmation.",
    category="system",
    parameters={
        "type": "object",
        "properties": {"pid": {"type": "integer", "description": "Process ID to terminate."}},
        "required": ["pid"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
)
def kill_process(pid: int) -> ToolResult:
    if not _HAS_PSUTIL:
        return ToolResult.failure("psutil is not installed.")
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return ToolResult.success({"pid": pid, "name": name},
                                  summary=f"Terminated {name} (pid {pid}).")
    except psutil.NoSuchProcess:
        return ToolResult.failure(f"No process with pid {pid}.")
    except psutil.AccessDenied:
        return ToolResult.failure(f"Access denied terminating pid {pid} (try running JARVIS as admin).")


# --------------------------------------------------------------------------- #
# Volume control via Windows virtual media keys (real, no extra deps).
_VK = {"mute": 0xAD, "down": 0xAE, "up": 0xAF}


@tool(
    name="control_volume",
    description="Adjust system volume. action=up/down changes it in steps; action=mute toggles mute.",
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["up", "down", "mute"]},
            "steps": {"type": "integer", "default": 4, "minimum": 1, "maximum": 20,
                      "description": "Number of 2%-ish steps for up/down."},
        },
        "required": ["action"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def control_volume(action: str, steps: int = 4) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Volume control is implemented for Windows only.")
    vk = _VK.get(action)
    if vk is None:
        return ToolResult.failure("action must be up, down, or mute.")
    user32 = ctypes.windll.user32
    presses = 1 if action == "mute" else max(1, min(steps, 20))
    for _ in range(presses):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
    return ToolResult.success({"action": action, "steps": presses},
                              summary=f"Volume {action}.")


# --------------------------------------------------------------------------- #
# Keyboard input via SendInput (real synthetic key events, no extra deps).
# --------------------------------------------------------------------------- #
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1

# Named keys the model can reference in hotkeys / key presses.
_KEY_CODES = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _KEY_CODES[_c] = ord(_c.upper())
for _d in "0123456789":
    _KEY_CODES[_d] = ord(_d)


def _parse_hotkey(combo: str) -> list[int] | None:
    """'ctrl+shift+t' -> [0x11, 0x10, 0x54]; None if any key is unknown."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    codes = []
    for p in parts:
        code = _KEY_CODES.get(p)
        if code is None:
            return None
        codes.append(code)
    return codes


def _key_event(vk: int, up: bool) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP if up else 0, 0)


@tool(
    name="press_keys",
    description="Press a keyboard shortcut in the active window, e.g. 'ctrl+s', 'alt+tab', "
                "'win+d', 'ctrl+shift+t'. Keys are pressed together and released in reverse.",
    category="system",
    parameters={
        "type": "object",
        "properties": {"combo": {"type": "string",
                                 "description": "Key combination, e.g. 'ctrl+s'."}},
        "required": ["combo"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def press_keys(combo: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Keyboard control is implemented for Windows only.")
    codes = _parse_hotkey(combo)
    if codes is None:
        return ToolResult.failure(
            f"Unrecognized key combination: {combo!r}. Use names like ctrl, alt, shift, "
            "win, enter, tab, esc, f1-f12, letters or digits.")
    for vk in codes:                      # press in order
        _key_event(vk, up=False)
    for vk in reversed(codes):            # release in reverse
        _key_event(vk, up=True)
    return ToolResult.success({"combo": combo}, summary=f"Pressed {combo}.")


@tool(
    name="type_text",
    description="Type text into the currently focused window, character by character. "
                "Use only when the right window already has focus.",
    category="system",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to type."}},
        "required": ["text"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
)
def type_text(text: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Keyboard control is implemented for Windows only.")
    if not text:
        return ToolResult.failure("Nothing to type.")
    if len(text) > 2000:
        return ToolResult.failure("Refusing to type more than 2000 characters at once.")
    user32 = ctypes.windll.user32
    # KEYEVENTF_UNICODE sends the character itself, so layouts/accents work.
    for ch in text:
        user32.keybd_event(0, ord(ch), _KEYEVENTF_UNICODE, 0)
        user32.keybd_event(0, ord(ch), _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0)
    return ToolResult.success({"chars": len(text)},
                              summary=f"Typed {len(text)} character(s).")


# --------------------------------------------------------------------------- #
@tool(
    name="lock_workstation",
    description="Lock the Windows session (the login screen). Requires confirmation.",
    category="system",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def lock_workstation() -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Lock is implemented for Windows only.")
    ok = ctypes.windll.user32.LockWorkStation()
    return (ToolResult.success(summary="Workstation locked.") if ok
            else ToolResult.failure("The OS refused the lock request."))


# --------------------------------------------------------------------------- #
@tool(
    name="clipboard_get",
    description="Read the current text contents of the clipboard.",
    category="system",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def clipboard_get() -> ToolResult:
    text = _clipboard_read()
    if text is None:
        return ToolResult.failure("Clipboard is empty or contains non-text data.")
    return ToolResult.success({"text": text}, summary=f"Clipboard: {text[:200]}")


@tool(
    name="clipboard_set",
    description="Replace the clipboard contents with the given text.",
    category="system",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def clipboard_set(text: str) -> ToolResult:
    if _clipboard_write(text):
        return ToolResult.success(summary="Copied to clipboard.")
    return ToolResult.failure("Could not access the clipboard.")


def _clipboard_read() -> str | None:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return data
    except Exception:
        return None


def _clipboard_write(text: str) -> bool:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
@tool(
    name="take_screenshot",
    description="Capture the screen to a PNG file under the data/ directory and return its path.",
    category="system",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def take_screenshot() -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Screenshot is implemented for Windows only.")
    out = settings.data_dir / "screenshots"
    out.mkdir(exist_ok=True)
    target = out / f"screen_{int(time.time())}.png"
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size);"
        f"$bmp.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                       capture_output=True, timeout=20, check=True)
    except subprocess.CalledProcessError as exc:
        return ToolResult.failure(f"Screenshot failed: {exc.stderr.decode(errors='ignore')[:200]}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return ToolResult.failure(f"Screenshot failed: {exc}")
    if not target.exists():
        return ToolResult.failure("Screenshot command ran but produced no file.")
    return ToolResult.success({"path": str(target)}, summary=f"Screenshot saved to {target}.")
