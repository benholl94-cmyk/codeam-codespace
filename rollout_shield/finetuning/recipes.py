"""Training recipes — a small, named registry of hyperparameter sets.

Each recipe is a frozen dataclass with the hyperparameters a backend
needs. Recipes intentionally avoid ML-specific knobs (rank, alpha, etc.)
in this CLI — those are exposed only by the ``peft`` backend, which
gets ``recipe.peft_overrides`` as a free-form ``dict`` to splat into
its config. The CLI surface stays minimal.

Adding a new recipe = add one entry to ``RECIPES`` below + a
corresponding stanza in ``docs/FINETUNING.md``.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


class RecipeError(ValueError):
    """Raised for unknown / unsupported recipes."""


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    objective: str                  # "sft" | "dpo" | "continued_pretraining"
    epochs: int
    batch_size: int
    lr: float
    seed: int = 42
    max_steps: int | None = None
    eval_threshold: float = 0.0     # min score on val to mark eval_passed
    peft_overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


RECIPES: dict[str, Recipe] = {
    r.name: r for r in [
        Recipe(
            name="sft-mini",
            description=("Quick supervised-finetune surrogate. The stdlib "
                         "backend treats it as a single-pass pattern capture; "
                         "the peft backend runs a single-epoch LoRA pass."),
            objective="sft",
            epochs=1,
            batch_size=8,
            lr=1e-4,
            max_steps=200,
            eval_threshold=0.10,
            peft_overrides={"lora_r": 4, "lora_alpha": 8},
        ),
        Recipe(
            name="lora-tiny",
            description=("Single-epoch LoRA pass on the base, with early "
                         "stopping at max_steps. For the peft backend only; "
                         "the stdlib backend falls back to sft-mini semantics."),
            objective="sft",
            epochs=1,
            batch_size=4,
            lr=2e-4,
            max_steps=100,
            eval_threshold=0.15,
            peft_overrides={"lora_r": 8, "lora_alpha": 16},
        ),
        Recipe(
            name="dpo-mini",
            description=("Direct-preference-optimization surrogate. Requires "
                         "format='prompt-target-score'. The stdlib backend "
                         "weights patterns by score; the peft backend runs a "
                         "single-epoch DPO pass."),
            objective="dpo",
            epochs=1,
            batch_size=4,
            lr=5e-5,
            max_steps=100,
            eval_threshold=0.10,
            peft_overrides={"beta": 0.1},
        ),
    ]
}


SUPPORTED_RECIPES = tuple(RECIPES.keys())


def get_recipe(name: str) -> Recipe:
    try:
        return RECIPES[name]
    except KeyError as exc:
        raise RecipeError(
            f"unknown recipe {name!r}; valid: {list(RECIPES)}"
        ) from exc


def recipe_needs_score(name: str) -> bool:
    return get_recipe(name).objective == "dpo"


__all__ = ["Recipe", "RECIPES", "SUPPORTED_RECIPES", "get_recipe",
           "recipe_needs_score", "RecipeError"]
