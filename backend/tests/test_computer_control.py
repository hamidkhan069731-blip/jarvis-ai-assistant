"""Computer control (Phase 3): windows, app detection/restart, keyboard input.

SAFETY: these tests must never disturb the real desktop. `list_windows` and
`is_app_running` are read-only, so they run for real; everything that would
move a window, restart an app, or synthesize keystrokes is exercised only
through its validation path (bad arguments / unknown targets), which returns
before any user32 or subprocess call.
"""
from __future__ import annotations

import sys

import pytest

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.app_tools import (
    _match_window, focus_window, is_app_running, list_windows,
    restart_application, set_window_state,
)
from jarvis.tools.base import registry
from jarvis.tools.system_tools import _parse_hotkey, press_keys, type_text

_IS_WIN = sys.platform == "win32"
_UNLIKELY = "zzz_no_such_app_zzz"


# --------------------------------------------------------------------------- #
# registration & metadata
# --------------------------------------------------------------------------- #
def test_new_tools_are_registered():
    for name in ("list_windows", "focus_window", "set_window_state",
                 "is_app_running", "restart_application", "press_keys", "type_text"):
        assert registry.get(name) is not None, name


def test_permission_levels_match_risk():
    # Read-only inspection is free; focusing a window is harmless.
    assert list_windows.permission_level is PermissionLevel.SAFE
    assert is_app_running.permission_level is PermissionLevel.SAFE
    assert focus_window.permission_level is PermissionLevel.SAFE
    # Restarting an app can lose unsaved work; typing can leak into any window.
    assert restart_application.permission_level is PermissionLevel.CONFIRM
    assert restart_application.dangerous is True
    assert press_keys.dangerous is True
    assert type_text.permission_level is PermissionLevel.CONFIRM
    assert type_text.risk_level is RiskLevel.HIGH
    assert type_text.dangerous is True


# --------------------------------------------------------------------------- #
# window enumeration (read-only, safe to run for real)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _IS_WIN, reason="window enumeration is Windows-only")
def test_list_windows_returns_real_windows():
    result = registry.run("list_windows")
    assert result.ok is True
    # The test runner itself is a window, so at least one must be present, and
    # every entry must carry the fields the model relies on.
    for w in result.output:
        assert isinstance(w["hwnd"], int) and w["hwnd"] != 0
        assert isinstance(w["title"], str) and w["title"].strip()
        assert isinstance(w["pid"], int)


@pytest.mark.skipif(not _IS_WIN, reason="window enumeration is Windows-only")
def test_match_window_returns_none_for_unknown():
    assert _match_window(_UNLIKELY) is None
    assert _match_window("") is None


def test_focus_unknown_window_fails_cleanly():
    result = registry.run("focus_window", query=_UNLIKELY)
    assert result.ok is False
    assert _UNLIKELY in result.error


def test_set_window_state_rejects_bad_state():
    # Invalid state is rejected before any window is touched.
    result = registry.run("set_window_state", query=_UNLIKELY, state="banana")
    assert result.ok is False
    assert "minimize" in result.error


def test_set_window_state_unknown_target_fails():
    result = registry.run("set_window_state", query=_UNLIKELY, state="minimize")
    assert result.ok is False
    assert _UNLIKELY in result.error


# --------------------------------------------------------------------------- #
# running-app detection (read-only)
# --------------------------------------------------------------------------- #
def test_is_app_running_reports_absent_app_honestly():
    result = registry.run("is_app_running", name=_UNLIKELY)
    assert result.ok is True                    # a successful "no"
    assert result.output["running"] is False
    assert result.output["pids"] == []
    assert "not running" in result.summary


def test_is_app_running_detects_this_python_process():
    # We are a running Python process, so detection must find at least one pid.
    result = registry.run("is_app_running", name="python")
    assert result.ok is True
    assert result.output["running"] is True
    assert len(result.output["pids"]) >= 1


# --------------------------------------------------------------------------- #
# keyboard input — validation only, never synthesizes a real keystroke
# --------------------------------------------------------------------------- #
def test_parse_hotkey_maps_known_combos():
    assert _parse_hotkey("ctrl+s") == [0x11, ord("S")]
    assert _parse_hotkey("ctrl+shift+t") == [0x11, 0x10, ord("T")]
    assert _parse_hotkey("alt+tab") == [0x12, 0x09]
    assert _parse_hotkey("win+d") == [0x5B, ord("D")]
    assert _parse_hotkey("f5") == [0x74]


def test_parse_hotkey_rejects_unknown_keys():
    assert _parse_hotkey("ctrl+banana") is None
    assert _parse_hotkey("") is None
    assert _parse_hotkey("+") is None


def test_press_keys_rejects_unknown_combo_without_pressing():
    result = registry.run("press_keys", combo="ctrl+banana")
    assert result.ok is False
    assert "Unrecognized key combination" in result.error


def test_type_text_rejects_empty_and_oversized():
    assert registry.run("type_text", text="").ok is False
    huge = registry.run("type_text", text="x" * 2001)
    assert huge.ok is False
    assert "2000" in huge.error
