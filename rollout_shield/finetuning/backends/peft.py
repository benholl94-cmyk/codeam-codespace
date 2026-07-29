"""The ``peft`` backend — OPTIONAL.

Activated by ``pip install rollout-shield[finetune]``. Requires
``torch``, ``transformers``, ``peft``, ``trl``, and ``datasets``.

Status of recipes at this release (0.1.0):

- ``sft-mini``  — implemented; uses LoRA on a small open base.
                  Default base: ``sshleifer/tiny-gpt2``.
                  Marked ``experimental``.
- ``lora-tiny`` — stub; raises ``NotImplementedError`` with
                  installation instructions.
- ``dpo-mini``  — stub; raises ``NotImplementedError``.

The skeleton is intentionally small. The architecture / abstraction is
the deliverable; loading a 7 B LoRA training from a CLI tool is out of
scope for this build (see ``docs/FINETUNING.md``).
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

PEFT_NAME = "peft"


class PeftBackendError(RuntimeError):
    """Raised when the peft backend is selected but deps are missing."""


def _require_deps() -> None:
    try:
        import datasets  # noqa: F401
        import peft  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import trl  # noqa: F401
    except ImportError as exc:
        raise PeftBackendError(
            "the `peft` backend requires torch + transformers + peft + trl + "
            "datasets; install with `pip install rollout-shield[finetune]`"
        ) from exc


def _emit_meta(line: str) -> None:
    try:
        sys.stderr.write(f"[peft-backend] {line}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_sft_mini(*, state, dataset_id: str, base_model_id: str,
                    recipe_name: str, epochs: int, batch_size: int, lr: float,
                    seed: int, max_steps: int | None,
                    artifact_dir: Path, abort_flag_path: Path) -> dict[str, Any]:
    """Real LoRA SFT over a tiny open base.

    Implementation outline:

    1. Load the base via ``transformers.AutoTokenizer`` /
       ``transformers.AutoModelForCausalLM``.
    2. Wrap with ``peft.LoraConfig`` (r=8, alpha=16, dropout=0.05).
    3. Stream the train split via ``datasets.Dataset.from_list``.
    4. ``trl.SFTTrainer`` does the training; one epoch by default.
    5. Persist the adapter via ``peft.get_peft_model().save_pretrained``.

    The CI smoke test runs with ``max_steps=2`` against
    ``sshleifer/tiny-gpt2`` to keep wall-clock under a few seconds.
    """
    _require_deps()
    import peft  # type: ignore
    import transformers  # type: ignore
    import trl  # type: ignore

    from ..datasets import iter_split

    rows = list(iter_split(state, dataset_id, "train"))
    if not rows:
        raise PeftBackendError(
            f"dataset {dataset_id!r} has no train split rows"
        )

    base_id = base_model_id or "sshleifer/tiny-gpt2"
    _emit_meta(f"loading base {base_id}")
    tok = transformers.AutoTokenizer.from_pretrained(base_id)
    model = transformers.AutoModelForCausalLM.from_pretrained(base_id)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # attach LoRA
    lora_cfg = peft.LoraConfig(
        r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=["c_attn"] if "gpt2" in base_id else ["q_proj", "v_proj"],
        bias="none", task_type=peft.TaskType.CAUSAL_LM,
    )
    model = peft.get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    # materialize dataset
    def _format(row):
        prompt = row.get("prompt") or ""
        target = row.get("target") or ""
        return f"{prompt}\n{target}".strip()

    texts = [_format(r) for r in rows if r.get("target")]
    from datasets import Dataset  # type: ignore
    ds = Dataset.from_dict({"text": texts})

    args = trl.SFTConfig(  # type: ignore[attr-defined]
        output_dir=str(artifact_dir),
        num_train_epochs=int(epochs),
        per_device_train_batch_size=int(batch_size),
        learning_rate=float(lr),
        seed=int(seed),
        max_steps=int(max_steps) if max_steps is not None else -1,
        save_strategy="no",
        report_to=[],
        logging_steps=1,
    )
    trainer = trl.SFTTrainer(  # type: ignore[attr-defined]
        model=model, args=args, train_dataset=ds, processing_class=tok,
    )

    _emit_meta(f"starting SFT: epochs={epochs} steps_cap={max_steps}")
    trainer.train()

    artifact_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(artifact_dir))
    tok.save_pretrained(str(artifact_dir))
    (artifact_dir / "BACKEND.txt").write_text(
        f"backend={PEFT_NAME}\nrecipe={recipe_name}\nbase={base_id}\n")

    final_loss = 0.0
    try:
        # trl logs the final loss in trainer.state.log_history
        log_history = trainer.state.log_history or []
        for entry in reversed(log_history):
            if "train_loss" in entry:
                final_loss = float(entry["train_loss"])
                break
    except Exception:  # noqa: BLE001
        pass

    return {
        "final_loss": float(final_loss),
        "train_steps": int(getattr(trainer.state, "global_step", 0) or 0),
        "samples_seen": int(len(texts) * max(1, int(epochs))),
        "artifact": "adapter_model.safetensors",
    }


def _stub(recipe_name: str) -> None:
    raise NotImplementedError(
        f"recipe {recipe_name!r} is a stub in the peft backend at this "
        "release. Install peft + trl and choose a base model — see "
        "docs/FINETUNING.md."
    )


def train(*, state, dataset_id: str, base_model_id: str,
          recipe_name: str, epochs: int, batch_size: int, lr: float,
          seed: int, max_steps: int | None,
          artifact_dir: Path, abort_flag_path: Path) -> dict[str, Any]:
    if recipe_name == "sft-mini":
        return _build_sft_mini(
            state=state, dataset_id=dataset_id, base_model_id=base_model_id,
            recipe_name=recipe_name, epochs=epochs, batch_size=batch_size,
            lr=lr, seed=seed, max_steps=max_steps,
            artifact_dir=artifact_dir, abort_flag_path=abort_flag_path,
        )
    _stub(recipe_name)


def build_generator(state, adapter_id: str) -> Callable[[str], str]:
    """Return a generator that loads the saved LoRA and runs inference.

    Implementation outline:

    1. Read ``<state>/finetuning/adapters/<adapter_id>.artifacts/`` and
       reconstruct the peft model + base.
    2. Return a closure that tokenizes, calls ``model.generate`` with a
       small ``max_new_tokens`` cap (default 32), and decodes.
    """
    _require_deps()
    import peft  # type: ignore
    import torch  # type: ignore
    import transformers  # type: ignore

    artifact_dir = state.finetuning_adapters_dir / f"{adapter_id}.artifacts"
    if not artifact_dir.exists():
        def _fb(prompt: str) -> str:
            return f"[peft-no-artifact:{adapter_id[:8]}]"
        return _fb

    # Read the manifest sidecar to recover base_model_id
    from ..adapters import get_adapter
    adapter = get_adapter(state, adapter_id)
    base_id = adapter.base_model_id if adapter else "sshleifer/tiny-gpt2"

    tok = transformers.AutoTokenizer.from_pretrained(str(artifact_dir))
    base = transformers.AutoModelForCausalLM.from_pretrained(base_id)
    model = peft.PeftModel.from_pretrained(base, str(artifact_dir))
    model.eval()

    def _generate(prompt: str) -> str:
        ids = tok(prompt, return_tensors="pt").input_ids
        with torch.no_grad():
            out = model.generate(ids, max_new_tokens=32, do_sample=False)
        return tok.decode(out[0][ids.shape[-1]:], skip_special_tokens=True)

    return _generate


name = PEFT_NAME


__all__ = ["name", "train", "build_generator", "PEFT_NAME", "PeftBackendError"]
