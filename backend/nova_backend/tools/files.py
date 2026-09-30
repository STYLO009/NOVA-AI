"""NOVA backend — file and folder tools."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from ..config import settings
from . import tool

MAX_SEARCH_RESULTS = 500
MAX_LIST_ENTRIES = 300

EXT_COLORS = {
    ".tsx": "#61dafb",
    ".ts": "#3178c6",
    ".js": "#f7df1e",
    ".jsx": "#61dafb",
    ".json": "#d9a04f",
    ".py": "#58a6d8",
    ".md": "#4d9bd9",
    ".mdx": "#4d9bd9",
    ".prisma": "#8a7fd9",
    ".db": "#8a7fd9",
    ".sqlite": "#8a7fd9",
    ".sql": "#8a7fd9",
    ".ps1": "#5b8def",
    ".psm1": "#5b8def",
    ".txt": "#9ca3af",
    ".log": "#9ca3af",
    ".css": "#8a7fd9",
    ".html": "#e0713d",
    ".yml": "#d97b86",
    ".yaml": "#d97b86",
    ".toml": "#d9a04f",
    ".env": "#5fb87a",
    ".sh": "#5fb87a",
    ".bat": "#d9a04f",
    ".png": "#d97b86",
    ".jpg": "#d97b86",
    ".svg": "#d9a04f",
}
DEFAULT_COLOR = "#8b95a5"


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------


def resolve_path(raw: object) -> Path:
    """Expand ``~``/env vars; relative paths resolve against NOVA_WORKSPACE."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise ValueError("a `path` is required")
    text = os.path.expandvars(os.path.expanduser(text))
    path = Path(text)
    if not path.is_absolute():
        path = Path(settings.ensure_workspace()) / path
    return path


def workspace() -> Path:
    return Path(settings.ensure_workspace())


def is_outside_workspace(path: Path) -> bool:
    try:
        return not path.resolve().is_relative_to(workspace().resolve())
    except Exception:
        return True


