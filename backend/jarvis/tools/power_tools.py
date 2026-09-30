"""Power tools: real PC power-state control (sleep, restart, shutdown, …) and
Wake-on-LAN.

Everything here performs a genuine OS action on Windows via the built-in
``shutdown`` command and ``powrprof.dll``; Wake-on-LAN sends a real magic packet
over UDP. There is no simulation. On non-Windows hosts the power actions report
that they are unavailable rather than pretending to succeed (project rule §43).

Every power action is *dangerous* and permission-gated: shutting a machine down
or signing out can lose unsaved work, so it always routes through the approval
flow (see :mod:`jarvis.permissions.engine`). Nothing here bypasses that.
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
from typing import Any, Optional

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool

_IS_WIN = sys.platform == "win32"

# action -> (argv, human summary). `sleep` uses powrprof; the rest use shutdown.
_ACTIONS: dict[str, tuple[list[str], str]] = {
    "shutdown":  (["shutdown", "/s", "/t", "0"], "Shutting down the computer."),
    "restart":   (["shutdown", "/r", "/t", "0"], "Restarting the computer."),
    "sign_out":  (["shutdown", "/l"],            "Signing out the current user."),
    "hibernate": (["shutdown", "/h"],            "Hibernating the computer."),
    "cancel":    (["shutdown", "/a"],            "Cancelled the pending shutdown."),
    # sleep/suspend: SetSuspendState(Hibernate=0, ForceCritical=1, DisableWake=0)
    "sleep":     (["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                  "Putting the computer to sleep."),
}

# Roman-Urdu / synonym aliases mapped to canonical actions.
_ALIASES = {
    "power_off": "shutdown", "poweroff": "shutdown", "turn_off": "shutdown",
    "reboot": "restart",
    "logoff": "sign_out", "logout": "sign_out", "log_off": "sign_out",
    "signout": "sign_out",
    "suspend": "sleep", "standby": "sleep",
    "abort": "cancel",
}

# Per-action honest permission/risk. Aborting a shutdown is benign; everything
# else is at least MEDIUM, and the destructive-to-unsaved-work ones are HIGH.
_LEVELS: dict[str, tuple[PermissionLevel, RiskLevel]] = {
    "shutdown":  (PermissionLevel.CONFIRM, RiskLevel.HIGH),
    "restart":   (PermissionLevel.CONFIRM, RiskLevel.HIGH),
    "sign_out":  (PermissionLevel.CONFIRM, RiskLevel.HIGH),
    "hibernate": (PermissionLevel.CONFIRM, RiskLevel.MEDIUM),
    "sleep":     (PermissionLevel.CONFIRM, RiskLevel.MEDIUM),
    "cancel":    (PermissionLevel.SAFE,    RiskLevel.LOW),
}


def _canonical(action: str) -> Optional[str]:
    a = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
    a = _ALIASES.get(a, a)
    return a if a in _ACTIONS else None


def _power_permission(action: str = "", **_: Any):
    """Argument-aware permission: the risk depends on which power action."""
    canon = _canonical(action)
    return _LEVELS.get(canon, (PermissionLevel.CONFIRM, RiskLevel.HIGH))


@tool(
    name="power_control",
    description=(
        "Control the computer's power state on Windows: 'sleep', 'restart', "
        "'shutdown', 'hibernate', 'sign_out', or 'cancel' (abort a pending "
        "shutdown). Every action needs confirmation. Note: 'sleep' uses the "
        "system suspend call — if hibernation is enabled the machine may "
        "hibernate instead of sleeping (a Windows/hardware behaviour, not a "
        "JARVIS choice)."
    ),
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["sleep", "restart", "shutdown", "hibernate",
                         "sign_out", "cancel"],
                "description": "The power action to perform.",
            }
        },
        "required": ["action"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
    dynamic_permission=_power_permission,
)
def power_control(action: str) -> ToolResult:
    canon = _canonical(action)
    if canon is None:
        return ToolResult.failure(
            f"Unknown power action '{action}'. Use one of: "
            "sleep, restart, shutdown, hibernate, sign_out, cancel.")
    if not _IS_WIN:
        return ToolResult.failure(
            "PC power control is only available on Windows.")

    argv, summary = _ACTIONS[canon]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return ToolResult.failure(f"System command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        # A blocking suspend can legitimately not return promptly; treat the
        # request as issued rather than claiming a false failure.
        return ToolResult.success(summary=summary, action=canon, issued=True)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return ToolResult.failure(
            f"Power command failed (exit {proc.returncode}): {detail or 'no detail'}")
    return ToolResult.success(summary=summary, action=canon, issued=True)


# --------------------------------------------------------------------------- #
# Wake-on-LAN
# --------------------------------------------------------------------------- #
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]?){5}[0-9A-Fa-f]{2}$")


def _normalize_mac(mac: str) -> Optional[bytes]:
    """Return the 6 raw MAC bytes, or None if *mac* isn't a valid address."""
    if not mac or not _MAC_RE.match(mac.strip()):
        return None
    hexstr = re.sub(r"[:\-]", "", mac.strip())
    try:
        return bytes.fromhex(hexstr)
    except ValueError:
        return None


def _magic_packet(mac: str) -> Optional[bytes]:
    """Build the 102-byte Wake-on-LAN magic packet for *mac* (6×0xFF + MAC×16)."""
    raw = _normalize_mac(mac)
    if raw is None:
        return None
    return b"\xff" * 6 + raw * 16


@tool(
    name="wake_on_lan",
    description=(
        "Send a Wake-on-LAN magic packet to power on a device you own by its "
        "MAC address. The target must have WoL enabled in its BIOS/network "
        "adapter. Sends over the local network; it cannot wake a device that is "
        "fully powered off at the wall or unreachable."
    ),
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "mac": {"type": "string",
                    "description": "Target MAC address, e.g. '1A:2B:3C:4D:5E:6F'."},
            "broadcast": {"type": "string",
                          "description": "Broadcast address (default 255.255.255.255)."},
            "port": {"type": "integer", "description": "UDP port (default 9)."},
        },
        "required": ["mac"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def wake_on_lan(mac: str, broadcast: str = "255.255.255.255",
                port: int = 9) -> ToolResult:
    packet = _magic_packet(mac)
    if packet is None:
        return ToolResult.failure(
            f"Invalid MAC address: '{mac}'. Expected 6 hex octets, "
            "e.g. 1A:2B:3C:4D:5E:6F.")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(5)
            sock.sendto(packet, (broadcast, int(port)))
    except OSError as exc:
        return ToolResult.failure(f"Could not send WoL packet: {exc}")
    return ToolResult.success(
        summary=f"Sent Wake-on-LAN packet to {mac} via {broadcast}:{port}.",
        mac=mac, broadcast=broadcast, port=int(port))
