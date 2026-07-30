"""Main CLI entry point for rollout-shield.

Top-level subcommands:

- ``install``        — initial setup (state dirs, default keypair)
- ``status``         — system summary
- ``keys``           — manage agent keys (new, list, show)
- ``claim``          — emit, list, show claims
- ``verify``         — verify a claim's signature
- ``monitor``        — run health-check cycles (one-shot or daemon)
- ``dashboard``      — serve the web dashboard
- ``reputation``     — show reputation leaderboard
- ``self-check``     — diagnose environment (Python version, state root, keys, etc.)

Run ``rollout-shield --help`` for details.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .state import State, atomic_write_json


def _cmd_install(args: argparse.Namespace) -> int:
    import os as _os, subprocess, sys as _sys
    state = State(root=args.state_root)
    # Lock the state root and private subdirs to Owner-only (0700).
    # This is what makes the Owner-uuid + session-id-agent the only
    # actors that can read/write at localspace.
    for d in (state.root, state.root / "keys_material"):
        try:
            _os.chmod(d, 0o700)
        except OSError:
            pass
    cfg = state.load_config()
    cfg["installed_at"] = int(__import__("time").time())
    cfg["installed_by"] = "rollout-shield install"
    state.save_config(cfg)

    # Ensure at least one agent key exists
    keys = state.list_keys()
    if not keys:
        from .commands.keys import cmd_keys_new
        key_id = cmd_keys_new(state, agent_id="default", description="default agent key (auto-generated at install)")
        keys = state.list_keys()

    summary = state.summary()
    print("rollout-shield installed.")
    print(f"  state root:      {state.root}")
    print(f"  schema version:  {summary['schema_version']}")
    print(f"  agents:          {summary['agents']['total']}")
    print(f"  keys registered: {len(keys)}")
    print()
    print("Next steps:")
    print("  rollout-shield status")
    print("  rollout-shield monitor --once")
    print("  rollout-shield dashboard")

    # A3: per user rule ("by conflicts start subp to hotpatch"), spawn
    # the audit in a fresh subprocess so a buggy audit cannot corrupt
    # the install we just finished. Failures here are non-fatal — the
    # audit result is informational; the install succeeded.
    try:
        result = subprocess.run(
            [_sys.executable, "-m", "rollout_shield", "audit",
             "--state-root", str(state.root), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            try:
                import json as _json
                report = _json.loads(result.stdout)
                if report.get("ok"):
                    print(f"  audit:           clean (9 known keys, 0 unknown, "
                          f"{len(report.get('suspect_bytes', []))} suspect bytes, "
                          f"{len(report.get('json_parse_errors', []))} JSON errors)")
                else:
                    print(f"  audit:           DRIFT DETECTED — run "
                          f"`rollout-shield audit --state-root {state.root}`")
            except Exception:
                print(f"  audit:           (could not parse audit output)")
        else:
            # Audit found drift. Don't fail the install — just announce.
            print(f"  audit:           drift detected (returncode={result.returncode}); "
                  f"run `rollout-shield audit --state-root {state.root}`")
    except (subprocess.SubprocessError, OSError) as exc:
        # Audit subprocess failure must not roll back a successful install.
        print(f"  audit:           could not run ({exc})", file=_sys.stderr)

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    summary = state.summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    print("rollout-shield status")
    print(f"  state root:      {summary['state_root']}")
    print(f"  schema version:  {summary['schema_version']}")
    print(f"  generated at:    {summary['generated_at']}")
    print(f"  agents:          {summary['agents']['total']}")
    for aid in summary["agents"]["ids"]:
        print(f"    - {aid}")
    print(f"  claims (logged): {summary['claims_count']}")
    print(f"  alerts (logged): {summary['alerts_count']}")
    latest = summary.get("latest_health") or {}
    if latest:
        print(f"  latest health:   {latest.get('status', 'unknown')} "
              f"({latest.get('ok', 0)}/{latest.get('total', 0)} ok, ts={latest.get('ts')})")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """Scan the state root for drift (registry / bytes / JSON / perms).

    When --repair is passed, deletes unknown config keys from
    config.json; refuses content-level fixes (suspect bytes, JSON
    errors, loose key files). Operators must address those by hand.
    """
    from .audit import audit_state_root, repair_state_root
    if args.state_root is None:
        # fall back to the env-default state root
        from .state import DEFAULT_STATE_ROOT
        state_root = Path(DEFAULT_STATE_ROOT)
    else:
        state_root = args.state_root
    rep = audit_state_root(state_root)
    if args.repair and not rep.ok:
        result = repair_state_root(rep)
        if args.json:
            print(json.dumps({"report": rep.to_dict(),
                              "repair": result}, indent=2,
                             sort_keys=True, default=str))
        else:
            print(f"repair: deleted {len(result.get('deleted_keys', []))} orphan keys")
            print(f"        refused {result.get('refused_suspect_bytes', 0)} suspect-byte issues")
            print(f"        refused {result.get('refused_json_errors', 0)} JSON errors")
            print(f"        refused {result.get('refused_loose_keys', 0)} loose key files")
    else:
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2,
                             sort_keys=True, default=str))
        else:
            print(f"state_root: {rep.state_root}")
            print(f"  unknown_keys:      {rep.unknown_keys}")
            print(f"  unused_keys:       {rep.unused_keys}")
            print(f"  suspect_bytes:     {len(rep.suspect_bytes)}")
            print(f"  json_parse_errors: {len(rep.json_parse_errors)}")
            print(f"  loose_key_files:   {len(rep.loose_key_files)}")
            print(f"  OK: {rep.ok}")
    return 0 if rep.ok else 1


def _cmd_keys(args: argparse.Namespace) -> int:
    from .commands.keys import cmd_keys
    state = State(root=args.state_root)
    return cmd_keys(state, args)


def _cmd_claim(args: argparse.Namespace) -> int:
    from .commands.claim import cmd_claim
    state = State(root=args.state_root)
    return cmd_claim(state, args)


def _cmd_verify(args: argparse.Namespace) -> int:
    from .commands.verify import cmd_verify
    state = State(root=args.state_root)
    return cmd_verify(state, args)


def _cmd_monitor(args: argparse.Namespace) -> int:
    from .monitor_daemon import main as monitor_main
    argv = []
    if args.once:
        argv.append("--once")
    if args.daemon:
        argv.append("--daemon")
    if args.interval:
        argv += ["--interval", str(args.interval)]
    if args.webhook_url:
        argv += ["--webhook-url", args.webhook_url]
    if args.disabled_checks:
        argv += ["--disabled-checks", args.disabled_checks]
    if args.json:
        argv.append("--json")
    if args.state_root:
        argv += ["--state-root", str(args.state_root)]
    return monitor_main(argv)


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from .http_server import main as dashboard_main
    argv = []
    if args.host:
        argv += ["--host", args.host]
    argv += ["--port", str(args.port)]
    if args.open:
        argv.append("--open")
    if args.state_root:
        argv += ["--state-root", str(args.state_root)]
    return dashboard_main(argv)


def _cmd_reputation(args: argparse.Namespace) -> int:
    from .commands.reputation import cmd_reputation
    state = State(root=args.state_root)
    return cmd_reputation(state, args)


def _cmd_self_check(args: argparse.Namespace) -> int:
    from .commands.self_check import cmd_self_check
    state = State(root=args.state_root)
    return cmd_self_check(state, args)


def _cmd_deploy(args: argparse.Namespace) -> int:
    from .deploy import main as deploy_main
    argv = []
    if args.deploy_cmd == "bundle":
        argv.append("bundle")
        if args.out:
            argv += ["--out", str(args.out)]
        if args.src:
            argv += ["--src", str(args.src)]
        if args.tarball:
            argv += ["--tarball", str(args.tarball)]
    elif args.deploy_cmd == "check":
        argv.append("check")
        argv += ["--bundle", str(args.bundle)]
    else:
        return 1
    return deploy_main(argv)


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run tools/doctor.py — 10 health checks across python/crypto/state/
    keys/logs/safeups/git/beads. Returns 0 if all OK, 1 on warnings, 2 on
    failures. JSON output available via --json."""
    import subprocess
    import sys as _sys
    doctor_py = Path(__file__).resolve().parent.parent / "tools" / "doctor.py"
    if not doctor_py.exists():
        print(f"doctor.py not found at {doctor_py}", file=_sys.stderr)
        return 1
    argv = [_sys.executable, str(doctor_py)]
    if args.json:
        argv.append("--json")
    # Forward --state-root so doctor inspects the same state dir the
    # caller targeted (otherwise doctor falls back to ./.rollout-shield
    # in cwd, which is rarely what the user wants when --state-root
    # was specified at the rollout-shield level).
    if getattr(args, "state_root", None):
        argv += ["--state-root", str(args.state_root)]
    if getattr(args, "doctor_root", None):
        argv += ["--root", str(args.doctor_root)]
    r = subprocess.run(argv)
    return r.returncode


