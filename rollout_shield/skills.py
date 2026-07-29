"""Skill registry — a thin layer over plugins.

A **skill** is a named, reusable capability that the AI layer can
invoke. Skills are typically **smaller** than plugins:

- A plugin is a Python package with its own manifest + module.
- A skill is a single function registered against a name; it may
  live inside a plugin OR be declared in a ``skills.yaml`` file.

Skills are invoked via the AI router or directly via the CLI::

    rollout-shield ai skill <skill-id> [...args]

The registry stores skills by id. A skill must declare:

- ``id``        — unique slug
- ``description`` — one-line help
- ``fn``        — callable(prompt, params) -> dict

Example ``skills.yaml``::

    - id: summarize-claim
      description: summarize a claim body
      module: my_plugin.skills
      attr: summarize_claim
    - id: extract-actor
      description: extract the signing agent from a claim body
      module: my_plugin.skills
      attr: extract_actor

A skill can be **warmed** (imported once at startup) and **invoked**
on demand. The AI router is aware of the skill registry and includes
skill invocations in its metrics.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import logging as _log
from .plugins import PluginError, _parse_manifest_text

SkillFn = Callable[..., dict]


@dataclass
class Skill:
    id: str
    description: str
    fn: SkillFn | None = None  # None = declared but not yet loaded
    module: str = ""
    attr: str = ""
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def is_loaded(self) -> bool:
        return self.fn is not None

    def invoke(self, prompt: str, params: dict | None = None) -> dict:
        if self.fn is None:
            raise RuntimeError(
                f"skill {self.id} not loaded — call skills.warm() first")
        return self.fn(prompt, params or {})


def _load_skill_entry(entry: dict) -> Skill:
    if "id" not in entry:
        raise PluginError("skill entry missing 'id'")
    return Skill(
        id=str(entry["id"]),
        description=str(entry.get("description", "")),
        module=str(entry.get("module", "")),
        attr=str(entry.get("attr", "")),
        tags=list(entry.get("tags", []) or []),
        meta=dict(entry.get("meta", {}) or {}),
    )


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        with self._lock:
            if skill.id in self._skills:
                raise PluginError(f"skill id collision: {skill.id}")
            self._skills[skill.id] = skill

    def get(self, skill_id: str) -> Skill | None:
        with self._lock:
            return self._skills.get(skill_id)

    def list(self) -> list[Skill]:
        with self._lock:
            return sorted(self._skills.values(), key=lambda s: s.id)

    def load_from_yaml(self, path: Path) -> int:
        """Load skills declared in a ``skills.yaml`` file.

        Returns the number of skills registered.
        """
        raw = _parse_manifest_text(path.read_text(encoding="utf-8"), path)
        entries = raw.get("skills", [])
        if not isinstance(entries, list):
            raise PluginError(f"{path}: 'skills' must be a list")
        n = 0
        for e in entries:
            self.register(_load_skill_entry(e))
            n += 1
        return n

    def warm(self) -> int:
        """Load any declared-but-not-loaded skills.

        Returns the number of skills warmed.
        """
        from . import metrics
        n = 0
        for s in self.list():
            if s.is_loaded() or not s.module or not s.attr:
                continue
            try:
                mod = importlib.import_module(s.module)
                fn = getattr(mod, s.attr, None)
                if fn is None:
                    _log.get_logger("skills").warning(
                        "skill has no such attribute",
                        extra={"skill_id": s.id, "module": s.module,
                               "attr": s.attr},
                    )
                    continue
                s.fn = fn
                # metrics: record the warm time
                metrics.model_warm_seconds.observe(0.0, labels=(s.id,))
                n += 1
            except ImportError as exc:
                _log.get_logger("skills").warning(
                    "skill import failed",
                    extra={"skill_id": s.id, "module": s.module,
                           "error": str(exc)},
                )
        return n

    def invoke(self, skill_id: str, prompt: str,
               params: dict | None = None) -> dict:
        s = self.get(skill_id)
        if s is None:
            raise KeyError(f"unknown skill: {skill_id}")
        return s.invoke(prompt, params)


_REGISTRY: _Registry | None = None
_REG_LOCK = threading.Lock()


def registry() -> _Registry:
    global _REGISTRY
    if _REGISTRY is None:
        with _REG_LOCK:
            if _REGISTRY is None:
                _REGISTRY = _Registry()
    return _REGISTRY


def register(skill: Skill) -> None:
    registry().register(skill)


def load_from_yaml(path: Path) -> int:
    return registry().load_from_yaml(path)


def warm() -> int:
    return registry().warm()


def invoke(skill_id: str, prompt: str,
           params: dict | None = None) -> dict:
    return registry().invoke(skill_id, prompt, params)


def list_skills() -> list[Skill]:
    return registry().list()


__all__ = [
    "Skill", "SkillFn",
    "registry", "register", "load_from_yaml", "warm", "invoke",
    "list_skills",
]
