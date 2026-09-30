"""Guard rails: dangerous-command detection and filesystem sandboxing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from jarvis.config import settings


class PathAccessError(Exception):
    """Raised when a path escapes the configured file roots."""


# --------------------------------------------------------------------------- #
# Dangerous command detection (defence in depth for the shell tool)
# --------------------------------------------------------------------------- #
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\brm\s+-rf\s+~",
    r"\bmkfs\b",
    r"\b:\(\)\s*\{",                    # fork bomb
    r"\bdd\s+if=",
    r"\bformat\b\s+[a-zA-Z]:",          # windows format
    r"\bdel\b\s+/[sqf]",                # recursive/force del
    r"\brmdir\b\s+/s",
    r"\bRebuild\b",
    r"Remove-Item.*-Recurse.*-Force.*[A-Za-z]:\\",
    r"\bformat-volume\b",
    r"\bcipher\b\s+/w",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bStop-Computer\b",
    r"\breg\s+delete\b",
    r"\bnet\s+user\b.*\/add",
    r"\bschtasks\b.*\/create",
    r"\bvssadmin\b.*delete",
    r"\bbcdedit\b",
    r"\bdiskpart\b",
    r"\bChoco(latey)?\b.*(uninstall|install)",
    r">\s*/dev/sda",
    r"\bcurl\b.*\|\s*(sh|bash|iex|powershell)",
    r"\bwget\b.*\|\s*(sh|bash)",
    r"Invoke-Expression",
    r"\biex\b\s*\(",
    r"\bStart-Process\b.*-Verb\s+RunAs",
]
_DANGEROUS_RE = re.compile("|".join(f"(?:{p})" for p in _DANGEROUS_PATTERNS), re.IGNORECASE)


def is_dangerous_command(command: str) -> Optional[str]:
    """Return the offending fragment if the command looks destructive, else None."""
    m = _DANGEROUS_RE.search(command or "")
    return m.group(0) if m else None


# --------------------------------------------------------------------------- #
# Filesystem sandbox
# --------------------------------------------------------------------------- #
def within_roots(path: Path, roots: Optional[list[Path]] = None) -> bool:
    roots = roots or settings.file_roots
    try:
        rp = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def resolve_in_roots(path_str: str, roots: Optional[list[Path]] = None) -> Path:
    """Resolve *path_str* and ensure it lives under an allowed root.

    Relative paths are anchored to the first configured root (typically the
    user's home directory).
    """
    roots = roots or settings.file_roots
    if not roots:
        raise PathAccessError("No file roots are configured.")
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = roots[0] / p
    if not within_roots(p, roots):
        allowed = ", ".join(str(r) for r in roots)
        raise PathAccessError(
            f"Path '{p}' is outside the allowed roots ({allowed})."
        )
    return p.resolve()
