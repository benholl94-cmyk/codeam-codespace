# Changelog

All notable changes to codeam-codespace / rollout-shield are documented
here. The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project adheres to [Semantic Versioning](https://semver.org/).

Versions are stamped by `scripts/install.sh` at the install prefix
(see `<prefix>/INSTALLED_AT`) and mirrored in `rollout_shield/__init__.py`
(`__version__`).

## [unreleased]

### Added — finetuning subsystem

- **`rollout-shield finetune ...`** CLI subcommand tree: `dataset add/list/show/remove`, `adapters list`, `adapter show/promote/unpromote`, `run`, `runs list/show/abort`, `stats`, `doctor`, `sign-test`.
- **`rollout_shield/finetuning/`** package — `models`, `datasets`, `adapters`, `recipes`, `training`, `evaluation`, `promote`, `doctor`, `lock`, `backends/{stdlib,peft}`.
- **Pluggable backends** — `stdlib` (deterministic pattern-capture, default, always available) and `peft` (real LoRA via `pip install rollout-shield[finetune]`).
- **Recipes** — `sft-mini`, `lora-tiny`, `dpo-mini` with overridable hyperparameters.
- **Eval harness** — `exact_match`, `bleu1_proxy`, `drift_from_baseline` with a threshold gate before promotion.
- **Adapter promotion** — registers `{base}-ft-{short8}` in `ai.models`; survives process restarts via `promoted.json` sidecar replayed at `ai.models` import time.
- **Dashboard tab** — `interface/finetuning.html` + `interface/finetuning.js`; auto-refresh every 15s; accessible from **Finetuning →** on the index.
- **HTTP API** — 11 endpoints under `/api/finetuning/{datasets,adapters,runs,stats,doctor}`.
- **8 metric families** — `rollout_shield_finetuning_{datasets_total, runs_total, run_steps_total, run_duration_seconds, eval_score, adapters_total, promoted_total, storage_bytes}`.
- **3 benchmarks** — `finetune_real`, `finetune_eval_passed`, `finetune_drift` in `ai.benchmarks.FINETUNE_BENCHMARKS`.
- **6 plugin events** — `finetuning.{dataset.created, dataset.removed, run.started, run.completed, adapter.promoted, adapter.unpromoted}`.
- **Tests** — `tests/test_unit_finetuning.py` (35+ cases) + `tests/test_integration_finetuning.py` (8 e2e).
- **Docs** — `docs/FINETUNING.md` operator reference.

### Added — production-grade pimp-up

- **Smart-routing binding** — government-version install stamps a
  SHA256-signed manifest at `<prefix>/etc/rollout-shield/smart-routing.json`.
  Inspect with `rollout-shield routing` or `rollout-shield ai routing`.
  Manifest records build_tier, controller_policy, default_strategy,
  bound_models, priority_order, and per-policy routing_profiles.
- **`rollout_shield/routing.py`** — typed loader (`manifest()`,
  `binding()`, `bound_models()`, `default_strategy()`, `active_profile()`,
  `is_government_build()`).
- **`rollout-shield routing`** CLI subcommand + top-level alias.
- **Sub-workspace hardening** — six new sub-workspaces under this repo:
  `docs/`, `examples/`, `benchmarks/`, `tests/`, plus top-level
  `WORKSPACES.md`, `Makefile`, `pyproject.toml`.
- **Pre-commit hooks** — `.pre-commit-config.yaml` (ruff, mypy,
  file hygiene, detect-private-key, shellcheck, no-tabs-in-YAML).
- **CI workflow** — `.github/workflows/tests.yml` runs ruff + mypy +
  pytest smoke + self-test on push / PR / dispatch, uploads artifacts.
- **Install hardening** — parallel cp via `find + xargs -P` skipping
  `__pycache__` / `.pyc`; pre-compile `.pyc` for warm cache; post-install
  verifies `rollout-shield routing` is callable.
- **Structured logging** — `rollout_shield/logging.py` (JSON logs,
  levels, rotation, request tracing).
- **Prometheus metrics** — `rollout_shield/metrics.py` (`/api/metrics`
  endpoint, AI cost tracking, cycle histograms).
- **Plugin / skill extension points** — `rollout_shield/plugins.py`
  (plugin manifest) and `rollout_shield/skills.py` (skill registry).
- **Dashboard** — extracted to a top-level sub-workspace with
  smart-routing view, real-time SSE updates, metrics panel.
- **Property-based tests** — `tests/test_property_based.py` using
  hypothesis for state machine invariants.
- **Webhook delivery subsystem** — production-grade outbox-based
  outbound webhooks. New package `rollout_shield/webhook_delivery/`
  with models, outbox, signer (HMAC-SHA256 + Ed25519), dispatcher,
  dedupe (per-target idempotency window), targets (with circuit
  breaker), replay (manual + replay-all). New CLI
  `rollout-shield webhooks ...` subcommand tree (target add/list/
  remove/show, deliver, deliveries list/show, replay, replay-all,
  drain, stats, sign-test, daemon). Persistent state under
  `<state_root>/webhooks/` (targets, deliveries, dlq, outbox,
  stats.json, advisory `.lock`). New HTTP API
  `/api/webhooks/{targets,deliveries,stats,health,sign-test}`.
  New dashboard `Webhooks` tab polling every 15s. Five new
  Prometheus metric families. New plugin event
  `webhook.delivered`. 44 tests pass (35 unit + 9 integration
  covering happy path, HMAC-verification-by-receiver, retry-then-
  success, DLQ-after-max-attempts, dedupe, replay, circuit breaker,
  concurrent dispatch, metrics emission). Documentation in
  `docs/WEBHOOKS.md`.

### Fixed

- CLI shim sys.path ordering — install prefix now wins over repo source
  so the government-version binding is honored when both are present.
- Interface dest-prefix — strip `rollout_shield/` prefix so the
  dashboard assets land at `<prefix>/share/rollout-shield/interface/`,
  not `<prefix>/share/rollout-shield/rollout_shield/interface/`.
- pytest `requires_cryptography` marker registered (was previously
  causing collection errors with `--strict-markers`).

### Added

- **Responsive dashboard** — `rollout_shield/interface/style.css` now
  carries three `@media` breakpoints (≤1024 / ≤768 / ≤480 px). Tables
  in both `index.html` and `ai-assistance.html` are wrapped in
  `<div class="table-scroll">` so they scroll horizontally on narrow
  viewports. Nav tabs wrap to two rows on tablet, single-line flex on
  phone. The AI prompt row stacks vertically below 768 px.

## [0.1.0] — 2026-07-29

Initial public release of the rollout-shield runtime.

### Added

- CLI (`rollout-shield`) + monitor daemon (`rollout-shield-monitor`)
  hard-built into `~/usr/` via `scripts/install.sh`.
- State at `~/.rollout-shield/` with atomic JSON writes and JSONL
  append-only claim log.
- Ed25519 key generation, hardware-anchored key support, claim
  create / list / show / verify.
- Monitor with 3-layer health checks (state + host + repo),
  self-heal cycle (idempotent + safe), self-test smoke.
- Parallel-lateral AI router with N models, strategies
  (`best`, `concat`, `consensus`, `first`, `median`).
- 5 own models (rollout, verifier, contradictor, repo-aware,
  spec-citation) + 4 mock models.
- Controller policy (`shared` / `device-only` / `human-only`).
- Dashboard on http://127.0.0.1:8765 with AI tab.
- `bd` issue tracking + Dolt DB integration.

[unreleased]: #unreleased
[0.1.0]: #010---2026-07-29