def _cmd_backup(args: argparse.Namespace) -> int:
    """Backup workflow: snapshot state dir (safeup) + print paper phrase
    (secure_state --backup). Safe to run repeatedly; both operations
    are idempotent (snapshot rotates, phrase re-derives deterministically
    from the same key)."""
    import subprocess
    import sys as _sys
    repo = Path(__file__).resolve().parent.parent
    safeup = repo / "tools" / "safeup.py"
    secure = repo / "tools" / "secure_state.py"

    # 1. snapshot the state dir
    op = f"backup-{int(__import__('time').time())}"
    # If the caller pinned --state-root to a non-default location,
    # derive a safeup root next to it so the snapshot captures that
    # exact tree (otherwise safeup defaults to cwd/.safeups).
    sr = getattr(args, "state_root", None)
    snapshot_argv = [_sys.executable, str(safeup)]
    if sr:
        sr_path = Path(sr).resolve()
        sr_path.mkdir(parents=True, exist_ok=True)
        safeup_root = sr_path.parent / f".safeups-{sr_path.name}"
        snapshot_argv += ["--root", str(safeup_root)]
    snapshot_argv += ["snapshot", "--op", op, "--keep", str(args.keep)]
    r = subprocess.run(snapshot_argv)
    if r.returncode != 0:
        print("snapshot failed; refusing to print phrase", file=_sys.stderr)
        return r.returncode

    # 2. print paper phrase (or recover info if --print-key)
    if args.print_key:
        r2 = subprocess.run([_sys.executable, str(secure), "--status"])
    else:
        r2 = subprocess.run([_sys.executable, str(secure), "--backup"])
    return r2.returncode


