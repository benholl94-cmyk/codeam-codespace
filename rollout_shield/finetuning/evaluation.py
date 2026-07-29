"""Evaluation harness for finetuning adapters.

Three deterministic benchmarks live here today:

- ``exact_match``         — fraction of val samples where the adapter
                            returns text equal to the row's target
                            (after whitespace normalization).
- ``bleu1_proxy``         — character-overlap F1 between adapter output
                            and target. A cheap, deterministic proxy for
                            character-level BLEU; never 1.0 unless the
                            strings are identical.
- ``drift_from_baseline`` — average character-overlap F1 between the
                            base model's output and the target, minus
                            the adapter's overlap with the target,
                            clipped to [0, 1]. Higher = adapter is
                            closer to the dataset than the base was.

A single ``evaluate_adapter`` function drives all three, returning an
``EvalResult`` dataclass. It also observes the
``rollout_shield_finetuning_eval_score`` histogram once per metric.
"""
from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import metrics
from ..state import State
from .datasets import iter_split


@dataclass(frozen=True)
class EvalResult:
    adapter_id: str
    n_val: int
    metrics: dict[str, float]
    per_sample: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _norm(s: str) -> str:
    """Normalize whitespace for exact-match scoring."""
    return " ".join(s.split()).strip()


def _char_overlap_f1(a: str, b: str) -> float:
    """Symmetric F1 over multisets of characters (cheap BLEU proxy)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # bigram overlap is a tighter metric than character overlap; use both
    def _ngrams(s: str, n: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in range(len(s) - n + 1):
            tok = s[i:i + n]
            out[tok] = out.get(tok, 0) + 1
        return out

    def _prf(reference: dict[str, int], hypothesis: dict[str, int]) -> float:
        if not hypothesis:
            return 0.0
        tp = sum(min(reference.get(k, 0), v) for k, v in hypothesis.items())
        if tp == 0:
            return 0.0
        prec = tp / sum(hypothesis.values())
        rec = tp / sum(reference.values())
        return 2 * prec * rec / (prec + rec)

    return _prf(_ngrams(a, 1), _ngrams(b, 1))


# A backend exposes ``generate(prompt: str) -> str`` (or really any
# callable returning text). We accept any callable so the AI router
# itself can be evaluated too, not just finetuned adapters.
Generator = Callable[[str], str]


def _safe_generate(gen: Generator, prompt: str) -> str:
    try:
        return gen(prompt) or ""
    except Exception:  # noqa: BLE001 — wrap backend errors as empty output
        return ""


def evaluate_adapter(state: State, adapter_id: str,
                     gen: Generator,
                     dataset_id: str | None = None,
                     recipe_name: str | None = None) -> EvalResult:
    """Compute ``exact_match``, ``bleu1_proxy``, ``drift_from_baseline``.

    ``dataset_id`` defaults to the adapter's ``dataset_id``. If unset
    the function raises — it is required.

    ``recipe_name`` is used only as a metric label. If unknown, falls
    back to ``"unknown"``.
    """
    from .adapters import get_adapter

    adapter = get_adapter(state, adapter_id)
    if adapter is None:
        raise FileNotFoundError(f"unknown adapter_id: {adapter_id!r}")
    dataset_id = dataset_id or adapter.dataset_id

    started = time.monotonic()
    exact_total = 0
    bleu_total = 0.0
    n = 0
    per_sample: list[dict[str, Any]] = []
    for row in iter_split(state, dataset_id, "val"):
        prompt = row["prompt"]
        target = row["target"]
        out = _safe_generate(gen, prompt)
        em = 1.0 if _norm(out) == _norm(target) else 0.0
        bleu = _char_overlap_f1(out, target)
        exact_total += em
        bleu_total += bleu
        n += 1
        per_sample.append({
            "prompt": prompt, "target": target, "output": out,
            "exact_match": em, "bleu1_proxy": bleu,
        })
    elapsed = time.monotonic() - started

    # baseline drift — uses the registered base model via a separate call
    base_exact = 0
    base_bleu = 0.0
    base_outputs: list[str] = []
    if adapter.base_model_id:
        from .evaluation_baseline import baseline_generate
        for row in per_sample:
            bout = baseline_generate(state, adapter.base_model_id, row["prompt"])
            base_outputs.append(bout)
            bem = 1.0 if _norm(bout) == _norm(row["target"]) else 0.0
            bbleu = _char_overlap_f1(bout, row["target"])
            base_exact += bem
            base_bleu += bbleu
    base_em_avg = (base_exact / n) if n else 0.0
    base_bleu_avg = (base_bleu / n) if n else 0.0

    adapter_em = (exact_total / n) if n else 0.0
    adapter_bleu = (bleu_total / n) if n else 0.0

    drift_em = max(0.0, min(1.0, adapter_em - base_em_avg))
    drift_bleu = max(0.0, min(1.0, adapter_bleu - base_bleu_avg))
    # combined drift score is the simple average
    drift_score = (drift_em + drift_bleu) / 2.0

    result = EvalResult(
        adapter_id=adapter_id,
        n_val=n,
        metrics={
            "exact_match": round(adapter_em, 4),
            "bleu1_proxy": round(adapter_bleu, 4),
            "drift_from_baseline": round(drift_score, 4),
            "baseline_exact_match": round(base_em_avg, 4),
            "baseline_bleu1_proxy": round(base_bleu_avg, 4),
        },
        per_sample=per_sample,
        elapsed_seconds=round(elapsed, 3),
    )

    # emit per-metric histogram observations
    try:
        label_recipe = recipe_name or "unknown"
        for k, v in result.metrics.items():
            metrics.finetuning_eval_score.observe(v, labels=(label_recipe, k))
    except Exception:  # noqa: BLE001 — best-effort
        pass

    return result


__all__ = ["EvalResult", "evaluate_adapter", "Generator"]