def human_size(num: float) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def ago(ts: float) -> str:
    """Relative English timestamp: ``3m ago`` / ``2h ago`` / ``Yesterday``."""
    delta = max(0.0, time.time() - ts)
    if delta < 60:
        return "Just now"
    minutes = int(delta // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days == 1:
        return "Yesterday"
    return f"{days} days ago"


def ext_color(path: Path) -> str:
    return EXT_COLORS.get(path.suffix.lower(), DEFAULT_COLOR)


def _tree_stats(path: Path, cap_items: int = 5000) -> tuple[int, int]:
    """Return ``(item_count, total_bytes)`` for a directory tree (bounded)."""
    count = 0
    total = 0
    stack = [path]
    while stack and count < cap_items:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except Exception:
            continue
        for entry in entries:
            if count >= cap_items:
                break
            count += 1
            try:
                if entry.is_dir():
                    stack.append(entry)
                else:
                    total += entry.stat().st_size
            except Exception:
                continue
    return count, total


def _target_for(src: Path, dest: Path) -> Path:
    """``dest`` may be an existing folder (move/copy into it) or a target path."""
    if dest.is_dir():
        return dest / src.name
    return dest


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# risk checks (registry is the only authority for destructiveness)
# ---------------------------------------------------------------------------


def _folder_risk(args: dict) -> Optional[str]:
    try:
        path = resolve_path(args.get("path"))
    except Exception:
        return None
    if path.exists() and path.is_file():
        return f"Would delete/replace the existing FILE at `{path}`"
    if is_outside_workspace(path):
        return f"Creates a folder outside the NOVA workspace (`{path}`)"
    return None


def _file_risk(args: dict) -> Optional[str]:
    try:
        path = resolve_path(args.get("path"))
    except Exception:
        return None
    if path.exists():
        return f"Overwrites an existing file: `{path}`"
    if is_outside_workspace(path):
        return f"Writes outside the NOVA workspace (`{path}`)"
    return None


def _delete_risk(args: dict) -> Optional[str]:
    try:
        path = resolve_path(args.get("path"))
    except Exception:
        return "Deletes a path from disk — this cannot be undone"
    if path.is_dir():
        try:
            count, total = _tree_stats(path)
            return f"Deletes the FOLDER `{path}` with {count} items ({human_size(total)}) — cannot be undone"
        except Exception:
            return f"Deletes the folder `{path}` recursively — cannot be undone"
    if path.is_file():
        try:
            size = human_size(path.stat().st_size)
        except Exception:
            size = "?"
        return f"Deletes the FILE `{path}` ({size}) — cannot be undone"
    return f"Deletes `{path}` from disk — cannot be undone"


def _rename_risk(args: dict) -> Optional[str]:
    try:
        path = resolve_path(args.get("path"))
        new_name = str(args.get("new_name") or "").strip()
    except Exception:
        return None
    if not new_name:
        return None
    target = path.parent / new_name
    if target.exists():
        return f"Replaces the existing path `{target}`"
    if is_outside_workspace(path) or is_outside_workspace(target):
        return f"Renames a path outside the NOVA workspace (`{path}`)"
    return None


def _move_risk(args: dict) -> Optional[str]:
    try:
        path = resolve_path(args.get("path"))
        dest = resolve_path(args.get("destination"))
    except Exception:
        return None
    target = _target_for(path, dest) if path.exists() else dest
    if target.exists():
        return f"Replaces the existing path `{target}`"
    if is_outside_workspace(target):
        return f"Moves a path outside the NOVA workspace (`{target}`)"
    return None


_copy_risk = _move_risk  # copying onto an existing target is equally destructive


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(
    "list_dir",
    "List the entries of a folder with size and age.",
    {"path": {"type": "string", "required": True, "description": "Folder to list (relative → NOVA workspace)"}},
    examples=["list what's in my workspace"],
)
def list_dir(args: dict, ctx: dict) -> dict:
    path = resolve_path(args.get("path"))
    if not path.exists():
        return {"ok": False, "output": f"Path not found: `{path}`", "data": {"path": str(path)}}
    if path.is_file():
        st = path.stat()
        return {
            "ok": True,
            "output": f"→ **{path.name}** · {human_size(st.st_size)} · {ago(st.st_mtime)}",
            "data": {"path": str(path), "is_file": True, "entries": []},
        }
    try:
        children = list(path.iterdir())
    except PermissionError as exc:
        return {"ok": False, "output": f"Permission denied: {exc}", "data": {"path": str(path)}}
    children.sort(key=_mtime, reverse=True)
    entries = []
    lines = []
    for child in children[:MAX_LIST_ENTRIES]:
        try:
            st = child.stat()
            if child.is_dir():
                entries.append({"name": child.name, "type": "dir", "size": 0, "modified": st.st_mtime})
                lines.append(f"→ 📁 {child.name}/ · {ago(st.st_mtime)}")
            else:
                entries.append({"name": child.name, "type": "file", "size": st.st_size, "modified": st.st_mtime})
                lines.append(f"→ {child.name} · {human_size(st.st_size)} · {ago(st.st_mtime)}")
        except Exception:
            continue
    hidden = len(children) - min(len(children), MAX_LIST_ENTRIES)
    header = f"**{len(lines)} entries** in `{path}`" + (f" (+{hidden} more)" if hidden > 0 else "")
    if not lines:
        header = f"`{path}` is empty"
    return {
        "ok": True,
        "output": header + ("\n" + "\n".join(lines) if lines else ""),
        "data": {"path": str(path), "entries": entries},
    }


@tool(
    "search_files",
    "Recursively search for files by pattern (max 500 results).",
    {
        "pattern": {"type": "string", "required": True, "description": "Glob or plain substring"},
        "path": {"type": "string", "required": False, "description": "Root folder", "default": "."},
    },
    examples=["find files named report", "search for *.log"],
)
def search_files(args: dict, ctx: dict) -> dict:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {"ok": False, "output": "A `pattern` is required.", "data": {}}
    root = resolve_path(args.get("path") or ".")
    if not root.exists():
        return {"ok": False, "output": f"Search root not found: `{root}`", "data": {"path": str(root)}}
    if root.is_file():
        return {"ok": True, "output": f"→ {root}", "data": {"path": str(root), "matches": [str(root)]}}

    matches: list[str] = []
    deadline = time.time() + 12
    try:
        if "/" in pattern or "\\" in pattern:
            iterator = root.glob(pattern)
        elif any(ch in pattern for ch in "*?["):
            iterator = root.rglob(pattern)
        else:
            iterator = root.rglob(f"*{pattern}*")
        for found in iterator:
            if len(matches) >= MAX_SEARCH_RESULTS or time.time() > deadline:
                break
            try:
                if found.is_file():
                    matches.append(str(found))
            except Exception:
                continue
    except Exception as exc:
        return {"ok": False, "output": f"Search failed: {exc}", "data": {"path": str(root)}}

    if not matches:
        return {
            "ok": True,
            "output": f"No files matching `{pattern}` under `{root}`.",
            "data": {"path": str(root), "pattern": pattern, "matches": []},
        }
    lines = []
    for m in matches[:30]:
        p = Path(m)
        try:
            st = p.stat()
            lines.append(f"→ {m} · {human_size(st.st_size)} · {ago(st.st_mtime)}")
        except Exception:
            lines.append(f"→ {m}")
    header = f"**{len(matches)} match(es)** for `{pattern}`"
    return {
        "ok": True,
        "output": header + "\n" + "\n".join(lines),
        "data": {"path": str(root), "pattern": pattern, "matches": matches, "count": len(matches)},
    }


@tool(
    "create_folder",
    "Create a folder (mkdir -p). Asks for confirmation only when it would overwrite a file.",
    {"path": {"type": "string", "required": True, "description": "Folder to create"}},
    destructive_check=_folder_risk,
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("folder was not created" if result.get("ok") else None),
    examples=["create a folder called assets in my workspace"],
)
def create_folder(args: dict, ctx: dict) -> dict:
    path = resolve_path(args.get("path"))
    if path.exists() and path.is_file():
        return {"ok": False, "output": f"Cannot create folder: `{path}` exists as a file.", "data": {"path": str(path)}}
    path.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "output": f"Created folder `{path}`",
        "data": {"path": str(path), "exists": path.is_dir()},
    }


