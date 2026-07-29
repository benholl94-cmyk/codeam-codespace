"""Backend registry + lazy loader.

Two backends ship in this release:

- ``stdlib``  — always available. A deterministic pattern-capture
                adapter for stdlib-only environments. NOT a learned
                model; captures n-gram frequency biases from the
                training split and applies them at inference. Useful
                for testing the subsystem end-to-end without GPU
                access or ML deps.
- ``peft``    — OPTIONAL. Activated by ``pip install
                rollout-shield[finetune]``. Only ``sft-mini`` is
                fully implemented at this release; ``lora-tiny`` and
                ``dpo-mini`` raise ``NotImplementedError`` until
                0.2.0. Marked ``experimental``.

Public surface:

- ``Backend``           — Protocol every backend satisfies
- ``resolve(name)``     — instantiate a backend by name (lazy)
- ``backends()``        — list available names (skips peft if not installed)
- ``BACKENDS``          — the registry dict (mutable, for tests)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    """Every backend satisfies this Protocol.

    Implementations need not subclass; structural typing is enough.
    Methods are sync — both backends ship synchronous at this release.
    """
    name: str

    def train(self, *, state, dataset_id: str, base_model_id: str,
              recipe_name: str, epochs: int, batch_size: int, lr: float,
              seed: int, max_steps: int | None,
              artifact_dir, abort_flag_path) -> dict:
        """Train for ``epochs`` epochs and return a summary dict:

        ``{"final_loss": float, "train_steps": int, "samples_seen": int}``
        """
        ...

    def build_generator(self, state, adapter_id: str):
        """Return a callable ``(prompt: str) -> str`` for inference.

        The callable may be invoked many times after the run completes.
        """
        ...


def backends() -> list[str]:
    """Names of backends that can be resolved right now."""
    out = ["stdlib"]
    try:
        import peft  # noqa: F401
        out.append("peft")
    except ImportError:
        pass
    return out


def _import_stdlib() -> None:
    from . import stdlib as _stdlib  # noqa: F401
    BACKENDS["stdlib"] = _stdlib


def _import_peft() -> None:
    try:
        import peft  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "the `peft` backend requires peft + transformers + torch; "
            "install with `pip install rollout-shield[finetune]`"
        ) from exc
    from . import peft as _peft  # noqa: F401
    BACKENDS["peft"] = _peft


BACKENDS: dict[str, object] = {}
_importer = {"stdlib": _import_stdlib, "peft": _import_peft}


def resolve(name: str) -> Backend:
    """Resolve a backend by name; lazily import on first call."""
    if name not in BACKENDS:
        if name not in _importer:
            raise RuntimeError(
                f"unknown backend: {name!r}; available: {sorted(_importer)}"
            )
        _importer[name]()
    obj = BACKENDS[name]
    if not isinstance(obj, Backend):
        raise RuntimeError(f"backend {name!r} does not satisfy the Backend Protocol")
    return obj


def _register_for_tests(name: str, obj) -> None:
    """Allow tests to inject a fake backend."""
    BACKENDS[name] = obj


def reset() -> None:
    """Reset the registry (used in tests)."""
    BACKENDS.clear()


__all__ = ["Backend", "resolve", "backends", "BACKENDS",
           "_register_for_tests", "reset"]
