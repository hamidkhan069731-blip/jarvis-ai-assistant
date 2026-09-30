"""Shared pytest fixtures and test-environment bootstrap.

CRITICAL: this module configures the environment *before* any ``jarvis`` import
happens, so the whole test run is redirected to a throwaway data directory and a
sandboxed file root. Nothing here touches the user's real ``data/`` or home.

pytest imports ``conftest.py`` before collecting the test modules, and the test
modules are what first import ``jarvis`` — so setting the environment at module
top level here is guaranteed to win.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# 1) make the backend package importable (tests live in backend/tests/)
# --------------------------------------------------------------------------- #
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# --------------------------------------------------------------------------- #
# 2) redirect all state into an isolated temp tree, force deterministic config
# --------------------------------------------------------------------------- #
_TMP = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
(_TMP / "data").mkdir(parents=True, exist_ok=True)
(_TMP / "sandbox").mkdir(parents=True, exist_ok=True)

os.environ["JARVIS_DATA_DIR"] = str(_TMP / "data")
os.environ["JARVIS_FILE_ROOTS"] = str(_TMP / "sandbox")
os.environ["JARVIS_AI_PROVIDER"] = "local"        # deterministic offline brain
os.environ["JARVIS_PERMISSION_MODE"] = "balanced"
os.environ["JARVIS_ENABLE_SHELL"] = "false"
os.environ["JARVIS_MEMORY_ENABLED"] = "true"
os.environ["JARVIS_PROACTIVE_ENABLED"] = "false"  # keep the monitor quiet in tests
# Force cloud keys empty so provider resolution can't reach the network and the
# redaction assertions have a known baseline. Set (not popped) so the .env loader
# — which skips keys already present — cannot re-populate them.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""


@atexit.register
def _cleanup_tmp() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


import pytest  # noqa: E402  (must come after env setup)


# --------------------------------------------------------------------------- #
# 3) one-time subsystem bootstrap: register built-in tools, wire live settings,
#    load skills — mirrors what core.startup() does, minus the async engines.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def _bootstrap():
    from jarvis.tools.base import load_builtin_tools
    from jarvis.db.repositories import bind_settings_override
    from jarvis.skills import get_skill_manager

    load_builtin_tools()
    bind_settings_override()
    get_skill_manager().load_all()
    yield


# --------------------------------------------------------------------------- #
# 4) per-test isolation: wipe the tables whose contents would otherwise leak
#    decisions/state from one test into the next.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_db():
    from jarvis.db.database import get_db

    db = get_db()
    for table in ("permissions", "memory", "settings", "tool_history",
                  "audit_log", "messages", "conversations", "tasks"):
        db.execute(f"DELETE FROM {table}")
    yield


# --------------------------------------------------------------------------- #
# Convenience fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def sandbox() -> Path:
    """The writable file root that tools are sandboxed to during tests."""
    return _TMP / "sandbox"


@pytest.fixture
def data_dir() -> Path:
    return _TMP / "data"
