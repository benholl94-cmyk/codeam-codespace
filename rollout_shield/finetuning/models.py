"""Dataclasses for the finetuning subsystem.

Records are JSON-serialized to ``<state>/finetuning/{datasets,adapters,runs}/``
via ``State.atomic_write_json`` and friends. They are immutable
``@dataclass(frozen=True)`` so callers cannot mutate a record in place —
every status transition must go through the module API and persist.

ID scheme (mirrors ``ai/generator.py`` ``fk_<sha256-prefix-16>``):

- Datasets:  ``ds_<16hex>``    (content-hashed — re-registering the same
  file produces the same id)
- Adapters:  ``ft_<16hex>``    (deterministic on
  ``sha256(base|dataset|recipe|backend|seed|hyperparams)``)
- Runs:      ``run_<16hex>``   (random — one per start_run call)
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _now() -> int:
    return int(time.time())


def _short_hash(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]


def new_dataset_id(path: Path, format_name: str,
                    content_sha256: str | None = None) -> str:
    """Deterministic dataset id; same content + same format → same id.

    ``content_sha256`` is preferred as the seed (so two paths to the same
    file produce the same id). Falls back to ``str(path)`` when not
    provided (e.g. tests).
    """
    seed = content_sha256 or str(path)
    return "ds_" + _short_hash([seed, format_name])


def new_adapter_id(*, base_model_id: str, dataset_id: str,
                   recipe_name: str, backend: str, seed: int,
                   hyperparams: dict[str, Any]) -> str:
    """Deterministic adapter id; same inputs → same manifest."""
    hp = json.dumps(hyperparams, sort_keys=True, separators=(",", ":"))
    return "ft_" + _short_hash(
        [base_model_id, dataset_id, recipe_name, backend, str(seed), hp])


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class DatasetRecord:
    """A registered dataset of training examples."""
    dataset_id: str
    name: str
    format: str                  # "prompt-target" | "prompt-target-score" | "raw"
    n_samples: int
    train_size: int
    val_size: int
    content_sha256: str
    path: Path
    created_at: int = field(default_factory=_now)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["path"] = str(self.path)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DatasetRecord:
        raw = dict(raw)
        raw["path"] = Path(raw["path"])
        return cls(**raw)


@dataclass(frozen=True)
class AdapterRecord:
    """A trained finetuning adapter."""
    adapter_id: str
    base_model_id: str
    dataset_id: str
    recipe_name: str
    backend: str                 # "stdlib" | "peft"
    status: str                  # "trained" | "eval_passed" | "eval_failed"
                                 # | "promoted" | "aborted"
    train_steps: int
    train_samples_seen: int
    train_loss_final: float
    eval_metrics: dict[str, float] = field(default_factory=dict)
    eval_threshold: float = 0.0
    artifact_path: Path | None = None
    created_at: int = field(default_factory=_now)
    finished_at: int | None = None
    promoted_as_model_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if self.artifact_path is not None:
            d["artifact_path"] = str(self.artifact_path)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AdapterRecord:
        raw = dict(raw)
        if raw.get("artifact_path") is not None:
            raw["artifact_path"] = Path(raw["artifact_path"])
        else:
            raw["artifact_path"] = None
        return cls(**raw)


@dataclass(frozen=True)
class RunRecord:
    """One training run; usually yields one adapter."""
    run_id: str
    dataset_id: str
    base_model_id: str
    recipe_name: str
    backend: str
    status: str                  # "started" | "trained" | "eval_passed"
                                 # | "eval_failed" | "promoted" | "aborted"
    epochs: int
    batch_size: int
    lr: float
    seed: int
    max_steps: int | None
    started_at: int = field(default_factory=_now)
    finished_at: int | None = None
    adapter_id: str | None = None
    last_error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunRecord:
        return cls(**raw)


# status enums (string-valued, not strict, so persistence survives refactor)
DATASET_STATUS_REGISTERED = "registered"
RUN_STATUS_STARTED = "started"
RUN_STATUS_TRAINED = "trained"
RUN_STATUS_EVAL_PASSED = "eval_passed"
RUN_STATUS_EVAL_FAILED = "eval_failed"
RUN_STATUS_PROMOTED = "promoted"
RUN_STATUS_ABORTED = "aborted"

ADAPTER_STATUS_TRAINED = "trained"
ADAPTER_STATUS_EVAL_PASSED = "eval_passed"
ADAPTER_STATUS_EVAL_FAILED = "eval_failed"
ADAPTER_STATUS_PROMOTED = "promoted"
ADAPTER_STATUS_ABORTED = "aborted"

ALL_RUN_STATUSES = (
    RUN_STATUS_STARTED, RUN_STATUS_TRAINED,
    RUN_STATUS_EVAL_PASSED, RUN_STATUS_EVAL_FAILED,
    RUN_STATUS_PROMOTED, RUN_STATUS_ABORTED,
)

ALL_ADAPTER_STATUSES = (
    ADAPTER_STATUS_TRAINED, ADAPTER_STATUS_EVAL_PASSED,
    ADAPTER_STATUS_EVAL_FAILED, ADAPTER_STATUS_PROMOTED,
    ADAPTER_STATUS_ABORTED,
)
