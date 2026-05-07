"""
system_monitor.py — real-time system health for Andrew.

Exposes the `get_system_health` tool. Uses `psutil` (cross-platform) to
read CPU (per-core + total), memory, disk (per-partition), and network
counters. Two detail levels:

    detail_level="basic"     → one-line summary
    detail_level="detailed"  → full block with per-core / per-partition

Caches readings for 2 seconds so repeated back-to-back calls don't pound
the system.

Sample output (basic):
    CPU 17.3% | RAM 62.1% (19.8/32.0 GB) | Disk(C:) 78.4% | Net delta in/out 1.2/0.3 MB

Sample output (detailed):
    ## System health (Windows-AMD64, 2026-05-06 22:56:18, uptime 4d 12h 31m)

    CPU
      total: 17.3%
      cores: [12.4, 23.1, 9.7, 14.0, 19.6, 22.8, 11.3, 18.9]

    Memory
      total: 32.00 GB | used: 19.85 GB (62.1%) | free: 12.15 GB
      swap : used 1.20 GB (8.3%)

    Disk
      C:\\  total 953.87 GB | used 747.55 GB (78.4%) | free 206.32 GB
      D:\\  total 1863.02 GB | used 1024.10 GB (54.9%) | free 838.92 GB

    Network (since boot)
      sent: 14.32 GB | recv: 89.10 GB | packets sent/recv: 14.2M / 31.7M

Standalone test:
    python src/tools/dynamic/system_monitor.py
"""
from __future__ import annotations

import asyncio
import platform
import time
from datetime import datetime

# psutil is required. If it's missing the tool reports a clear error rather
# than crashing the dynamic loader at import time.
try:
    import psutil  # type: ignore
    _PSUTIL_OK = True
    _PSUTIL_ERR = ""
except Exception as e:  # pragma: no cover - import failure path
    psutil = None  # type: ignore
    _PSUTIL_OK = False
    _PSUTIL_ERR = (
        f"psutil not available: {e}. Install with: pip install psutil"
    )


# ---- TOOL_DEF: what the dynamic loader hands to Andrew's function set -----

TOOL_DEF = {
    "name": "get_system_health",
    "description": (
        "Retrieve real-time system health metrics like CPU, memory, disk, and "
        "network usage. Use when asked about the rig's status, performance, "
        "or when running a routine system check."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "detail_level": {
                "type": "string",
                "enum": ["basic", "detailed"],
                "description": (
                    "'basic' returns a one-line summary; 'detailed' returns "
                    "the full breakdown with per-core CPU and per-partition "
                    "disk. Defaults to 'basic'."
                ),
            }
        },
        "required": [],
    },
}


# ---- Caching ---------------------------------------------------------------
# Cheap protection against being polled too aggressively. 2s is short enough
# that "what's CPU doing right now" stays accurate, long enough that if Andrew
# (or anything else) calls the tool five times in quick succession we don't
# re-sample every single time.

_CACHE_TTL_S = 2.0
_cache: dict[str, tuple[float, str]] = {}


def _cached(key: str, builder) -> str:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    value = builder()
    _cache[key] = (now, value)
    return value


# ---- Formatting helpers ----------------------------------------------------


def _gb(n: int | float) -> float:
    return round(n / (1024 ** 3), 2)


def _mb(n: int | float) -> float:
    return round(n / (1024 ** 2), 2)


def _human_count(n: int | float) -> str:
    """14_200_000 → '14.2M', 31_700 → '31.7K'."""
    n = float(n)
    for unit in ("", "K", "M", "B", "T"):
        if abs(n) < 1000:
            return f"{n:.1f}{unit}"
        n /= 1000
    return f"{n:.1f}P"


def _uptime_phrase() -> str:
    secs = int(time.time() - psutil.boot_time())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ---- Network delta tracking -----------------------------------------------
# psutil.net_io_counters returns since-boot totals. For the basic one-liner
# we want a "what's happening RIGHT NOW" delta, so we stash the previous
# reading and the timestamp.

_last_net: tuple[float, int, int] | None = None  # (ts, sent, recv)


def _net_delta_mb_per_call() -> tuple[float, float]:
    """Return (sent_mb, recv_mb) since the previous call. Zeros on first call."""
    global _last_net
    counters = psutil.net_io_counters()
    now = time.monotonic()
    if _last_net is None:
        _last_net = (now, counters.bytes_sent, counters.bytes_recv)
        return (0.0, 0.0)
    prev_ts, prev_sent, prev_recv = _last_net
    sent_delta = max(0, counters.bytes_sent - prev_sent)
    recv_delta = max(0, counters.bytes_recv - prev_recv)
    _last_net = (now, counters.bytes_sent, counters.bytes_recv)
    return (_mb(sent_delta), _mb(recv_delta))


