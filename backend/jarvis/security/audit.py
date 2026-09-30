"""Structured logging + audit trail with secret redaction.

Everything that matters (commands, tool calls, permission decisions, errors)
is recorded to:
  * a rotating log file under data/logs/
  * the console (respecting JARVIS_LOG_LEVEL)
  * the `audit_log` DB table (for the in-UI activity viewer)

All three paths pass through :func:`redact` so API keys, tokens and obvious
secrets never hit disk in plaintext.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from jarvis.config import settings

# --- redaction ------------------------------------------------------------- #
_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{8,})"),
    re.compile(r"(sk-ant-[A-Za-z0-9\-_]{8,})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"(AKIA[0-9A-Z]{12,})"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)"
               r"\s*[:=]\s*['\"]?([^\s'\"]{6,})"),
]


def redact(value: Any) -> Any:
    """Recursively mask secrets in strings / dicts / lists."""
    if isinstance(value, dict):
        return {k: ("***REDACTED***"
                    if re.search(r"(?i)(key|token|secret|password|passwd|auth)", str(k))
                    else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        out = value
        for pat in _SECRET_PATTERNS:
            out = pat.sub(lambda m: m.group(0).replace(m.groups()[-1], "***REDACTED***"), out)
        return out
    return value


# --- logger factory -------------------------------------------------------- #
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger("jarvis")
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        fileh = RotatingFileHandler(
            settings.logs_dir / "jarvis.log",
            maxBytes=2_000_000, backupCount=5, encoding="utf-8",
        )
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        pass  # console-only if the file can't be opened


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(f"jarvis.{name}")


# --- audit (DB + log) ------------------------------------------------------ #
def audit(event: str, level: str = "INFO", **data: Any) -> None:
    """Write an audit record to both the log and the DB (redacted)."""
    clean = redact(data)
    logger = get_logger("audit")
    logger.log(getattr(logging, level, logging.INFO), "%s %s", event, clean)
    try:
        from jarvis.db.repositories import AuditRepo
        AuditRepo().log(level, event, clean)
    except Exception:  # never let auditing crash the caller
        logger.debug("audit DB write failed", exc_info=True)
