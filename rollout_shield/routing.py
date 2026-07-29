"""Smart-routing manifest loader.

The government-version build of rollout-shield is bound with a smart
routing layer. The manifest records which AI models are bound at
install time, the default lateral-combination strategy, and the
routing profile (controller policy, family priority).

The manifest lives at:

    <prefix>/etc/rollout-shield/smart-routing.json
    = ~/usr/etc/rollout-shield/smart-routing.json  (default install)

A missing manifest means the install is **not** the government-version
build — fall back to the inline defaults below.

Three roles:

- inspector — read the manifest, expose ``manifest()`` and
  ``bound_models()`` to the rest of the runtime
- CLI       — ``rollout-shield routing`` shows the binding
- AI layer  — ``router.py`` consults the manifest's default strategy
  and model set before applying its own logic

The manifest is **stamped by install.sh at build time**, not edited
by hand. Re-running install regenerates it from the current AI layer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Default location: the install prefix. The CLI lives in
# $PREFIX/bin/rollout-shield and the manifest sits at $PREFIX/etc/.
def _default_manifest_path() -> Path:
    here = Path(__file__).resolve()
    # Path: .../usr/lib/python/rollout_shield/routing.py
    #    -> .../usr/etc/rollout-shield/smart-routing.json
    # 4 levels up: routing.py -> rollout_shield/ -> python/ -> lib/ -> usr/
    usr = here.parent.parent.parent.parent
    candidate = usr / "etc" / "rollout-shield" / "smart-routing.json"
    if candidate.exists():
        return candidate
    # dev fallback: repo-local manifest when running from a checkout
    # Path: .../rollout_shield/routing.py  ->  .../etc/...
    dev = here.parent.parent / "etc" / "smart-routing.json"
    if dev.exists():
        return dev
    return candidate  # caller will detect non-existence


MANIFEST_PATH = Path(
    os.environ.get("ROLLOUT_SHIELD_ROUTING_MANIFEST")
    or _default_manifest_path()
)


# Fallback when no manifest is present (developer checkout, test env).
# Mirrors the same shape the install script writes — keeping the
# router contract consistent across both runtimes.
DEFAULT_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "build_tier": "developer",
    "controller_policy": "shared",
    "default_strategy": "best",
    "bound_families": ["mock", "own"],
    "bound_models": [
        "mock:echo", "mock:expand", "mock:bullet", "mock:code",
        "own:rollout-model", "own:verifier-model", "own:contradictor-model",
        "own:repo-aware-model", "own:spec-citation-model",
    ],
    "priority_order": ["own", "mock"],
    "routing_profiles": {
        "shared":      {"strategy": "best",     "families": ["own", "mock"]},
        "device-only": {"strategy": "consensus", "families": ["own"]},
        "human-only":  {"strategy": "first",     "families": ["mock", "own"]},
    },
    "installed_at": None,
    "repo_source": None,
    "manifest_signature": None,  # filled by install.sh
}


@dataclass
class RoutingBinding:
    """Strongly-typed view of the manifest for the runtime."""
    build_tier: str = "developer"
    controller_policy: str = "shared"
    default_strategy: str = "best"
    bound_families: list[str] = field(default_factory=list)
    bound_models: list[str] = field(default_factory=list)
    priority_order: list[str] = field(default_factory=list)
    routing_profiles: dict[str, dict] = field(default_factory=dict)

    def profile_for(self, controller_policy: str) -> dict:
        """Return the routing profile matching the active policy."""
        return self.routing_profiles.get(
            controller_policy,
            {"strategy": self.default_strategy,
             "families": self.bound_families or ["mock"]},
        )


def manifest() -> dict:
    """Return the manifest as a dict, or DEFAULT_MANIFEST if missing."""
    if not MANIFEST_PATH.exists():
        return dict(DEFAULT_MANIFEST)
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        # Manifest is corrupted; fall back to defaults. Do not crash
        # the runtime — the manifest is advisory, not authoritative.
        return dict(DEFAULT_MANIFEST)
    # Merge with defaults so any new field appears with a sane value.
    merged = dict(DEFAULT_MANIFEST)
    merged.update(data)
    return merged


def binding() -> RoutingBinding:
    """Strongly-typed view of the manifest."""
    m = manifest()
    return RoutingBinding(
        build_tier=m.get("build_tier", "developer"),
        controller_policy=m.get("controller_policy", "shared"),
        default_strategy=m.get("default_strategy", "best"),
        bound_families=list(m.get("bound_families", [])),
        bound_models=list(m.get("bound_models", [])),
        priority_order=list(m.get("priority_order", [])),
        routing_profiles=dict(m.get("routing_profiles", {})),
    )


def bound_models() -> list[str]:
    """Return the list of model IDs the router is bound to use."""
    return binding().bound_models


def default_strategy() -> str:
    """Return the default lateral-combination strategy."""
    return binding().default_strategy


def active_profile(controller_policy: str | None = None) -> dict:
    """Return the routing profile for the given (or current) policy.

    If controller_policy is None, reads ``controller_policy`` from
    the active state config. Falls back to the manifest's policy
    when no state is loaded.
    """
    if controller_policy is None:
        try:
            from .state import State  # local import to avoid cycles
            controller_policy = State().load_config().get(
                "controller_policy", "shared"
            )
        except Exception:
            controller_policy = binding().controller_policy
    return binding().profile_for(controller_policy)


def is_government_build() -> bool:
    """True when the manifest is present and marked build_tier=government."""
    m = manifest()
    return m.get("build_tier") == "government"


def to_dict() -> dict:
    """Return the manifest as a plain dict (for ``routing show``)."""
    return manifest()
