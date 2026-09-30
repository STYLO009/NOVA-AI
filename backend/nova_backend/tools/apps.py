"""NOVA backend — Windows application tools (open / close / detect)."""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ..config import settings
from . import tool

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000

#: Windows-only tools get one consistent, non-cryptic answer elsewhere.
_UNSUPPORTED: dict = {"supported": False, "platform": sys.platform}


def _unsupported(tool_name: str) -> dict:
    return {
        "ok": False,
        "output": (
            f"`{tool_name}` only works on Windows (it drives .exe processes, "
            f"the shell and taskkill). This backend is running on {sys.platform} "
            "— the file, memory, search, system and browser tools still work."
        ),
        "data": dict(_UNSUPPORTED, tool=tool_name),
    }

#: alias -> windows image name
APP_ALIASES: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "windows powershell": "powershell.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "taskmgr": "taskmgr.exe",
    "task manager": "taskmgr.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "mozilla firefox": "firefox.exe",
    "vscode": "Code.exe",
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "code": "Code.exe",
    "word": "WINWORD.EXE",
    "microsoft word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "microsoft excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "microsoft powerpoint": "POWERPNT.EXE",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "spotify": "Spotify.exe",
    "steam": "steam.exe",
    "blender": "blender.exe",
    "figma": "Figma.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
}

#: image name -> known install locations (checked before `where.exe`)
KNOWN_PATHS: dict[str, list[str]] = {
    "notepad.exe": [r"%WINDIR%\System32\notepad.exe", r"%WINDIR%\SysWOW64\notepad.exe"],
    "calc.exe": [r"%WINDIR%\System32\calc.exe"],
    "cmd.exe": [r"%WINDIR%\System32\cmd.exe"],
    "powershell.exe": [r"%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe"],
    "explorer.exe": [r"%WINDIR%\explorer.exe"],
    "taskmgr.exe": [r"%WINDIR%\System32\taskmgr.exe"],
    "mspaint.exe": [r"%WINDIR%\System32\mspaint.exe"],
    "chrome.exe": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ],
    "msedge.exe": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox.exe": [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ],
    "Code.exe": [
        r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
    ],
    "WINWORD.EXE": [
        r"%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\WINWORD.EXE",
    ],
    "EXCEL.EXE": [
        r"%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\EXCEL.EXE",
    ],
    "POWERPNT.EXE": [
        r"%ProgramFiles%\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\POWERPNT.EXE",
    ],
    "Spotify.exe": [r"%AppData%\Spotify\Spotify.exe", r"%LocalAppData%\Spotify\Spotify.exe"],
    "steam.exe": [
        r"%ProgramFiles(x86)%\Steam\steam.exe",
        r"%ProgramFiles%\Steam\steam.exe",
    ],
    "wt.exe": [r"%LocalAppData%\Microsoft\WindowsApps\wt.exe"],
    "Figma.exe": [r"%LocalAppData%\Figma\Figma.exe"],
}

#: never force-kill these
SYSTEM_PROCESSES = {
    "csrss.exe",
    "smss.exe",
    "wininit.exe",
    "winlogon.exe",
    "system",
    "system idle process",
    "idle",
    "lsass.exe",
    "services.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "registry",
    "memory compression",
}

#: ordered list used by ``GET /api/apps``
CURATED_APPS: list[tuple[str, str]] = [
    ("Notepad", "notepad.exe"),
    ("Google Chrome", "chrome.exe"),
    ("Microsoft Edge", "msedge.exe"),
    ("File Explorer", "explorer.exe"),
    ("PowerShell", "powershell.exe"),
    ("Visual Studio Code", "Code.exe"),
    ("Calculator", "calc.exe"),
    ("Command Prompt", "cmd.exe"),
    ("Task Manager", "taskmgr.exe"),
    ("Windows Terminal", "wt.exe"),
    ("Spotify", "Spotify.exe"),
    ("Mozilla Firefox", "firefox.exe"),
    ("Paint", "mspaint.exe"),
    ("Steam", "steam.exe"),
    ("Blender", "blender.exe"),
    ("Figma", "Figma.exe"),
    ("Microsoft Word", "WINWORD.EXE"),
    ("Microsoft Excel", "EXCEL.EXE"),
]

PALETTE = ["#2b8fd9", "#5b8def", "#8a7fd9", "#d97b86", "#5fb87a", "#d9a04f"]

_where_cache: dict[str, list[str]] = {}
_detect_cache: dict[str, list[dict]] = {}
_detect_ts: dict[str, float] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _norm_name(name: str) -> str:
    n = re.sub(r"\s+", " ", str(name or "")).strip().lower()
    n = re.sub(r"\s+(?:app|application|program)$", "", n)
    return n.strip(" .\"'")


def resolve_image(name: str) -> tuple[str, str]:
    """``resolve_image("my chrome")`` → ``("Google Chrome", "chrome.exe")``."""
    n = _norm_name(name)
    if n.endswith(".exe"):
        n = n[:-4]
    image = APP_ALIASES.get(n)
    if image is None:
        for alias, img in APP_ALIASES.items():
            if n == alias or n.startswith(alias + " ") or n.endswith(" " + alias):
                image = img
                break
    if image is None:
        image = f"{n}.exe" if n else ""
    display = (name or image).strip() or image
    return display, image.lower()