def _cmd_restore(args: argparse.Namespace) -> int:
    """Restore state from a safeup snapshot."""
    import subprocess
    import sys as _sys
    safeup = Path(__file__).resolve().parent.parent / "tools" / "safeup.py"
    argv = [_sys.executable, str(safeup)]
    sr = getattr(args, "state_root", None)
    if sr:
        sr_path = Path(sr).resolve()
        sr_path.mkdir(parents=True, exist_ok=True)
        safeup_root = sr_path.parent / f".safeups-{sr_path.name}"
        argv += ["--root", str(safeup_root)]
    argv += ["restore", args.snapshot_id]
    if args.dry_run:
        argv.append("--dry-run")
    argv += ["--keep", str(args.keep)]
    r = subprocess.run(argv)
    return r.returncode


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove state directories, audit log, and unlock file. Confirms
    unless --yes is passed. Refuses to remove the source repo."""
    import shutil
    import sys as _sys
    repo = Path(__file__).resolve().parent.parent
    targets = [
        repo / ".rollout-shield",  # state dir
        repo / ".audit",            # audit log + unlock
        repo / ".safeups",          # snapshots
    ]
    found = [t for t in targets if t.exists()]
    if not found:
        print("nothing to uninstall (no state dirs present)")
        return 0
    print("Will remove:")
    for t in found:
        try:
            size = sum(f.stat().st_size for f in t.rglob("*") if f.is_file())
            print(f"  {t}  ({size} bytes across {sum(1 for _ in t.rglob('*'))} entries)")
        except OSError as e:
            print(f"  {t}  (cannot stat: {e})")
    if not args.yes:
        try:
            ans = input("Proceed? [yes/NO]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "yes":
            print("aborted")
            return 1
    for t in found:
        try:
            shutil.rmtree(t)
            print(f"removed {t}")
        except OSError as e:
            print(f"failed to remove {t}: {e}", file=_sys.stderr)
    print("uninstall complete. source repo preserved.")
    print("to remove the package: pip uninstall rollout-shield")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Common args available on every subcommand. Defining them on the
    # parent parser lets them appear before the subcommand; the
    # subparser inheritance (`parents=[common]`) lets them also appear
    # after. `_extract_common_args` in `main()` ensures the value lands
    # on the top-level namespace regardless of position.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-root", type=Path, default=None,
                        help="override state root directory (default: ./.rollout-shield)")
    common.add_argument("--json", action="store_true",
                        help="emit JSON instead of human-readable output")

    parser = argparse.ArgumentParser(
        prog="rollout-shield",
        description="Hardware+software platform for repo-safe-rollouts (CLI)",
        parents=[common],
    )
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    sub = parser.add_subparsers(dest="command", required=False)

    # install
    p = sub.add_parser("install", help="install rollout-shield state and default keypair",
                       parents=[common])
    p.set_defaults(func=_cmd_install)

    # status
    p = sub.add_parser("status", help="print system summary", parents=[common])
    p.set_defaults(func=_cmd_status)

    # keys
    p = sub.add_parser("keys", help="manage agent keys", parents=[common])
    sub_keys = p.add_subparsers(dest="keys_command", required=False)
    pk = sub_keys.add_parser("list", help="list registered keys", parents=[common])
    pk.set_defaults(keys_command="list")
    pk = sub_keys.add_parser("new", help="generate a new agent keypair", parents=[common])
    pk.add_argument("--agent-id", required=True, help="agent id (becomes part of the key id)")
    pk.add_argument("--description", default="", help="free-text description")
    pk.set_defaults(keys_command="new")
    pk = sub_keys.add_parser("show", help="show a registered key (public fields only)",
                             parents=[common])
    pk.add_argument("key_id", help="key id (e.g., agk_default)")
    pk.set_defaults(keys_command="show")
    p.set_defaults(func=_cmd_keys)

    # claim
    p = sub.add_parser("claim", help="emit, list, or show claims", parents=[common])
    sub_claim = p.add_subparsers(dest="claim_command", required=False)
    pc = sub_claim.add_parser("list", help="list recent claims", parents=[common])
    pc.add_argument("--limit", type=int, default=50)
    pc.add_argument("--agent-id", default=None)
    pc.add_argument("--since", type=int, default=None,
                    help="only claims with ts >= this unix timestamp")
    pc.set_defaults(claim_command="list")
    pc = sub_claim.add_parser("create", help="create a new claim", parents=[common])
    pc.add_argument("--agent-id", required=True, help="agent id (signing identity)")
    pc.add_argument("--type", required=True,
                    choices=["intent", "change", "test", "verify", "contradict", "delegate"],
                    help="claim type per protocol/CLAIM-FORMAT.md")
    pc.add_argument("--body", default="", help="claim body (free text or JSON string)")
    pc.add_argument("--parent", default=None,
                    help="parent claim id (for DAG linkage)")
    pc.set_defaults(claim_command="create")
    pc = sub_claim.add_parser("show", help="show one claim by id", parents=[common])
    pc.add_argument("claim_id", help="claim id (e.g., clm_xxx)")
    pc.set_defaults(claim_command="show")
    p.set_defaults(func=_cmd_claim)

    # verify
    p = sub.add_parser("verify", help="verify a claim's signature", parents=[common])
    p.add_argument("claim_id", help="claim id")
    p.set_defaults(func=_cmd_verify)

    # monitor
    p = sub.add_parser("monitor", help="run health-check cycles (one-shot or daemon)",
                       parents=[common])
    p.add_argument("--once", action="store_true", help="run a single cycle")
    p.add_argument("--daemon", action="store_true", help="run as long-lived daemon")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--webhook-url", default="")
    p.add_argument("--disabled-checks", default="")
    p.set_defaults(func=_cmd_monitor)

    # dashboard
    p = sub.add_parser("dashboard", help="serve the web dashboard", parents=[common])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=_cmd_dashboard)

    # reputation
    p = sub.add_parser("reputation", help="show agent reputation leaderboard",
                       parents=[common])
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=_cmd_reputation)

    # self-check
    p = sub.add_parser("self-check", help="diagnose environment", parents=[common])
    p.set_defaults(func=_cmd_self_check)

    # audit — registry / bytes / JSON / permission drift detector (A2)
    p = sub.add_parser("audit",
                       help="scan state root for drift (registry / bytes / JSON / perms)",
                       parents=[common])
    p.add_argument("--repair", action="store_true",
                   help="delete unknown config keys; refuse content-level fixes")
    p.set_defaults(func=_cmd_audit)

    # deploy (generate or check a deploy bundle)
    p = sub.add_parser("deploy", help="generate / verify a deploy bundle",
                       parents=[common])
    deploy_sub = p.add_subparsers(dest="deploy_cmd", required=True)

    db = deploy_sub.add_parser("bundle", help="generate the deploy bundle")
    db.add_argument("--out", default="./dist/deploy-bundle",
                    help="output directory (default ./dist/deploy-bundle)")
    db.add_argument("--src", default=".",
                    help="source repo root (default .)")
    db.add_argument("--tarball", default=None,
                    help="also write a .tar.gz at this path")
    db.set_defaults(func=_cmd_deploy)

    dc = deploy_sub.add_parser("check", help="verify a bundle against MANIFEST.json")
    dc.add_argument("--bundle", required=True, help="path to bundle directory")
    dc.set_defaults(func=_cmd_deploy)

    # doctor — 10 health checks across python/crypto/state/keys/logs/safeups/git/beads
    p = sub.add_parser("doctor", help="run 10 health checks (python, crypto, state, "
                                       "keys, logs, safeups, git, beads)", parents=[common])
    p.add_argument("--root", dest="doctor_root", default=None,
                   help="root directory for the doctor to scan (default: repo root)")
    p.set_defaults(func=_cmd_doctor)

    # backup — safeup snapshot + paper phrase
    p = sub.add_parser("backup", help="snapshot state dir + print 32-word paper phrase",
                       parents=[common])
    p.add_argument("--keep", type=int, default=10,
                   help="how many snapshots to retain (default 10)")
    p.add_argument("--print-key", action="store_true",
                   help="print key status (path/mode) instead of paper phrase")
    p.set_defaults(func=_cmd_backup)

    # restore — restore from a safeup snapshot id
    p = sub.add_parser("restore", help="restore state from a safeup snapshot",
                       parents=[common])
    p.add_argument("snapshot_id", help="snapshot id (run 'safeup list' to see)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be restored without writing")
    p.add_argument("--keep", type=int, default=10,
                   help="snapshots to retain during restore (default 10)")
    p.set_defaults(func=_cmd_restore)

    # uninstall — remove state dirs, audit log, unlock; keep source repo
    p = sub.add_parser("uninstall", help="remove state dirs + unlock + snapshots "
                                         "(source repo preserved)", parents=[common])
    p.add_argument("--yes", action="store_true",
                   help="skip confirmation prompt")
    p.set_defaults(func=_cmd_uninstall)

    return parser


def _extract_common_args(argv: list[str]) -> tuple[list[str], Path | None, bool]:
    """Pre-scan argv for --state-root VALUE and --json, regardless of where
    they appear (before or after the subcommand). Strip them from argv and
    return the cleaned argv plus the extracted values.

    This works around a long-standing argparse quirk: when the same flag
    is defined on both a parent and child parser via ``parents=``, the
    child parser consumes the token and the value never lands on the
    top-level namespace. By extracting up-front we ensure `args.state_root`
    is set regardless of token position.
    """
    cleaned: list[str] = []
    state_root: Path | None = None
    json_flag = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--state-root":
            # take the next token as the value (skip if it's another flag
            # or the end of argv)
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                state_root = Path(argv[i + 1])
                i += 2
                continue
            else:
                # --state-root at end with no value; let argparse error
                cleaned.append(a)
                i += 1
                continue
        if a.startswith("--state-root="):
            state_root = Path(a.split("=", 1)[1])
            i += 1
            continue
        if a == "--json":
            json_flag = True
            i += 1
            continue
        cleaned.append(a)
        i += 1
    return cleaned, state_root, json_flag


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0
    if argv == ["--version"]:
        from . import __version__
        print(f"rollout-shield {__version__}")
        return 0
    cleaned, pre_state_root, pre_json = _extract_common_args(argv)
    args = parser.parse_args(cleaned)
    # Merge pre-extracted values (these take priority because the user
    # wrote them explicitly on the command line).
    if pre_state_root is not None:
        args.state_root = pre_state_root
    if pre_json:
        args.json = True
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[rollout-shield] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
