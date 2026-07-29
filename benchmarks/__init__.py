"""Performance benchmarks sub-workspace.

Measures the runtime cost of common rollout-shield operations:

- micro: state I/O, key generation, signature verify, monitor cycle
- ai:    router parallel execution, model cold/warm, leaderboard lookup

Run:

    python -m benchmarks                  # full suite
    python -m benchmarks --kind micro     # only micro
    python -m benchmarks --kind ai        # only AI
    python -m benchmarks --output results/snapshot.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__version__ = "0.1.0"


@dataclass
class BenchResult:
    """A single benchmark measurement."""
    name: str
    kind: str           # "micro" | "ai"
    iterations: int
    total_ms: float
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- micro benchmarks ----------


def _time_n(fn: Callable[[], Any], iterations: int) -> list[float]:
    """Run ``fn`` ``iterations`` times and return per-call ms."""
    out: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000)
    return out


def _stats(name: str, kind: str, samples: list[float], notes: str = "") -> BenchResult:
    return BenchResult(
        name=name,
        kind=kind,
        iterations=len(samples),
        total_ms=sum(samples),
        mean_ms=statistics.mean(samples),
        median_ms=statistics.median(samples),
        min_ms=min(samples),
        max_ms=max(samples),
        stdev_ms=statistics.stdev(samples) if len(samples) > 1 else 0.0,
        notes=notes,
    )


def bench_state_load_config(tmp: Path) -> BenchResult:
    from rollout_shield.state import State
    s = State(root=tmp)
    s.save_config({"x": 1})
    samples = _time_n(lambda: s.load_config(), iterations=200)
    return _stats("state.load_config", "micro", samples, "load+parse JSON config")


def bench_state_save_config(tmp: Path) -> BenchResult:
    from rollout_shield.state import State
    s = State(root=tmp)
    samples = _time_n(lambda: s.save_config({"x": 1, "y": 2}), iterations=200)
    return _stats("state.save_config", "micro", samples, "atomic write + temp+rename")


def bench_state_summary(tmp: Path) -> BenchResult:
    from rollout_shield.state import State
    s = State(root=tmp)
    # add a few claims so iter counts are non-trivial
    for i in range(20):
        s.append_claim({"id": f"clm_{i}", "ts": i, "type": "intent",
                        "agent_id": "a", "body": "", "parent": None,
                        "signing": {}, "schema": "rollout-shield.claim/v1"})
    samples = _time_n(lambda: s.summary(), iterations=200)
    return _stats("state.summary", "micro", samples,
                  "summary with 20 claims loaded")


def bench_state_iter_claims(tmp: Path) -> BenchResult:
    from rollout_shield.state import State
    s = State(root=tmp)
    for i in range(50):
        s.append_claim({"id": f"clm_{i}", "ts": i, "type": "intent",
                        "agent_id": "a", "body": "", "parent": None,
                        "signing": {}, "schema": "rollout-shield.claim/v1"})
    samples = _time_n(lambda: list(s.iter_claims(limit=1000)), iterations=200)
    return _stats("state.iter_claims", "micro", samples,
                  "iterate 50 claims end-to-end")


def bench_key_generation(tmp: Path) -> BenchResult:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return _stats("keys.new", "micro", [0.0], "SKIPPED: cryptography not installed")
    from rollout_shield.commands.keys import cmd_keys_new
    from rollout_shield.state import State
    s = State(root=tmp)
    counter = {"i": 0}
    def _gen():
        counter["i"] += 1
        cmd_keys_new(s, agent_id=f"agent-{counter['i']}", description="bench")
    samples = _time_n(_gen, iterations=20)
    return _stats("keys.new", "micro", samples, "Ed25519 keypair generation")


def bench_health_checks(tmp: Path) -> BenchResult:
    from rollout_shield.health_checks import run_all_checks
    from rollout_shield.state import State
    s = State(root=tmp)
    samples = _time_n(lambda: run_all_checks(s), iterations=100)
    return _stats("health.state_checks", "micro", samples,
                  "6 state-level health checks (default)")


def bench_repo_checks(tmp: Path) -> BenchResult:
    from rollout_shield.repo_checks import run_repo_checks
    from rollout_shield.state import State
    s = State(root=tmp)
    samples = _time_n(lambda: run_repo_checks(s), iterations=20)
    return _stats("health.repo_checks", "micro", samples,
                  "7 repo-level checks (clean repo)")


def bench_host_checks(tmp: Path) -> BenchResult:
    from rollout_shield.host_checks import run_host_checks
    from rollout_shield.state import State
    s = State(root=tmp)
    samples = _time_n(lambda: run_host_checks(s), iterations=100)
    return _stats("health.host_checks", "micro", samples,
                  "host kernel checks (load, mem, mounts, sockets, DNS)")


# ---------- AI benchmarks ----------


def bench_router_cold(tmp: Path) -> BenchResult:
    from rollout_shield.ai.router import route
    from rollout_shield.state import State
    s = State(root=tmp)
    samples = _time_n(
        lambda: route(prompt="cold router", strategy="concat",
                      models=["mock-deterministic", "mock-structured"],
                      state=s),
        iterations=20,
    )
    return _stats("router.cold", "ai", samples,
                  "N=2 mock models, parallel, first call is cold")


def bench_router_warm(tmp: Path) -> BenchResult:
    from rollout_shield.ai.router import route
    from rollout_shield.state import State
    s = State(root=tmp)
    # warm up
    for _ in range(5):
        route(prompt="warmup", strategy="concat",
              models=["mock-deterministic"], state=s)
    samples = _time_n(
        lambda: route(prompt="warm router", strategy="concat",
                      models=["mock-deterministic", "mock-structured"],
                      state=s),
        iterations=50,
    )
    return _stats("router.warm", "ai", samples,
                  "N=2 mock models, parallel, after warm-up")


def bench_model_cold() -> BenchResult:
    from rollout_shield.ai.models import get_model
    samples = _time_n(lambda: get_model("mock-deterministic").run("x"), iterations=20)
    return _stats("model.cold", "ai", samples, "single model cold call")


def bench_model_warm() -> BenchResult:
    from rollout_shield.ai.models import get_model
    m = get_model("mock-deterministic")
    for _ in range(5):
        m.run("warmup")
    samples = _time_n(lambda: m.run("warm"), iterations=100)
    return _stats("model.warm", "ai", samples, "single model warm call")


def bench_own_models_warm(tmp: Path) -> BenchResult:
    from rollout_shield.ai.own_models import (
        repo_aware_model,
        rollout_model,
        spec_citation_model,
    )
    from rollout_shield.state import State
    s = State(root=tmp)
    # warm up
    for _ in range(3):
        rollout_model("warm", {"state": s})
        repo_aware_model("warm", {"state": s})
        spec_citation_model("warm", {"state": s})
    samples = _time_n(
        lambda: (
            rollout_model("bench", {"state": s}),
            repo_aware_model("bench", {"state": s}),
            spec_citation_model("bench", {"state": s}),
        ),
        iterations=30,
    )
    return _stats("own_models.warm", "ai", samples,
                  "rollout-model + repo-aware + spec-citation (warm)")


# ---------- runner ----------


MICRO_BENCHES = [
    bench_state_load_config,
    bench_state_save_config,
    bench_state_summary,
    bench_state_iter_claims,
    bench_key_generation,
    bench_health_checks,
    bench_repo_checks,
    bench_host_checks,
]

AI_BENCHES = [
    bench_router_cold,
    bench_router_warm,
    bench_model_cold,
    bench_model_warm,
    bench_own_models_warm,
]


def _tmp_root() -> Path:
    import tempfile
    return Path(tempfile.mkdtemp(prefix="rollout-shield-bench-"))


def _print_table(results: list[BenchResult]) -> None:
    print()
    print(f"{'name':<28} {'iter':>5} {'mean (ms)':>12} {'median (ms)':>12} "
          f"{'min (ms)':>10} {'max (ms)':>10} {'stdev':>8}")
    print("-" * 92)
    for r in results:
        print(f"{r.name:<28} {r.iterations:>5} {r.mean_ms:>12.3f} "
              f"{r.median_ms:>12.3f} {r.min_ms:>10.3f} {r.max_ms:>10.3f} "
              f"{r.stdev_ms:>8.3f}")
    print()


def _write_markdown(results: list[BenchResult], path: Path) -> None:
    lines = ["| name | iter | mean (ms) | median (ms) | min (ms) | max (ms) | stdev |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r.name} | {r.iterations} | {r.mean_ms:.3f} | "
                     f"{r.median_ms:.3f} | {r.min_ms:.3f} | {r.max_ms:.3f} | "
                     f"{r.stdev_ms:.3f} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmarks",
                                     description="rollout-shield perf benchmarks")
    parser.add_argument("--kind", choices=["micro", "ai", "all"], default="all")
    parser.add_argument("--output", default=None,
                        help="write JSON results to this path")
    parser.add_argument("--markdown", default=None,
                        help="write a Markdown table to this path")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON to stdout instead of a table")
    args = parser.parse_args(argv)

    tmp = _tmp_root()
    try:
        results: list[BenchResult] = []
        if args.kind in ("micro", "all"):
            for bench in MICRO_BENCHES:
                try:
                    r = bench(tmp)
                except Exception as exc:  # noqa: BLE001
                    r = _stats(bench.__name__, "micro", [0.0],
                               notes=f"FAIL: {exc!r}")
                results.append(r)
        if args.kind in ("ai", "all"):
            for bench in AI_BENCHES:
                try:
                    r = bench(tmp)
                except Exception as exc:  # noqa: BLE001
                    r = _stats(bench.__name__, "ai", [0.0],
                               notes=f"FAIL: {exc!r}")
                results.append(r)

        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            print(f"rollout-shield benchmarks ({len(results)} result(s))")
            _print_table(results)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps([r.to_dict() for r in results], indent=2),
                encoding="utf-8",
            )
        if args.markdown:
            _write_markdown(results, Path(args.markdown))
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
