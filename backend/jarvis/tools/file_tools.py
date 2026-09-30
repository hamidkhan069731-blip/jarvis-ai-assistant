"""File-system intelligence tools, sandboxed to the configured file roots.

Read/search/analyse operations are SAFE. Anything that mutates the filesystem
(write, move, copy, delete, organize, compress) is gated by the permission
engine and, for deletes, treated as HIGH risk.
"""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.security.guard import PathAccessError, resolve_in_roots
from jarvis.tools.base import ToolResult, tool

# Category buckets used by analyze/organize.
_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".ppt",
                  ".pptx", ".xls", ".xlsx", ".csv"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Installers": {".exe", ".msi", ".dmg", ".deb", ".rpm", ".appx"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs",
             ".html", ".css", ".json", ".xml", ".sh", ".ps1"},
}


def _category_for(suffix: str) -> str:
    s = suffix.lower()
    for cat, exts in _CATEGORIES.items():
        if s in exts:
            return cat
    return "Other"


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


# --------------------------------------------------------------------------- #
@tool(
    name="list_directory",
    description="List files and folders in a directory (non-recursive).",
    category="files",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path. Defaults to home."}},
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def list_directory(path: str = ".") -> ToolResult:
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not target.is_dir():
        return ToolResult.failure(f"Not a directory: {target}")
    entries = []
    for child in sorted(target.iterdir()):
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "size_h": _human(stat.st_size) if child.is_file() else "",
            })
        except OSError:
            continue
    return ToolResult.success(
        {"path": str(target), "entries": entries},
        summary=f"{len(entries)} items in {target}.",
    )


@tool(
    name="search_files",
    description="Recursively search for files by name/extension under a directory. "
                "Great for 'find my PDFs', 'find files named invoice'.",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Substring to match in the filename."},
            "extension": {"type": "string", "description": "Optional extension filter, e.g. 'pdf'."},
            "path": {"type": "string", "description": "Root to search. Defaults to home."},
            "limit": {"type": "integer", "default": 100, "maximum": 500},
        },
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def search_files(query: str = "", extension: str = "", path: str = ".",
                 limit: int = 100) -> ToolResult:
    try:
        root = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    ext = extension.lower().lstrip(".")
    q = query.lower()
    matches: list[dict[str, Any]] = []
    try:
        for p in root.rglob("*"):
            if len(matches) >= limit:
                break
            if not p.is_file():
                continue
            if ext and p.suffix.lower().lstrip(".") != ext:
                continue
            if q and q not in p.name.lower():
                continue
            try:
                matches.append({"path": str(p), "name": p.name,
                                "size_h": _human(p.stat().st_size)})
            except OSError:
                continue
    except OSError as exc:
        return ToolResult.failure(f"Search error: {exc}")
    return ToolResult.success(
        matches,
        summary=f"Found {len(matches)} file(s)" + (f" matching '{query}'." if query else "."),
    )


