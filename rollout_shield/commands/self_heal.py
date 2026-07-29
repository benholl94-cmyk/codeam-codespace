"""Self-heal: diagnose and automatically repair common rollout-shield issues.

The goal is a **closed-loop** runtime: when something drifts (state dir
removed, keys chmod wrong, PATH lost, daemon heartbeat stuck), the system
attempts a deterministic repair instead of waiting for the user to notice.

Design:

- Each **check** is a pure function returning ``(ok, message)``.
- Each **repair** is a function that attempts to fix one issue and returns
  ``(ok, message, actions_taken)``.
- ``cmd_self_heal`` runs all checks; for every failing check it invokes
  the repair; then re-runs to confirm. The exit code is 0 when every
  failing check was successfully repaired (or no check failed at all).

Idempotency: every repair is idempotent — running twice is safe.

Safety:

- Repairs only touch files inside the state root (``~/.rollout-shield``)
  and the user-local install prefix (``~/usr/``). They never delete
  data — they create missing dirs, fix permissions, or rewrite
  config defaults.
- Repairs that would have user-visible effects (PATH hint, systemd
  unit enable) are opt-in via flags.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..state import State

PREFIX_DEFAULT = Path.home() / "usr"


# ---------- diagnostic + repair records ----------


@dataclass
class CheckRecord:
    name: str
    ok: bool
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class RepairRecord:
    name: str
    invoked: bool
    ok: bool
    message: str
    actions: list[str] = field(default_factory=list)


# ---------- check + repair helpers ----------


def _check(name: str) -> Callable:
    """Decorator to register a check + optional repair pair."""
    def wrap(fn: Callable) -> Callable:
        CHECKS[name] = fn
        return fn
    return wrap


def _repair_for(name: str) -> Callable:
    def wrap(fn: Callable) -> Callable:
        REPAIRS[name] = fn
        return fn
    return wrap


CHECKS: dict[str, Callable[[State], CheckRecord]] = {}
REPAIRS: dict[str, Callable[[State], RepairRecord]] = {}


# ---------- checks ----------


@_check("state_root_writable")
def check_state_root_writable(state: State) -> CheckRecord:
    test = state.root / ".write-test"
    try:
        test.write_text("ok")
        test.unlink()
        return CheckRecord("state_root_writable", True,
                           f"state root {state.root} writable",
                           {})
    except OSError as exc:
        return CheckRecord("state_root_writable", False,
                           f"state root not writable: {exc}",
                           {"state_root": str(state.root)})


@_repair_for("state_root_writable")
def repair_state_root_writable(state: State) -> RepairRecord:
    actions: list[str] = []
    try:
        state.root.mkdir(parents=True, exist_ok=True)
        actions.append(f"mkdir -p {state.root}")
        # re-test
        test = state.root / ".write-test"
        test.write_text("ok")
        test.unlink()
        return RepairRecord("state_root_writable", True, True,
                            f"created {state.root}", actions)
    except OSError as exc:
        return RepairRecord("state_root_writable", True, False,
                            f"repair failed: {exc}", actions)


@_check("state_subdirs_present")
def check_state_subdirs_present(state: State) -> CheckRecord:
    required = ["claims", "alerts", "keys", "keys_material", "health", "ai"]
    missing = [d for d in required if not (state.root / d).is_dir()]
    if missing:
        return CheckRecord("state_subdirs_present", False,
                           f"missing dirs: {missing}",
                           {"missing": missing, "state_root": str(state.root)})
    return CheckRecord("state_subdirs_present", True,
                       "all required state subdirs present",
                       {"checked": required})


@_repair_for("state_subdirs_present")
def repair_state_subdirs_present(state: State) -> RepairRecord:
    actions: list[str] = []
    required = ["claims", "alerts", "keys", "keys_material", "health", "ai"]
    try:
        for d in required:
            target = state.root / d
            if not target.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                actions.append(f"mkdir -p {target}")
        # re-test
        rec = check_state_subdirs_present(state)
        return RepairRecord("state_subdirs_present", True, rec.ok, rec.message, actions)
    except OSError as exc:
        return RepairRecord("state_subdirs_present", True, False,
                            f"repair failed: {exc}", actions)


@_check("keys_material_perms")
def check_keys_material_perms(state: State) -> CheckRecord:
    p = state.root / "keys_material"
    if not p.is_dir():
        return CheckRecord("keys_material_perms", False, "keys_material/ missing",
                           {"path": str(p)})
    mode = p.stat().st_mode & 0o777
    if mode != 0o700:
        return CheckRecord("keys_material_perms", False,
                           f"keys_material/ mode={oct(mode)} (want 0o700)",
                           {"mode": oct(mode)})
    return CheckRecord("keys_material_perms", True,
                       f"keys_material/ mode={oct(mode)}", {})


@_repair_for("keys_material_perms")
def repair_keys_material_perms(state: State) -> RepairRecord:
    p = state.root / "keys_material"
    actions: list[str] = []
    try:
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, 0o700)
        actions.append(f"chmod 700 {p}")
        rec = check_keys_material_perms(state)
        return RepairRecord("keys_material_perms", True, rec.ok, rec.message, actions)
    except OSError as exc:
        return RepairRecord("keys_material_perms", True, False,
                            f"repair failed: {exc}", actions)


@_check("config_present")
def check_config_present(state: State) -> CheckRecord:
    if not state.config_path.exists():
        return CheckRecord("config_present", False,
                           f"config.json missing at {state.config_path}",
                           {"path": str(state.config_path)})
    try:
        cfg = state.load_config()
    except (OSError, ValueError) as exc:
        return CheckRecord("config_present", False,
                           f"config.json unreadable: {exc}",
                           {"path": str(state.config_path)})
    if "schema_version" not in cfg:
        return CheckRecord("config_present", False,
                           "config.json missing schema_version",
                           {"path": str(state.config_path)})
    return CheckRecord("config_present", True, "config.json present + valid",
                       {"schema_version": cfg.get("schema_version")})


@_repair_for("config_present")
def repair_config_present(state: State) -> RepairRecord:
    actions: list[str] = []
    try:
        # state.save_config writes defaults if the file is missing
        cfg = state.load_config() if state.config_path.exists() else {}
        cfg.setdefault("schema_version", 1)
        cfg.setdefault("monitor_interval_seconds", 60)
        cfg.setdefault("alert_webhook_url", "")
        cfg.setdefault("claim_retention_days", 2555)
        cfg.setdefault("health_window_seconds", 300)
        cfg.setdefault("reputation_decay_days", 30)
        cfg.setdefault("self_heal_enabled", True)
        cfg.setdefault("self_heal_interval_cycles", 5)
        state.save_config(cfg)
        actions.append(f"write default config → {state.config_path}")
        rec = check_config_present(state)
        return RepairRecord("config_present", True, rec.ok, rec.message, actions)
    except OSError as exc:
        return RepairRecord("config_present", True, False,
                            f"repair failed: {exc}", actions)


@_check("at_least_one_key")
def check_at_least_one_key(state: State) -> CheckRecord:
    keys = state.list_keys()
    if not keys:
        return CheckRecord("at_least_one_key", False,
                           "no agent keys registered", {"count": 0})
    return CheckRecord("at_least_one_key", True,
                       f"{len(keys)} key(s) registered",
                       {"count": len(keys), "ids": [k.get("id") for k in keys]})


@_repair_for("at_least_one_key")
def repair_at_least_one_key(state: State) -> RepairRecord:
    from .keys import cmd_keys_new
    actions: list[str] = []
    try:
        key_id = cmd_keys_new(state, agent_id="default",
                              description="default key (auto-generated by self-heal)")
        actions.append(f"generated key {key_id}")
        rec = check_at_least_one_key(state)
        return RepairRecord("at_least_one_key", True, rec.ok, rec.message, actions)
    except Exception as exc:  # noqa: BLE001
        return RepairRecord("at_least_one_key", True, False,
                            f"repair failed: {exc}", actions)


@_check("daemon_heartbeat_fresh")
def check_daemon_heartbeat_fresh(state: State, max_age_s: int = 300) -> CheckRecord:
    hb_path = state.root / "daemon.json"
    if not hb_path.exists():
        return CheckRecord("daemon_heartbeat_fresh", False,
                           "daemon.json missing (daemon never started?)",
                           {})
    try:
        import json as _json
        with open(hb_path, encoding="utf-8") as fh:
            hb = _json.load(fh)
        last = int(hb.get("last_beat_ts", 0))
        age = int(time.time()) - last
    except (OSError, ValueError) as exc:
        return CheckRecord("daemon_heartbeat_fresh", False,
                           f"daemon.json unreadable: {exc}", {})
    if age > max_age_s:
        return CheckRecord("daemon_heartbeat_fresh", False,
                           f"daemon heartbeat stale ({age}s old, max {max_age_s}s)",
                           {"age_seconds": age, "max_age_s": max_age_s})
    return CheckRecord("daemon_heartbeat_fresh", True,
                       f"daemon heartbeat fresh ({age}s old)",
                       {"age_seconds": age})


@_repair_for("daemon_heartbeat_fresh")
def repair_daemon_heartbeat_fresh(state: State) -> RepairRecord:
    # self-heal cannot actually restart a daemon — that requires the user
    # to launch it. We can only flag that it is stale.
    return RepairRecord(
        "daemon_heartbeat_fresh", True, False,
        "stale heartbeat cannot be auto-repaired; run "
        "`rollout-shield monitor --daemon` (or systemd --user start "
        "rollout-shield) to resume",
        ["(manual action required)"],
    )


@_check("cli_in_path")
def check_cli_in_path(prefix: Path = PREFIX_DEFAULT) -> CheckRecord:
    target = prefix / "bin"
    on_path = str(target) in (os.environ.get("PATH") or "").split(":")
    if on_path:
        return CheckRecord("cli_in_path", True,
                           f"{target} is on PATH", {})
    return CheckRecord("cli_in_path", False,
                       f"{target} is NOT on PATH",
                       {"target": str(target)})


@_repair_for("cli_in_path")
def repair_cli_in_path(prefix: Path = PREFIX_DEFAULT) -> RepairRecord:
    # self-heal cannot reliably mutate the user's shell rc files — that
    # is opt-in (the install script prints the hint, but does not edit).
    # We mark this as failed with a clear instruction.
    target = prefix / "bin"
    return RepairRecord(
        "cli_in_path", True, False,
        f"add {target} to PATH; e.g. add to ~/.bashrc: "
        f"export PATH=\"{target}:$PATH\"",
        ["(manual action required)"],
    )


@_check("prefix_install_present")
def check_prefix_install_present(prefix: Path = PREFIX_DEFAULT) -> CheckRecord:
    shim = prefix / "bin" / "rollout-shield"
    pkg = prefix / "lib" / "python" / "rollout_shield"
    missing = []
    if not shim.exists():
        missing.append(str(shim))
    if not pkg.is_dir():
        missing.append(str(pkg))
    if missing:
        return CheckRecord("prefix_install_present", False,
                           f"install prefix incomplete; missing: {missing}",
                           {"missing": missing})
    return CheckRecord("prefix_install_present", True,
                       f"install at {prefix} intact",
                       {"prefix": str(prefix)})


@_repair_for("prefix_install_present")
def repair_prefix_install_present(state: State,
                                  prefix: Path = PREFIX_DEFAULT) -> RepairRecord:
    # Repair here is "find the repo and reinstall" — useful when running
    # from inside the repo source tree.
    actions: list[str] = []
    repo_root = _discover_repo_root()
    if not repo_root:
        return RepairRecord(
            "prefix_install_present", True, False,
            "could not locate repo source; rerun scripts/install.sh from the repo",
            ["(manual action required)"],
        )
    install_script = repo_root / "scripts" / "install.sh"
    if not install_script.exists():
        return RepairRecord(
            "prefix_install_present", True, False,
            f"install script not found at {install_script}",
            ["(manual action required)"],
        )
    # We do NOT actually invoke the install — that would be a heavy
    # side effect. Instead, we report what to run.
    actions.append(f"run: {install_script}")
    return RepairRecord(
        "prefix_install_present", True, False,
        f"to repair, run: bash {install_script}",
        actions,
    )


def _discover_repo_root() -> Path | None:
    """Find the rollout-shield repo root by walking up from CWD.

    Looks for the marker file ``rollout_shield/__init__.py`` in a parent
    directory. Returns None if not found.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "rollout_shield" / "__init__.py").exists() \
                and (parent / "scripts" / "install.sh").exists():
            return parent
    return None