def _where(image: str) -> list[str]:
    if image in _where_cache:
        return _where_cache[image]
    results: list[str] = []
    try:
        out = subprocess.run(
            ["where.exe", image], capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        if out.returncode == 0 and out.stdout:
            for line in out.stdout.splitlines():
                line = line.strip()
                if line and Path(line).exists():
                    results.append(line)
    except Exception:
        results = []
    _where_cache[image] = results
    return results


def candidate_paths(image: str) -> list[str]:
    paths: list[str] = []
    for raw in KNOWN_PATHS.get(image, []):
        try:
            expanded = os.path.expandvars(raw)
        except Exception:
            expanded = raw
        paths.append(expanded)
    paths.extend(_where(image))
    # de-duplicate, keep order
    seen, ordered = set(), []
    for p in paths:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
    return ordered


def resolve_executable(name: str) -> tuple[str, str, Optional[str]]:
    """Return ``(display, image, executable_path_or_None)``."""
    display, image = resolve_image(name)
    for path in candidate_paths(image):
        if path and Path(path).exists():
            return display, image, path
    return display, image, None


def is_console_exe(exe: str) -> bool:
    """True when ``exe`` is a console (CUI) binary rather than a GUI one.

    Read straight from the PE optional header, so it works for anything on
    disk without a hard-coded name list. Needed because the two kinds of
    program want opposite creation flags: a GUI app can be detached from the
    parent's console, while a console app started that way gets *no* window at
    all — PowerShell or cmd would "open" with nothing visible to click.
    """
    try:
        with open(exe, "rb") as fh:
            fh.seek(0x3C)
            (pe_offset,) = struct.unpack("<I", fh.read(4))
            fh.seek(pe_offset)
            if fh.read(4) != b"PE\0\0":
                return False
            fh.seek(pe_offset + 24)  # start of the optional header
            (magic,) = struct.unpack("<H", fh.read(2))
            if magic not in (0x10B, 0x20B):  # PE32 / PE32+
                return False
            # `Subsystem` lands on the same offset in both header flavours:
            # PE32+ drops `BaseOfImageData` (−4) but widens `ImageBase` (+4).
            fh.seek(pe_offset + 24 + 68)
            (subsystem,) = struct.unpack("<H", fh.read(2))
            return subsystem == 3  # IMAGE_SUBSYSTEM_WINDOWS_CUI
    except OSError:
        return False


def _popen(exe: str) -> None:
    cwd = str(Path(exe).parent) if Path(exe).is_file() else None
    if is_console_exe(exe):
        # Own console, standard handles left untouched so the window is a real
        # interactive session (typing and output both work).
        subprocess.Popen([exe], cwd=cwd, creationflags=CREATE_NEW_CONSOLE, shell=False)
        return
    subprocess.Popen(
        [exe],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=DETACHED,
        close_fds=True,
        shell=False,
    )


def _process_names(image: str) -> set[str]:
    names = {image.lower()}
    # some aliases map to several images (e.g. terminal)
    for img in APP_ALIASES.values():
        if img.lower() == image.lower():
            names.add(img.lower())
    return names


def _find_processes(image: str) -> list[str]:
    """Best effort: pids of processes matching ``image``."""
    try:
        import psutil
    except Exception:
        return []
    wanted = _process_names(image)
    found: list[str] = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name in wanted:
                found.append(str(proc.info.get("pid")))
    except Exception:
        return []
    return found


def detect_apps(limit: int = 8) -> list[dict]:
    """Detected applications (deterministic order, up to ``limit``)."""
    if not settings.is_windows:
        # No `where.exe` / .exe registry outside Windows.
        return []
    key = str(limit)
    now = time.time()
    cached = _detect_cache.get(key)
    if cached is not None and now - _detect_ts.get(key, 0) < 900:
        return cached
    apps: list[dict] = []
    for index, (display, image) in enumerate(CURATED_APPS):
        if len(apps) >= limit:
            break
        _, _, exe = resolve_executable(display)
        if not exe:
            continue
        apps.append(
            {
                "name": display,
                "abbr": _abbr(display),
                "color": PALETTE[len(apps) % len(PALETTE)],
                "command": exe,
            }
        )
    _detect_cache[key] = apps
    _detect_ts[key] = now
    return apps


def _abbr(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name[:2] or "??").upper()


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(
    "open_app",
    "Launch a Windows application (alias aware: notepad, calc, chrome, vscode, ...).",
    {"app": {"type": "string", "required": True, "description": "Application name or alias"}},
    verify=lambda args, result: _verify_opened(args, result),
    examples=["open notepad", "launch chrome", "start visual studio code"],
)
def open_app(args: dict, ctx: dict) -> dict:
    name = str(args.get("app") or "").strip()
    if not name:
        return {"ok": False, "output": "An `app` name is required.", "data": {}}
    if not settings.is_windows:
        return _unsupported("open_app")

    # If the "app" is actually a .com URL, open it in Chrome instead.
    if re.search(r"\.com(?::\d+)?(?:[/?#]|$)", name, re.I):
        from .browser import normalize_url, _open_in_chrome, _is_com_url
        url = normalize_url(name)
        if url and _is_com_url(url):
            try:
                ok = _open_in_chrome(url)
                if ok:
                    return {"ok": True, "output": f"Opened **{url}** in Chrome", "data": {"url": url, "engine": "chrome"}}
            except Exception as exc:
                return {"ok": False, "output": f"Could not open {url} in Chrome: {exc}", "data": {"url": url}}

    display, image, exe = resolve_executable(name)
    if exe:
        try:
            _popen(exe)
        except Exception as exc:
            return {"ok": False, "output": f"Could not launch {exe}: {exc}", "data": {"image": image}}
        return {
            "ok": True,
            "output": f"Launched **{display}** (`{exe}`)",
            "data": {"app": display, "image": image, "exe": exe},
        }

    # last resort: shell association / `start`
    for attempt in (name, image):
        try:
            os.startfile(attempt)  # noqa: S606 - windows shell launch
            return {
                "ok": True,
                "output": f"Opened **{display}** through the Windows shell",
                "data": {"app": display, "image": image, "exe": None},
            }
        except Exception:
            continue
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED,
            close_fds=True,
        )
        return {
            "ok": True,
            "output": f"Asked Windows to start **{display}**",
            "data": {"app": display, "image": image, "exe": None, "via": "start"},
        }
    except Exception as exc:
        return {
            "ok": False,
            "output": f"Could not find or start “{name}” ({exc}). Try the exact app name.",
            "data": {"app": display, "image": image},
        }


