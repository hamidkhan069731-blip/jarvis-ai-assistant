"""Application control: launch known apps, open URLs/paths, close apps.

Launching an app on the configured allowlist is SAFE; launching anything else
needs confirmation (enforced via the dynamic-permission hook).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import webbrowser
from ctypes import wintypes
from typing import Any

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool

# Friendly name -> Windows executable / command.
_IS_WIN = sys.platform == "win32"

_APP_MAP = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "task manager": "taskmgr.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "code": "code",
    "word": "winword.exe",
    "excel": "excel.exe",
    "settings": "ms-settings:",
    "spotify": "spotify.exe",
}


def _resolve(name: str) -> str:
    key = name.strip().lower()
    return _APP_MAP.get(key, name)


def _is_allowlisted(name: str) -> bool:
    allow = settings.get("app_allowlist", settings.app_allowlist)
    key = name.strip().lower()
    exe = _resolve(name).lower()
    stem = os.path.splitext(os.path.basename(exe))[0]
    return any(a in (key, exe, stem) for a in allow)


def _app_permission(name: str = "", **_: Any):
    """Allowlisted apps launch automatically; unknown apps need confirmation."""
    if _is_allowlisted(name):
        return (PermissionLevel.SAFE, RiskLevel.LOW)
    return (PermissionLevel.CONFIRM, RiskLevel.MEDIUM)


def _launch(name: str) -> tuple[bool, str, str]:
    """Start an app by friendly name/executable. Returns (ok, error, exe)."""
    exe = _resolve(name)
    try:
        if exe.endswith(":") or exe.startswith("ms-"):  # protocol handler
            os.startfile(exe)  # type: ignore[attr-defined]
        else:
            # `start` resolves apps on PATH and via the App Paths registry.
            subprocess.Popen(["cmd", "/c", "start", "", exe], shell=False)
    except (OSError, ValueError) as exc:
        return (False, str(exc), exe)
    return (True, "", exe)


def _kill(name: str) -> tuple[bool, str]:
    """Force-close every process of an app by name. Returns (ok, error)."""
    exe = _resolve(name)
    image = os.path.basename(exe)
    if not image.lower().endswith(".exe"):
        image += ".exe"
    try:
        proc = subprocess.run(["taskkill", "/IM", image, "/F"],
                              capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return (False, str(exc))
    if proc.returncode == 0:
        return (True, "")
    return (False, proc.stderr.strip() or f"No running instance of {name} was found.")


@tool(
    name="open_application",
    description="Launch a desktop application by name (e.g. 'Chrome', 'VS Code', 'Notepad', 'Calculator'). "
                "Allowlisted apps open immediately; others ask first.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "App name or executable."}},
        "required": ["name"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dynamic_permission=_app_permission,
)
def open_application(name: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("App launching is implemented for Windows only.")
    ok, err, exe = _launch(name)
    if not ok:
        return ToolResult.failure(f"Could not launch '{name}': {err}")
    return ToolResult.success({"app": name, "exe": exe}, summary=f"Launching {name}.")


@tool(
    name="open_url",
    description="Open a web URL in the default browser. Use for 'open youtube', 'go to a website'.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def open_url(url: str) -> ToolResult:
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not open URL: {exc}")
    return ToolResult.success({"url": url}, summary=f"Opened {url}.")


@tool(
    name="open_path",
    description="Open a file or folder with its default program / in File Explorer.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
)
def open_path(path: str) -> ToolResult:
    from jarvis.security.guard import PathAccessError, resolve_in_roots
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not target.exists():
        return ToolResult.failure(f"Path does not exist: {target}")
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
    except OSError as exc:
        return ToolResult.failure(f"Could not open: {exc}")
    return ToolResult.success({"path": str(target)}, summary=f"Opened {target}.")


@tool(
    name="close_application",
    description="Close all windows/processes of an application by name (e.g. 'notepad'). Requires confirmation.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Executable name, e.g. 'notepad'."}},
        "required": ["name"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def close_application(name: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("App control is implemented for Windows only.")
    ok, err = _kill(name)
    if ok:
        return ToolResult.success({"app": name}, summary=f"Closed {name}.")
    return ToolResult.failure(err)


# --------------------------------------------------------------------------- #
# Window management (real user32 calls — no extra dependencies)
# --------------------------------------------------------------------------- #
# ShowWindow commands we expose.
_SW = {"minimize": 6, "maximize": 3, "restore": 9}


def _enum_windows() -> list[dict[str, Any]]:
    """Every visible, titled top-level window, with its owning process."""
    if not _IS_WIN:
        return []
    user32 = ctypes.windll.user32
    windows: list[dict[str, Any]] = []

    # WNDENUMPROC: BOOL CALLBACK(HWND, LPARAM)
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append({
            "hwnd": int(hwnd),
            "title": title,
            "pid": int(pid.value),
            "process": _process_name(int(pid.value)),
        })
        return True

    user32.EnumWindows(proto(_cb), 0)
    return windows


def _process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001 — psutil optional / process may have exited
        return ""


def _match_window(query: str) -> dict[str, Any] | None:
    """Find a window by title or process name (case-insensitive substring)."""
    q = query.strip().lower()
    if not q:
        return None
    # An app alias ("chrome") should also match its executable ("chrome.exe").
    stem = os.path.splitext(os.path.basename(_resolve(query)))[0].lower()
    best = None
    for w in _enum_windows():
        title = w["title"].lower()
        proc = (w["process"] or "").lower()
        proc_stem = os.path.splitext(proc)[0]
        if q in title or q in proc or stem == proc_stem:
            # Prefer a title match; it's the more specific signal.
            if q in title:
                return w
            best = best or w
    return best


@tool(
    name="list_windows",
    description="List the open application windows (title + process). Use for 'what's open', "
                "'what am I working on', before switching or focusing a window.",
    category="apps",
    parameters={"type": "object", "properties": {}},
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def list_windows() -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Window management is implemented for Windows only.")
    wins = _enum_windows()
    if not wins:
        return ToolResult.success([], summary="No visible application windows.")
    lines = [f"{w['title']}" + (f"  ({w['process']})" if w["process"] else "")
             for w in wins]
    return ToolResult.success(
        wins, summary=f"{len(wins)} open window(s):\n" + "\n".join(lines))


@tool(
    name="focus_window",
    description="Bring an application's window to the foreground by title or app name "
                "(e.g. 'switch to Chrome', 'focus VS Code').",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string",
                                 "description": "Window title or app name to focus."}},
        "required": ["query"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def focus_window(query: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Window management is implemented for Windows only.")
    win = _match_window(query)
    if win is None:
        return ToolResult.failure(f"No open window matches '{query}'.")
    user32 = ctypes.windll.user32
    hwnd = win["hwnd"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW["restore"])
    # Windows only lets the foreground thread steal focus; attaching to its input
    # queue is the documented way for a background process to raise a window.
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(our_thread, fg_thread, True))
    try:
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)
    if not ok:
        return ToolResult.failure(
            f"Windows refused to foreground '{win['title']}' (another app is holding focus).")
    return ToolResult.success(win, summary=f"Focused {win['title']}.")


@tool(
    name="set_window_state",
    description="Minimize, maximize, or restore an application's window by title or app name.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Window title or app name."},
            "state": {"type": "string", "enum": ["minimize", "maximize", "restore"]},
        },
        "required": ["query", "state"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def set_window_state(query: str, state: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("Window management is implemented for Windows only.")
    cmd = _SW.get(state.strip().lower())
    if cmd is None:
        return ToolResult.failure("state must be minimize, maximize, or restore.")
    win = _match_window(query)
    if win is None:
        return ToolResult.failure(f"No open window matches '{query}'.")
    ctypes.windll.user32.ShowWindow(win["hwnd"], cmd)
    return ToolResult.success({**win, "state": state},
                              summary=f"{state.capitalize()}d {win['title']}.")


@tool(
    name="is_app_running",
    description="Check whether an application is currently running, and how many processes/windows "
                "it has. Use before restarting or closing something.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "App name, e.g. 'chrome'."}},
        "required": ["name"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def is_app_running(name: str) -> ToolResult:
    try:
        import psutil
    except ImportError:
        return ToolResult.failure("psutil is not installed, so process detection is unavailable.")
    stem = os.path.splitext(os.path.basename(_resolve(name)))[0].lower()
    if not stem:
        return ToolResult.failure("Provide an application name.")
    pids = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = (p.info.get("name") or "").lower()
            if os.path.splitext(pname)[0] == stem:
                pids.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    titles = [w["title"] for w in _enum_windows() if w["pid"] in set(pids)]
    running = bool(pids)
    data = {"name": name, "running": running, "pids": pids, "windows": titles}
    if not running:
        return ToolResult.success(data, summary=f"{name} is not running.")
    summary = f"{name} is running ({len(pids)} process(es))."
    if titles:
        summary += " Windows: " + ", ".join(titles[:5])
    return ToolResult.success(data, summary=summary)


@tool(
    name="restart_application",
    description="Restart an application: close it, then launch it again. Use for 'restart Chrome'. "
                "Requires confirmation because unsaved work in that app may be lost.",
    category="apps",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "App name, e.g. 'chrome'."}},
        "required": ["name"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def restart_application(name: str) -> ToolResult:
    if not _IS_WIN:
        return ToolResult.failure("App control is implemented for Windows only.")
    closed, close_err = _kill(name)
    if closed:
        time.sleep(1.0)  # let the process release its files/ports before relaunch
    ok, err, exe = _launch(name)
    if not ok:
        return ToolResult.failure(f"Closed {name} but could not relaunch it: {err}")
    if closed:
        return ToolResult.success({"app": name, "exe": exe, "was_running": True},
                                  summary=f"Restarted {name}.")
    # Not previously running: report honestly rather than implying a restart.
    return ToolResult.success(
        {"app": name, "exe": exe, "was_running": False, "detail": close_err},
        summary=f"{name} wasn't running, so I just started it.")
