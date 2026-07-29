"""Finetuning subsystem — public API.

The finetuning subsystem provides:

- Dataset registration (JSONL → ``ds_*`` id, content-hashed,
  train/val split)
- Adapter training (deterministic stdlib backend by default; optional
  peft backend when ``pip install rollout-shield[finetune]``)
- Evaluation (exact_match, bleu1_proxy, drift_from_baseline)
- Adapter promotion (registers the adapter as a routable model in
  ``ai.models``)
- Self-check (``doctor``)

The CLI surface lives in ``rollout_shield.commands.finetune``; the HTTP
API is registered in ``rollout_shield.http_server``.

Public surface (re-exported here for callers that prefer one import):

- datasets: ``register_dataset``, ``get_dataset``, ``list_datasets``,
  ``remove_dataset``, ``iter_split``
- adapters: ``get_adapter``, ``list_adapters``, ``remove_adapter``
- training: ``start_run``, ``list_runs``, ``get_run``, ``abort_run``
- evaluation: ``evaluate_adapter``, ``EvalResult``
- recipes: ``get_recipe``, ``RECIPES``, ``SUPPORTED_RECIPES``,
  ``recipe_needs_score``
- promote: ``promote_adapter``, ``unpromote_adapter``, ``list_promoted``,
  ``is_promoted``, ``re_register_promoted``
- doctor: ``doctor``, ``DoctorReport``
"""
from __future__ import annotations

from .adapters import get_adapter, list_adapters, remove_adapter
from .datasets import (
    DEFAULT_SPLIT,
    VALID_FORMATS,
    DatasetError,
    get_dataset,
    iter_split,
    list_datasets,
    register_dataset,
    remove_dataset,
)
from .doctor import DoctorReport, doctor
from .evaluation import EvalResult, evaluate_adapter
from .promote import (
    is_promoted,
    list_promoted,
    promote_adapter,
    re_register_promoted,
    unpromote_adapter,
)
from .recipes import (
    RECIPES,
    SUPPORTED_RECIPES,
    Recipe,
    RecipeError,
    get_recipe,
    recipe_needs_score,
)
from .training import (
    TrainingError,
    abort_run,
    get_run,
    list_runs,
    start_run,
)

__all__ = [
    # datasets
    "register_dataset", "get_dataset", "list_datasets", "remove_dataset",
    "iter_split", "VALID_FORMATS", "DEFAULT_SPLIT", "DatasetError",
    # adapters
    "get_adapter", "list_adapters", "remove_adapter",
    # training
    "start_run", "list_runs", "get_run", "abort_run", "TrainingError",
    # evaluation
    "evaluate_adapter", "EvalResult",
    # recipes
    "get_recipe", "RECIPES", "SUPPORTED_RECIPES", "recipe_needs_score",
    "Recipe", "RecipeError",
    # promote
    "promote_adapter", "unpromote_adapter", "list_promoted",
    "is_promoted", "re_register_promoted",
    # doctor
    "doctor", "DoctorReport",
]
