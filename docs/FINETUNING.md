# Finetuning subsystem — operator reference

> Status: **0.1.0** — stdlib backend fully shipped; peft backend ships as
> an optional extra (`pip install rollout-shield[finetune]`) with
> `sft-mini` implemented; `lora-tiny` and `dpo-mini` are stubs.

The `rollout-shield finetune ...` subsystem is a lifecycle manager for
adapter finetuning over the existing AI layer. It registers datasets,
runs training, evaluates on a held-out split, and promotes adapters as
routable models in `ai.models`.

The architecture is intentionally **stdlib-first**: the default backend
is a deterministic pattern-capture adapter that runs in any Python
environment, no GPU, no ML deps. Operators who want real LoRA training
opt in to `[finetune]` and use the `peft` backend.

---

## Quick start

```bash
# 0. (Optional) install real ML deps — only needed for the `peft` backend
pip install rollout-shield[finetune]

# 1. Self-check
rollout-shield finetune doctor

# 2. Register a JSONL dataset (one JSON object per line)
rollout-shield finetune dataset add ./data.jsonl \
    --name demo --split 0.9 --format prompt-target

# 3. Start a training run (stdlib backend is the default)
rollout-shield finetune run \
    --dataset ds_xxxxxxxxxxxxxxxx \
    --base-model mock-deterministic \
    --recipe sft-mini --backend stdlib \
    --epochs 1 --max-steps 50

# 4. List runs + promote a passing one
rollout-shield finetune runs list
rollout-shield finetune adapters list
rollout-shield finetune adapter promote ft_yyyyyyyyyyyyyyyy

# 5. Route through the promoted adapter
rollout-shield ai route "hello" --models mock-deterministic-ft-zzzzzzzz
```

The promoted adapter's model id is `{base_model_id}-ft-{short8}` —
`mock-deterministic-ft-12345678` in this example. The finetuning
subsystem re-registers all promoted adapters at `ai.models` import time,
so they survive a process restart.

---

## Recipes

| Recipe       | Purpose                                              | Dataset format            | Backends    |
|--------------|------------------------------------------------------|---------------------------|-------------|
| `sft-mini`   | Supervised fine-tune. Default choice for adaptation. | `prompt-target`           | stdlib, peft |
| `lora-tiny`  | LoRA-only stub (requires peft backend)               | `prompt-target`           | peft (stub) |
| `dpo-mini`   | Direct preference optimization                       | `prompt-target-score`     | peft (stub) |

Every recipe records its hyperparameters in the `Recipe` object and
overrides only what the operator passes. Recipes are intentionally
small and boring — the deliverable is the lifecycle, not the weights.

---

## State layout

```
~/.rollout-shield/finetuning/
    .lock                                       # advisory cross-process lock
    datasets/<dataset-id>/
        manifest.json                           # DatasetRecord
        samples.jsonl                           # the actual rows
        events.jsonl                            # audit log
    adapters/<adapter-id>.json                  # AdapterRecord
    adapters/<adapter-id>.artifacts/            # backend-specific artifacts
        pattern_capture.json                    # stdlib backend
        BACKEND.txt                             # backend marker
        adapter_model.safetensors               # peft backend (if installed)
    runs/<run-id>/
        run.json                                # RunRecord
        events.jsonl                            # audit log
        eval.json                               # eval result on val split
    promoted.json                                # list of promoted adapter ids
    stats.json                                   # rolled counters
```

Every state mutation is atomic-write; a crash mid-run leaves a
recoverable record. Concurrent run attempts serialize on `.lock`.

---

## Backend plumbing

The `backends/` subpackage defines a `Backend` Protocol. The default
implementation is always available:

- `stdlib` — deterministic pattern-capture. Captures bigram frequency
  biases from the training split and applies them at inference. NOT
  a learned model. Useful for testing the subsystem end-to-end.

The optional implementation ships behind the `[finetune]` extra:

- `peft` — real LoRA training via `peft` + `trl` + `transformers` +
  `torch` + `datasets`. `sft-mini` is implemented on top of
  `sshleifer/tiny-gpt2` so the smoke test stays sub-second.

Both backends expose the same `train(...)` and `build_generator(...)`
shape, so the lifecycle code is backend-agnostic.

Install:

```bash
pip install rollout-shield[finetune]
rollout-shield finetune doctor   # confirms backend_peft=True
```

---

## Reproducibility

Every adapter manifest records:

- base model id
- dataset id (which is itself content-hashed from the JSONL)
- recipe name
- backend name
- seed
- hyperparameters

Same inputs → same adapter id → deterministic re-run. The CLI flag
`--seed` overrides only when the operator asks; the default is the
recipe's seed.

---

## Eval harness

Three deterministic metrics on the val split:

- `exact_match`         — fraction of val samples where the adapter
                          returns text equal to the row's target
                          (whitespace-normalized).
- `bleu1_proxy`         — character-overlap F1 between adapter output
                          and target. Cheap, deterministic proxy for
                          character-level BLEU.
- `drift_from_baseline` — average of (adapter-overlap − base-overlap)
                          on exact-match and bleu1, clipped to [0, 1].

The eval gate is `drift_from_baseline >= eval_threshold`. Adapters
below the threshold are marked `eval_failed` and cannot be promoted
without explicit intervention. Override per-run with
`--eval-threshold` or set the threshold in the recipe.

---

## Lifecycle

```
register dataset  →  start run  →  trained  →  evaluated  →  eval_passed?
                                                              ↓ yes
                                                          promote
                                                              ↓
                                                   registered in ai.models
                                                              ↓
                                                   routable via ai route
```

