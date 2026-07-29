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


def _cmd_self_heal(args: argparse.Namespace) -> int:
    from .commands.self_heal import cmd_self_heal
    state = State(root=args.state_root)
    return cmd_self_heal(state, args)


def _cmd_self_test(args: argparse.Namespace) -> int:
    from .commands.self_test import cmd_self_test
    state = State(root=args.state_root)
    return cmd_self_test(state, args)


def _cmd_space(args: argparse.Namespace) -> int:
    from .commands.space import cmd_space
    state = State(root=args.state_root)
    return cmd_space(state, args)


def _cmd_host_workspace(args: argparse.Namespace) -> int:
    from .commands.host_workspace import cmd_host_workspace
    state = State(root=args.state_root)
    return cmd_host_workspace(state, args)


def _cmd_ai(args: argparse.Namespace) -> int:
    from .commands.ai import cmd_ai
    state = State(root=args.state_root)
    return cmd_ai(state, args)


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
    pk.add_argument("--hardware-anchored", action="store_true",
                    help="mark this key as hardware-anchored (device-attested); "
                         "required when controller_policy=device-only")
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

    # self-heal — diagnose + auto-repair
    p = sub.add_parser("self-heal",
                       help="diagnose common issues and attempt automatic repair",
                       parents=[common])
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be repaired, but make no changes")
    p.add_argument("--no-repair", action="store_true",
                   help="report failing checks without invoking any repair")
    p.add_argument("--include-path-repair", action="store_true",
                   help="also flag the PATH-hint check (manual action required)")
    p.set_defaults(func=_cmd_self_heal)

    # self-test — end-to-end smoke test against a scratch state
    p = sub.add_parser("self-test",
                       help="end-to-end smoke test against a scratch state root",
                       parents=[common])
    p.add_argument("--scratch-root", default=None,
                   help="override the scratch state directory "
                        "(default: mkdtemp under TMPDIR)")
    p.add_argument("--keep-scratch", action="store_true",
                   help="do not delete the scratch state on exit")
    p.add_argument("--skip-dashboard", action="store_true",
                   help="skip the dashboard HTTP smoke step")
    p.set_defaults(func=_cmd_self_test)

    # space — controller policy (shared | device-only | human-only)
    p = sub.add_parser("space",
                       help="inspect/manage the controller policy "
                            "(which keys are permitted to sign claims)",
                       parents=[common])
    sub_space = p.add_subparsers(dest="space_command", required=False)
    ps = sub_space.add_parser("show", help="print current policy + statistics",
                              parents=[common])
    ps.set_defaults(space_command="show")
    ps = sub_space.add_parser("set-policy", help="switch the controller policy",
                              parents=[common])
    ps.add_argument("policy", choices=["shared", "device-only", "human-only"],
                    help="target controller policy")
    ps.add_argument("--yes", action="store_true",
                    help="confirm the policy change (skip the safety prompt)")
    ps.set_defaults(space_command="set-policy")
    ps = sub_space.add_parser("validate",
                              help="enforce the policy against the current state",
                              parents=[common])
    ps.set_defaults(space_command="validate")
    ps = sub_space.add_parser("quarantine", help="move a key out of the active registry",
                              parents=[common])
    ps.add_argument("key_id", help="key id to quarantine")
    ps.add_argument("--reason", default="", help="reason for the quarantine")
    ps.set_defaults(space_command="quarantine")
    ps = sub_space.add_parser("unquarantine", help="reverse a quarantine",
                              parents=[common])
    ps.add_argument("key_id", help="key id to unquarantine")
    ps.set_defaults(space_command="unquarantine")
    p.set_defaults(func=_cmd_space)

    # host-workspace
    p = sub.add_parser("host-workspace",
                       help="cross-cut view of the two workspaces (repo + host kernel)",
                       parents=[common])
    p.add_argument("--include-checks", action="store_true",
                   help="include the full health-check results in the JSON output")
    p.set_defaults(func=_cmd_host_workspace)

    # ai
    p = sub.add_parser("ai", help="AI assistance: parallel-lateral router, benchmarks, "
                                    "self-cycles, First-of-kind generator",
                       parents=[common])
    sub_ai = p.add_subparsers(dest="ai_command", required=False)
    # ai route
    pa = sub_ai.add_parser("route", help="route a prompt through N models in parallel",
                           parents=[common])
    pa.add_argument("prompt", nargs="+", help="prompt text")
    pa.add_argument("--strategy", default="best",
                    choices=["best", "concat", "consensus", "first", "median"])
    pa.add_argument("--model", dest="models", action="append", default=None,
                    help="model id (repeatable); defaults to all registered models")
    pa.add_argument("--no-leaderboard", dest="use_leaderboard",
                    action="store_false", default=True,
                    help="don't use leaderboard scores for the 'best' strategy")
    pa.set_defaults(ai_command="route")
    # ai benchmark
    pa = sub_ai.add_parser("benchmark", help="benchmark a model", parents=[common])
    pa.add_argument("--model", required=True, help="model id to benchmark")
    pa.add_argument("--prompt", dest="prompts", action="append", default=None,
                    help="prompt text (repeatable); defaults to a fixed set")
    pa.add_argument("--record", action="store_true",
                    help="record the results to the leaderboard")
    pa.set_defaults(ai_command="benchmark")
    # ai cycle
    pa = sub_ai.add_parser("cycle", help="run a self-cycle", parents=[common])
    pa.add_argument("--count", type=int, default=1,
                    help="number of cycles to run (default 1)")
    pa.add_argument("--prompt", default=None, help="override the cycle prompt")
    pa.add_argument("--strategy", default="best",
                    choices=["best", "concat", "consensus", "first", "median"])
    pa.add_argument("--kind", default="summary",
                    choices=["poem", "slogan", "code", "structured", "summary"],
                    help="first-of-kind artifact kind to generate")
    pa.add_argument("--no-artifact", action="store_true",
                    help="don't generate a first-of-kind artifact for the cycle")
    pa.set_defaults(ai_command="cycle")
    # ai leaderboard
    pa = sub_ai.add_parser("leaderboard", help="show benchmark leaderboard",
                           parents=[common])
    pa.set_defaults(ai_command="leaderboard")
    # ai first-of-kind
    pa = sub_ai.add_parser("first-of-kind", help="generate a First-of-kind artifact",
                           parents=[common])
    pa.add_argument("prompt", nargs="+", help="prompt text")
    pa.add_argument("--kind", default="poem",
                    choices=["poem", "slogan", "code", "structured", "summary"])
    pa.add_argument("--tag", action="append", default=[],
                    help="tag to attach to the artifact (repeatable)")
    pa.set_defaults(ai_command="first-of-kind")
    # ai dashboard
    pa = sub_ai.add_parser("dashboard", help="show the AI tab URL",
                           parents=[common])
    pa.add_argument("--host", default="127.0.0.1")
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--open", action="store_true")
    pa.set_defaults(ai_command="dashboard")
    p.set_defaults(func=_cmd_ai)

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
