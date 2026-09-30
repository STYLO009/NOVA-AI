"""NOVA backend — PowerShell execution tool (with a deterministic risk check)."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from typing import Optional

from ..config import settings
from . import tool

log = logging.getLogger("nova.powershell")

CREATE_NO_WINDOW = 0x08000000
MAX_OUTPUT = 8000

# Ordered (regex, human label) pairs — the reason is quoted back to the user.
_RISKY: list[tuple[str, str]] = [
    (r"(?i)\bremove-item\b", "Remove-Item"),
    (r"(?i)(?:^|[\s;|])ri\s+", "ri "),
    (r"(?i)\bdel\s+", "del "),
    (r"(?i)\berase\b", "erase"),
    (r"(?i)\brmdir\b", "rmdir"),
    (r"(?i)(?:^|[\s;|])rd\s+", "rd "),
    (r"(?i)\bclear-content\b", "Clear-Content"),
    (r"(?i)\bset-content\b", "Set-Content"),
    (r"(?i)\bout-file\b", "Out-File"),
    (r"(?i)\bmove-item\b", "Move-Item"),
    (r"(?i)\bcopy-item\b", "Copy-Item"),
    (r"(?i)\bstop-process\b", "Stop-Process"),
    (r"(?i)\btaskkill\b", "taskkill"),
    (r"(?i)\bkill\s*\(", "Kill("),
    (r"(?i)\bformat-", "Format-"),
    (r"(?i)\bdiskpart\b", "diskpart"),
    (r"(?i)\bremove-registry\b", "Remove-Registry"),
    (r"(?i)\breg\s+delete\b", "reg delete"),
    (r"(?i)\bremove-computer\b", "Remove-Computer"),
    (r"(?i)\bshutdown\b", "shutdown"),
    (r"(?i)\brestart-computer\b", "Restart-Computer"),
    (r"(?i)\bstop-computer\b", "Stop-Computer"),
    (r"(?i)\bset-executionpolicy\b", "Set-ExecutionPolicy"),
    (r"(?i)\bdisable-", "Disable-"),
    (r"(?i)\buninstall\b", "Uninstall"),
    (r"(?i)\bremove-module\b", "Remove-Module"),
    (r"(?i)\bclear-disk\b", "Clear-Disk"),
    (r"(?i)\binitialize-disk\b", "Initialize-Disk"),
    (r"(?i)\bremove-net\b", "Remove-Net"),
    (r"(?i)\bblock-", "Block-"),
    (r"(?i)\bsuspend-", "Suspend-"),
    # build / test pipelines also change the disk - NOVA asks before running them
    (r"(?i)(?:^|[\s;|])(?:npm|pnpm|yarn|bun)\s+run\s+(?:build|test|deploy|release|publish|start|preview)\b", "package build/test script"),
    (r"(?i)(?:^|[\s;|])make\s+(?:install|deploy|release|all)\b", "make target"),
    (r"(?i)(?:^|[\s;|])git\s+(?:push|reset|clean|rebase|merge)\b", "git write command"),
]

# Read-only commands never ask for confirmation (checked before the risk list).
_READONLY = re.compile(
    r"""(?ix)^\s*(?:\|\s*)?(
        get-\w+|select-\w+|sort-\w+|measure-\w+|format-\w+|group-\w+|where-object|
        foreach-object|out-string|out-null|write-output|write-host|write-verbose|
        test-\w+|compare-object|select-string|trace-\w+|debug-\w+|resolve-\w+|
        ipconfig|ifconfig|systeminfo|whoami|hostname|ver|vol|date|time|cls|pwd|cd|chdir|
        dir|ls|ll|cat|type|echo|tree|tasklist|netstat|ping|tracert|nslookup|curl|wget|
        df|free|uname|nvidia-smi|gh\s+\w+|
        git\s+(?:status|log|diff|branch|remote|show|config|tag|stash\s+list)|
        npm\s+(?:list|ls|view|info|outdated|audit|why|explain)|
        pnpm\s+(?:list|ls|view|info|audit|why)|
        yarn\s+(?:list|info|why)|
        bun\s+(?:list|info|pm\s+ls)|
        node\s+--version|python\d*(?:\.\d+)?\s+--version|python\s+-m\s+(?:pip\s+list|sys\.version)|
        uv\s+(?:pip\s+list|--version|tree)|pip\d*\s+(?:list|show|freeze)|
        docker\s+(?:ps|images|logs|version|inspect)|kubectl\s+(?:get|describe|logs)
    )\b.*$"""
)


def _segments(command: str) -> list[str]:
    return [s for s in re.split(r"(?:\||;|&&|\n|\r)+", command) if s.strip()]


def _is_read_only(command: str) -> bool:
    segments = _segments(command)
    if not segments:
        return True
    return all(_READONLY.match(seg) for seg in segments)


def command_risk(command: str) -> Optional[str]:
    """Risk reason quoting the matched pattern, or ``None`` when read-only."""
    text = str(command or "").strip()
    if not text:
        return None
    if _is_read_only(text):
        return None
    for pattern, label in _RISKY:
        match = re.search(pattern, text)
        if match:
            snippet = text
            if len(snippet) > 110:
                snippet = snippet[:107] + "..."
            return f"Runs a destructive command — matched `{label}` in: `{snippet}`"
    return None


@tool(
    "run_powershell",
    "Run a PowerShell command and return stdout, stderr and the exit code.",
    {"command": {"type": "string", "required": True, "description": "PowerShell command to execute"}},
    destructive_check=lambda args: command_risk(args.get("command", "")),
    verify=lambda args, result: _verify(result),
    examples=["run powershell Get-Process", "execute ipconfig"],
)
def run_powershell(args: dict, ctx: dict) -> dict:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"ok": False, "output": "A `command` is required.", "data": {}}
    if not settings.is_windows:
        # The Linux Docker image has no PowerShell; say so instead of failing
        # with a cryptic FileNotFoundError.
        return {
            "ok": False,
            "output": (
                "The PowerShell tool only runs on Windows. This backend is on "
                f"{sys.platform}, so shell commands are unavailable — the file, "
                "memory, search, system and browser tools still work."
            ),
            "data": {"command": command, "supported": False, "platform": sys.platform},
        }
    timeout = max(1, int(getattr(settings, "powershell_timeout", 30) or 30))

    # Keep the console on UTF-8 so non-ASCII output survives the round trip.
    wrapped = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; " + command
    )
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                wrapped,
            ],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": f"Command timed out after {timeout}s: `{command}`",
            "data": {"command": command, "timeout": timeout, "timed_out": True},
        }
    except Exception as exc:
        log.warning("powershell failed: %s", exc)
        return {"ok": False, "output": f"Could not run PowerShell: {exc}", "data": {"command": command}}

    stdout = (proc.stdout or b"").decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")
    exit_code = int(getattr(proc, "returncode", 0) or 0)

    body = stdout.strip()
    if stderr.strip():
        body += ("\n" if body else "") + f"[stderr] {stderr.strip()}"
    if not body:
        body = "(no output)" if exit_code == 0 else f"(no output, exit code {exit_code})"
    if len(body) > MAX_OUTPUT:
        body = body[:MAX_OUTPUT] + "\n… (truncated)"

    return {
        "ok": exit_code == 0,
        "output": body,
        "data": {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout[:MAX_OUTPUT],
            "stderr": stderr[:MAX_OUTPUT],
        },
    }


def _verify(args: dict, result: dict) -> Optional[str]:
    data = result.get("data") or {}
    if data.get("timed_out"):
        return "command timed out"
    code = data.get("exit_code")
    if code is None:
        return None
    if int(code) != 0:
        return f"command exited with code {code}"
    return None


__all__ = ["command_risk", "run_powershell"]