@tool(
    name="read_text_file",
    description="Read the contents of a text file (max ~200 KB). For code, notes, logs, config.",
    category="files",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def read_text_file(path: str) -> ToolResult:
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not target.is_file():
        return ToolResult.failure(f"Not a file: {target}")
    if target.stat().st_size > 200_000:
        return ToolResult.failure("File is larger than 200 KB; refuse to load fully.")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.failure(f"Read error: {exc}")
    return ToolResult.success({"path": str(target), "content": text},
                              summary=f"Read {target.name} ({len(text)} chars).")


@tool(
    name="write_text_file",
    description="Create or overwrite a text file with the given content. Requires confirmation.",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def write_text_file(path: str, content: str) -> ToolResult:
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult.failure(f"Write error: {exc}")
    verb = "Overwrote" if existed else "Created"
    return ToolResult.success({"path": str(target), "bytes": len(content)},
                              summary=f"{verb} {target} ({len(content)} bytes).")


@tool(
    name="create_folder",
    description="Create a new folder (and any missing parents).",
    category="files",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def create_folder(path: str) -> ToolResult:
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult.failure(f"Could not create folder: {exc}")
    return ToolResult.success({"path": str(target)}, summary=f"Folder ready: {target}.")


@tool(
    name="move_path",
    description="Move or rename a file/folder. Requires confirmation.",
    category="files",
    parameters={
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def move_path(source: str, destination: str) -> ToolResult:
    try:
        src = resolve_in_roots(source)
        dst = resolve_in_roots(destination)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not src.exists():
        return ToolResult.failure(f"Source does not exist: {src}")
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as exc:
        return ToolResult.failure(f"Move failed: {exc}")
    return ToolResult.success({"source": str(src), "destination": str(dst)},
                              summary=f"Moved to {dst}.")


@tool(
    name="delete_path",
    description="Delete a file or folder. HIGH risk — always requires confirmation.",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean", "default": False,
                          "description": "Required True to delete a non-empty folder."},
        },
        "required": ["path"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.HIGH,
    dangerous=True,
)
def delete_path(path: str, recursive: bool = False) -> ToolResult:
    try:
        target = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not target.exists():
        return ToolResult.failure(f"Nothing to delete at {target}.")
    try:
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()  # only succeeds if empty
        else:
            target.unlink()
    except OSError as exc:
        return ToolResult.failure(f"Delete failed: {exc}")
    return ToolResult.success({"path": str(target)}, summary=f"Deleted {target}.")


# --------------------------------------------------------------------------- #
@tool(
    name="analyze_folder",
    description="Analyse a folder: count files, total size, and how they'd group by type. "
                "Use this BEFORE organize_folder to preview the plan for the user.",
    category="files",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def analyze_folder(path: str) -> ToolResult:
    try:
        root = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not root.is_dir():
        return ToolResult.failure(f"Not a directory: {root}")
    groups: dict[str, int] = defaultdict(int)
    total_size = 0
    files = 0
    for child in root.iterdir():
        if child.is_file():
            files += 1
            groups[_category_for(child.suffix)] += 1
            try:
                total_size += child.stat().st_size
            except OSError:
                pass
    plan = ", ".join(f"{v} → {k}" for k, v in sorted(groups.items(), key=lambda x: -x[1]))
    return ToolResult.success(
        {"path": str(root), "file_count": files, "total_size_h": _human(total_size),
         "groups": dict(groups)},
        summary=(f"{files} files ({_human(total_size)}) in {root.name}. "
                 f"Would group into: {plan}." if files else f"{root.name} has no loose files."),
    )


@tool(
    name="organize_folder",
    description="Organize loose files in a folder into subfolders by type "
                "(Documents, Images, Videos, Installers, Archives, ...). Requires confirmation.",
    category="files",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
    dangerous=True,
)
def organize_folder(path: str) -> ToolResult:
    try:
        root = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not root.is_dir():
        return ToolResult.failure(f"Not a directory: {root}")
    moved = 0
    errors = 0
    for child in list(root.iterdir()):
        if not child.is_file():
            continue
        cat = _category_for(child.suffix)
        dest_dir = root / cat
        try:
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(child), str(dest_dir / child.name))
            moved += 1
        except (OSError, shutil.Error):
            errors += 1
    summary = f"Organized {moved} file(s) in {root.name}."
    if errors:
        summary += f" {errors} could not be moved."
    return ToolResult.success({"moved": moved, "errors": errors}, summary=summary)


@tool(
    name="find_large_files",
    description="Find the largest files under a directory (e.g. 'what's eating my disk').",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "min_mb": {"type": "number", "default": 100},
            "limit": {"type": "integer", "default": 20, "maximum": 100},
        },
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def find_large_files(path: str = ".", min_mb: float = 100, limit: int = 20) -> ToolResult:
    try:
        root = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    threshold = int(min_mb * 1_000_000)
    found: list[dict[str, Any]] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size >= threshold:
                found.append({"path": str(p), "bytes": size, "size_h": _human(size)})
    except OSError as exc:
        return ToolResult.failure(f"Scan error: {exc}")
    found.sort(key=lambda x: x["bytes"], reverse=True)
    found = found[:limit]
    lines = [f"{f['size_h']}  {f['path']}" for f in found]
    return ToolResult.success(found, summary=(
        f"{len(found)} file(s) over {min_mb} MB:\n" + "\n".join(lines) if found
        else f"No files over {min_mb} MB under {root}."))


@tool(
    name="find_duplicate_files",
    description="Find duplicate files under a directory by content hash.",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer", "default": 50, "maximum": 200},
        },
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def find_duplicate_files(path: str = ".", limit: int = 50) -> ToolResult:
    try:
        root = resolve_in_roots(path)
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_file():
            try:
                by_size[p.stat().st_size].append(p)
            except OSError:
                continue
    dupes: list[dict[str, Any]] = []
    for size, paths in by_size.items():
        if len(paths) < 2 or size == 0:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            h = _quick_hash(p)
            if h:
                by_hash[h].append(str(p))
        for h, members in by_hash.items():
            if len(members) > 1:
                dupes.append({"hash": h, "size_h": _human(size), "files": members})
            if len(dupes) >= limit:
                break
    return ToolResult.success(dupes, summary=(
        f"Found {len(dupes)} set(s) of duplicate files." if dupes
        else "No duplicates found."))


def _quick_hash(p: Path) -> str | None:
    """Hash size + first & last 64 KB — fast and collision-safe enough for dedupe."""
    try:
        h = hashlib.sha1()
        with p.open("rb") as f:
            h.update(f.read(65536))
            f.seek(0, 2)
            if f.tell() > 131072:
                f.seek(-65536, 2)
                h.update(f.read(65536))
        return h.hexdigest()
    except OSError:
        return None


@tool(
    name="compress_to_zip",
    description="Compress a file or folder into a .zip archive. Requires confirmation.",
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "destination": {"type": "string", "description": "Output .zip path (optional)."},
        },
        "required": ["source"],
    },
    permission_level=PermissionLevel.CONFIRM,
    risk_level=RiskLevel.MEDIUM,
)
def compress_to_zip(source: str, destination: str = "") -> ToolResult:
    try:
        src = resolve_in_roots(source)
        dst = resolve_in_roots(destination) if destination else src.with_suffix(".zip")
    except PathAccessError as exc:
        return ToolResult.failure(str(exc))
    if not src.exists():
        return ToolResult.failure(f"Source does not exist: {src}")
    try:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            if src.is_file():
                zf.write(src, src.name)
            else:
                for p in src.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(src.parent))
    except (OSError, zipfile.BadZipFile) as exc:
        return ToolResult.failure(f"Compression failed: {exc}")
    size = _human(dst.stat().st_size)
    return ToolResult.success({"archive": str(dst), "size_h": size},
                              summary=f"Created {dst.name} ({size}).")
