"""``rollout-shield webhooks ...`` CLI subcommand.

Subcommands:

- ``target add|list|remove`` — manage delivery targets
- ``deliver`` — enqueue a delivery
- ``deliveries list|show`` — inspect outbox / DLQ
- ``replay <id>`` — re-enqueue a single delivery
- ``replay-all --status failed|dlq`` — re-enqueue many
- ``drain`` — process pending deliveries once (CI / smoke use)
- ``stats`` — counters + live depth
- ``sign-test`` — print canonical headers + signature for a target

All subcommands emit both human and JSON output (``--json``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from ..state import State
from ..webhook_delivery import (
    DEFAULT_DEDUPE_WINDOW_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    DeliveryStatus,
    DeliveryTarget,
    SignMode,
    add_target,
    enqueue,
    get_delivery,
    get_target,
    list_deliveries,
    list_targets,
    remove_target,
    replay,
    replay_all,
    run_once,
    stats,
)
from ..webhook_delivery.signer import build_headers

# --- helpers ---------------------------------------------------------------


def _emit(data: Any, as_json: bool) -> int:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
    else:
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
        elif isinstance(data, list):
            for row in data:
                print(f"  - {row}")
        else:
            print(data)
    return 0


def _print_targets_table(rows: list[DeliveryTarget], as_json: bool) -> int:
    if as_json:
        return _emit([t.to_dict() for t in rows], True)
    if not rows:
        print("no targets configured.")
        return 0
    print(f"{'NAME':<20} {'URL':<48} {'SIGN':<10} {'PAUSED':<8} {'STREAK':<7}")
    print("-" * 96)
    for t in rows:
        paused = "yes" if t.is_paused() else "no"
        url = t.url if len(t.url) <= 48 else t.url[:45] + "..."
        print(f"{t.name:<20} {url:<48} {t.sign_mode.value:<10} {paused:<8} {t.fail_streak:<7}")
    return 0


def _print_deliveries_table(rows, as_json: bool) -> int:
    if as_json:
        return _emit([r.to_dict() for r in rows], True)
    if not rows:
        print("no deliveries.")
        return 0
    print(f"{'DELIVERY ID':<28} {'TARGET':<18} {'STATUS':<11} {'ATT':<4} {'UPDATED':<11}  ERROR")
    print("-" * 110)
    for r in rows:
        updated = time.strftime("%H:%M:%S", time.localtime(r.updated_at))
        err = (r.last_error or "")[:50]
        print(f"{r.delivery_id:<28} {r.target_name:<18} {r.status.value:<11} {r.attempt_count:<4} {updated:<11}  {err}")
    return 0


# --- target subcommands ----------------------------------------------------


def _target_add(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    try:
        tgt = add_target(
            state,
            name=args.name,
            url=args.url,
            sign_mode=args.sign_mode,
            signing_key=args.signing_key or "",
            description=args.description or "",
            timeout_seconds=args.timeout,
            max_attempts=args.max_attempts,
            dedupe_window_seconds=args.dedupe_window,
            enabled=not args.disabled,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit({"created": tgt.to_dict()}, args.json)


def _target_list(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    return _print_targets_table(list_targets(state), args.json)


def _target_remove(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    removed = remove_target(state, args.name)
    if not removed:
        print(f"error: target {args.name!r} not found", file=sys.stderr)
        return 1
    return _emit({"removed": args.name}, args.json)


# --- deliver / deliveries --------------------------------------------------


def _deliver(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        print(f"error: --payload must be valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("error: --payload must be a JSON object", file=sys.stderr)
        return 2
    rec = enqueue(
        state,
        target_name=args.target,
        payload=payload,
        idempotency_key=args.idempotency_key,
    )
    return _emit({"enqueued": rec.to_dict()}, args.json)


def _deliveries_list(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    status = args.status
    if status and status != "all":
        try:
            status = DeliveryStatus(status)
        except ValueError:
            print(f"error: unknown status {status!r}", file=sys.stderr)
            return 2
    else:
        status = None
    rows = list_deliveries(state, status=status,
                           target=args.target, limit=args.limit)
    return _print_deliveries_table(rows, args.json)


def _deliveries_show(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    rec = get_delivery(state, args.delivery_id)
    if rec is None:
        print(f"error: delivery {args.delivery_id!r} not found", file=sys.stderr)
        return 1
    return _emit(rec.to_dict(), args.json)


def _replay_cmd(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    try:
        rec = replay(state, args.delivery_id)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit({"replayed": rec.to_dict()}, args.json)


def _replay_all_cmd(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    try:
        status = DeliveryStatus(args.status)
    except ValueError:
        print(f"error: unknown status {args.status!r}", file=sys.stderr)
        return 2
    ids = replay_all(state, status)
    return _emit({"replayed": ids, "count": len(ids)}, args.json)


def _drain(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    counts = run_once(state)
    return _emit({"drained": counts}, args.json)


def _stats_cmd(args: argparse.Namespace) -> int:
    state = State(root=args.state_root)
    s = stats(state)
    return _emit(s, args.json)


def _sign_test(args: argparse.Namespace) -> int:
    """Print canonical headers + signature for a target + payload."""
    state = State(root=args.state_root)
    tgt = get_target(state, args.target)
    if tgt is None:
        print(f"error: target {args.target!r} not found", file=sys.stderr)
        return 1
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        print(f"error: --payload must be valid JSON: {exc}", file=sys.stderr)
        return 2
    headers = build_headers(tgt, payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    out = {
        "target": tgt.name,
        "url": tgt.url,
        "sign_mode": tgt.sign_mode.value,
        "headers": headers,
        "canonical_payload": canonical,
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"target:       {out['target']} ({out['url']})")
        print(f"sign_mode:    {out['sign_mode']}")
        print("headers:")
        for k, v in headers.items():
            print(f"  {k}: {v}")
        print(f"canonical_payload: {canonical}")
    return 0


# --- dispatcher ------------------------------------------------------------


# --- main entry ------------------------------------------------------------


def build_parser(parent: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = parent.add_parser(
        "webhooks",
        help="Outbound webhook delivery subsystem (outbox + retry + sign + DLQ).",
        description=(
            "Manage webhook targets, deliver payloads with durable retries, "
            "inspect deliveries, replay failures, and verify signatures."
        ),
    )
    sub = p.add_subparsers(dest="webhooks_cmd", required=True)

    # target
    t = sub.add_parser("target", help="Manage webhook delivery targets.")
    tsub = t.add_subparsers(dest="target_cmd", required=True)

    ta = tsub.add_parser("add", help="Add or update a target.")
    ta.add_argument("name", help="Target name (filesystem-safe).")
    ta.add_argument("url", help="Target URL (http:// or https://).")
    ta.add_argument("--sign-mode", choices=[m.value for m in SignMode],
                    default="none", help="Signing mode (default: none).")
    ta.add_argument("--signing-key", default="",
                    help="HMAC secret or Ed25519 key_id.")
    ta.add_argument("--description", default="", help="Operator description.")
    ta.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                    help=f"Per-attempt timeout seconds (default {DEFAULT_TIMEOUT_SECONDS}).")
    ta.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                    help=f"Max attempts before DLQ (default {DEFAULT_MAX_ATTEMPTS}).")
    ta.add_argument("--dedupe-window", type=int,
                    default=DEFAULT_DEDUPE_WINDOW_SECONDS,
                    help=f"Dedupe window seconds (default {DEFAULT_DEDUPE_WINDOW_SECONDS}).")
    ta.add_argument("--disabled", action="store_true",
                    help="Register the target as disabled.")
    ta.add_argument("--json", action="store_true")
    ta.set_defaults(func=_target_add)

    tl = tsub.add_parser("list", help="List configured targets.")
    tl.add_argument("--json", action="store_true")
    tl.set_defaults(func=_target_list)

    tr = tsub.add_parser("remove", help="Remove a target by name.")
    tr.add_argument("name")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=_target_remove)

    # deliver
    dv = sub.add_parser("deliver", help="Enqueue a delivery.")
    dv.add_argument("--target", required=True, help="Target name.")
    dv.add_argument("--payload", required=True, help="JSON payload (object).")
    dv.add_argument("--idempotency-key", default=None,
                    help="Idempotency key (auto-generated if absent).")
    dv.add_argument("--json", action="store_true")
    dv.set_defaults(func=_deliver)

    # deliveries
    dl = sub.add_parser("deliveries", help="Inspect the outbox / DLQ.")
    dlsub = dl.add_subparsers(dest="deliveries_cmd", required=True)

    dll = dlsub.add_parser("list", help="List deliveries.")
    dll.add_argument("--status", default="all",
                     choices=["all", "pending", "in_flight", "delivered",
                              "failed", "dlq", "cancelled"])
    dll.add_argument("--target", default=None)
    dll.add_argument("--limit", type=int, default=100)
    dll.add_argument("--json", action="store_true")
    dll.set_defaults(func=_deliveries_list)

    dls = dlsub.add_parser("show", help="Show a single delivery.")
    dls.add_argument("delivery_id")
    dls.add_argument("--json", action="store_true")
    dls.set_defaults(func=_deliveries_show)

    # replay
    rp = sub.add_parser("replay", help="Replay a single delivery.")
    rp.add_argument("delivery_id")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=_replay_cmd)

    rpa = sub.add_parser("replay-all", help="Replay all deliveries with a given status.")
    rpa.add_argument("--status", required=True,
                     choices=[s.value for s in DeliveryStatus])
    rpa.add_argument("--json", action="store_true")
    rpa.set_defaults(func=_replay_all_cmd)

    # drain
    dr = sub.add_parser("drain", help="Process pending deliveries once.")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=_drain)

    # stats
    st = sub.add_parser("stats", help="Show counters + live depth.")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_stats_cmd)

    # sign-test
    sg = sub.add_parser("sign-test",
                        help="Print canonical headers + signature for a target.")
    sg.add_argument("--target", required=True)
    sg.add_argument("--payload", required=True, help="JSON payload object.")
    sg.add_argument("--json", action="store_true")
    sg.set_defaults(func=_sign_test)


def cmd_webhooks(state: State, args: argparse.Namespace) -> int:
    """Top-level handler dispatched by ``cli.py``."""
    return args.func(args)
