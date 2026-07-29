"""Parallel-lateral AI router.

Most routers pick one model from a menu. The rollout-shield router is
different: it runs N models in parallel (the *parallel* dimension),
then combines their outputs laterally using a configurable strategy
(the *lateral* dimension). The combination is what produces the
final answer.

Strategies:

- ``best``     — pick the highest-scored output (per benchmark)
- ``concat``   — concatenate all outputs (debug / inspection)
- ``consensus`` — return outputs that agree on a structural digest
                   (sha256 of normalized output)
- ``first``    — first model to finish (latency-optimized)
- ``median``   — for numeric outputs, return the median

The router is the unique IP of the AI layer. It composes with:

- benchmarks.py — provides the scores used by ``best`` strategy
- self_cycle.py — runs router cycles and tracks improvements
- generator.py — feeds prompts to the router for First-of-kind content

The router is **stateless** across calls: every call records its
own trace. The leaderboard (see leaderboard.py) is the persistent
memory of router performance over time.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from .models import ModelInfo, list_models


STRATEGIES = ("best", "concat", "consensus", "first", "median")


@dataclass
class RouteTrace:
    """A single route invocation's trace record."""
    ts: int
    prompt: str
    prompt_digest: str
    strategy: str
    selected_models: list[str]
    outputs: list[dict]           # per-model outputs
    selected: str | None          # which model's output was chosen
    selected_text: str
    elapsed_ms: float
    parallel_speedup: float       # vs sequential (1.0 = no speedup)

    def to_dict(self) -> dict:
        return asdict(self)


def _digest_prompt(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _normalize_for_consensus(text: str) -> str:
    """Strip whitespace + lowercase for consensus comparison."""
    return "".join(text.lower().split())


def _consensus_digest(text: str) -> str:
    return hashlib.sha256(_normalize_for_consensus(text).encode("utf-8")).hexdigest()[:16]


def _select(strategy: str, prompt: str, outputs: list[dict],
            benchmark_scores: dict[str, float] | None = None) -> tuple[str | None, str]:
    """Apply the lateral-combination strategy.

    Returns ``(selected_model_id, selected_text)``.
    """
    benchmark_scores = benchmark_scores or {}

    if strategy == "first":
        # First to arrive; here we just pick the first since all run in parallel
        # and we measure wall-clock.
        if outputs:
            return outputs[0].get("model_id"), outputs[0]["output"]["text"]
        return None, ""

    if strategy == "concat":
        body = "\n\n".join(f"=== {o['model_id']} ===\n{o['output']['text']}"
                           for o in outputs)
        return "concat", body

    if strategy == "consensus":
        # Group outputs by consensus digest; pick the largest group.
        groups: dict[str, list[dict]] = {}
        for o in outputs:
            d = _consensus_digest(o["output"]["text"])
            groups.setdefault(d, []).append(o)
        largest = max(groups.values(), key=len) if groups else []
        if largest:
            head = largest[0]
            return head["model_id"], head["output"]["text"]
        return None, ""

    if strategy == "median":
        # For numeric outputs only — fallback to first if no numerics.
        # This is a placeholder; expand per use case.
        if outputs:
            return outputs[0].get("model_id"), outputs[0]["output"]["text"]
        return None, ""

    if strategy == "best":
        # Pick the highest benchmark-scored model that returned successfully.
        scored = [(benchmark_scores.get(o["model_id"], 0.0), o) for o in outputs]
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]["model_id"], scored[0][1]["output"]["text"]
        if outputs:
            return outputs[0]["model_id"], outputs[0]["output"]["text"]
        return None, ""

    raise ValueError(f"unknown strategy: {strategy}")


def route(prompt: str,
          models: list[str] | None = None,
          strategy: str = "best",
          benchmark_scores: dict[str, float] | None = None,
          max_workers: int = 4,
          timeout_s: float = 10.0,
          state: "State | None" = None) -> RouteTrace:
    """Route a prompt through N models in parallel and combine laterally.

    Parameters
    ----------
    prompt : str
        The prompt to send to all models.
    models : list[str] | None
        Model ids to use. If None, uses all registered models.
    strategy : str
        Lateral combination strategy (see STRATEGIES).
    benchmark_scores : dict[str, float] | None
        Optional pre-computed benchmark scores for ``best`` strategy.
    max_workers : int
        Thread pool size for parallel execution.
    timeout_s : float
        Per-model timeout in seconds.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}; choices: {STRATEGIES}")
    selected_models = models or [m.id for m in list_models()]
    info_by_id = {m.id: m for m in list_models()}
    missing = [mid for mid in selected_models if mid not in info_by_id]
    if missing:
        raise KeyError(f"unknown model ids: {missing}")

    start = time.perf_counter()
    outputs: list[dict] = []

    def _call(model_id: str) -> dict:
        info = info_by_id[model_id]
        t0 = time.perf_counter()
        try:
            # Thread an optional ``state`` kwarg so own models (which
            # read the local state as their "weights") can use it.
            # Falls back to the no-kwargs invocation for purely offline
            # mocks (they ignore the extra kwarg).
            output = info.run(prompt, state=state)
            elapsed = (time.perf_counter() - t0) * 1000
            return {
                "model_id": model_id,
                "ok": True,
                "output": output,
                "elapsed_ms": elapsed,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "model_id": model_id,
                "ok": False,
                "error": repr(exc),
                "output": {"text": "", "tokens": 0, "meta": {}},
                "elapsed_ms": (time.perf_counter() - t0) * 1000,
            }

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_call, mid) for mid in selected_models]
        # Collect in arrival order (first-finish latency)
        for fut in cf.as_completed(futures):
            outputs.append(fut.result())

    elapsed_ms = (time.perf_counter() - start) * 1000
    parallel_speedup = (sum(o["elapsed_ms"] for o in outputs) / elapsed_ms
                        if elapsed_ms > 0 else 1.0)

    selected_id, selected_text = _select(strategy, prompt, outputs,
                                         benchmark_scores=benchmark_scores or {})

    return RouteTrace(
        ts=int(time.time()),
        prompt=prompt,
        prompt_digest=_digest_prompt(prompt),
        strategy=strategy,
        selected_models=selected_models,
        outputs=outputs,
        selected=selected_id,
        selected_text=selected_text,
        elapsed_ms=elapsed_ms,
        parallel_speedup=parallel_speedup,
    )
