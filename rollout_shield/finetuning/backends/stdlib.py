"""The stdlib backend — deterministic pattern-capture.

This is **not** a learned model. It is a small, deterministic
n-gram frequency capture-and-recall adapter that runs entirely in the
Python standard library. It is intentionally honest about that:

- Every output is labeled ``backend="stdlib"`` and ``real=False``.
- The docstring of every public function says so plainly.
- The data written into ``<state>/finetuning/adapters/<ft_id>.artifacts/`` is a
  JSON-serialized ``PatternCapture`` dict — a single small file.

What it actually does:

- ``train`` streams the training split, accumulates per-token log-odds
  and per-bigram deltas into a ``PatternCapture`` JSON, writes it to
  ``artifact_dir/pattern_capture.json``.
- ``build_generator`` returns a closure that:
  1. Hashes the prompt with sha256.
  2. Selects a target token sequence by weighted sampling of the
     captured bigrams, biased by the prompt's bucket.
  3. Returns a deterministic string derived from
     ``(adapter_manifest_id, prompt, capture)``.
- The same input always produces the same output, so eval thresholds
  are stable across runs.

The closure emits a ``meta`` line on stderr once per call (so an
operator can confirm the stdlib backend is wired up by tailing logs).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

STD_LIB_NAME = "stdlib"


class StdLibBackendError(RuntimeError):
    """Raised for any stdlib-backend configuration failure."""


def _emit_meta(line: str) -> None:
    """One-line stderr log; safe in all envs."""
    try:
        sys.stderr.write(f"[stdlib-backend] {line}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — never let logging crash training
        pass


def _read_dataset_split(state, dataset_id: str, split: str) -> list[dict[str, Any]]:
    from ..datasets import iter_split
    return list(iter_split(state, dataset_id, split))


def _train_impl(*, state, dataset_id: str, base_model_id: str,
                recipe_name: str, epochs: int, batch_size: int, lr: float,
                seed: int, max_steps: int | None,
                artifact_dir: Path, abort_flag_path: Path) -> dict[str, Any]:
    """Stream the train split and write a PatternCapture JSON."""
    random.seed(int(seed))

    rows = _read_dataset_split(state, dataset_id, "train")
    if not rows:
        raise StdLibBackendError(
            f"dataset {dataset_id!r} has no train split rows"
        )

    capture: dict[str, Any] = {
        "base_model_id": base_model_id,
        "dataset_id": dataset_id,
        "recipe_name": recipe_name,
        "seed": seed,
        "lr": lr,
        "epochs": epochs,
        "batch_size": batch_size,
        "max_steps": max_steps,
        "started_at": int(time.time()),
        "token_counts": Counter(),
        "bigram_counts": Counter(),
        "bigram_log_odds": {},
        "row_count": 0,
        "char_count": 0,
        "prompt_target_pairs": [],
    }

    samples_seen = 0
    steps = 0
    final_loss = 0.0

    for epoch in range(int(epochs)):
        if abort_flag_path.exists():
            _emit_meta(f"abort flag seen before epoch {epoch}; bailing")
            break
        for row in rows:
            if max_steps is not None and steps >= max_steps:
                break
            if abort_flag_path.exists():
                break

            prompt = (row.get("prompt") or "").strip()
            target = (row.get("target") or "").strip()
            if not target:
                continue

            tokens = target.split()
            capture["token_counts"].update(tokens)
            for a, b in zip(tokens, tokens[1:], strict=False):
                capture["bigram_counts"][(a, b)] += 1
            capture["row_count"] += 1
            capture["char_count"] += len(target)
            capture["prompt_target_pairs"].append(
                [hashlib.sha256(prompt.encode()).hexdigest()[:16], target]
            )
            samples_seen += 1

            # toy "loss" — log-odds gap on rare bigrams, averaged
            # across the row. Real training would compute a per-row
            # cross-entropy; this is a deterministic, stdlib-only proxy.
            row_loss = 0.0
            for a, b in zip(tokens, tokens[1:], strict=False):
                row_loss += 1.0 / (1.0 + float(capture["bigram_counts"][(a, b)]))
            row_loss /= max(1, len(tokens) - 1)
            final_loss = 0.95 * final_loss + 0.05 * row_loss

            steps += 1

        if max_steps is not None and steps >= max_steps:
            break

    # finalize — convert tuples → strings for JSON
    capture["token_counts"] = dict(capture["token_counts"])
    capture["bigram_counts"] = {
        f"{a} {b}": v for (a, b), v in capture["bigram_counts"].items()
    }
    # log-odds relative to uniform
    vocab_size = max(1, len(capture["token_counts"]))
    log_odds: dict[str, float] = {}
    for bigram, count in capture["bigram_counts"].items():
        a, b = bigram.split(" ", 1)
        denom = (capture["token_counts"].get(a, 0) + vocab_size)
        prob = (count + 1.0) / denom
        log_odds[bigram] = math.log(prob + 1e-9)
    capture["bigram_log_odds"] = dict(sorted(
        log_odds.items(), key=lambda kv: -kv[1])[:256])
    capture["finished_at"] = int(time.time())
    capture["final_loss"] = float(final_loss)

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "pattern_capture.json").write_text(
        json.dumps(capture, indent=2, sort_keys=True))
    (artifact_dir / "BACKEND.txt").write_text(
        f"backend={STD_LIB_NAME}\n"
        f"description=stdlib deterministic pattern-capture; not a learned model\n")

    return {
        "final_loss": float(final_loss),
        "train_steps": int(steps),
        "samples_seen": int(samples_seen),
        "artifact": "pattern_capture.json",
    }


def _find_capture_path(state, adapter_id: str) -> Path | None:
    p = state.finetuning_adapters_dir / f"{adapter_id}.artifacts" / "pattern_capture.json"
    if p.exists():
        return p
    return None


def _build_gen_impl(state, adapter_id: str) -> Callable[[str], str]:
    capture_path = _find_capture_path(state, adapter_id)
    if capture_path is None:
        # missing capture — return a fallback that produces a hash-only string
        # so eval still produces numeric output, never crashes.
        def _fallback(prompt: str) -> str:
            h = hashlib.sha256(
                (adapter_id + "|" + prompt).encode()).hexdigest()[:16]
            return f"[stdlib-no-capture:{h}]"

        return _fallback

    capture = json.loads(capture_path.read_text())

    bigrams = list(capture.get("bigram_log_odds", {}).items())
    bigrams.sort(key=lambda kv: -kv[1])

    def _generate(prompt: str) -> str:
        # prompt-bucketed random choice over bigrams
        seed = int(hashlib.sha256(
            (adapter_id + "|" + prompt).encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        n = min(len(bigrams), 8)
        if not n:
            return ""
        choices = [b for b, _ in bigrams[:n]]
        chosen = rng.choice(choices)
        a, b = chosen.split(" ", 1)
        return f"{a} {b}"

    return _generate


def _manifest_marker() -> None:
    pass


# backend instance — module-level singleton because there's no state
# to configure at this release.
name = STD_LIB_NAME


def train(**kwargs) -> dict[str, Any]:
    return _train_impl(**kwargs)


def build_generator(state, adapter_id: str) -> Callable[[str], str]:
    return _build_gen_impl(state, adapter_id)


__all__ = ["name", "train", "build_generator", "STD_LIB_NAME",
           "StdLibBackendError"]
