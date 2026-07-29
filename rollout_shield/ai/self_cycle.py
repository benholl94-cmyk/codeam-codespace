"""Self-cycle engine for rollout-shield.

A "cycle" is a single iteration of: prompt → router → benchmarks →
leaderboard → optional improvement.

Each cycle:

1. Picks a benchmark prompt (from a rotating set or a user-provided one).
2. Runs the prompt through every registered model.
3. Grades every output against the benchmark suite.
4. Updates the leaderboard.
5. Optionally generates a First-of-kind artifact.

The cycle record (prompt, per-model outputs, per-model benchmark
scores, chosen artifact) is written to persistent state so the
engine can be introspected later.

The engine is **deterministic by default**: every cycle uses the
same prompt set unless overridden. This makes the leaderboard a
faithful record of model behavior over time, not noise.

Persistence:

- Cycles: ``<state_root>/ai/cycles.jsonl`` (append-only)
- Leaderboard: ``<state_root>/ai/leaderboard.jsonl`` (append-only)
- First-of-kind artifacts: ``<state_root>/ai/first_of_kind/<id>.json``
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from ..state import State, atomic_append_jsonl
from .benchmarks import (DEFAULT_BENCHMARKS, BenchmarkResult,
                         aggregate_benchmark_results, run_model_benchmarks)
from .generator import generate as generate_fok
from .leaderboard import (LeaderboardEntry, aggregate_scores, append_entries,
                          iter_entries, top_model)
from .models import list_models
from .router import route


# A small fixed prompt set so cycles are reproducible.
DEFAULT_PROMPTS = [
    "ship a canary rollout for the auth refactor",
    "summarize the last 5 deploys and their health signals",
    "investigate why p99 latency spiked at 14:32 UTC",
    "design a rollback procedure for the database migration",
    "generate a First-of-kind poem about signed claims",
]


@dataclass
class CycleRecord:
    cycle: int
    ts: int
    prompt: str
    prompt_digest: str
    router_strategy: str
    selected_model: str | None
    selected_text: str
    parallel_speedup: float
    benchmark_scores: dict[str, float]   # model_id -> avg score
    artifacts: list[str] = field(default_factory=list)  # first-of-kind ids
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def cycle_path(state: State) -> Path:
    p = state.root / "ai" / "cycles.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_cycle(state: State, record: CycleRecord) -> Path:
    atomic_append_jsonl(cycle_path(state), record.to_dict())
    return cycle_path(state)


def iter_cycles(state: State, limit: int | None = None) -> list[CycleRecord]:
    p = cycle_path(state)
    if not p.exists():
        return []
    out: list[CycleRecord] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                rec = CycleRecord(**data)
            except TypeError:
                continue
            out.append(rec)
    out.sort(key=lambda r: r.cycle, reverse=True)
    if limit is not None:
        out = out[:limit]
    return out


def current_cycle_count(state: State) -> int:
    cycles = iter_cycles(state)
    return max((c.cycle for c in cycles), default=0)


def run_one_cycle(state: State, cycle: int | None = None,
                  prompt: str | None = None,
                  models: list[str] | None = None,
                  strategy: str = "best",
                  generate_artifact: bool = True,
                  artifact_kind: str = "summary",
                  benchmarks: list | None = None) -> CycleRecord:
    """Run one self-cycle.

    Parameters
    ----------
    state : State
        Persistent state container.
    cycle : int | None
        Cycle number; defaults to ``current_cycle_count(state) + 1``.
    prompt : str | None
        Prompt to use; defaults to a rotating entry from DEFAULT_PROMPTS.
    models : list[str] | None
        Model ids to route through; defaults to all registered models.
    strategy : str
        Router strategy.
    generate_artifact : bool
        Whether to also generate a First-of-kind artifact.
    artifact_kind : str
        Kind of First-of-kind artifact.
    benchmarks : list | None
        Benchmark functions to use; defaults to DEFAULT_BENCHMARKS.

    Returns
    -------
    CycleRecord
        The cycle's record, also persisted to disk.
    """
    benchmarks = benchmarks or DEFAULT_BENCHMARKS
    cycle_num = cycle if cycle is not None else current_cycle_count(state) + 1

    if prompt is None:
        prompt = DEFAULT_PROMPTS[(cycle_num - 1) % len(DEFAULT_PROMPTS)]

    start = time.perf_counter()
    # Use the latest leaderboard scores for the best-strategy routing
    benchmark_scores = aggregate_scores(state)
    trace = route(prompt=prompt, models=models, strategy=strategy,
                  benchmark_scores=benchmark_scores, max_workers=4)

    # Run benchmarks per model
    all_results: list[BenchmarkResult] = []
    ctx: dict = {}
    for o in trace.outputs:
        if not o.get("ok"):
            continue
        all_results.extend(run_model_benchmarks(o["model_id"], o["output"],
                                                benchmarks=benchmarks, ctx=ctx))
    benchmark_scores = aggregate_benchmark_results(all_results)

    # Write leaderboard entries
    cycle_marker = cycle_num
    entries = [
        LeaderboardEntry(ts=trace.ts, model_id=r.model_id,
                         benchmark_name=r.name, score=r.score,
                         cycle=cycle_marker, notes=r.notes)
        for r in all_results
    ]
    if entries:
        append_entries(state, entries)

    # Optionally generate a First-of-kind artifact
    artifacts: list[str] = []
    if generate_artifact:
        artifact = generate_fok(state, prompt=prompt, kind=artifact_kind)
        artifacts.append(artifact.id)

    duration_ms = (time.perf_counter() - start) * 1000

    record = CycleRecord(
        cycle=cycle_num,
        ts=int(time.time()),
        prompt=prompt,
        prompt_digest=trace.prompt_digest,
        router_strategy=strategy,
        selected_model=trace.selected,
        selected_text=trace.selected_text,
        parallel_speedup=trace.parallel_speedup,
        benchmark_scores=benchmark_scores,
        artifacts=artifacts,
        duration_ms=duration_ms,
    )
    append_cycle(state, record)
    return record


def run_n_cycles(state: State, n: int, **kwargs) -> list[CycleRecord]:
    out: list[CycleRecord] = []
    for _ in range(n):
        out.append(run_one_cycle(state, **kwargs))
    return out
