"""Persistent monitor daemon for rollout-shield.

The daemon runs as a long-lived process. Each cycle:

1. Run every enabled health check (see ``health_checks.py``).
2. Persist the cycle result to the daily health log.
3. If any check failed, dispatch an alert via ``alerter.py``.
4. Sleep for the configured interval, then repeat.

The daemon is **resilient**:

- A transient error in one check does not stop the daemon — the
  exception is captured in the result record.
- All state writes are atomic (write-temp + rename for JSON,
  fsync + append for JSONL) so a crash mid-write cannot corrupt
  state.
- The daemon writes a heartbeat to ``<state_root>/daemon.json``
  so an external supervisor can detect a stuck process.

Run modes:

- ``python -m rollout_shield monitor --once`` — single cycle, exit
- ``python -m rollout_shield monitor --daemon`` — long-running
- ``python -m rollout_shield monitor --daemon --foreground`` — long-running, log to stderr (for systemd / launchd)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from .alerter import dispatch_alert
from .health_checks import aggregate, run_all_checks
from .host_checks import run_host_checks
from .repo_checks import run_repo_checks
from .state import State


DAEMON_HEARTBEAT_FILE = "daemon.json"


class Daemon:
    """Long-running monitor daemon."""

    def __init__(self, state: State, interval: int, webhook_url: str = "",
                 disabled_checks: list[str] | None = None,
                 self_heal_enabled: bool = True,
                 self_heal_interval_cycles: int = 5):
        self.state = state
        self.interval = max(1, interval)
        self.webhook_url = webhook_url
        self.disabled_checks = disabled_checks or []
        self.self_heal_enabled = self_heal_enabled
        self.self_heal_interval_cycles = max(1, self_heal_interval_cycles)
        self._stop = False
        self.heartbeat_path = self.state.root / DAEMON_HEARTBEAT_FILE
        self._register_signal_handlers()

    # ---------- signal handling ----------

    def _register_signal_handlers(self) -> None:
        def _handle(signum, _frame):
            print(f"[daemon] received signal {signum}; finishing current cycle then exiting",
                  file=sys.stderr)
            self._stop = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):
                # not in main thread (e.g., during tests) — skip
                pass

    # ---------- heartbeat ----------

    def _write_heartbeat(self, cycle: int, last_status: str) -> None:
        from .state import atomic_write_json
        atomic_write_json(self.heartbeat_path, {
            "pid": os.getpid(),
            "cycle": cycle,
            "last_status": last_status,
            "last_beat_ts": int(time.time()),
            "interval_seconds": self.interval,
            "state_root": str(self.state.root),
        })

    # ---------- one cycle ----------

    def _maybe_self_heal(self, cycle: int, summary: dict) -> None:
        """Every N cycles, run self-heal if anything failed.

        Closed-loop healing: when a check fails repeatedly, attempt a
        deterministic repair. Failures-of-repair themselves are logged
        but never crash the daemon.
        """
        if not self.self_heal_enabled:
            return
        if cycle == 0:
            return  # never self-heal on the bootstrap cycle
        if cycle % self.self_heal_interval_cycles != 0:
            return
        if summary["status"] == "healthy":
            return
        try:
            from .commands.self_heal import run_self_heal
            heal_summary = run_self_heal(
                self.state,
                dry_run=False,
                auto_repair=True,
                include_path_repair=False,  # don't mutate the user's shell rc
            )
            # If anything was repaired, append a follow-up alert so the
            # operator sees it on the timeline.
            if heal_summary["repairs_attempted"] > 0:
                alert = {
                    "severity": "info" if heal_summary["all_healthy"] else "warning",
                    "source": "monitor_daemon.self_heal",
                    "message": (f"self-heal cycle: "
                                f"{heal_summary['repairs_fixed']} fixed, "
                                f"{heal_summary['repairs_unfixed']} unfixed"),
                    "summary": heal_summary,
                }
                dispatch_alert(self.state, alert, webhook_url=self.webhook_url)
        except Exception as exc:  # noqa: BLE001
            err_alert = {
                "severity": "warning",
                "source": "monitor_daemon.self_heal",
                "message": f"self-heal cycle raised: {exc}",
            }
            dispatch_alert(self.state, err_alert, webhook_url=self.webhook_url)

    def run_once(self, cycle: int = 0) -> dict:
        # Run THREE classes of checks every cycle:
        #   1. rollout-shield state checks  (observe the runtime)
        #   2. host kernel checks          (observe the user's machine + OS)
        #   3. repo-level checks           (observe the repo as a self-managing tool)
        results = run_all_checks(self.state, disabled=self.disabled_checks)
        results += run_host_checks(self.state, disabled=self.disabled_checks)
        results += run_repo_checks(self.state, disabled=self.disabled_checks)
        summary = aggregate(results)
        self.state.append_health(summary)
        self._write_heartbeat(cycle, summary["status"])
        if summary["status"] != "healthy":
            alert = {
                "severity": "error" if summary["status"] == "unhealthy" else "warning",
                "source": "monitor_daemon",
                "message": f"health status: {summary['status']} ({summary['degraded']}/{summary['total']} checks degraded)",
                "summary": summary,
            }
            dispatch_alert(self.state, alert, webhook_url=self.webhook_url)
        # Drain the webhook outbox once per cycle (best-effort; never fails the cycle)
        try:
            from .webhook_delivery.dispatcher import run_once as webhook_run_once
            webhook_run_once(self.state)
        except Exception as exc:  # noqa: BLE001
            print(f"[daemon] webhook drain raised: {exc}", file=sys.stderr)
        # Closed-loop: periodically attempt repairs
        self._maybe_self_heal(cycle, summary)
        return summary

    # ---------- main loop ----------

    def run_forever(self) -> None:
        print(f"[daemon] starting rollout-shield monitor (interval={self.interval}s, "
              f"state={self.state.root})", file=sys.stderr)
        cycle = 0
        while not self._stop:
            cycle += 1
            try:
                summary = self.run_once(cycle)
                print(f"[daemon] cycle {cycle}: status={summary['status']} "
                      f"({summary['ok']}/{summary['total']} ok)", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 — never let the daemon die
                err_alert = {
                    "severity": "critical",
                    "source": "monitor_daemon",
                    "message": f"daemon cycle raised: {exc}",
                    "details": {"exception": repr(exc), "cycle": cycle},
                }
                dispatch_alert(self.state, err_alert, webhook_url=self.webhook_url)
                print(f"[daemon] cycle {cycle}: EXCEPTION {exc}", file=sys.stderr)
            # sleep in small chunks so SIGTERM is responsive
            slept = 0
            while slept < self.interval and not self._stop:
                step = min(1, self.interval - slept)
                time.sleep(step)
                slept += step
        print(f"[daemon] stopped after {cycle} cycles", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rollout-shield monitor")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit")
    parser.add_argument("--daemon", action="store_true",
                        help="run as a long-lived daemon")
    parser.add_argument("--interval", type=int, default=60,
                        help="cycle interval in seconds (default 60)")
    parser.add_argument("--state-root", type=Path, default=None,
                        help="override state root directory")
    parser.add_argument("--webhook-url", default="",
                        help="alert webhook URL")
    parser.add_argument("--disabled-checks", default="",
                        help="comma-separated list of check names to disable")
    parser.add_argument("--json", action="store_true",
                        help="output result as JSON")
    args = parser.parse_args(argv)

    state = State(root=args.state_root)
    cfg = state.load_config()
    if not args.webhook_url:
        args.webhook_url = cfg.get("alert_webhook_url", "")
    interval = args.interval or cfg.get("monitor_interval_seconds", 60)
    disabled = [c.strip() for c in args.disabled_checks.split(",") if c.strip()]
    self_heal_enabled = bool(cfg.get("self_heal_enabled", True))
    self_heal_interval_cycles = int(cfg.get("self_heal_interval_cycles", 5))

    daemon = Daemon(state, interval=interval,
                    webhook_url=args.webhook_url,
                    disabled_checks=disabled,
                    self_heal_enabled=self_heal_enabled,
                    self_heal_interval_cycles=self_heal_interval_cycles)

    if args.once:
        summary = daemon.run_once(cycle=0)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"status: {summary['status']} "
                  f"({summary['ok']}/{summary['total']} ok)")
            for c in summary["checks"]:
                mark = "OK" if c["ok"] else "FAIL"
                print(f"  [{mark}] {c['name']}: {c['message']}")
        return 0 if summary["status"] == "healthy" else 1

    if args.daemon or not args.once:
        daemon.run_forever()
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
