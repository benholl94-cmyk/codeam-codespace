"""Benchmark suite for rollout-shield models.

Each benchmark is a callable that takes a model's output dict (as
returned by ``models.ModelInfo.run``) and returns a score in [0, 1].

Benchmarks are deterministic (no LLM-as-judge; that requires a real
model and a network round-trip). The suite is small but covers the
key dimensions:

- structural (JSON / Python syntax validity)
- stability (deterministic — same prompt → same output)
- diversity (different prompts → different outputs)
- length (output within an expected token range)

The leaderboard (see leaderboard.py) aggregates scores across the
suite and tracks them over time.

Adding a benchmark: implement a function ``benchmark_xxx(model_id,
output, ctx) -> float`` and add it to ``DEFAULT_BENCHMARKS``.
``ctx`` carries per-benchmark state across calls (for the stability
benchmark, for example).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..state import State


BenchmarkFn = Callable[[str, dict, dict], float]


@dataclass
class BenchmarkResult:
    name: str
    model_id: str
    score: float
    notes: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "model_id": self.model_id,
                "score": round(self.score, 4), "notes": self.notes,
                "duration_ms": round(self.duration_ms, 4)}


# ---------- benchmarks ----------


def benchmark_json_validity(model_id: str, output: dict, ctx: dict) -> BenchmarkResult:
    """Grade JSON structural validity. 1.0 if valid, 0.0 otherwise."""
    import time
    start = time.perf_counter()
    text = output.get("text", "")
    try:
        json.loads(text)
        score = 1.0
        notes = "valid JSON"
    except (ValueError, TypeError):
        score = 0.0
        notes = "invalid JSON"
    return BenchmarkResult(
        name="json_validity", model_id=model_id, score=score, notes=notes,
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def benchmark_python_syntax(model_id: str, output: dict, ctx: dict) -> BenchmarkResult:
    """Grade Python syntactic validity (AST parse). 1.0 if parses, 0.0 otherwise."""
    import time
    start = time.perf_counter()
    text = output.get("text", "")
    try:
        ast.parse(text)
        score = 1.0
        notes = "parses as Python"
    except SyntaxError:
        score = 0.0
        notes = "syntax error"
    except (ValueError, TypeError):
        score = 0.0
        notes = "invalid input"
    return BenchmarkResult(
        name="python_syntax", model_id=model_id, score=score, notes=notes,
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def benchmark_stability(model_id: str, output: dict, ctx: dict) -> BenchmarkResult:
    """Grade stability: do identical prompts produce identical outputs?

    Requires the model to have already been called twice with the same
    prompt (see run_model_benchmarks). 1.0 if stable, 0.0 if diverged.
    """
    import time
    start = time.perf_counter()
    prior = ctx.get("stability_prior", {}).get(model_id)
    text = output.get("text", "")
    if prior is None:
        # First call — can't grade yet; defer to second call.
        return BenchmarkResult(
            name="stability", model_id=model_id, score=0.5,
            notes="first call (deferred)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    score = 1.0 if prior == text else 0.0
    return BenchmarkResult(
        name="stability", model_id=model_id, score=score,
        notes="stable" if score == 1.0 else "diverged",
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def benchmark_diversity(model_id: str, output: dict, ctx: dict) -> BenchmarkResult:
    """Grade diversity: across different prompts, are outputs different?

    Requires the model to have been called with at least 2 prompts.
    Score is 1 - normalized_similarity (1.0 = maximally diverse).
    """
    import time
    start = time.perf_counter()
    seen = ctx.setdefault("diversity_seen", {}).setdefault(model_id, [])
    text = output.get("text", "")
    if not seen:
        seen.append(text)
        return BenchmarkResult(
            name="diversity", model_id=model_id, score=0.5,
            notes="first call (deferred)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    # Average jaccard distance to prior outputs
    def _tokens(s: str) -> set[str]:
        return set(re.findall(r"\w+", s.lower()))
    new_tokens = _tokens(text)
    similarities = []
    for prior in seen:
        prior_tokens = _tokens(prior)
        if not new_tokens and not prior_tokens:
            similarities.append(1.0)
            continue
        union = new_tokens | prior_tokens
        if not union:
            similarities.append(0.0)
            continue
        similarities.append(len(new_tokens & prior_tokens) / len(union))
    seen.append(text)
    avg_sim = sum(similarities) / len(similarities) if similarities else 1.0
    score = max(0.0, 1.0 - avg_sim)
    return BenchmarkResult(
        name="diversity", model_id=model_id, score=score,
        notes=f"avg_jaccard={avg_sim:.3f}",
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def benchmark_length_in_range(model_id: str, output: dict, ctx: dict,
                              min_tokens: int = 5, max_tokens: int = 500) -> BenchmarkResult:
    """Grade length: output is between min_tokens and max_tokens (inclusive)."""
    import time
    start = time.perf_counter()
    tokens = output.get("tokens", 0)
    if min_tokens <= tokens <= max_tokens:
        score = 1.0
        notes = f"{tokens} tokens (in [{min_tokens},{max_tokens}])"
    else:
        # Linear falloff outside the range
        if tokens < min_tokens:
            score = tokens / min_tokens if min_tokens else 0.0
        else:
            score = max(0.0, 1.0 - (tokens - max_tokens) / max_tokens)
        notes = f"{tokens} tokens (out of range)"
    return BenchmarkResult(
        name="length_in_range", model_id=model_id, score=score, notes=notes,
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def benchmark_has_marker(model_id: str, output: dict, ctx: dict,
                         marker: str = "[") -> BenchmarkResult:
    """Grade: output starts with the expected marker (e.g., '[' for tagged output)."""
    import time
    start = time.perf_counter()
    text = output.get("text", "")
    score = 1.0 if text.lstrip().startswith(marker) else 0.0
    return BenchmarkResult(
        name=f"has_marker_{marker}", model_id=model_id, score=score,
        notes="marker present" if score == 1.0 else "marker missing",
        duration_ms=(time.perf_counter() - start) * 1000,
    )


# ---------- registry ----------


DEFAULT_BENCHMARKS: list[BenchmarkFn] = [
    benchmark_json_validity,
    benchmark_python_syntax,
    benchmark_stability,
    benchmark_diversity,
    benchmark_length_in_range,
]


def run_model_benchmarks(model_id: str, output: dict,
                         benchmarks: list[BenchmarkFn] | None = None,
                         ctx: dict | None = None) -> list[BenchmarkResult]:
    """Run all benchmarks on one (model_id, output) pair."""
    benchmarks = benchmarks or DEFAULT_BENCHMARKS
    ctx = ctx or {}
    results: list[BenchmarkResult] = []
    for fn in benchmarks:
        try:
            res = fn(model_id, output, ctx)
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            results.append(BenchmarkResult(
                name=fn.__name__, model_id=model_id,
                score=0.0, notes=f"benchmark raised: {exc!r}",
            ))
    return results


def aggregate_benchmark_results(results: list[BenchmarkResult]) -> dict[str, float]:
    """Aggregate a list of benchmark results into a per-model score map."""
    by_model: dict[str, list[float]] = {}
    for r in results:
        by_model.setdefault(r.model_id, []).append(r.score)
    return {mid: (sum(s) / len(s) if s else 0.0) for mid, s in by_model.items()}