def _verify_opened(args: dict, result: dict) -> Optional[str]:
    """Process visible in psutil (best effort, 3s poll)."""
    if not result.get("ok"):
        return None
    image = (result.get("data") or {}).get("image") or resolve_image(args.get("app", ""))[1]
    if not image:
        return None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if _find_processes(image):
            return None
        time.sleep(0.3)
    try:
        import psutil  # noqa: F401
    except Exception:
        return None  # cannot verify without psutil => pass
    return f"no running process named {image} was detected within 3s"


@tool(
    "close_app",
    "Force-close a running application by ending its processes (taskkill /IM /F).",
    {"app": {"type": "string", "required": True, "description": "Application name or alias"}},
    destructive_check=lambda args: _close_risk(args),
    verify=lambda args, result: _verify_closed(args, result),
    examples=["close notepad", "kill spotify"],
)
def close_app(args: dict, ctx: dict) -> dict:
    name = str(args.get("app") or "").strip()
    if not name:
        return {"ok": False, "output": "An `app` name is required.", "data": {}}
    if not settings.is_windows:
        return _unsupported("close_app")
    display, image = resolve_image(name)
    if image in SYSTEM_PROCESSES:
        return {
            "ok": False,
            "output": f"Refusing to end the protected system process `{image}`.",
            "data": {"image": image, "protected": True},
        }

    pids = _find_processes(image)
    try:
        proc = subprocess.run(
            ["taskkill", "/IM", image, "/F"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"taskkill timed out for `{image}`", "data": {"image": image}}
    except Exception as exc:
        return {"ok": False, "output": f"taskkill failed: {exc}", "data": {"image": image}}

    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return {
            "ok": True,
            "output": f"Ended **{display}** (`{image}`)" + (f" — {len(pids)} process(es)" if pids else ""),
            "data": {"image": image, "exit_code": proc.returncode, "killed": pids, "taskkill": out},
        }
    if "not found" in out.lower() or "no running" in out.lower():
        return {
            "ok": True,
            "output": f"**{display}** was not running — nothing to close.",
            "data": {"image": image, "exit_code": proc.returncode, "already_closed": True, "taskkill": out},
        }
    return {
        "ok": False,
        "output": out or f"taskkill exited with code {proc.returncode}",
        "data": {"image": image, "exit_code": proc.returncode, "taskkill": out},
    }


def _close_risk(args: dict) -> Optional[str]:
    name = str(args.get("app") or "").strip()
    if not name:
        return None
    _, image = resolve_image(name)
    if image in SYSTEM_PROCESSES:
        return f"Ends a protected system process (`{image}`)"
    running = _find_processes(image)
    extra = f" — {len(running)} running now" if running else ""
    return f"Ends running processes of **{image}**{extra} (taskkill /F)"


def _verify_closed(args: dict, result: dict) -> Optional[str]:
    if not result.get("ok"):
        return None
    image = (result.get("data") or {}).get("image") or resolve_image(args.get("app", ""))[1]
    if (result.get("data") or {}).get("already_closed"):
        return None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _find_processes(image):
            return None
        time.sleep(0.3)
    return f"{image} is still running"


__all__ = [
    "APP_ALIASES",
    "KNOWN_PATHS",
    "SYSTEM_PROCESSES",
    "CURATED_APPS",
    "resolve_image",
    "resolve_executable",
    "candidate_paths",
    "detect_apps",
    "is_console_exe",
]
