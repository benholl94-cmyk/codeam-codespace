"""Reputation leaderboard command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..state import State


def cmd_reputation(state: State, args: argparse.Namespace) -> int:
    rep = state.load_reputation()
    agents = rep.get("agents", {})
    if not agents:
        if args.json:
            print(json.dumps({"agents": [], "schema": "rollout-shield.reputation/v1"}, indent=2))
        else:
            print("no reputation data yet")
        return 0

    rows = []
    for aid, entry in agents.items():
        rows.append({
            "agent_id": aid,
            "score": entry.get("score", 0.0),
            "events": len(entry.get("history", [])),
            "last_event_ts": (entry.get("history", [{}])[-1].get("ts")
                              if entry.get("history") else None),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:args.limit]

    if args.json:
        print(json.dumps({"agents": rows}, indent=2))
        return 0

    print(f"{len(rows)} agent(s) on the leaderboard (top {args.limit}):")
    print(f"  {'agent_id':<24} {'score':>8}  {'events':>7}  {'last_event_ts':<11}")
    for r in rows:
        ts = r["last_event_ts"]
        ts_str = str(ts) if ts is not None else "-"
        print(f"  {r['agent_id']:<24} {r['score']:>8.2f}  {r['events']:>7}  {ts_str:<11}")
    return 0
