"""Skill (plugin) system.

A *skill* is a self-contained, user-supplied extension that adds new tools (and
therefore new capabilities) to JARVIS without touching the core. Skills live in
a ``skills/`` directory as a folder containing a ``skill.json`` manifest and a
Python entry module that exposes a ``register(registry)`` function.

Skills are real, executed Python — loaded with :mod:`importlib`, isolated so one
broken skill can't take down the others, and observable via ``/api/skills`` and
the diagnostics panel. See ``docs/PLUGIN_DEVELOPMENT.md`` for the authoring guide.

Security note: skills run with the same privileges as JARVIS itself. Only install
skills you trust. Every tool a skill registers still passes through the same
permission engine as the built-ins, so risky actions remain gated.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jarvis.config import settings
from jarvis.security.audit import get_logger
from jarvis.tools.base import registry

log = get_logger("skills")


@dataclass
class LoadedSkill:
    name: str
    path: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    status: str = "loaded"          # loaded | error | disabled
    tools: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "version": self.version, "description": self.description,
            "author": self.author, "status": self.status, "tools": self.tools,
            "error": self.error, "path": self.path,
        }


class SkillManager:
    """Discovers and loads skills from one or more skill directories."""

    def __init__(self) -> None:
        self.skills: list[LoadedSkill] = []

    # -- discovery ------------------------------------------------------ #
    def directories(self) -> list[Path]:
        dirs = [settings.project_root / "skills", settings.data_dir / "skills"]
        extra = settings.get("skills_dir", "")
        if extra:
            dirs.append(Path(extra))
        # de-dup, keep order
        seen: set[str] = set()
        out: list[Path] = []
        for d in dirs:
            key = str(d.resolve())
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out

    # -- loading -------------------------------------------------------- #
    def load_all(self) -> list[LoadedSkill]:
        self.skills = []
        for base in self.directories():
            if not base.exists() or not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                manifest = child / "skill.json"
                if child.is_dir() and manifest.exists():
                    self._load_one(child, manifest)
        loaded = [s for s in self.skills if s.status == "loaded"]
        log.info("skills: %d loaded, %d failed",
                 len(loaded), len(self.skills) - len(loaded))
        return self.skills

    def _load_one(self, path: Path, manifest_path: Path) -> None:
        name = path.name
        record = LoadedSkill(name=name, path=str(path))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            record.name = manifest.get("name", name)
            record.version = str(manifest.get("version", "0.0.0"))
            record.description = manifest.get("description", "")
            record.author = manifest.get("author", "")
            if manifest.get("enabled") is False:
                record.status = "disabled"
                self.skills.append(record)
                log.info("skill '%s' disabled via manifest", record.name)
                return

            entry = manifest.get("entry", "skill.py")
            entry_path = path / entry
            if not entry_path.exists():
                raise FileNotFoundError(f"entry file '{entry}' not found")

            # Capture exactly the tools THIS skill registers during import. We
            # instrument registry.register rather than diffing names before/after,
            # so re-loading a skill (whose tools are already registered) still
            # attributes them correctly instead of reporting zero.
            registered: list[str] = []
            original_register = registry.register

            def _capture(t, _orig=original_register):
                registered.append(t.name)
                return _orig(t)

            registry.register = _capture  # type: ignore[method-assign]
            try:
                module = self._import_module(record.name, entry_path)
                if hasattr(module, "register"):
                    module.register(registry)
            finally:
                registry.register = original_register  # type: ignore[method-assign]

            record.tools = sorted(set(registered))
            record.status = "loaded"
            log.info("loaded skill '%s' v%s (+%d tools)",
                     record.name, record.version, len(record.tools))
        except Exception as exc:  # noqa: BLE001 - never let one skill break the rest
            record.status = "error"
            record.error = f"{type(exc).__name__}: {exc}"
            log.warning("failed to load skill '%s': %s", name, record.error)
        self.skills.append(record)

    @staticmethod
    def _import_module(name: str, entry_path: Path):
        mod_name = f"jarvis_skill_{name}".replace("-", "_").replace(" ", "_")
        spec = importlib.util.spec_from_file_location(mod_name, entry_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import skill entry {entry_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    # -- reporting ------------------------------------------------------ #
    def describe_all(self) -> list[dict[str, Any]]:
        return [s.describe() for s in self.skills]

    @property
    def loaded_count(self) -> int:
        return sum(1 for s in self.skills if s.status == "loaded")


_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager
