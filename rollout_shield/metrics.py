"""Prometheus-style metrics for rollout-shield.

A small, stdlib-only metrics implementation that exposes a
``/api/metrics`` endpoint on the dashboard HTTP server and a
``rollout-shield metrics`` CLI command.

Three metric kinds:

- **Counter** — monotonically increasing value (e.g. ``claims_total``)
- **Gauge**   — point-in-time value (e.g. ``state_size_bytes``)
- **Histogram** — distribution of values with bucket counts
  (e.g. ``router_latency_seconds``)

Labels are supported (string-keyed). Histograms use the standard
Prometheus default bucket boundaries.

The metrics are stored in-process (per daemon). For a Prometheus
deployment, scrape ``/api/metrics`` with a 15s interval.

This module is **safe to import from anywhere** — it has no
side effects and uses no globals besides the registry.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from collections.abc import Iterable

# --- Prometheus default buckets ---
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
    2.5, 5.0, 10.0, float("inf"),
)


class Counter:
    """Monotonic counter, optionally labelled.

    ``labelnames`` are positional; ``labels=()`` is required when
    labelnames is set; the same label cardinality MUST always be
    passed to ``inc``.
    """

    def __init__(self, name: str, help: str,
                 labelnames: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help
        self.labelnames = labelnames
        self._lock = threading.Lock()
        self._values: dict[tuple[str, ...], float] = defaultdict(float)

    def inc(self, amount: float = 1.0, labels: tuple[str, ...] = ()) -> None:
        if len(labels) != len(self.labelnames):
            raise ValueError(
                f"{self.name} expects {len(self.labelnames)} labels, "
                f"got {len(labels)}"
            )
        with self._lock:
            self._values[labels] += amount

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        with self._lock:
            items = sorted(self._values.items())
        for labels, value in items:
            if self.labelnames:
                lstr = ",".join(
                    f'{n}="{_escape(v)}"'
                    for n, v in zip(self.labelnames, labels, strict=False)
                )
                yield f"{self.name}{{{lstr}}} {_fmt_float(value)}"
            else:
                yield f"{self.name} {_fmt_float(value)}"


class Gauge:
    """Point-in-time gauge, optionally labelled."""

    def __init__(self, name: str, help: str,
                 labelnames: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help
        self.labelnames = labelnames
        self._lock = threading.Lock()
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, labels: tuple[str, ...] = ()) -> None:
        if len(labels) != len(self.labelnames):
            raise ValueError(
                f"{self.name} expects {len(self.labelnames)} labels, "
                f"got {len(labels)}"
            )
        with self._lock:
            self._values[labels] = value

    def inc(self, amount: float = 1.0, labels: tuple[str, ...] = ()) -> None:
        with self._lock:
            self._values[labels] = self._values.get(labels, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: tuple[str, ...] = ()) -> None:
        with self._lock:
            self._values[labels] = self._values.get(labels, 0.0) - amount

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} gauge"
        with self._lock:
            items = sorted(self._values.items())
        for labels, value in items:
            if self.labelnames:
                lstr = ",".join(
                    f'{n}="{_escape(v)}"'
                    for n, v in zip(self.labelnames, labels, strict=False)
                )
                yield f"{self.name}{{{lstr}}} {_fmt_float(value)}"
            else:
                yield f"{self.name} {_fmt_float(value)}"


class Histogram:
    """Histogram with cumulative bucket counts and a sum/count.

    Observes individual values; ``render`` emits the standard
    Prometheus histogram format with ``_bucket{le=...}``.
    """

    def __init__(self, name: str, help: str,
                 buckets: tuple[float, ...] = DEFAULT_BUCKETS,
                 labelnames: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help
        self.buckets = buckets
        self.labelnames = labelnames
        self._lock = threading.Lock()
        # per-label-set: list of bucket counts + sum + count
        self._values: dict[tuple[str, ...], _HistogramChild] = {}

    def observe(self, value: float, labels: tuple[str, ...] = ()) -> None:
        if len(labels) != len(self.labelnames):
            raise ValueError(
                f"{self.name} expects {len(self.labelnames)} labels, "
                f"got {len(labels)}"
            )
        with self._lock:
            child = self._values.get(labels)
            if child is None:
                child = _HistogramChild(len(self.buckets))
                self._values[labels] = child
            child.observe(value)

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        with self._lock:
            items = sorted(self._values.items())
        for labels, child in items:
            base = self.name
            if self.labelnames:
                base_lstr = ",".join(
                    f'{n}="{_escape(v)}"'
                    for n, v in zip(self.labelnames, labels, strict=False)
                )
            else:
                base_lstr = ""
            cumulative = 0
            for i, upper in enumerate(self.buckets):
                cumulative = child.buckets[i]
                le = "+Inf" if math.isinf(upper) else _fmt_float(upper)
                if base_lstr:
                    lstr = f'{base_lstr},le="{le}"'
                else:
                    lstr = f'le="{le}"'
                yield f"{base}_bucket{{{lstr}}} {cumulative}"
            if base_lstr:
                yield (f"{base}_count{{{base_lstr}}} "
                       f"{child.count}")
                yield (f"{base}_sum{{{base_lstr}}} "
                       f"{_fmt_float(child.sum)}")
            else:
                yield f"{base}_count {child.count}"
                yield f"{base}_sum {_fmt_float(child.sum)}"


class _HistogramChild:
    __slots__ = ("buckets", "sum", "count")

    def __init__(self, n_buckets: int) -> None:
        self.buckets = [0] * n_buckets
        self.sum = 0.0
        self.count = 0

    def observe(self, value: float) -> None:
        # cumulative buckets: each bucket counts all observations
        # ≤ its upper bound
        for i, upper in enumerate(DEFAULT_BUCKETS):
            if value <= upper:
                self.buckets[i] += 1
        self.sum += value
        self.count += 1


class _Registry:
    """In-process metric registry."""

    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}
        self._lock = threading.Lock()

    def register(self, metric: Counter | Gauge | Histogram) -> None:
        with self._lock:
            if metric.name in self._metrics:
                # already registered — return the existing one so
                # duplicate-registration in import paths doesn't
                # double-count.
                existing = self._metrics[metric.name]
                if type(existing) is not type(metric):
                    raise ValueError(
                        f"metric {metric.name} already registered "
                        f"as a different type"
                    )
                return
            self._metrics[metric.name] = metric

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics.values())
        lines: list[str] = []
        for m in metrics:
            lines.extend(m.render())
        return "\n".join(lines) + "\n"

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metrics.keys())


# Global registry — lazily constructed so import has no side effect.
_REGISTRY: _Registry | None = None
_REG_LOCK = threading.Lock()


def registry() -> _Registry:
    """Return the global registry, constructing on first call."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REG_LOCK:
            if _REGISTRY is None:
                _REGISTRY = _Registry()
                # register the well-known metrics so they always show
                # up at /api/metrics even before anything observes.
                _REGISTRY.register(claims_total)
                _REGISTRY.register(claim_verifications_total)
                _REGISTRY.register(claim_verification_failures_total)
                _REGISTRY.register(router_latency_seconds)
                _REGISTRY.register(router_calls_total)
                _REGISTRY.register(router_cost_usd_total)
                _REGISTRY.register(state_size_bytes)
                _REGISTRY.register(monitor_cycles_total)
                _REGISTRY.register(monitor_cycle_duration_seconds)
                _REGISTRY.register(self_heal_repairs_total)
                _REGISTRY.register(http_requests_total)
                _REGISTRY.register(http_request_duration_seconds)
                _REGISTRY.register(model_warm_seconds)
                # webhook delivery metrics
                _REGISTRY.register(webhook_deliveries_total)
                _REGISTRY.register(webhook_delivery_attempts_total)
                _REGISTRY.register(webhook_delivery_duration_seconds)
                _REGISTRY.register(webhook_outbox_depth)
                _REGISTRY.register(webhook_dlq_depth)
                _REGISTRY.register(webhook_targets_count)
                # finetuning metrics
                _REGISTRY.register(finetuning_datasets_total)
                _REGISTRY.register(finetuning_runs_total)
                _REGISTRY.register(finetuning_run_steps_total)
                _REGISTRY.register(finetuning_run_duration_seconds)
                _REGISTRY.register(finetuning_eval_score)
                _REGISTRY.register(finetuning_adapters_total)
                _REGISTRY.register(finetuning_promoted_total)
                _REGISTRY.register(finetuning_storage_bytes)
    return _REGISTRY


def render() -> str:
    """Render the full metrics registry as Prometheus text format."""
    return registry().render()


# --- the well-known metrics ---

claims_total = Counter(
    "rollout_shield_claims_total",
    "Total number of claims emitted.",
    labelnames=("type", "agent"),
)
claim_verifications_total = Counter(
    "rollout_shield_claim_verifications_total",
    "Total number of claim verifications.",
    labelnames=("result",),
)
claim_verification_failures_total = Counter(
    "rollout_shield_claim_verification_failures_total",
    "Total number of claim verifications that failed signature check.",
    labelnames=("reason",),
)

router_latency_seconds = Histogram(
    "rollout_shield_router_latency_seconds",
    "End-to-end latency of the parallel-lateral AI router, in seconds.",
    labelnames=("strategy",),
)
router_calls_total = Counter(
    "rollout_shield_router_calls_total",
    "Total number of router invocations.",
    labelnames=("strategy", "n_models"),
)
router_cost_usd_total = Counter(
    "rollout_shield_router_cost_usd_total",
    "Total estimated USD cost of router invocations.",
    labelnames=("model",),
)

state_size_bytes = Gauge(
    "rollout_shield_state_size_bytes",
    "Size of the state directory in bytes.",
)
monitor_cycles_total = Counter(
    "rollout_shield_monitor_cycles_total",
    "Total number of monitor cycles executed.",
    labelnames=("result",),
)
monitor_cycle_duration_seconds = Histogram(
    "rollout_shield_monitor_cycle_duration_seconds",
    "Wall-clock duration of a monitor cycle.",
)
self_heal_repairs_total = Counter(
    "rollout_shield_self_heal_repairs_total",
    "Total number of self-heal repairs attempted, by outcome.",
    labelnames=("check", "result"),
)

