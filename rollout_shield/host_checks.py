"""Kernel / host-aware health checks for rollout-shield.

These checks observe the user's actual hardware + software environment,
not just the rollout-shield state. They read:

- ``/proc/loadavg``     — system load (1m / 5m / 15m)
- ``/proc/meminfo``      — memory pressure
- ``/proc/stat``         — cumulative CPU time + idle ratio
- ``/proc/net/dev``      — per-interface byte counters
- ``/proc/net/tcp``      — listening-socket count
- ``/proc/mounts``       — mount table (for disk-by-mount)
- ``os.getloadavg()``    — convenience load accessor
- ``resource.getrusage`` — process-level CPU/memory (this process)
- ``psutil`` (optional)  — process list, network, per-CPU; gracefully
                          skipped if not installed

Each check is independent and read-only. A failing check does not
affect any other check.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

from .health_checks import HealthResult
from .state import State

# ---------- helpers ----------


def _read_text(path: str | Path, max_bytes: int = 64 * 1024) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(max_bytes)
    except OSError:
        return ""


def _parse_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in _read_text("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        # value is like "16384256 kB"
        parts = val.split()
        try:
            n = int(parts[0])
        except (ValueError, IndexError):
            continue
        unit = parts[1] if len(parts) > 1 else ""
        if unit.lower() == "kb":
            n *= 1024
        out[key] = n
    return out


def _loadavg() -> tuple[float, float, float]:
    try:
        one, five, fifteen = os.getloadavg()
        return one, five, fifteen
    except (OSError, AttributeError):
        return 0.0, 0.0, 0.0


def _cpu_count() -> int:
    return os.cpu_count() or 1


# ---------- checks ----------


def check_load_average(state: State, per_cpu_threshold: float = 2.0) -> HealthResult:
    """System load average is below per_cpu_threshold × ncpu."""
    start = time.perf_counter()
    one, five, fifteen = _loadavg()
    ncpu = _cpu_count()
    per_cpu = one / ncpu if ncpu else one
    ok = per_cpu <= per_cpu_threshold
    return HealthResult(
        name="load_average",
        ok=ok,
        message=f"load1={one:.2f} (per_cpu={per_cpu:.2f}, ncpu={ncpu}, threshold={per_cpu_threshold})",
        details={"load1": one, "load5": five, "load15": fifteen,
                 "ncpu": ncpu, "per_cpu": per_cpu, "threshold": per_cpu_threshold},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_memory_pressure(state: State,
                          used_pct_threshold: float = 90.0) -> HealthResult:
    """Memory used < used_pct_threshold % of total."""
    start = time.perf_counter()
    mi = _parse_meminfo()
    total = mi.get("MemTotal", 0)
    avail = mi.get("MemAvailable", 0)
    if total <= 0:
        return HealthResult(
            name="memory_pressure",
            ok=False,
            message="could not read MemTotal",
            details={},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    used = total - avail
    used_pct = (used / total) * 100.0
    ok = used_pct <= used_pct_threshold
    return HealthResult(
        name="memory_pressure",
        ok=ok,
        message=f"memory used {used_pct:.1f}% (threshold {used_pct_threshold}%)",
        details={"total_bytes": total, "available_bytes": avail,
                 "used_bytes": used, "used_pct": used_pct,
                 "threshold_pct": used_pct_threshold},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_listening_sockets(state: State,
                            min_sockets: int = 1,
                            max_sockets: int = 5000) -> HealthResult:
    """Count of TCP sockets in LISTEN state is within sane bounds."""
    start = time.perf_counter()
    count = 0
    for line in _read_text("/proc/net/tcp").splitlines()[1:]:
        # fields: sl  local_address rem_address st ...
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            state_code = int(parts[3], 16)
        except ValueError:
            continue
        if state_code == 0x0A:  # TCP_LISTEN
            count += 1
    ok = min_sockets <= count <= max_sockets
    return HealthResult(
        name="listening_sockets",
        ok=ok,
        message=f"{count} TCP socket(s) in LISTEN (expected {min_sockets}..{max_sockets})",
        details={"listen_count": count, "min": min_sockets, "max": max_sockets},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_uptime(state: State, max_uptime_days: int = 365) -> HealthResult:
    """System uptime is below max_uptime_days (warns about machines that
    haven't been rebooted in a long time)."""
    start = time.perf_counter()
    content = _read_text("/proc/uptime")
    try:
        up_seconds = float(content.split()[0])
    except (ValueError, IndexError):
        return HealthResult(
            name="uptime",
            ok=False,
            message="could not read /proc/uptime",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    days = up_seconds / 86400.0
    ok = days <= max_uptime_days
    return HealthResult(
        name="uptime",
        ok=ok,
        message=f"uptime {days:.1f} days (max {max_uptime_days})",
        details={"uptime_seconds": up_seconds, "uptime_days": days,
                 "max_days": max_uptime_days},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_disk_mounts(state: State,
                      min_free_pct: float = 10.0) -> HealthResult:
    """All mounted filesystems have at least min_free_pct % free."""
    start = time.perf_counter()
    import shutil
    mounts: list[dict] = []
    worst: dict | None = None
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point = parts[1]
                fstype = parts[2]
                if fstype in ("proc", "sysfs", "devtmpfs", "devpts",
                              "tmpfs", "cgroup", "cgroup2"):
                    continue
                try:
                    usage = shutil.disk_usage(mount_point)
                except OSError:
                    continue
                if usage.total <= 0:
                    continue
                free_pct = (usage.free / usage.total) * 100.0
                entry = {"mount": mount_point, "fstype": fstype,
                         "free_pct": round(free_pct, 2),
                         "free_bytes": usage.free}
                mounts.append(entry)
                if worst is None or entry["free_pct"] < worst["free_pct"]:
                    worst = entry
    except OSError as exc:
        return HealthResult(
            name="disk_mounts",
            ok=False,
            message=f"could not read /proc/mounts: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    ok = worst is None or worst["free_pct"] >= min_free_pct
    return HealthResult(
        name="disk_mounts",
        ok=ok,
        message=(f"worst mount: {worst['mount']} @ {worst['free_pct']:.1f}% free"
                 if worst else "no relevant mounts found"),
        details={"mounts": mounts, "worst": worst, "min_free_pct": min_free_pct},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_dns_resolves(state: State, hostname: str = "github.com",
                       timeout: float = 2.0) -> HealthResult:
    """DNS resolves a known hostname to an IP address."""
    start = time.perf_counter()
    try:
        socket.setdefaulttimeout(timeout)
        info = socket.getaddrinfo(hostname, None)
        ips = sorted({(entry[4][0] if isinstance(entry[4], tuple) else entry[4][0])
                      for entry in info})
        return HealthResult(
            name="dns_resolves",
            ok=bool(ips),
            message=f"{hostname} → {', '.join(ips[:3])}",
            details={"hostname": hostname, "ips": ips},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except (socket.gaierror, TimeoutError, OSError) as exc:
        return HealthResult(
            name="dns_resolves",
            ok=False,
            message=f"{hostname}: {exc}",
            details={"hostname": hostname},
            duration_ms=(time.perf_counter() - start) * 1000,
        )


# ---------- registry ----------


HOST_CHECKS = [
    check_load_average,
    check_memory_pressure,
    check_listening_sockets,
    check_uptime,
    check_disk_mounts,
    check_dns_resolves,
]


def run_host_checks(state: State,
                    disabled: list[str] | None = None) -> list[dict]:
    """Run all enabled host checks and return their results as dicts."""
    disabled = set(disabled or [])
    out: list[dict] = []
    for check in HOST_CHECKS:
        name = check.__name__.replace("check_", "")
        if name in disabled:
            continue
        try:
            result = check(state)
            out.append(result.to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append({
                "name": name,
                "ok": False,
                "message": f"check raised: {exc}",
                "duration_ms": 0.0,
                "details": {"exception": repr(exc)},
            })
    return out


def all_checks_combined(state: State,
                        disabled: list[str] | None = None) -> list[dict]:
    """Run both rollout-shield state checks and host kernel checks."""
    from .health_checks import run_all_checks
    return run_all_checks(state, disabled=disabled) + run_host_checks(state, disabled=disabled)


def aggregate_host_checks(results: list[dict]) -> dict:
    """Aggregate a list of host check results into a top-level health summary."""
    n = len(results)
    ok = sum(1 for r in results if r.get("ok"))
    degraded = sum(1 for r in results if not r.get("ok"))
    status = "healthy" if degraded == 0 else ("degraded" if ok > 0 else "unhealthy")
    return {
        "status": status,
        "total": n,
        "ok": ok,
        "degraded": degraded,
        "checks": results,
        "ts": int(time.time()),
    }