@tool(
    "create_file",
    "Create (or overwrite) a file with optional content.",
    {
        "path": {"type": "string", "required": True, "description": "File to create"},
        "content": {"type": "string", "required": False, "description": "Text content", "default": ""},
    },
    destructive_check=_file_risk,
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("file was not created" if result.get("ok") else None),
    examples=["create a file called notes.txt with hello"],
)
def create_file(args: dict, ctx: dict) -> dict:
    path = resolve_path(args.get("path"))
    if path.exists() and path.is_dir():
        return {"ok": False, "output": f"Cannot write a file: `{path}` is a folder.", "data": {"path": str(path)}}
    content = args.get("content")
    content = "" if content is None else str(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "output": f"Wrote `{path}` ({len(content)} chars)",
        "data": {"path": str(path), "exists": path.exists(), "bytes": path.stat().st_size, "created": True},
    }


@tool(
    "read_file",
    "Read a text file (first 120 lines by default).",
    {
        "path": {"type": "string", "required": True, "description": "File to read"},
        "max_lines": {"type": "integer", "required": False, "description": "Line cap", "default": 120},
    },
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("file does not exist" if result.get("ok") else None),
    examples=["read the file notes.txt"],
)
def read_file(args: dict, ctx: dict) -> dict:
    path = resolve_path(args.get("path"))
    if not path.exists():
        return {"ok": False, "output": f"File not found: `{path}`", "data": {"path": str(path), "exists": False}}
    if path.is_dir():
        return {"ok": False, "output": f"`{path}` is a folder — use list_dir instead.", "data": {"path": str(path)}}
    try:
        max_lines = int(args.get("max_lines") or 120)
    except Exception:
        max_lines = 120
    max_lines = max(1, min(max_lines, 2000))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "output": f"Could not read `{path}`: {exc}", "data": {"path": str(path)}}
    lines = text.splitlines()
    shown = lines[:max_lines]
    body = "\n".join(shown)
    if len(body) > 12000:
        body = body[:12000] + "\n… (truncated)"
    header = f"**{path.name}** · {len(lines)} lines · {human_size(path.stat().st_size)}"
    if len(lines) > max_lines:
        header += f" (showing first {max_lines})"
    return {
        "ok": True,
        "output": header + "\n" + body,
        "data": {
            "path": str(path),
            "exists": True,
            "lines": len(lines),
            "preview": "\n".join(shown[:40]),
            "content": body,
        },
    }


