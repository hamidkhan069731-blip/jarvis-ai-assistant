"""Screen understanding (Phase 4): read what is actually on screen.

Two distinct capabilities, with very different requirements:

* **OCR** — reading the *text* on screen. Implemented with the OCR engine built
  into Windows 10/11 (``Windows.Media.Ocr``) via ``screen_ocr.ps1``. This is
  fully local: no API key, no third-party package, no network call. It also
  returns per-word bounding boxes, so JARVIS knows *where* text is, not just
  what it says.

* **Vision** — *describing* a screenshot ("what am I looking at?"). This needs a
  vision-capable cloud model. When no such provider is configured, the tool
  reports NOT CONFIGURED and explains what to set, rather than inventing a
  description of a screen it cannot see.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool

_IS_WIN = sys.platform == "win32"
_SCRIPT = Path(__file__).with_name("screen_ocr.ps1")
_OCR_TIMEOUT = 60


# --------------------------------------------------------------------------- #
# OCR plumbing
# --------------------------------------------------------------------------- #
def _run_ocr(hwnd: int = 0, image_path: str = "", save: str = "") -> dict[str, Any]:
    """Invoke the PowerShell OCR helper. Returns its parsed JSON payload."""
    if not _IS_WIN:
        return {"ok": False, "error": "Screen OCR uses the Windows OCR engine and is Windows-only."}
    if not _SCRIPT.exists():
        return {"ok": False, "error": f"OCR helper script is missing: {_SCRIPT}"}
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA",
            "-File", str(_SCRIPT)]
    if hwnd:
        argv += ["-Hwnd", str(hwnd)]
    if image_path:
        argv += ["-Path", image_path]
    if save:
        argv += ["-Save", save]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=_OCR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "OCR timed out."}
    except FileNotFoundError:
        return {"ok": False, "error": "PowerShell was not found on this system."}
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    if not out:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        return {"ok": False, "error": err[:300] or "OCR produced no output."}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Could not parse OCR output: {out[:200]}"}


def _resolve_window(target: str) -> tuple[int, str, str]:
    """Map a window/app name to an hwnd. Returns (hwnd, title, error)."""
    from jarvis.tools.app_tools import _match_window
    win = _match_window(target)
    if win is None:
        return (0, "", f"No open window matches '{target}'.")
    return (win["hwnd"], win["title"], "")


# --------------------------------------------------------------------------- #
@tool(
    name="read_screen",
    description="Read the text currently visible on screen using OCR. Use for 'what does the screen "
                "say', 'read this error', 'what's in that window'. Optionally target one window by "
                "title or app name. Runs fully offline.",
    category="screen",
    parameters={
        "type": "object",
        "properties": {
            "window": {"type": "string",
                       "description": "Optional window title or app name to read instead of the whole screen."},
        },
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def read_screen(window: str = "") -> ToolResult:
    hwnd, title, err = (0, "", "")
    if window:
        hwnd, title, err = _resolve_window(window)
        if err:
            return ToolResult.failure(err)
    data = _run_ocr(hwnd=hwnd)
    if not data.get("ok"):
        return ToolResult.failure(data.get("error", "OCR failed."))
    text = (data.get("text") or "").strip()
    where = f" of {title}" if title else ""
    if not text:
        return ToolResult.success(
            {"text": "", "lines": []},
            summary=f"I captured the screen{where} but found no readable text on it.")
    lines = [ln.get("text", "") for ln in data.get("lines", [])]
    return ToolResult.success(
        {"text": text, "lines": lines, "width": data.get("width"), "height": data.get("height"),
         "window": title},
        summary=f"Screen text{where} ({len(lines)} lines):\n{text[:1500]}")


@tool(
    name="find_on_screen",
    description="Locate a piece of text on screen and return its position (x, y). Use to check whether "
                "something is visible, or to find where to click. Optionally search one window.",
    category="screen",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to look for (case-insensitive)."},
            "window": {"type": "string", "description": "Optional window title or app name to search."},
        },
        "required": ["text"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def find_on_screen(text: str, window: str = "") -> ToolResult:
    needle = (text or "").strip().lower()
    if not needle:
        return ToolResult.failure("Provide the text to search for.")
    hwnd, title, err = (0, "", "")
    if window:
        hwnd, title, err = _resolve_window(window)
        if err:
            return ToolResult.failure(err)
    data = _run_ocr(hwnd=hwnd)
    if not data.get("ok"):
        return ToolResult.failure(data.get("error", "OCR failed."))

    matches: list[dict[str, Any]] = []
    for line in data.get("lines", []):
        line_text = (line.get("text") or "")
        if needle not in line_text.lower():
            continue
        words = line.get("words") or []
        # Prefer the specific word(s) that matched; fall back to the whole line.
        hits = [w for w in words if needle in (w.get("text") or "").lower()]
        box = hits[0] if hits else (words[0] if words else None)
        if box is None:
            continue
        matches.append({
            "line": line_text,
            "x": box["x"] + box["w"] // 2,      # center point, ready for a click
            "y": box["y"] + box["h"] // 2,
            "box": {k: box[k] for k in ("x", "y", "w", "h")},
        })

    where = f" in {title}" if title else " on screen"
    if not matches:
        return ToolResult.success(
            {"found": False, "matches": []},
            summary=f"I couldn't find '{text}'{where}.")
    first = matches[0]
    return ToolResult.success(
        {"found": True, "matches": matches},
        summary=(f"Found '{text}'{where} at ({first['x']}, {first['y']})"
                 + (f" — {len(matches)} matches." if len(matches) > 1 else ".")))


# --------------------------------------------------------------------------- #
# Vision: requires a vision-capable cloud model. Never faked.
# --------------------------------------------------------------------------- #
def _vision_status() -> tuple[bool, str]:
    """(configured, detail) for image understanding."""
    if not settings.anthropic_api_key:
        return (False,
                "NOT CONFIGURED — describing a screenshot needs a vision-capable cloud model. "
                "Set ANTHROPIC_API_KEY and select Anthropic in Settings. Reading on-screen *text* "
                "works offline right now: ask me to read the screen instead.")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return (False,
                "NOT CONFIGURED — the `anthropic` package is not installed. "
                "Run `pip install anthropic`, or ask me to read the screen instead (OCR is offline).")
    return (True, "anthropic")


def _capture_png() -> tuple[str, str]:
    """Capture the screen to a PNG under data/. Returns (path, error)."""
    out_dir = settings.data_dir / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"vision_{int(time.time())}.png"
    # Reuse the OCR helper's capture path so there is one capture implementation.
    data = _run_ocr(save=str(target))
    if not data.get("ok"):
        return ("", data.get("error", "Screen capture failed."))
    if not target.exists():
        return ("", "Screen capture produced no file.")
    return (str(target), "")


@tool(
    name="describe_screen",
    description="Describe what is on screen visually (layout, images, UI state) using a vision model. "
                "Use for 'what am I looking at', 'what's this diagram'. Requires a cloud vision key; "
                "for plain text prefer read_screen, which works offline.",
    category="screen",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string",
                         "description": "Optional specific question about the screen."},
        },
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
)
def describe_screen(question: str = "") -> ToolResult:
    configured, detail = _vision_status()
    if not configured:
        # Honest capability report — never invent a description of an unseen screen.
        return ToolResult.failure(detail)

    path, err = _capture_png()
    if err:
        return ToolResult.failure(err)

    try:
        image_b64 = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError as exc:
        return ToolResult.failure(f"Could not read the capture: {exc}")

    prompt = (question or "").strip() or (
        "Describe what is on this screen: the application in focus, the main content, "
        "and anything that looks like an error or needs attention. Be concise.")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/png",
                                                 "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as exc:  # noqa: BLE001 — surface the real API error
        return ToolResult.failure(f"Vision request failed: {type(exc).__name__}: {exc}"[:400])

    text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    if not text:
        return ToolResult.failure("The vision model returned an empty description.")
    return ToolResult.success({"description": text, "image": path, "question": prompt},
                              summary=text[:1500])
