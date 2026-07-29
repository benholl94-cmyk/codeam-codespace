"""CLI commands for plugin + skill management.

- ``rollout-shield plugin list``         — list discovered plugins
- ``rollout-shield plugin show <id>``    — show a plugin manifest
- ``rollout-shield plugin activate <id>`` — activate a plugin
- ``rollout-shield plugin deactivate <id>``
- ``rollout-shield plugin discover``     — re-walk discovery paths
- ``rollout-shield plugin run <id> <event> [--key=val ...]``

- ``rollout-shield skill list``
- ``rollout-shield skill show <id>``
- ``rollout-shield skill warm``
- ``rollout-shield skill invoke <id> <prompt> [--param key=val ...]``
"""

from __future__ import annotations

import argparse
import json
import sys

from ..state import State


def cmd_plugin(state: State, args: argparse.Namespace) -> int:
    from .. import plugins
    sub = args.plugin_command or "list"
    if sub == "list":
        plugins.discover(state_root=state.root)
        for p in plugins.list_plugins():
            active = "ACTIVE" if plugins.registry().is_active(p.id) else "------"
            print(f"{active}  {p.id:30s}  v{p.version:10s}  {p.description}")
        return 0
    if sub == "show":
        plugins.discover(state_root=state.root)
        m = plugins.registry().get(args.plugin_id)
        if m is None:
            print(f"unknown plugin: {args.plugin_id}", file=sys.stderr)
            return 2
        print(json.dumps(m.to_dict(), indent=2, sort_keys=True))
        return 0
    if sub == "activate":
        plugins.discover(state_root=state.root)
        try:
            plugins.activate(args.plugin_id)
        except plugins.PluginError as exc:
            print(f"activate failed: {exc}", file=sys.stderr)
            return 2
        print(f"activated: {args.plugin_id}")
        return 0
    if sub == "deactivate":
        plugins.deactivate(args.plugin_id)
        print(f"deactivated: {args.plugin_id}")
        return 0
    if sub == "discover":
        n = plugins.discover(state_root=state.root)
        print(f"discovered {n} new plugin(s)")
        return 0
    if sub == "run":
        plugins.discover(state_root=state.root)
        try:
            plugins.activate(args.plugin_id)
        except plugins.PluginError as exc:
            print(f"activate failed: {exc}", file=sys.stderr)
            return 2
        kwargs = {}
        if args.param:
            for kv in args.param:
                k, _, v = kv.partition("=")
                kwargs[k.strip()] = v
        results = plugins.dispatch(args.event, **kwargs)
        print(json.dumps({"results": results}, indent=2, sort_keys=True))
        return 0
    print(f"unknown plugin subcommand: {sub}", file=sys.stderr)
    return 2


def cmd_skill(state: State, args: argparse.Namespace) -> int:
    from .. import skills
    sub = args.skill_command or "list"
    if sub == "list":
        for s in skills.list_skills():
            loaded = "READY" if s.is_loaded() else "STAGED"
            print(f"{loaded:6s}  {s.id:30s}  {s.description}")
        return 0
    if sub == "show":
        s = skills.registry().get(args.skill_id)
        if s is None:
            print(f"unknown skill: {args.skill_id}", file=sys.stderr)
            return 2
        d = {
            "id": s.id, "description": s.description,
            "module": s.module, "attr": s.attr,
            "tags": s.tags, "meta": s.meta,
            "loaded": s.is_loaded(),
        }
        print(json.dumps(d, indent=2, sort_keys=True))
        return 0
    if sub == "warm":
        n = skills.warm()
        print(f"warmed {n} skill(s)")
        return 0
    if sub == "invoke":
        params = {}
        if args.param:
            for kv in args.param:
                k, _, v = kv.partition("=")
                params[k.strip()] = v
        try:
            out = skills.invoke(args.skill_id, " ".join(args.prompt), params)
        except KeyError as exc:
            print(f"unknown skill: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(f"unknown skill subcommand: {sub}", file=sys.stderr)
    return 2


__all__ = ["cmd_plugin", "cmd_skill"]