@tool(
    "delete_path",
    "Delete a file or folder (recursive). Always asks for confirmation.",
    {"path": {"type": "string", "required": True, "description": "Path to delete"}},
    destructive_check=_delete_risk,
    verify=lambda args, result: None if (result.get("ok") and not (result.get("data") or {}).get("exists", True)) else ("path still exists" if result.get("ok") else None),
    examples=["delete the file notes.txt in my workspace"],
)
def delete_path(args: dict, ctx: dict) -> dict:
    path = resolve_path(args.get("path"))
    if not path.exists():
        return {"ok": False, "output": f"Nothing to delete — `{path}` does not exist.", "data": {"path": str(path), "exists": False}}
    protected = set()
    for candidate in (Path(settings.workspace), Path.home(), Path(os.environ.get("SystemDrive", "C:") + "\\")):
        try:
            protected.add(candidate.resolve())
        except Exception:
            continue
    if path.resolve() in protected:
        return {"ok": False, "output": f"Refusing to delete `{path}`.", "data": {"path": str(path)}}
    if path.is_dir():
        count, total = _tree_stats(path)
        shutil.rmtree(path, ignore_errors=False)
        return {
            "ok": True,
            "output": f"Deleted folder `{path}` ({count} items, {human_size(total)})",
            "data": {"path": str(path), "kind": "dir", "items": count, "bytes": total, "exists": path.exists()},
        }
    size = path.stat().st_size
    path.unlink()
    return {
        "ok": True,
        "output": f"Deleted `{path}` ({human_size(size)})",
        "data": {"path": str(path), "kind": "file", "bytes": size, "exists": path.exists()},
    }


@tool(
    "rename_path",
    "Rename a file or folder within its parent directory.",
    {
        "path": {"type": "string", "required": True, "description": "Existing path"},
        "new_name": {"type": "string", "required": True, "description": "New name"},
    },
    destructive_check=_rename_risk,
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("renamed path not found" if result.get("ok") else None),
    examples=["rename old.txt to new.txt"],
)
def rename_path(args: dict, ctx: dict) -> dict:
    src = resolve_path(args.get("path"))
    new_name = str(args.get("new_name") or "").strip()
    if not new_name:
        return {"ok": False, "output": "`new_name` is required.", "data": {}}
    if not src.exists():
        return {"ok": False, "output": f"Not found: `{src}`", "data": {"path": str(src)}}
    target = src.parent / new_name
    if target.resolve() == src.resolve():
        return {"ok": False, "output": "Source and target are the same.", "data": {"path": str(src)}}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        os.replace(src, target)
    else:
        src.rename(target)
    return {
        "ok": True,
        "output": f"Renamed `{src.name}` → `{target.name}`",
        "data": {"path": str(target), "from": str(src), "exists": target.exists()},
    }


@tool(
    "move_path",
    "Move a file or folder to a destination.",
    {
        "path": {"type": "string", "required": True, "description": "Source path"},
        "destination": {"type": "string", "required": True, "description": "Destination folder or file path"},
    },
    destructive_check=_move_risk,
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("destination not found after move" if result.get("ok") else None),
    examples=["move notes.txt to archive"],
)
def move_path(args: dict, ctx: dict) -> dict:
    src = resolve_path(args.get("path"))
    dest = resolve_path(args.get("destination"))
    if not src.exists():
        return {"ok": False, "output": f"Not found: `{src}`", "data": {"path": str(src)}}
    target = _target_for(src, dest)
    if target.resolve() == src.resolve():
        return {"ok": False, "output": "Source and destination are the same.", "data": {"path": str(src)}}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_dir() and src.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()
    shutil.move(str(src), str(target))
    return {
        "ok": True,
        "output": f"Moved `{src.name}` → `{target}`",
        "data": {"path": str(target), "from": str(src), "exists": target.exists(), "source_exists": src.exists()},
    }


@tool(
    "copy_path",
    "Copy a file or folder to a destination.",
    {
        "path": {"type": "string", "required": True, "description": "Source path"},
        "destination": {"type": "string", "required": True, "description": "Destination folder or file path"},
    },
    destructive_check=_copy_risk,
    verify=lambda args, result: None if (result.get("ok") and (result.get("data") or {}).get("exists")) else ("copy not found after copying" if result.get("ok") else None),
    examples=["copy notes.txt to backup"],
)
def copy_path(args: dict, ctx: dict) -> dict:
    src = resolve_path(args.get("path"))
    dest = resolve_path(args.get("destination"))
    if not src.exists():
        return {"ok": False, "output": f"Not found: `{src}`", "data": {"path": str(src)}}
    target = _target_for(src, dest)
    if target.resolve() == src.resolve():
        return {"ok": False, "output": "Source and destination are the same.", "data": {"path": str(src)}}
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, target, dirs_exist_ok=True)
    else:
        shutil.copy2(src, target)
    return {
        "ok": True,
        "output": f"Copied `{src.name}` → `{target}`",
        "data": {"path": str(target), "from": str(src), "exists": target.exists()},
    }


__all__ = [
    "resolve_path",
    "workspace",
    "is_outside_workspace",
    "human_size",
    "ago",
    "ext_color",
    "EXT_COLORS",
    "DEFAULT_COLOR",
]
