"""Permission engine — the safety core. These tests pin the exact decision matrix
the master spec requires: SAFE auto-runs, risky actions confirm, CRITICAL always
confirms and can never be remembered-away, and an explicit deny is honoured.
"""
from __future__ import annotations

import pytest

from jarvis.permissions.engine import (
    Outcome, PermissionEngine, PermissionLevel, RiskLevel,
)
from jarvis.db.repositories import SettingsRepo


@pytest.fixture
def engine() -> PermissionEngine:
    return PermissionEngine()


def _set_mode(mode: str) -> None:
    SettingsRepo().set("permission_mode", mode)


# --------------------------------------------------------------------------- #
def test_safe_low_auto_runs(engine):
    d = engine.evaluate("get_time", PermissionLevel.SAFE, RiskLevel.LOW)
    assert d.outcome is Outcome.AUTO
    assert not d.needs_user


def test_confirm_high_requires_user_and_is_rememberable(engine):
    d = engine.evaluate("run_shell", PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert d.outcome is Outcome.CONFIRM
    assert d.needs_user
    assert d.rememberable is True


def test_critical_is_always_confirm_and_not_rememberable(engine):
    # even a nominally SAFE permission is overridden to ALWAYS_CONFIRM by CRITICAL risk
    d = engine.evaluate("wipe_disk", PermissionLevel.SAFE, RiskLevel.CRITICAL)
    assert d.outcome is Outcome.ALWAYS_CONFIRM
    assert d.needs_user
    assert d.rememberable is False


def test_always_confirm_permission_never_auto(engine):
    d = engine.evaluate("send_money", PermissionLevel.ALWAYS_CONFIRM, RiskLevel.MEDIUM)
    assert d.outcome is Outcome.ALWAYS_CONFIRM
    assert d.rememberable is False


def test_remembered_deny_blocks(engine):
    engine.remember("open_application", "deny")
    d = engine.evaluate("open_application", PermissionLevel.SAFE, RiskLevel.LOW)
    assert d.outcome is Outcome.DENY
    assert not d.rememberable


def test_remembered_allow_auto_runs_confirm_tool(engine):
    engine.remember("organize_folder", "allow")
    d = engine.evaluate("organize_folder", PermissionLevel.CONFIRM, RiskLevel.MEDIUM)
    assert d.outcome is Outcome.AUTO


def test_remembered_allow_cannot_override_critical(engine):
    engine.remember("format_drive", "allow")
    d = engine.evaluate("format_drive", PermissionLevel.ALWAYS_CONFIRM, RiskLevel.CRITICAL)
    # allow must NOT downgrade a critical/always-confirm action
    assert d.outcome is Outcome.ALWAYS_CONFIRM


def test_balanced_mode_medium_needs_confirmation(engine):
    _set_mode("balanced")
    d = engine.evaluate("close_application", PermissionLevel.SAFE, RiskLevel.MEDIUM)
    assert d.outcome is Outcome.CONFIRM


def test_trusted_mode_allows_medium_auto(engine):
    _set_mode("trusted")
    d = engine.evaluate("close_application", PermissionLevel.SAFE, RiskLevel.MEDIUM)
    assert d.outcome is Outcome.AUTO


def test_strict_mode_only_low_auto(engine):
    _set_mode("strict")
    assert engine.evaluate("t", PermissionLevel.SAFE, RiskLevel.LOW).outcome is Outcome.AUTO
    assert engine.evaluate("t", PermissionLevel.SAFE, RiskLevel.MEDIUM).outcome is Outcome.CONFIRM


def test_strict_mode_ignores_remembered_allow(engine):
    # A remembered "allow" auto-runs under balanced, but strict re-confirms every time.
    engine.remember("organize_folder", "allow")
    _set_mode("balanced")
    assert engine.evaluate("organize_folder", PermissionLevel.CONFIRM,
                           RiskLevel.MEDIUM).outcome is Outcome.AUTO
    _set_mode("strict")
    assert engine.evaluate("organize_folder", PermissionLevel.CONFIRM,
                           RiskLevel.MEDIUM).outcome is Outcome.CONFIRM


def test_strict_mode_confirmations_are_not_rememberable(engine):
    _set_mode("strict")
    d = engine.evaluate("run_shell", PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert d.outcome is Outcome.CONFIRM
    assert d.rememberable is False


def test_strict_mode_still_honours_remembered_deny(engine):
    # Denials are safety-positive and must be honoured in every mode.
    engine.remember("open_application", "deny")
    _set_mode("strict")
    assert engine.evaluate("open_application", PermissionLevel.SAFE,
                           RiskLevel.LOW).outcome is Outcome.DENY
