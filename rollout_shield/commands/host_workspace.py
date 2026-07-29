"""Cross-cut view of the two workspaces: repo + host kernel.

The user_hardware+software_kernel-workspace is the runtime substrate:
- the host machine (CPU, memory, disk, network)
- the OS kernel + userspace
- the user's home directory

The repo-workspace is the artifact:
- the source tree (current branch + last commit)
- the issue tracker state (bd ready / open counts)
- the spec / runtime code

This command joins both views into a single printout so the operator
sees the full state of the system at a glance.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..host_checks import run_host_checks
from ..state import State


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", repr(exc)


def _repo_state() -> dict:
    """Read state from the repo workspace (current working dir if it's a git repo)."""
    cwd = Path.cwd()
    info: dict = {"cwd": str(cwd), "is_git_repo": (cwd / ".git").exists()}
    if info["is_git_repo"]:
        rc, out, _err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        info["branch"] = out if rc == 0 else "(unknown)"
        rc, out, _err = _run(["git", "rev-parse", "HEAD"], cwd=cwd)
        info["commit"] = out if rc == 0 else "(unknown)"
        rc, out, _err = _run(["git", "log", "-1", "--format=%s"], cwd=cwd)
        info["last_commit_subject"] = out if rc == 0 else ""
        rc, out, _err = _run(["git", "status", "--porcelain"], cwd=cwd)
        info["dirty"] = bool(out)
        info["dirty_files"] = out.splitlines()[:10]
    # bd counts (best effort)
    bd_info: dict = {}
    rc, out, _err = _run(["bd", "ready"], cwd=cwd)
    if rc == 0:
        bd_info["ready_lines"] = len([ln for ln in out.splitlines() if ln.strip()])
    rc, out, _err = _run(["bd", "list", "--status=open", "--json"], cwd=cwd)
    if rc == 0:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                bd_info["open_count"] = len(data)
        except json.JSONDecodeError:
            pass
    info["beads"] = bd_info
    return info


def _host_state(state: State) -> dict:
    """Read state from the host kernel + userspace."""
    info: dict = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
        "user": os.environ.get("USER", "(unknown)"),
        "home": str(Path.home()),
        "state_root": str(state.root),
        "uptime_seconds": None,
        "load_average": None,
        "memory": {},
    }
    # uptime
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            up = float(fh.read().split()[0])
        info["uptime_seconds"] = up
        info["uptime_human"] = f"{int(up // 86400)}d {int((up % 86400) // 3600)}h {int((up % 3600) // 60)}m"
    except (OSError, ValueError):
        pass
    # load
    try:
        info["load_average"] = list(os.getloadavg())
    except (OSError, AttributeError):
        pass
    # memory
    mem: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                parts = val.strip().split()
                try:
                    n = int(parts[0])
                except (ValueError, IndexError):
                    continue
                unit = parts[1] if len(parts) > 1 else ""
                if unit.lower() == "kb":
                    n *= 1024
                mem[key.strip()] = n
    except OSError:
        pass
    info["memory"] = mem
    if mem.get("MemTotal") and mem.get("MemAvailable"):
        used = mem["MemTotal"] - mem["MemAvailable"]
        info["memory_used_pct"] = round(used / mem["MemTotal"] * 100.0, 1)
    # disk
    try:
        usage = shutil.disk_usage(Path.home())
        info["disk_home"] = {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "free_pct": round(usage.free / usage.total * 100.0, 1) if usage.total else 0,
        }
    except OSError:
        pass
    # daemon heartbeat
    hb_path = state.root / "daemon.json"
    if hb_path.exists():
        try:
            info["daemon_heartbeat"] = json.loads(hb_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return info


def cmd_host_workspace(state: State, args: argparse.Namespace) -> int:
    repo = _repo_state()
    host = _host_state(state)
    summary = state.summary()
    payload = {
        "generated_at": int(time.time()),
        "repo_workspace": repo,
        "host_workspace": host,
        "rollout_shield": summary,
    }
    if args.include_checks:
        from ..health_checks import run_all_checks
        checks = run_all_checks(state) + run_host_checks(state)
        from ..health_checks import aggregate
        payload["health_checks"] = aggregate(checks)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    # human-readable
    print("rollout-shield: cross-workspace view")
    print("=" * 60)
    print()
    print("REPO WORKSPACE")
    print(f"  cwd:           {repo['cwd']}")
    print(f"  is_git_repo:   {repo['is_git_repo']}")
    if repo.get("branch"):
        print(f"  branch:        {repo['branch']}")
        print(f"  commit:        {repo['commit']}")
        if repo.get("last_commit_subject"):
            print(f"  last commit:   {repo['last_commit_subject']}")
        print(f"  dirty:         {repo.get('dirty', False)}")
    bd = repo.get("beads", {})
    if bd:
        print(f"  bd ready:      {bd.get('ready_lines', '?')}")
        print(f"  bd open:       {bd.get('open_count', '?')}")
    print()
    print("HOST WORKSPACE (kernel + userspace)")
    print(f"  hostname:      {host['hostname']}")
    print(f"  platform:      {host['platform']}")
    print(f"  python:        {host['python']}")
    print(f"  user:          {host['user']}")
    print(f"  home:          {host['home']}")
    if host.get("uptime_human"):
        print(f"  uptime:        {host['uptime_human']}")
    if host.get("load_average"):
        la = host["load_average"]
        print(f"  load:          {la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}")
    if host.get("memory_used_pct") is not None:
        print(f"  memory used:   {host['memory_used_pct']}%")
    if host.get("disk_home"):
        d = host["disk_home"]
        print(f"  home disk:     {d['used_gb']}/{d['total_gb']} GB ({d['free_pct']}% free)")
    hb = host.get("daemon_heartbeat")
    if hb:
        print(f"  daemon:        cycle={hb.get('cycle')} status={hb.get('last_status')} "
              f"last_beat={hb.get('last_beat_ts')}")
    else:
        print("  daemon:        not running")
    print()
    print("ROLLOUT-SHIELD STATE")
    print(f"  state root:    {summary['state_root']}")
    print(f"  agents:        {summary['agents']['total']}")
    print(f"  claims:        {summary['claims_count']}")
    print(f"  alerts:        {summary['alerts_count']}")
    return 0
