"""AI-assistance subcommands.

Implements the ``rollout-shield ai`` subcommand family:

- ``ai route <prompt>``            — send a prompt through the router
- ``ai benchmark --model <id>``    — run one model against the benchmark suite
- ``ai cycle``                     — run one self-cycle
- ``ai cycle --count N``           — run N self-cycles
- ``ai leaderboard``               — show benchmark leaderboard
- ``ai first-of-kind --kind poem`` — generate a First-of-kind artifact
- ``ai dashboard``                 — open the AI tab in the dashboard
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser

from ..state import State


def cmd_route(state: State, args: argparse.Namespace) -> int:
    from ..ai.leaderboard import aggregate_scores
    from ..ai.router import STRATEGIES
    from ..ai.router import route as router_route
    prompt = " ".join(args.prompt)
    if not prompt:
        print("usage: rollout-shield ai route <prompt...>", file=sys.stderr)
        return 2
    if args.strategy not in STRATEGIES:
        print(f"unknown strategy: {args.strategy}; choices: {STRATEGIES}", file=sys.stderr)
        return 2
    scores = aggregate_scores(state) if args.use_leaderboard else None
    trace = router_route(prompt=prompt,
                         models=args.models,
                         strategy=args.strategy,
                         benchmark_scores=scores or {},
                         state=state)
    if args.json:
        print(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"prompt:    {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    print(f"digest:    {trace.prompt_digest}")
    print(f"strategy:  {trace.strategy}")
    print(f"models:    {', '.join(trace.selected_models)}")
    print(f"elapsed:   {trace.elapsed_ms:.2f} ms (parallel speedup x{trace.parallel_speedup:.2f})")
    print(f"selected:  {trace.selected}")
    print()
    for o in trace.outputs:
        mark = "OK" if o.get("ok") else "FAIL"
        elapsed = o.get("elapsed_ms", 0)
        print(f"  [{mark}] {o['model_id']:<20} ({elapsed:.1f} ms)")
        if o.get("ok"):
            text = o["output"]["text"][:200]
            print(f"      {text}{'...' if len(o['output']['text']) > 200 else ''}")
    print()
    print("--- selected output ---")
    print(trace.selected_text[:600] + ("..." if len(trace.selected_text) > 600 else ""))
    return 0


def cmd_benchmark(state: State, args: argparse.Namespace) -> int:
    from ..ai.benchmarks import aggregate_benchmark_results, run_model_benchmarks
    from ..ai.leaderboard import LeaderboardEntry, append_entries
    from ..ai.models import get_model
    model = get_model(args.model)
    prompts = args.prompts if args.prompts else [
        "ship a canary rollout",
        "summarize the last 5 deploys",
        "investigate why p99 latency spiked",
    ]
    all_results = []
    ctx: dict = {}
    cycle_ts = int(time.time())
    for prompt in prompts:
        # pass state so own models can read their weights
        output = model.run(prompt, state=state)
        results = run_model_benchmarks(args.model, output, ctx=ctx)
        all_results.extend(results)
    scores = aggregate_benchmark_results(all_results)
    if args.record:
        entries = [LeaderboardEntry(ts=cycle_ts, model_id=r.model_id,
                                     benchmark_name=r.name, score=r.score,
                                     cycle=-1, notes=r.notes)
                   for r in all_results]
        append_entries(state, entries)
    if args.json:
        print(json.dumps({
            "model": args.model,
            "scores": scores,
            "results": [r.to_dict() for r in all_results],
        }, indent=2, ensure_ascii=False))
        return 0
    print(f"benchmark: model={args.model} ({len(prompts)} prompt(s))")
    print(f"  aggregate score: {scores.get(args.model, 0.0):.4f}")
    print()
    for r in all_results:
        print(f"  [{r.score:.2f}] {r.name:<22} {r.notes}")
    return 0


def cmd_cycle(state: State, args: argparse.Namespace) -> int:
    from ..ai.self_cycle import iter_cycles, run_n_cycles, run_one_cycle
    if args.count and args.count > 1:
        records = run_n_cycles(state, n=args.count, prompt=args.prompt,
                                strategy=args.strategy, generate_artifact=not args.no_artifact,
                                artifact_kind=args.kind)
    else:
        records = [run_one_cycle(state, prompt=args.prompt, strategy=args.strategy,
                                  generate_artifact=not args.no_artifact,
                                  artifact_kind=args.kind)]
    if args.json:
        print(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False))
        return 0
    for r in records:
        print(f"cycle {r.cycle}: {r.prompt[:60]}{'...' if len(r.prompt) > 60 else ''}")
        print(f"  strategy:       {r.router_strategy}")
        print(f"  selected:       {r.selected_model}")
        print(f"  speedup:        x{r.parallel_speedup:.2f}")
        print(f"  duration:       {r.duration_ms:.1f} ms")
        print("  benchmark:")
        for mid, score in sorted(r.benchmark_scores.items(), key=lambda kv: -kv[1]):
            print(f"    {mid:<22} {score:.4f}")
        if r.artifacts:
            print(f"  artifacts:      {', '.join(r.artifacts)}")
        print()
    print(f"total cycles recorded: {len(iter_cycles(state))}")
    return 0


def cmd_leaderboard(state: State, args: argparse.Namespace) -> int:
    from ..ai.leaderboard import aggregate_scores, latest_per_model_benchmark, top_model
    entries = latest_per_model_benchmark(state)
    scores = aggregate_scores(state)
    best = top_model(state)
    if args.json:
        print(json.dumps({
            "best": {"model_id": best[0], "score": best[1]} if best else None,
            "scores": scores,
            "entries": [e.to_dict() for e in entries],
        }, indent=2))
        return 0
    if best:
        print(f"top model: {best[0]} (avg score {best[1]:.4f})")
    else:
        print("no leaderboard data yet — run a cycle first")
    print()
    print(f"{'model':<22} {'avg score':>10}  benchmarks")
    for mid, avg in sorted(scores.items(), key=lambda kv: -kv[1]):
        per_bench = sorted(
            [(e.benchmark_name, e.score) for e in entries if e.model_id == mid],
            key=lambda kv: kv[0])
        bstr = ", ".join(f"{n}={s:.2f}" for n, s in per_bench)
        print(f"{mid:<22} {avg:>10.4f}  {bstr}")
    return 0


def cmd_first_of_kind(state: State, args: argparse.Namespace) -> int:
    from ..ai.generator import first_of_kind_id
    from ..ai.generator import generate as gen_fok
    prompt = " ".join(args.prompt)
    if not prompt:
        print("usage: rollout-shield ai first-of-kind <prompt...>", file=sys.stderr)
        return 2
    artifact = gen_fok(state, prompt=prompt, kind=args.kind,
                       tags=args.tag if args.tag else [])
    if args.json:
        print(json.dumps(artifact.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"id:        {artifact.id}")
    print(f"kind:      {artifact.kind}")
    print(f"prompt:    {artifact.prompt[:80]}{'...' if len(artifact.prompt) > 80 else ''}")
    print(f"digest:    {artifact.prompt_digest}")
    print(f"model:     {artifact.model_id}")
    print(f"strategy:  {artifact.route_strategy}")
    print(f"tags:      {', '.join(artifact.tags) or '(none)'}")
    print(f"deterministic_id_for_same_inputs: {first_of_kind_id(prompt, artifact.kind)}")
    print()
    print("--- artifact ---")
    print(artifact.text)
    return 0


def cmd_ai_dashboard(state: State, args: argparse.Namespace) -> int:
    """Open the AI tab in the running dashboard (or print URL)."""
    host = args.host or "127.0.0.1"
    port = args.port or 8765
    url = f"http://{host}:{port}/ai-assistance.html"
    print(f"AI tab URL: {url}")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            print(f"could not open browser: {exc}", file=sys.stderr)
    return 0


def cmd_ai(state: State, args: argparse.Namespace) -> int:
    sub = args.ai_command or "leaderboard"
    if sub == "route":
        return cmd_route(state, args)
    if sub == "benchmark":
        return cmd_benchmark(state, args)
    if sub == "cycle":
        return cmd_cycle(state, args)
    if sub == "leaderboard":
        return cmd_leaderboard(state, args)
    if sub == "first-of-kind":
        return cmd_first_of_kind(state, args)
    if sub == "dashboard":
        return cmd_ai_dashboard(state, args)
    if sub == "routing":
        return cmd_routing(state, args)
    print(f"unknown ai subcommand: {sub}", file=sys.stderr)
    return 2


def cmd_routing(state: State, args: argparse.Namespace) -> int:
    """Show the smart-routing binding manifest.

    The manifest is stamped at install time (see scripts/install.sh) and
    records which AI models are bound to the government-version build,
    which lateral-combination strategy is the default, and the
    per-policy routing profiles.
    """
    import json as _json

    from .. import routing

    profile_name = getattr(args, "profile", None)
    if not profile_name:
        # derive from active state config
        try:
            cfg = state.load_config()
            profile_name = cfg.get("controller_policy", "shared")
        except Exception:
            profile_name = "shared"

    if getattr(args, "routing_json", False):
        out = routing.manifest()
        out["active_profile"] = routing.active_profile(profile_name)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0

    m = routing.manifest()
    active = routing.active_profile(profile_name)

    print(f"build tier        : {m.get('build_tier')}")
    print(f"controller policy : {m.get('controller_policy')}")
    print(f"default strategy  : {m.get('default_strategy')}")
    print(f"bound families    : {', '.join(m.get('bound_families', []))}")
    print(f"bound models      : {len(m.get('bound_models', []))}")
    for mid in m.get("bound_models", []):
        print(f"  - {mid}")
    print(f"priority order    : {', '.join(m.get('priority_order', []))}")
    print(f"active profile    : {profile_name}")
    print(f"  strategy        : {active.get('strategy')}")
    print(f"  families        : {', '.join(active.get('families', []))}")
    if m.get("manifest_signature"):
        print(f"manifest signature: {m.get('manifest_signature')}")
    if m.get("installed_at"):
        print(f"installed at      : {m.get('installed_at')}")
    if m.get("repo_source"):
        print(f"repo source       : {m.get('repo_source')}")
    return 0