# ---- Builders --------------------------------------------------------------


def _build_basic() -> str:
    """One-line summary suitable for inline mention in a chat reply."""
    if not _PSUTIL_OK:
        return _PSUTIL_ERR
    try:
        cpu_total = psutil.cpu_percent(interval=None)  # non-blocking sample
        mem = psutil.virtual_memory()
        # Pick the "primary" disk: on Windows that's C:\, on POSIX it's /.
        # Fall back to the first usable partition if neither is mounted.
        primary_path = "C:\\" if platform.system() == "Windows" else "/"
        try:
            disk = psutil.disk_usage(primary_path)
        except Exception:
            for part in psutil.disk_partitions(all=False):
                try:
                    disk = psutil.disk_usage(part.mountpoint)
                    primary_path = part.mountpoint
                    break
                except Exception:
                    continue
            else:  # no break
                disk = None  # type: ignore
        sent_mb, recv_mb = _net_delta_mb_per_call()

        parts = [
            f"CPU {cpu_total:.1f}%",
            f"RAM {mem.percent:.1f}% ({_gb(mem.used)}/{_gb(mem.total)} GB)",
        ]
        if disk is not None:
            parts.append(f"Disk({primary_path.rstrip(chr(92))}) {disk.percent:.1f}%")
        parts.append(f"Net delta in/out {recv_mb:.1f}/{sent_mb:.1f} MB")
        return " | ".join(parts)
    except Exception as e:
        return f"system_monitor failed: {e}"


def _build_detailed() -> str:
    """Multi-section breakdown for diagnostics or when the user asks for full."""
    if not _PSUTIL_OK:
        return _PSUTIL_ERR
    try:
        os_label = f"{platform.system()}-{platform.machine()}"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"## System health ({os_label}, {ts}, uptime {_uptime_phrase()})"

        # CPU: total + per-core. interval=None uses the previous call's
        # baseline, so the first reading after import is 0.0 — sample once
        # with a tiny interval to seed it.
        cpu_total = psutil.cpu_percent(interval=0.1)
        cpu_per = psutil.cpu_percent(interval=None, percpu=True)
        cpu_block = (
            "CPU\n"
            f"  total: {cpu_total:.1f}%\n"
            f"  cores: {[round(x, 1) for x in cpu_per]}"
        )

        # Memory + swap
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        mem_block = (
            "Memory\n"
            f"  total: {_gb(mem.total):.2f} GB | used: {_gb(mem.used):.2f} GB "
            f"({mem.percent:.1f}%) | free: {_gb(mem.available):.2f} GB\n"
            f"  swap : used {_gb(swap.used):.2f} GB ({swap.percent:.1f}%)"
        )

        # Disk: every real partition. Skip CD-ROMs / removable drives that
        # error out (common on Windows when no media is inserted).
        disk_lines = ["Disk"]
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            disk_lines.append(
                f"  {part.mountpoint:<6s} total {_gb(u.total):.2f} GB | "
                f"used {_gb(u.used):.2f} GB ({u.percent:.1f}%) | "
                f"free {_gb(u.free):.2f} GB"
            )
        disk_block = "\n".join(disk_lines) if len(disk_lines) > 1 else "Disk\n  (no readable partitions)"

        # Network: since-boot totals. Cheaper than a real per-second rate and
        # gives Andrew a sense of overall throughput.
        net = psutil.net_io_counters()
        net_block = (
            "Network (since boot)\n"
            f"  sent: {_gb(net.bytes_sent):.2f} GB | recv: {_gb(net.bytes_recv):.2f} GB | "
            f"packets sent/recv: {_human_count(net.packets_sent)} / {_human_count(net.packets_recv)}"
        )

        return "\n\n".join([header, cpu_block, mem_block, disk_block, net_block])
    except Exception as e:
        return f"system_monitor failed: {e}"


# ---- Public entry points ---------------------------------------------------


def get_system_health(detail_level: str = "basic") -> str:
    """Direct callable for non-async contexts (the standalone test below)."""
    level = (detail_level or "basic").lower().strip()
    if level not in ("basic", "detailed"):
        level = "basic"
    return _cached(level, _build_basic if level == "basic" else _build_detailed)


async def run(**kwargs) -> str:
    """Dynamic-tool entry point. Awaitable wrapper around the sync builders."""
    detail_level = kwargs.get("detail_level", "basic")
    # The psutil calls are quick enough that we don't need a thread pool;
    # but keeping the function async satisfies the loader contract.
    return await asyncio.to_thread(get_system_health, detail_level)


# ---- Standalone test -------------------------------------------------------

if __name__ == "__main__":
    print("=== get_system_health(detail_level='basic') ===")
    print(get_system_health("basic"))
    print()
    print("=== get_system_health(detail_level='detailed') ===")
    print(get_system_health("detailed"))
