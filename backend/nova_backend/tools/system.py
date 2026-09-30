"""NOVA backend — system metrics (psutil based, graceful when psutil is missing)."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from typing import Optional

from ..config import settings
from . import tool

log = logging.getLogger("nova.system")

CREATE_NO_WINDOW = 0x08000000

_net_sample: dict = {"t": 0.0, "bytes": 0.0}
_gpu_cache: dict = {"t": 0.0, "value": None}


def _has_psutil() -> bool:
    try:
        import psutil  # noqa: F401

        return True
    except Exception:
        return False


def _network_mbps() -> float:
    """Throughput in MB/s (measured over a short window)."""
    try:
        import psutil
    except Exception:
        return 0.0
    try:
        counters = psutil.net_io_counters()
        total = float(counters.bytes_sent) + float(counters.bytes_recv)
        now = time.monotonic()
        previous_t = _net_sample["t"]
        previous_b = _net_sample["bytes"]
        if not previous_t:
            _net_sample.update({"t": now, "bytes": total})
            time.sleep(0.4)
            counters = psutil.net_io_counters()
            total2 = float(counters.bytes_sent) + float(counters.bytes_recv)
            elapsed = max(1e-6, time.monotonic() - _net_sample["t"])
            mbps = (total2 - _net_sample["bytes"]) / elapsed / 1e6
            _net_sample.update({"t": time.monotonic(), "bytes": total2})
            return round(max(0.0, mbps), 2)
        elapsed = now - previous_t
        if elapsed < 0.35:
            time.sleep(0.35 - elapsed)
            counters = psutil.net_io_counters()
            total = float(counters.bytes_sent) + float(counters.bytes_recv)
            elapsed = max(1e-6, time.monotonic() - previous_t)
        mbps = (total - previous_b) / elapsed / 1e6
        _net_sample.update({"t": time.monotonic(), "bytes": total})
        return round(max(0.0, mbps), 2)
    except Exception as exc:  # pragma: no cover
        log.debug("network sample failed: %s", exc)
        return 0.0


def gpu_temperature() -> Optional[float]:
    """GPU temperature via ``nvidia-smi`` (None when unknown)."""
    now = time.time()
    if now - _gpu_cache["t"] < 10 and _gpu_cache["value"] is not None:
        return _gpu_cache["value"]
    value: Optional[float] = None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            value = float(proc.stdout.strip().splitlines()[0].strip())
    except Exception:
        value = None
    _gpu_cache.update({"t": now, "value": value})
    return value


def network_display(mbps: float) -> str:
    if mbps >= 1000:
        return f"{mbps / 1000:.1f} GB/s"
    if mbps >= 1:
        return f"{mbps:.0f} MB/s"
    if mbps > 0:
        return f"{mbps * 1000:.0f} KB/s"
    return "0 MB/s"


def collect_system() -> dict:
    """Payload for ``GET /api/system`` (0-100 floats, gpu_temp may be None)."""
    data: dict = {
        "cpu": 0.0,
        "ram": 0.0,
        "ssd": 0.0,
        "network_mbps": 0.0,
        "network_display": "0 MB/s",
        "gpu_temp": None,
        "os": platform.platform(),
        "hostname": platform.node(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "python": platform.python_version(),
        "cores": os.cpu_count() or 0,
        "ram_total_gb": 0.0,
        "uptime_s": 0.0,
    }
    try:
        import psutil
    except Exception:
        return data

    try:
        data["cpu"] = round(float(psutil.cpu_percent(interval=0.25)), 1)
        vm = psutil.virtual_memory()
        data["ram"] = round(float(vm.percent), 1)
        data["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
        disk = psutil.disk_usage(str(settings.ensure_workspace()))
        data["ssd"] = round(float(disk.percent), 1)
        data["uptime_s"] = round(time.time() - float(psutil.boot_time()), 1)
        data["cores"] = psutil.cpu_count(logical=True) or data["cores"]
    except Exception as exc:  # pragma: no cover
        log.debug("psutil sampling failed: %s", exc)

    data["network_mbps"] = _network_mbps()
    data["network_display"] = network_display(data["network_mbps"])
    data["gpu_temp"] = gpu_temperature()
    return data


# ---------------------------------------------------------------------------
# tool
# ---------------------------------------------------------------------------


@tool(
    "system_info",
    "Report CPU, RAM, disk, network and platform details of this PC.",
    {"section": {"type": "string", "required": False, "description": "overview|cpu|memory|disk|network", "default": "overview"}},
    examples=["system info", "how much storage is left"],
)
def system_info(args: dict, ctx: dict) -> dict:
    section = str(args.get("section") or "overview").strip().lower()
    if section not in {"overview", "cpu", "memory", "disk", "network", "os", "gpu"}:
        section = "overview"

    if not _has_psutil():
        return {
            "ok": True,
            "output": "**System**\n"
            + f"→ OS: {platform.platform()}\n"
            + f"→ Host: {platform.node()} · User: {os.environ.get('USERNAME', '')}\n"
            + f"→ Python: {platform.python_version()} · Cores: {os.cpu_count()}\n"
            + "→ (psutil is not installed — detailed metrics unavailable)",
            "data": {"section": section, "psutil": False},
        }

    metrics = collect_system()
    lines: list[str] = []
    if section in {"overview", "cpu"}:
        lines.append(f"→ **CPU** {metrics['cpu']}% · {metrics['cores']} logical cores")
    if section in {"overview", "memory"}:
        lines.append(f"→ **RAM** {metrics['ram']}% of {metrics['ram_total_gb']} GB")
    if section in {"overview", "disk"}:
        lines.append(f"→ **Disk** {metrics['ssd']}% used on the workspace drive")
    if section in {"overview", "network"}:
        lines.append(f"→ **Network** {metrics['network_display']} ({metrics['network_mbps']} MB/s)")
    if section in {"overview", "gpu"}:
        temp = metrics["gpu_temp"]
        lines.append(f"→ **GPU** {'not reported' if temp is None else str(temp) + '°C'}")
    if section == "overview":
        days = int(metrics["uptime_s"] // 86400)
        hours = int((metrics["uptime_s"] % 86400) // 3600)
        lines.append(f"→ **OS** {metrics['os']}")
        lines.append(f"→ **Host** {metrics['host'] if 'host' in metrics else metrics['hostname']} · {metrics['user']}")
        lines.append(f"→ **Uptime** {days}d {hours}h")

    header = f"**System health · {section}**"
    return {
        "ok": True,
        "output": header + "\n" + "\n".join(lines),
        "data": {"section": section, **metrics, "psutil": True},
    }


__all__ = ["collect_system", "gpu_temperature", "network_display", "system_info"]