@_check("repo_clean")
def check_repo_clean() -> CheckRecord:
    repo_root = _discover_repo_root()
    if not repo_root:
        return CheckRecord("repo_clean", True,
                           "(not in a git repo; skipped)", {})
    import subprocess as _sp
    try:
        proc = _sp.run(["git", "status", "--porcelain"], cwd=repo_root,
                       capture_output=True, text=True, timeout=5, check=False)
        out = proc.stdout.strip()
    except (FileNotFoundError, _sp.TimeoutExpired, OSError) as exc:
        return CheckRecord("repo_clean", True,
                           f"(git unavailable: {exc})", {})
    if out:
        return CheckRecord("repo_clean", False,
                           f"working tree dirty ({len(out.splitlines())} entries)",
                           {"dirty_count": len(out.splitlines())})
    return CheckRecord("repo_clean", True, "working tree clean",
                       {"repo_root": str(repo_root)})


@_check("monitoring_recent")
def check_monitoring_recent(state: State, max_age_s: int = 7200) -> CheckRecord:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = state.root / "health" / f"{today}.jsonl"
    if not log_path.exists():
        return CheckRecord("monitoring_recent", False,
                           f"no health log for today ({today}.jsonl)",
                           {"log_path": str(log_path)})
    try:
        age = time.time() - log_path.stat().st_mtime
    except OSError as exc:
        return CheckRecord("monitoring_recent", False,
                           f"cannot stat {log_path}: {exc}", {})
    if age > max_age_s:
        return CheckRecord("monitoring_recent", False,
                           f"last health entry {age:.0f}s old (max {max_age_s}s)",
                           {"age_seconds": age})
    return CheckRecord("monitoring_recent", True,
                       f"last health entry {age:.0f}s old",
                       {"age_seconds": age})


@_check("controller_policy")
def check_controller_policy(state: State) -> CheckRecord:
    """Active state is consistent with the controller policy."""
    from ..space import load_policy, validate_space
    try:
        policy = load_policy(state)
        consistent, violations = validate_space(state)
    except Exception as exc:  # noqa: BLE001
        return CheckRecord("controller_policy", False,
                           f"policy check failed: {exc}",
                           {"exception": repr(exc)})
    errors = [v for v in violations if v[0] == "error"]
    if not consistent:
        return CheckRecord(
            "controller_policy", False,
            f"policy={policy}: {len(errors)} active violations",
            {"policy": policy, "violations": violations},
        )
    return CheckRecord(
        "controller_policy", True,
        f"policy={policy}: state consistent ({len(violations)} advisory)",
        {"policy": policy, "violations": violations},
    )


@_repair_for("controller_policy")
def repair_controller_policy(state: State) -> RepairRecord:
    """Quarantine non-policy-compliant keys (does NOT delete them)."""
    from ..space import check_key_allowed, load_policy, quarantine_key
    policy = load_policy(state)
    actions: list[str] = []
    quarantined: list[str] = []
    try:
        for k in state.list_keys():
            if k.get("quarantined"):
                continue
            allowed, reason = check_key_allowed(policy, k)
            if not allowed:
                ok = quarantine_key(state, k["id"], reason=f"auto-quarantine: {reason}")
                if ok:
                    actions.append(f"quarantined {k['id']} ({reason})")
                    quarantined.append(k["id"])
        rec = check_controller_policy(state)
        return RepairRecord(
            "controller_policy", True, rec.ok, rec.message, actions,
        )
    except Exception as exc:  # noqa: BLE001
        return RepairRecord(
            "controller_policy", True, False,
            f"repair failed: {exc}", actions,
        )


# ---------- orchestration ----------


def run_self_heal(state: State, *, dry_run: bool, auto_repair: bool,
                  include_path_repair: bool) -> dict:
    """Run every registered check; for each failure, optionally repair.

    Returns a dict summary suitable for both human and JSON output.
    """
    checks_run: list[CheckRecord] = []
    repairs: list[RepairRecord] = []

    # Inspect each check's signature so we can dispatch with the right args.
    # Some checks want (state), some want (prefix), some want (state, prefix=).
    import inspect as _inspect
    for name, fn in CHECKS.items():
        sig = _inspect.signature(fn)
        params = list(sig.parameters.keys())
        try:
            if "prefix" in params and params[0] == "prefix":
                rec = fn(PREFIX_DEFAULT)
            elif "state" in params and params[0] == "state":
                rec = fn(state)
            else:
                rec = fn()
        except Exception as exc:  # noqa: BLE001
            rec = CheckRecord(name, False, f"check raised: {exc}",
                              {"exception": repr(exc)})
        checks_run.append(rec)

    failing = [r for r in checks_run if not r.ok]
    fixed: list[RepairRecord] = []
    unfixed: list[RepairRecord] = []

    if not auto_repair:
        for r in failing:
            repairs.append(RepairRecord(
                name=r.name, invoked=False, ok=False,
                message="skipped (auto_repair disabled)",
                actions=["(skipped)"],
            ))
    else:
        for r in failing:
            repair_fn = REPAIRS.get(r.name)
            if not repair_fn:
                repairs.append(RepairRecord(
                    name=r.name, invoked=True, ok=False,
                    message="no repair registered",
                    actions=["(no repair)"],
                ))
                unfixed.append(repairs[-1])
                continue

            # Some checks need extra args (prefix); sniff the signature
            try:
                import inspect
                sig = inspect.signature(repair_fn)
                params = list(sig.parameters.keys())
                if "prefix" in params:
                    rr = repair_fn(prefix=PREFIX_DEFAULT)
                else:
                    rr = repair_fn(state)
            except Exception as exc:  # noqa: BLE001
                repairs.append(RepairRecord(
                    name=r.name, invoked=True, ok=False,
                    message=f"repair raised: {exc}",
                    actions=[],
                ))
                unfixed.append(repairs[-1])
                continue

            repairs.append(rr)
            if rr.ok:
                fixed.append(rr)
            else:
                unfixed.append(rr)

    # Filter the (informational) PATH-hint repair when the user didn't ask
    if not include_path_repair:
        repairs = [r for r in repairs if r.name != "cli_in_path"]

    # After all repairs, re-run the checks that failed to confirm
    re_checks: list[CheckRecord] = []
    if auto_repair:
        for r in failing:
            fn = CHECKS[r.name]
            sig = _inspect.signature(fn)
            params = list(sig.parameters.keys())
            try:
                if "prefix" in params and params[0] == "prefix":
                    rec = fn(PREFIX_DEFAULT)
                elif "state" in params and params[0] == "state":
                    rec = fn(state)
                else:
                    rec = fn()
            except Exception as exc:  # noqa: BLE001
                rec = CheckRecord(r.name, False, f"check raised: {exc}",
                                  {"exception": repr(exc)})
            re_checks.append(rec)

    summary = {
        "dry_run": dry_run,
        "auto_repair": auto_repair,
        "checks_total": len(checks_run),
        "checks_ok": sum(1 for r in checks_run if r.ok),
        "checks_failing": len(failing),
        "repairs_attempted": sum(1 for r in repairs if r.invoked),
        "repairs_fixed": len(fixed),
        "repairs_unfixed": len(unfixed),
        # "all_healthy" = (nothing failing) OR (auto-repair ran AND all repairs succeeded).
        # In dry-run / no-repair mode, all_healthy reflects whether the original checks
        # passed (since no repair was attempted).
        "all_healthy": (
            len(failing) == 0
            if not auto_repair
            else (len(failing) == 0 and len(unfixed) == 0)
        ),
        "checks": [asdict(r) for r in checks_run],
        "repairs": [asdict(r) for r in repairs],
        "re_checks": [asdict(r) for r in re_checks],
        "ts": int(time.time()),
    }
    return summary


