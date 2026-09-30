"""Power tools (Phase 3): PC power control + Wake-on-LAN.

SAFETY: these tests must NEVER invoke a real power action. The test machine is
Windows, so calling ``power_control(action="shutdown")`` would genuinely shut it
down. Every test here therefore exercises only:
  * argument validation / canonicalization (rejected before any command runs),
  * the argument-aware permission mapping (pure function, no side effect),
  * the magic-packet builder (pure bytes, no socket).
No test issues a valid power action or sends a real WoL packet.
"""
from __future__ import annotations

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import registry
from jarvis.tools.power_tools import (
    _canonical, _magic_packet, _normalize_mac, _power_permission,
    power_control, wake_on_lan,
)


# --------------------------------------------------------------------------- #
# registration & metadata
# --------------------------------------------------------------------------- #
def test_power_tools_are_registered():
    assert registry.get("power_control") is not None
    assert registry.get("wake_on_lan") is not None
    assert power_control.category == "system"
    assert power_control.dangerous is True
    # WoL merely emits a packet: not gated behind confirmation.
    assert wake_on_lan.permission_level is PermissionLevel.SAFE
    assert wake_on_lan.risk_level is RiskLevel.LOW


# --------------------------------------------------------------------------- #
# action canonicalization
# --------------------------------------------------------------------------- #
def test_canonical_maps_aliases():
    assert _canonical("shutdown") == "shutdown"
    assert _canonical("power off") == "shutdown"
    assert _canonical("reboot") == "restart"
    assert _canonical("log off") == "sign_out"
    assert _canonical("suspend") == "sleep"
    assert _canonical("ABORT") == "cancel"


def test_canonical_rejects_unknown():
    assert _canonical("banana") is None
    assert _canonical("") is None


def test_invalid_action_fails_without_executing():
    # Goes through the registry (validate + execute) but must bail out at the
    # unknown-action check BEFORE any subprocess is spawned.
    result = registry.run("power_control", action="banana")
    assert result.ok is False
    assert "Unknown power action" in result.error


# --------------------------------------------------------------------------- #
# argument-aware permissions (destructive actions must be HIGH)
# --------------------------------------------------------------------------- #
def test_permission_scales_with_action():
    assert _power_permission("shutdown") == (PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert _power_permission("restart") == (PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert _power_permission("sign_out") == (PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert _power_permission("sleep") == (PermissionLevel.CONFIRM, RiskLevel.MEDIUM)
    assert _power_permission("cancel") == (PermissionLevel.SAFE, RiskLevel.LOW)
    # unknown actions default to the most cautious levels
    assert _power_permission("banana") == (PermissionLevel.CONFIRM, RiskLevel.HIGH)


def test_effective_levels_use_dynamic_permission():
    # The tool wires _power_permission as its dynamic_permission hook.
    assert power_control.effective_levels(action="shutdown") == (
        PermissionLevel.CONFIRM, RiskLevel.HIGH)
    assert power_control.effective_levels(action="cancel") == (
        PermissionLevel.SAFE, RiskLevel.LOW)


# --------------------------------------------------------------------------- #
# Wake-on-LAN packet building (no socket I/O)
# --------------------------------------------------------------------------- #
def test_normalize_mac_accepts_common_formats():
    expected = bytes.fromhex("1a2b3c4d5e6f")
    assert _normalize_mac("1A:2B:3C:4D:5E:6F") == expected
    assert _normalize_mac("1a-2b-3c-4d-5e-6f") == expected
    assert _normalize_mac("1a2b3c4d5e6f") == expected


def test_normalize_mac_rejects_bad_input():
    assert _normalize_mac("nonsense") is None
    assert _normalize_mac("1A:2B:3C:4D:5E") is None      # too short
    assert _normalize_mac("") is None


def test_magic_packet_shape():
    pkt = _magic_packet("1A:2B:3C:4D:5E:6F")
    assert pkt is not None
    assert len(pkt) == 102                    # 6 sync bytes + 16 * 6 MAC bytes
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("1a2b3c4d5e6f")
    assert pkt[6:] == bytes.fromhex("1a2b3c4d5e6f") * 16


def test_magic_packet_invalid_mac_is_none():
    assert _magic_packet("bogus") is None


def test_wake_on_lan_rejects_bad_mac_without_sending():
    # Invalid MAC -> failure returned before any socket is opened.
    result = registry.run("wake_on_lan", mac="not-a-mac")
    assert result.ok is False
    assert "Invalid MAC" in result.error
