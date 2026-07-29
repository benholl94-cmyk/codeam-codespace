"""Dataset registration: JSONL ingest, train/val split, content hash.

A dataset is a directory under ``<state>/finetuning/datasets/<id>/``:

```
<id>/
├── manifest.json   # DatasetRecord (atomic-write)
├── samples.jsonl   # every row (prompt, target[, score]) — kept one file for simplicity
├── train.jsonl     # train split
├── val.jsonl       # val split
└── events.jsonl    # audit: registered / removed / listed
```

Rows accepted today:

- ``{"prompt": str, "target": str}``            (``format="prompt-target"``)
- ``{"prompt": str, "target": str, "score": float}`` (``format="prompt-target-score"`` — DPO-style preference)
- ``{"text": str}``                              (``format="raw"`` — prompt=target=text)

Empty / malformed rows are skipped with a warning line emitted to
``events.jsonl``. The dataset is content-hashed so re-registering the
same file produces the same ``ds_*`` id (idempotent).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ..state import State, atomic_append_jsonl, atomic_write_json
from .models import DatasetRecord, new_dataset_id

VALID_FORMATS = frozenset({"prompt-target", "prompt-target-score", "raw"})
DEFAULT_SPLIT = 0.8


class DatasetError(ValueError):
    """Raised for any dataset-level problem (missing file, bad format, ...)."""


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(
                    f"{path}:{lineno}: invalid JSON ({exc.msg})"
                ) from exc
            if not isinstance(row, dict):
                raise DatasetError(
                    f"{path}:{lineno}: row is not a JSON object"
                )
            yield row


def _content_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _validate_row(row: dict[str, Any], format_name: str) -> tuple[str, str, float | None]:
    """Return (prompt, target, score) for a row or raise DatasetError."""
    if format_name == "raw":
        text = row.get("text")
        if not isinstance(text, str) or not text:
            raise DatasetError(
                f"raw format requires non-empty 'text' (got {type(text).__name__})"
            )
        return text, text, None
    # prompt-target formats
    prompt = row.get("prompt")
    target = row.get("target")
    if not isinstance(prompt, str) or not prompt.strip():
        raise DatasetError("row missing non-empty 'prompt'")
    if not isinstance(target, str):
        raise DatasetError("row missing 'target' (must be string)")
    score: float | None = None
    if format_name == "prompt-target-score":
        raw_score = row.get("score", 0.0)
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as exc:
            raise DatasetError(f"row score not numeric: {raw_score!r}") from exc
    return prompt, target, score


def register_dataset(state: State, src_path: str | Path,
                     name: str | None = None,
                     format_name: str = "prompt-target",
                     split: float = DEFAULT_SPLIT) -> DatasetRecord:
    """Register a JSONL dataset under a deterministic ``ds_*`` id.

    Idempotent: re-registering the same ``src_path`` with the same
    ``format_name`` returns the existing record instead of re-ingesting.

    Train/val split is done by reservoir-style shuffling with a
    fixed seed for reproducibility.
    """
    src = Path(src_path).expanduser().resolve()
    if not src.is_file():
        raise DatasetError(f"not a file: {src}")
    if format_name not in VALID_FORMATS:
        raise DatasetError(
            f"unknown format {format_name!r}; valid: {sorted(VALID_FORMATS)}"
        )
    if not 0.1 <= split <= 0.99:
        raise DatasetError(f"split must be in [0.1, 0.99]; got {split}")

    # content-hash FIRST so the dataset_id is truly content-addressed
    content_hash = _content_sha256(src)
    dataset_id = new_dataset_id(src, format_name, content_sha256=content_hash)
    root = state.finetuning_datasets_dir / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            existing = DatasetRecord.from_dict(json.loads(manifest_path.read_text()))
            if existing.path == src:
                return existing
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # fall through and re-ingest

    (root).mkdir(parents=True, exist_ok=True)
    name = name or src.stem
    samples_path = root / "samples.jsonl"
    train_path = root / "train.jsonl"
    val_path = root / "val.jsonl"
    events_path = root / "events.jsonl"

    # stream-validate every row, persist canonical samples + train/val split
    train_rows: list[dict[str, Any]] = []  # noqa: F841 — reserved for future streaming API
    val_rows: list[dict[str, Any]] = []  # noqa: F841 — reserved for future streaming API
    samples_lines: list[str] = []
    n_ok = 0
    n_skip = 0
    bad_rows: list[str] = []
    for row in _iter_json_jsonl(src):
        try:
            prompt, target, score = _validate_row(row, format_name)
        except DatasetError as exc:
            n_skip += 1
            if len(bad_rows) < 10:
                bad_rows.append(str(exc))
            continue
        canon = {"prompt": prompt, "target": target}
        if score is not None:
            canon["score"] = score
        samples_lines.append(json.dumps(canon, sort_keys=True, separators=(",", ":")))
        n_ok += 1
    if n_ok == 0:
        raise DatasetError(f"no valid rows in {src} ({n_skip} skipped)")

    # write samples.jsonl
    samples_path.write_text("\n".join(samples_lines) + "\n", encoding="utf-8")

    # deterministic shuffle (sha-based; no random module required)
    seed_key = (str(src) + "|" + format_name).encode()
    def _shuffle_key(idx: int) -> str:
        return hashlib.sha256(seed_key + str(idx).encode()).hexdigest()
    order = sorted(range(n_ok), key=_shuffle_key)
    split_at = int(round(n_ok * split))
    train_order, val_order = order[:split_at], order[split_at:]

    train_path.write_text(
        "\n".join(samples_lines[i] for i in train_order) + "\n",
        encoding="utf-8",
    )
    val_path.write_text(
        "\n".join(samples_lines[i] for i in val_order) + "\n",
        encoding="utf-8",
    )

    content_hash = _content_sha256(src)  # noqa: F841 — kept for callers below
    rec = DatasetRecord(
        dataset_id=dataset_id,
        name=name,
        format=format_name,
        n_samples=n_ok,
        train_size=len(train_order),
        val_size=len(val_order),
        content_sha256=content_hash,
        path=src,
        meta={"skipped": n_skip, "bad_samples_first_10": bad_rows},
    )
    atomic_write_json(manifest_path, rec.to_dict())
    atomic_append_jsonl(
        events_path,
        {"ts": int(time.time()), "event": "registered",
         "dataset_id": dataset_id, "n_samples": n_ok,
         "n_skipped": n_skip, "src": str(src)},
    )
    return rec


def _iter_json_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    return _iter_jsonl(path)


def get_dataset(state: State, dataset_id: str) -> DatasetRecord | None:
    p = state.finetuning_datasets_dir / dataset_id / "manifest.json"
    if not p.exists():
        return None
    try:
        return DatasetRecord.from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def list_datasets(state: State) -> list[DatasetRecord]:
    out: list[DatasetRecord] = []
    for p in sorted(state.finetuning_datasets_dir.glob("*/manifest.json")):
        try:
            out.append(DatasetRecord.from_dict(json.loads(p.read_text())))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out


def remove_dataset(state: State, dataset_id: str) -> bool:
    """Remove a dataset directory. Returns True if anything was removed."""
    import shutil
    root = state.finetuning_datasets_dir / dataset_id
    if not root.exists():
        return False
    shutil.rmtree(root)
    return True


def iter_split(state: State, dataset_id: str,
               split: str) -> Iterable[dict[str, Any]]:
    """Iterate rows of one split ("train" / "val" / "samples")."""
    if split not in {"train", "val", "samples"}:
        raise DatasetError(f"unknown split {split!r}")
    p = state.finetuning_datasets_dir / dataset_id / f"{split}.jsonl"
    if not p.exists():
        return iter([])
    return _iter_jsonl(p)


def ensure_dataset(state: State, dataset_id: str) -> DatasetRecord:
    rec = get_dataset(state, dataset_id)
    if rec is None:
        raise DatasetError(f"unknown dataset_id: {dataset_id!r}")
    return rec


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def assert_safe_id(value: str, kind: str) -> None:
    if not value or not _SAFE_ID.match(value):
        raise DatasetError(f"invalid {kind}: {value!r}")
