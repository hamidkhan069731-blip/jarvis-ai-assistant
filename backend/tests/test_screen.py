"""Screen understanding (Phase 4): OCR, text location, and vision gating.

OCR here is genuinely local (the Windows 10/11 ``Windows.Media.Ocr`` engine), so
the read paths are exercised for real against the actual screen — that is the
only way to prove the capability is not a stub.

SAFETY: no test may make a paid vision API call. `describe_screen` is tested
only through its NOT-CONFIGURED gate, with the key forced empty.
"""
from __future__ import annotations

import sys

import pytest

from jarvis.config import settings
from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import registry
from jarvis.tools.screen_tools import (
    _SCRIPT, _run_ocr, _vision_status, describe_screen, find_on_screen, read_screen,
)

_IS_WIN = sys.platform == "win32"
pytestmark = pytest.mark.skipif(not _IS_WIN, reason="screen OCR is Windows-only")


# --------------------------------------------------------------------------- #
# registration & gating
# --------------------------------------------------------------------------- #
def test_screen_tools_are_registered():
    for name in ("read_screen", "find_on_screen", "describe_screen"):
        assert registry.get(name) is not None, name


def test_screen_reading_is_permission_gated():
    # Reading the screen can expose passwords and private messages, so it is
    # never silently automatic.
    assert read_screen.permission_level is PermissionLevel.CONFIRM
    assert read_screen.dangerous is True
    assert find_on_screen.dangerous is True
    # Vision additionally ships pixels to a third party.
    assert describe_screen.risk_level is RiskLevel.HIGH
    assert describe_screen.dangerous is True


def test_ocr_helper_script_ships_with_the_package():
    assert _SCRIPT.exists(), f"missing OCR helper: {_SCRIPT}"


# --------------------------------------------------------------------------- #
# real OCR against the real screen
# --------------------------------------------------------------------------- #
def test_ocr_reads_the_real_screen():
    data = _run_ocr()
    assert data.get("ok") is True, data.get("error")
    assert isinstance(data.get("text"), str)
    assert data.get("width", 0) > 0 and data.get("height", 0) > 0
    # Every word must carry a usable bounding box.
    for line in data.get("lines", []):
        for w in line.get("words", []):
            assert all(k in w for k in ("text", "x", "y", "w", "h"))
            assert w["w"] > 0 and w["h"] > 0


def test_read_screen_returns_text_through_the_registry():
    result = registry.run("read_screen")
    assert result.ok is True, result.error
    assert "text" in result.output
    assert isinstance(result.output["lines"], list)


def test_read_screen_rejects_unknown_window():
    result = registry.run("read_screen", window="zzz_no_such_window_zzz")
    assert result.ok is False
    assert "zzz_no_such_window_zzz" in result.error


def test_find_on_screen_requires_text():
    result = registry.run("find_on_screen", text="   ")
    assert result.ok is False
    assert "text to search" in result.error


def test_find_on_screen_reports_absence_honestly():
    # A string that will not be on screen must be reported as not found rather
    # than invented.
    result = registry.run("find_on_screen", text="zzq_not_on_screen_zzq")
    assert result.ok is True
    assert result.output["found"] is False
    assert result.output["matches"] == []


def test_find_on_screen_locates_text_that_is_present():
    # Whatever OCR just read from the real screen must be locatable by the same
    # search, with a plausible on-screen coordinate.
    data = _run_ocr()
    words = [w for line in data.get("lines", []) for w in line.get("words", [])
             if len(w.get("text", "")) >= 4 and w["text"].isalpha()]
    if not words:
        pytest.skip("no suitable text on screen to search for")
    needle = words[0]["text"]
    result = registry.run("find_on_screen", text=needle)
    assert result.ok is True
    assert result.output["found"] is True, f"OCR saw {needle!r} but search missed it"
    hit = result.output["matches"][0]
    assert 0 <= hit["x"] <= data["width"] * 2      # generous: multi-monitor offsets
    assert isinstance(hit["y"], int)


# --------------------------------------------------------------------------- #
# vision gating — must never invent a description, never call the API here
# --------------------------------------------------------------------------- #
def test_vision_reports_not_configured_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    configured, detail = _vision_status()
    assert configured is False
    assert "NOT CONFIGURED" in detail
    # It must point the user at the offline alternative rather than dead-end.
    assert "read the screen" in detail


def test_describe_screen_fails_loudly_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    result = registry.run("describe_screen")
    assert result.ok is False
    assert "NOT CONFIGURED" in result.error
    # Crucially: no fabricated description of the screen.
    assert result.output is None