http_requests_total = Counter(
    "rollout_shield_http_requests_total",
    "Total number of HTTP requests served.",
    labelnames=("method", "path", "status"),
)
http_request_duration_seconds = Histogram(
    "rollout_shield_http_request_duration_seconds",
    "HTTP request latency.",
    labelnames=("method", "path"),
)

model_warm_seconds = Histogram(
    "rollout_shield_model_warm_seconds",
    "Time to warm a model (import + first call).",
    labelnames=("model",),
)

# --- webhook delivery metrics (rollout_shield.webhook_delivery) -----------

webhook_deliveries_total = Counter(
    "rollout_shield_webhook_deliveries_total",
    "Total number of webhook deliveries, terminal status only.",
    labelnames=("target", "status"),
)
webhook_delivery_attempts_total = Counter(
    "rollout_shield_webhook_delivery_attempts_total",
    "Total number of webhook delivery attempts (including retries).",
    labelnames=("target", "result"),
)
webhook_delivery_duration_seconds = Histogram(
    "rollout_shield_webhook_delivery_duration_seconds",
    "HTTP latency of a single webhook delivery attempt, in seconds.",
    labelnames=("target",),
)
webhook_outbox_depth = Gauge(
    "rollout_shield_webhook_outbox_depth",
    "Number of webhook deliveries currently in the outbox (pending).",
)
webhook_dlq_depth = Gauge(
    "rollout_shield_webhook_dlq_depth",
    "Number of webhook deliveries currently in the dead-letter queue.",
)
webhook_targets_count = Gauge(
    "rollout_shield_webhook_targets_count",
    "Number of webhook targets configured.",
)


# --- finetuning metrics (rollout_shield.finetuning) ----------------------

finetuning_datasets_total = Gauge(
    "rollout_shield_finetuning_datasets_total",
    "Number of registered finetuning datasets.",
)
finetuning_runs_total = Counter(
    "rollout_shield_finetuning_runs_total",
    "Total number of finetuning runs started, by terminal status.",
    labelnames=("backend", "recipe", "status"),
)
finetuning_run_steps_total = Counter(
    "rollout_shield_finetuning_run_steps_total",
    "Total number of training steps executed across all finetuning runs.",
    labelnames=("backend", "recipe"),
)
finetuning_run_duration_seconds = Histogram(
    "rollout_shield_finetuning_run_duration_seconds",
    "End-to-end duration of a finetuning run, in seconds.",
    labelnames=("backend", "status"),
)
finetuning_eval_score = Histogram(
    "rollout_shield_finetuning_eval_score",
    "Evaluation metric scores produced by the finetuning eval harness.",
    labelnames=("recipe", "metric"),
)
finetuning_adapters_total = Gauge(
    "rollout_shield_finetuning_adapters_total",
    "Number of finetuning adapters persisted to state.",
    labelnames=("backend", "status"),
)
finetuning_promoted_total = Gauge(
    "rollout_shield_finetuning_promoted_total",
    "Number of finetuning adapters currently promoted as routable models.",
)
finetuning_storage_bytes = Gauge(
    "rollout_shield_finetuning_storage_bytes",
    "Approximate disk usage of the finetuning state directory, in bytes.",
)


# --- helpers ---

def _escape(value: str) -> str:
    """Escape a label value per the Prometheus text format spec."""
    return (value.replace("\\", "\\\\")
                 .replace("\n", "\\n")
                 .replace('"', '\\"'))


def _fmt_float(value: float) -> str:
    """Format a float Prometheus-style."""
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


# --- AI cost tracking ---

def record_router_call(
    *,
    strategy: str,
    n_models: int,
    elapsed_s: float,
    per_model_cost: dict[str, float],
) -> None:
    """Convenience: emit all router-call metrics in one shot."""
    router_calls_total.inc(labels=(strategy, str(n_models)))
    router_latency_seconds.observe(elapsed_s, labels=(strategy,))
    for model_id, cost in per_model_cost.items():
        router_cost_usd_total.inc(amount=cost, labels=(model_id,))


__all__ = [
    "Counter", "Gauge", "Histogram",
    "registry", "render", "record_router_call",
    # well-known metrics
    "claims_total", "claim_verifications_total", "claim_verification_failures_total",
    "router_latency_seconds", "router_calls_total", "router_cost_usd_total",
    "state_size_bytes",
    "monitor_cycles_total", "monitor_cycle_duration_seconds",
    "self_heal_repairs_total",
    "http_requests_total", "http_request_duration_seconds",
    "model_warm_seconds",
    "webhook_deliveries_total", "webhook_delivery_attempts_total",
    "webhook_delivery_duration_seconds",
    "webhook_outbox_depth", "webhook_dlq_depth", "webhook_targets_count",
]
