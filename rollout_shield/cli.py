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
    state = State(root=args.state_root)
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


def build_parser() -> argparse.ArgumentParser:
    # Common args available on every subcommand. Using `parents=` makes
    # argparse pass them through to subparsers as if they were defined
    # there. This lets `rollout-shield <subcmd> --state-root DIR` work
    # without duplicating the argument on each subparser.
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

    return parser


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
    args = parser.parse_args(argv)
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
