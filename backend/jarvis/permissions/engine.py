"""The permission engine decides whether a tool call may run automatically,
needs one-time confirmation, or must always be confirmed.

Inputs to a decision:
  * the tool's declared ``permission_level`` and ``risk_level``
  * the active ``permission_mode`` (strict / balanced / trusted)
  * any remembered user decision for that tool (allow/deny)

The engine is intentionally conservative: when levels disagree, the *stricter*
outcome wins, and CRITICAL actions can never be silently auto-approved.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from jarvis.config import settings


class PermissionLevel(str, enum.Enum):
    SAFE = "safe"                       # run automatically
    CONFIRM = "confirm"                 # ask once (rememberable)
    ALWAYS_CONFIRM = "always_confirm"   # ask every time, never remember-allow


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


class Outcome(str, enum.Enum):
    AUTO = "auto"                       # execute now
    CONFIRM = "confirm"                 # ask the user, may remember
    ALWAYS_CONFIRM = "always_confirm"   # ask the user, never remember
    DENY = "deny"                       # blocked by a remembered deny


@dataclass
class Decision:
    outcome: Outcome
    reason: str
    risk: RiskLevel
    permission: PermissionLevel
    rememberable: bool

    @property
    def needs_user(self) -> bool:
        return self.outcome in (Outcome.CONFIRM, Outcome.ALWAYS_CONFIRM)


# For each mode, the highest risk level that may run automatically.
_MODE_AUTO_CEILING = {
    "strict": RiskLevel.LOW,
    "balanced": RiskLevel.LOW,
    "trusted": RiskLevel.MEDIUM,
}


class PermissionEngine:
    def __init__(self) -> None:
        # lazy import to avoid a hard DB dependency at construction
        from jarvis.db.repositories import PermissionRepo
        self._perm_repo = PermissionRepo()

    # ------------------------------------------------------------------ #
    def evaluate(self, tool_name: str, permission: PermissionLevel,
                 risk: RiskLevel, scope: str = "*") -> Decision:
        mode = settings.get("permission_mode", "balanced")

        # 1) remembered explicit deny always wins
        remembered = self._perm_repo.lookup(tool_name, scope)
        if remembered == "deny":
            return Decision(Outcome.DENY, "You previously denied this action.",
                            risk, permission, rememberable=False)

        # 2) CRITICAL is never auto and never rememberable
        if risk == RiskLevel.CRITICAL or permission == PermissionLevel.ALWAYS_CONFIRM:
            return Decision(Outcome.ALWAYS_CONFIRM,
                            "Critical action — explicit approval required every time.",
                            risk, permission, rememberable=False)

        # 3) remembered allow (only for confirm-level, non-critical).
        #    In strict mode we deliberately ignore remembered allows, so every
        #    confirmable action is re-confirmed — this is what makes strict
        #    stricter than balanced (both share the same LOW auto-ceiling).
        if (remembered == "allow" and mode != "strict"
                and permission != PermissionLevel.ALWAYS_CONFIRM):
            return Decision(Outcome.AUTO, "Previously approved for this session/tool.",
                            risk, permission, rememberable=False)

        # 4) SAFE tools within the auto ceiling run automatically
        ceiling = _MODE_AUTO_CEILING.get(mode, RiskLevel.LOW)
        if permission == PermissionLevel.SAFE and _RISK_ORDER[risk] <= _RISK_ORDER[ceiling]:
            return Decision(Outcome.AUTO, f"Safe action (mode={mode}).",
                            risk, permission, rememberable=False)

        # 5) everything else needs confirmation. Under strict mode the decision
        #    is not rememberable, so the user is asked again next time.
        return Decision(
            Outcome.CONFIRM,
            f"{risk.value.capitalize()}-risk action requires your approval "
            f"(mode={mode}).",
            risk, permission, rememberable=(mode != "strict"),
        )

    # ------------------------------------------------------------------ #
    def remember(self, tool_name: str, decision: str, scope: str = "*") -> None:
        if decision not in ("allow", "deny"):
            return
        self._perm_repo.remember(tool_name, decision, scope)

    def forget(self, tool_name: str, scope: str = "*") -> None:
        """Revoke a remembered decision so the tool is confirmed again."""
        self._perm_repo.forget(tool_name, scope)

    def remembered(self) -> list[dict]:
        """Everything the user has standing-approved or standing-denied."""
        return [dict(r) for r in self._perm_repo.list()]
