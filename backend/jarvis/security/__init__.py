"""Security subsystem: audit logging, secret redaction, dangerous-command
detection, and path sandboxing helpers used across the tool layer."""
from jarvis.security.audit import get_logger, redact, audit
from jarvis.security.guard import (
    is_dangerous_command,
    within_roots,
    resolve_in_roots,
    PathAccessError,
)

__all__ = [
    "get_logger", "redact", "audit",
    "is_dangerous_command", "within_roots", "resolve_in_roots", "PathAccessError",
]