def cmd_self_heal(state: State, args: argparse.Namespace) -> int:
    summary = run_self_heal(
        state,
        dry_run=args.dry_run,
        auto_repair=not args.dry_run and not args.no_repair,
        include_path_repair=args.include_path_repair,
    )

    if args.json:
        print(json.dumps(summary, indent=2, default=str, sort_keys=True))
    else:
        mode = "DRY RUN" if summary["dry_run"] else ("AUTO REPAIR" if summary["auto_repair"] else "CHECK ONLY")
        print(f"self-heal [{mode}]")
        print(f"  checks:    {summary['checks_ok']}/{summary['checks_total']} ok "
              f"({summary['checks_failing']} failing)")
        print(f"  repairs:   {summary['repairs_attempted']} attempted, "
              f"{summary['repairs_fixed']} fixed, {summary['repairs_unfixed']} unfixed")
        print()
        if summary["checks_failing"] > 0:
            print("failing checks:")
            for c in summary["checks"]:
                if not c["ok"]:
                    print(f"  - {c['name']}: {c['message']}")
            print()
            print("repairs:")
            for r in summary["repairs"]:
                if r["invoked"]:
                    status = "OK" if r["ok"] else "FAIL"
                    print(f"  [{status}] {r['name']}: {r['message']}")
                    for a in r.get("actions", []):
                        print(f"          → {a}")
            print()
        if summary["re_checks"]:
            print("post-repair verification:")
            for c in summary["re_checks"]:
                mark = "OK" if c["ok"] else "FAIL"
                print(f"  [{mark}] {c['name']}: {c['message']}")
            print()
        if summary["all_healthy"]:
            print("self-heal: ALL GREEN")
            return 0
        print("self-heal: issues remain — see unfixed repairs above")
        return 1
