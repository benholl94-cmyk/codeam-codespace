"""Self-check (``finetune doctor``).

Reports on:

- Python version + path
- state root existence + writability
- presence of [crypto] / [finetune] extras
- state subdirs (datasets / adapters / runs) and free space
- count of registered datasets / adapters / runs (current state)
- whether the live AI registry has any models (so we can promote)
- whether the stdlib backend is loadable

Returns a single ``DoctorReport`` dataclass — all string fields,
JSON-serializable, so it doubles as the /api/finetuning/doctor response.
"""
from __future__ import annotations

import dataclasses
import shutil
import sys
from typing import Any

from ..state import State


@dataclasses.dataclass
class DoctorReport:
    python: str
    rollout_shield_path: str
    state_root: str
    state_writable: bool
    subdirs: dict[str, str]
    has_crypto: bool
    has_finetune: bool
    backend_stdlib: bool
    backend_peft: bool
    ai_registry_size: int
    datasets: int
    adapters: int
    promoted: int
    runs: int
    disk_free_bytes: int
    issues: list[str] = dataclasses.field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def doctor(state: State) -> DoctorReport:
    issues: list[str] = []

    # state writability
    writable = False
    try:
        p = state.root / ".doctor.touch"
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        writable = True
    except Exception as exc:  # noqa: BLE001
        issues.append(f"state_root not writable: {exc}")

    subdirs = {
        "datasets": str(state.finetuning_datasets_dir),
        "adapters": str(state.finetuning_adapters_dir),
        "runs": str(state.finetuning_runs_dir),
    }

    has_crypto = False
    try:
        import cryptography  # noqa: F401
        has_crypto = True
    except ImportError:
        pass

    has_peft = False
    try:
        import peft  # noqa: F401
        has_peft = True
    except ImportError:
        pass

    backend_stdlib = True
    try:
        from .backends import resolve as _r
        _r("stdlib")
    except Exception as exc:  # noqa: BLE001
        backend_stdlib = False
        issues.append(f"stdlib backend failed to load: {exc}")

    try:
        from .promote import list_promoted
        promoted_count = len(list_promoted(state))
    except Exception:
        promoted_count = 0

    ai_registry_size = 0
    try:
        from ..ai.models import REGISTRY as _REG
        ai_registry_size = len(_REG)
    except Exception:
        pass

    try:
        from .adapters import list_adapters as _la
        from .datasets import list_datasets
        from .training import list_runs as _lr
        datasets_count = len(list_datasets(state))
        adapters_count = len(_la(state))
        runs_count = len(_lr(state))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"state scan failed: {exc}")
        datasets_count = adapters_count = runs_count = 0

    try:
        usage = shutil.disk_usage(state.root)
        free = int(usage.free)
    except Exception:
        free = -1

    # need at least 10 MB free to claim healthy
    if free < 10 * 1024 * 1024:
        issues.append(f"low disk: only {free} bytes free under state root")

    try:
        from rollout_shield import __file__ as _pk
        rs_path = _pk
    except Exception:
        rs_path = "(unknown)"

    return DoctorReport(
        python=f"{sys.version_info.major}.{sys.version_info.minor}",
        rollout_shield_path=rs_path,
        state_root=str(state.root),
        state_writable=writable,
        subdirs=subdirs,
        has_crypto=has_crypto,
        has_finetune=has_peft,
        backend_stdlib=backend_stdlib,
        backend_peft=has_peft,
        ai_registry_size=ai_registry_size,
        datasets=datasets_count,
        adapters=adapters_count,
        promoted=promoted_count,
        runs=runs_count,
        disk_free_bytes=free,
        issues=issues,
        passed=not issues,
    )


__all__ = ["doctor", "DoctorReport"]