After promotion, the adapter's `promoted_as_model_id`
(`{base}-ft-{short8}`) shows up in:

- `rollout-shield ai models`
- `ai route --models ...`
- the dashboard's **Finetuning →** tab

Use `rollout-shield finetune adapter unpromote <adapter-id>` to remove
from the live registry. The adapter record persists; only the
`promoted.json` sidecar is rewritten.

---

## Plugin events

Plugins subscribe to these events via `plugins.register(name, fn)`:

- `finetuning.dataset.created`     `{dataset_id, name, n_samples, content_sha256}`
- `finetuning.dataset.removed`     `{dataset_id}`
- `finetuning.run.started`         `{run_id, dataset_id, base_model_id, recipe_name, backend}`
- `finetuning.run.completed`       `{run_id, status, adapter_id, eval_metrics}`
- `finetuning.adapter.promoted`    `{adapter_id, promoted_as_model_id, base_model_id}`
- `finetuning.adapter.unpromoted`  `{adapter_id}`

The webhook-reporter plugin (in `examples/plugins/`) can forward
`finetuning.adapter.promoted` to a Slack channel without code changes.

---

## Metrics

| Family                                          | Type      | Labels                |
|-------------------------------------------------|-----------|-----------------------|
| `rollout_shield_finetuning_datasets_total`      | Gauge     | —                     |
| `rollout_shield_finetuning_runs_total`          | Counter   | backend, recipe, status |
| `rollout_shield_finetuning_run_steps_total`     | Counter   | backend, recipe       |
| `rollout_shield_finetuning_run_duration_seconds`| Histogram | backend, status       |
| `rollout_shield_finetuning_eval_score`          | Histogram | recipe, metric        |
| `rollout_shield_finetuning_adapters_total`      | Gauge     | backend, status       |
| `rollout_shield_finetuning_promoted_total`      | Gauge     | —                     |
| `rollout_shield_finetuning_storage_bytes`       | Gauge     | —                     |

`rollout-shield metrics | grep finetune_` shows all eight families.

---

## CLI reference

```
rollout-shield finetune dataset add <path> --name N [--split 0.9] [--format FMT]
rollout-shield finetune dataset list [--json]
rollout-shield finetune dataset show <id> [--stats] [--json]
rollout-shield finetune dataset remove <id>

rollout-shield finetune adapters list [--json]
rollout-shield finetune adapter show <id> [--json]
rollout-shield finetune adapter promote <id>
rollout-shield finetune adapter unpromote <id>

rollout-shield finetune run --dataset D --base-model M [--recipe R] [--backend B]
                          [--epochs N] [--batch-size N] [--lr FLOAT] [--seed N]
                          [--max-steps N] [--eval-threshold FLOAT]
                          [--register|--no-register] [--json]
rollout-shield finetune runs list [--status S] [--json]
rollout-shield finetune runs show <id> [--events] [--json]
rollout-shield finetune runs abort <id>

rollout-shield finetune stats [--json]
rollout-shield finetune doctor
rollout-shield finetune sign-test --adapter A --prompt "..."
```

---

## HTTP API

```
GET    /api/finetuning/datasets
POST   /api/finetuning/datasets
GET    /api/finetuning/datasets/<id>
DELETE /api/finetuning/datasets/<id>          (via /api/finetuning/datasets/<id>/remove)
GET    /api/finetuning/adapters
GET    /api/finetuning/adapters/<id>
POST   /api/finetuning/adapters/<id>/promote
POST   /api/finetuning/adapters/<id>/unpromote
GET    /api/finetuning/runs
POST   /api/finetuning/runs
GET    /api/finetuning/runs/<id>
POST   /api/finetuning/runs/<id>/abort
GET    /api/finetuning/stats
GET    /api/finetuning/doctor
```

---

## Dashboard

`rollout-shield dashboard --port 8765` → click **Finetuning →** for the
live view. Cards show datasets / adapters / promoted / runs counts.
Tables are auto-refreshed every 15s. Forms:

- **Register dataset** — pick a path, name, format; POSTs to the API.
- **Start run** — pick a dataset id + base model; the run form picks
  recipe + backend + hyperparams.

---

## Troubleshooting

| Symptom                                   | Likely cause                          | Fix                                   |
|-------------------------------------------|---------------------------------------|---------------------------------------|
| `dataset_id collision` (re-using name)   | dataset re-register with same content | expected; content hash wins           |
| `eval_failed` on every run               | `eval_threshold` too high             | lower the threshold or use stdlib     |
| `lock acquisition failed`                | another run in flight                 | wait, or kill the other run via abort |
| `peft backend not available`             | `[finetune]` extra not installed      | `pip install rollout-shield[finetune]`|
| `unknown backend: not-a-backend`         | typo                                  | use `auto`, `stdlib`, or `peft`       |
| adapter manifest missing                 | aborted run                           | inspect `<state>/finetuning/runs/<id>` |

---

## When you want a real LoRA training

The CLI ships a lifecycle manager, not a trainer. For production-scale
LoRA training (7 B+ base models, distributed, DeepSpeed, etc.) use
`trl` / `peft` directly:

```python
from peft import LoraConfig, get_peft_model
from trl import SFTTrainer, SFTConfig
# ...your dataset, your base, your compute budget
```

Then use the `finetune` subsystem to **promote** the result:

```bash
# 1. drop your adapter artifacts into:
#    ~/.rollout-shield/finetuning/adapters/<ft_id>.artifacts/
# 2. write a manifest.json conforming to AdapterRecord schema
# 3. register it as promoted:
rollout-shield finetune adapter promote <ft_id>
```

The `ai.models` registry will pick it up at next import.