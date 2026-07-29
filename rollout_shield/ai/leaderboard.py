"""Persistent benchmark leaderboard for rollout-shield models.

The leaderboard tracks per-model benchmark scores over time. Each
entry is a snapshot from one benchmark run; the leaderboard view is
the most recent snapshot per (model_id, benchmark_name) pair.

Storage: ``<state_root>/ai/leaderboard.jsonl`` (append-only).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..state import State, atomic_append_jsonl


@dataclass
class LeaderboardEntry:
    ts: int
    model_id: str
    benchmark_name: str
    score: float
    cycle: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def leaderboard_path(state: State) -> Path:
    p = state.root / "ai" / "leaderboard.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_entries(state: State, entries: list[LeaderboardEntry]) -> Path:
    path = leaderboard_path(state)
    for entry in entries:
        atomic_append_jsonl(path, entry.to_dict())
    return path


def iter_entries(state: State, model_id: str | None = None,
                 since_ts: int | None = None) -> list[LeaderboardEntry]:
    path = leaderboard_path(state)
    if not path.exists():
        return []
    out: list[LeaderboardEntry] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = LeaderboardEntry(
                ts=data.get("ts", 0),
                model_id=data.get("model_id", ""),
                benchmark_name=data.get("benchmark_name", ""),
                score=data.get("score", 0.0),
                cycle=data.get("cycle", 0),
                notes=data.get("notes", ""),
            )
            if model_id and entry.model_id != model_id:
                continue
            if since_ts is not None and entry.ts < since_ts:
                continue
            out.append(entry)
    return out


def latest_per_model_benchmark(state: State) -> list[LeaderboardEntry]:
    """Return the latest entry per (model_id, benchmark_name) pair."""
    entries = iter_entries(state)
    latest: dict[tuple[str, str], LeaderboardEntry] = {}
    for e in entries:
        key = (e.model_id, e.benchmark_name)
        if key not in latest or e.ts > latest[key].ts:
            latest[key] = e
    return sorted(latest.values(), key=lambda e: (e.model_id, e.benchmark_name))


def aggregate_scores(state: State) -> dict[str, float]:
    """Average benchmark score per model (across all benchmarks)."""
    latest = latest_per_model_benchmark(state)
    by_model: dict[str, list[float]] = {}
    for e in latest:
        by_model.setdefault(e.model_id, []).append(e.score)
    return {mid: round(sum(s) / len(s), 4) if s else 0.0
            for mid, s in by_model.items()}


def top_model(state: State) -> tuple[str, float] | None:
    """Return the highest-scoring model id and its score."""
    scores = aggregate_scores(state)
    if not scores:
        return None
    best = max(scores.items(), key=lambda kv: kv[1])
    return best
