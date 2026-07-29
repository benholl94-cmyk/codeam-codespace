"""Plugin extension points for rollout-shield.

A plugin is a Python package that lives anywhere on the import path
and exposes a top-level ``plugin.yaml`` (or ``plugin.json``) manifest.

Plugin lifecycle:

1. **Discovery**   — `discover()` walks each registered search path and
                     picks up directories containing ``plugin.yaml``.
2. **Registration** — each manifest is loaded, validated, and turned
                     into a `Plugin` instance on the global registry.
3. **Activation** — `activate(plugin_id)` imports the plugin's
                     Python module, registers its commands + hooks.
4. **Use**         — the CLI exposes `rollout-shield plugin list/show/run`
                     and the runtime dispatches events to activated
                     plugins via `dispatch(event, **kwargs)`.

The runtime ships with **zero built-in plugins**; activation is
explicit. A plugin cannot mutate state — it can only observe events
and emit derived signals (e.g. dashboards, external webhooks).

Manifest schema (``plugin.yaml``)::

    id: my-plugin
    name: My Plugin
    version: 1.0.0
    description: short one-liner
    module: my_plugin  # importable Python module
    hooks:
      - on_claim_create
      - on_monitor_cycle
    commands:
      - name: my-command
        help: do something useful
    permissions:
      - read:claims
      - read:state

Discovery paths (set via env or config):

- ``~/.rollout-shield/plugins/``  (default user-level)
- ``<prefix>/share/rollout-shield/plugins/`` (system, installed)
- ``$ROLLOUT_SHIELD_PLUGIN_PATHS``  (extra; ``:``-separated)
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


# --- manifest validation ---

_REQUIRED_FIELDS = ("id", "name", "version", "module")


class PluginError(Exception):
    """Raised on any plugin-related failure (load, validation, activate)."""


@dataclass
class PluginManifest:
    """Validated view of a ``plugin.yaml`` / ``plugin.json`` file."""
    id: str
    name: str
    version: str
    description: str = ""
    module: str = ""
    hooks: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    path: Path | None = None  # directory the manifest was loaded from

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description, "module": self.module,
            "hooks": list(self.hooks), "commands": list(self.commands),
            "permissions": list(self.permissions), "meta": dict(self.meta),
        }
        if self.path is not None:
            d["path"] = str(self.path)
        return d


def _parse_manifest_text(text: str, source_path: Path) -> dict:
    """Parse a YAML-ish manifest. We avoid PyYAML to keep stdlib-only.

    Supports the subset of YAML used by plugin manifests:
    ``key: value`` pairs, lists with ``- item`` syntax, and inline
    lists ``key: [a, b]``. Comments start with ``#``.
    """
    if not text.strip():
        raise PluginError(f"empty manifest: {source_path}")
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    current_list: list | None = None
    current_dict: dict | None = None
    last_key_in_dict: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        if stripped.startswith("- "):
            # list item
            if current_list is None:
                raise PluginError(
                    f"unexpected list item in {source_path}: {stripped}")
            content = stripped[2:].strip()
            if ":" in content and not content.startswith("["):
                # inline dict: "key: value"
                k, _, v = content.partition(":")
                item_dict: dict[str, Any] = {}
                item_dict[k.strip()] = _coerce(v.strip())
                current_list.append(item_dict)
                current_dict = item_dict
                last_key_in_dict = k.strip()
            else:
                current_list.append(_coerce(content))
                current_dict = None
                last_key_in_dict = None
            continue

        if ":" in stripped:
            if current_list_key is not None and indent <= 0:
                # out of the previous list
                current_list_key = None
                current_list = None
                current_dict = None
                last_key_in_dict = None
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                # key with list/dict below
                if indent == 0:
                    current_list_key = k
                    current_list = []
                    out[k] = current_list
                    current_dict = None
                    last_key_in_dict = None
            else:
                if current_dict is not None and indent > 0:
                    current_dict[last_key_in_dict or k] = _coerce(v)
                    last_key_in_dict = k
                elif current_list is not None and indent > 0:
                    # shouldn't normally happen — YAML lists-of-dicts
                    pass
                else:
                    out[k] = _coerce(v)
                    current_dict = out if isinstance(out, dict) else None
                    last_key_in_dict = k
            continue

        raise PluginError(f"unparseable line in {source_path}: {line!r}")

    return out


def _coerce(value: str) -> Any:
    """Coerce a string to int / float / bool / list / str."""
    v = value.strip()
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p) for p in inner.split(",")]
    if v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _load_manifest_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return _parse_manifest_text(text, path)
    return json.loads(text)


def load_manifest(path: Path) -> PluginManifest:
    """Load + validate a plugin manifest from ``path``."""
    raw = _load_manifest_file(path)
    missing = [k for k in _REQUIRED_FIELDS if k not in raw]
    if missing:
        raise PluginError(f"{path}: missing fields: {missing}")
    return PluginManifest(
        id=str(raw["id"]),
        name=str(raw["name"]),
        version=str(raw["version"]),
        description=str(raw.get("description", "")),
        module=str(raw.get("module", "")),
        hooks=list(raw.get("hooks", []) or []),
        commands=list(raw.get("commands", []) or []),
        permissions=list(raw.get("permissions", []) or []),
        meta=dict(raw.get("meta", {}) or {}),
        path=path.parent.resolve(),
    )


# --- discovery paths ---

def default_search_paths(state_root: Path | None = None) -> list[Path]:
    """Return the default plugin discovery paths (no I/O)."""
    paths: list[Path] = []
    env = os.environ.get("ROLLOUT_SHIELD_PLUGIN_PATHS")
    if env:
        for p in env.split(":"):
            p = p.strip()
            if p:
                paths.append(Path(p).expanduser())
    if state_root is None:
        state_root = Path.home() / ".rollout-shield"
    paths.append(state_root / "plugins")
    # System-level: $PREFIX/share/rollout-shield/plugins
    # The runtime module lives at <prefix>/lib/python/rollout_shield/
    here = Path(__file__).resolve()
    share = here.parent.parent.parent / "share" / "rollout-shield" / "plugins"
    if share.exists():
        paths.append(share)
    return paths


# --- registry ---

class _Registry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[str, PluginManifest] = {}
        self._active: dict[str, Any] = {}         # plugin_id -> module
        self._hooks: dict[str, list[Callable]] = {}
        self._last_discover_ts: float = 0.0

    def register(self, manifest: PluginManifest) -> None:
        with self._lock:
            if manifest.id in self._plugins:
                raise PluginError(f"plugin id collision: {manifest.id}")
            self._plugins[manifest.id] = manifest

    def list(self) -> list[PluginManifest]:
        with self._lock:
            return sorted(self._plugins.values(), key=lambda p: p.id)

    def get(self, plugin_id: str) -> PluginManifest | None:
        with self._lock:
            return self._plugins.get(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        with self._lock:
            return plugin_id in self._active

    def activate(self, plugin_id: str) -> PluginManifest:
        with self._lock:
            m = self._plugins.get(plugin_id)
            if m is None:
                raise PluginError(f"unknown plugin: {plugin_id}")
            if plugin_id in self._active:
                return m
            # import the module
            mod_path = m.path if m.path else None
            if mod_path is None:
                raise PluginError(f"plugin {plugin_id} has no source path")
            sys.path.insert(0, str(mod_path))
            try:
                mod = importlib.import_module(m.module)
            except ImportError as exc:
                raise PluginError(
                    f"plugin {plugin_id}: failed to import {m.module} "
                    f"({exc})"
                ) from exc
            # register any hooks the module exposed
            for hook_name in m.hooks:
                fn = getattr(mod, "on_" + hook_name, None)
                if fn is None:
                    continue
                self._hooks.setdefault(hook_name, []).append(fn)
            self._active[plugin_id] = mod
            return m

    def deactivate(self, plugin_id: str) -> None:
        with self._lock:
            self._active.pop(plugin_id, None)
            # remove hooks from this module — best-effort
        # we don't unregister from self._plugins — only deactivate

    def dispatch(self, event: str, **kwargs: Any) -> list[Any]:
        """Dispatch an event to all activated plugins subscribed to it.

        Returns a list of return values from each handler. Exceptions
        in a handler are swallowed (logged) — a bad plugin must not
        crash the runtime.
        """
        from . import logging as _log
        log = _log.get_logger("plugins")
        with self._lock:
            hooks = list(self._hooks.get(event, []))
        results: list[Any] = []
        for fn in hooks:
            try:
                results.append(fn(**kwargs))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "plugin hook failed",
                    extra={"event": event, "fn": getattr(fn, "__name__", "?"),
                           "error": repr(exc)},
                )
        return results

    def discover(self, paths: Iterable[Path] | None = None,
                 state_root: Path | None = None) -> int:
        """Walk the search paths and load any new manifests.

        Returns the number of NEW plugins registered.
        """
        if paths is None:
            paths = default_search_paths(state_root)
        n_new = 0
        for p in paths:
            if not p.exists() or not p.is_dir():
                continue
            for entry in sorted(p.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = None
                for name in ("plugin.yaml", "plugin.yml", "plugin.json"):
                    candidate = entry / name
                    if candidate.exists():
                        manifest_path = candidate
                        break
                if manifest_path is None:
                    continue
                try:
                    m = load_manifest(manifest_path)
                except PluginError as exc:
                    from . import logging as _log
                    _log.get_logger("plugins").warning(
                        "skipping invalid plugin manifest",
                        extra={"path": str(manifest_path),
                               "error": str(exc)},
                    )
                    continue
                with self._lock:
                    if m.id in self._plugins:
                        continue
                self.register(m)
                n_new += 1
        self._last_discover_ts = time.time()
        return n_new

    def last_discover_ts(self) -> float:
        return self._last_discover_ts


_REGISTRY: _Registry | None = None
_REG_LOCK = threading.Lock()


def registry() -> _Registry:
    global _REGISTRY
    if _REGISTRY is None:
        with _REG_LOCK:
            if _REGISTRY is None:
                _REGISTRY = _Registry()
    return _REGISTRY


def discover(paths: Iterable[Path] | None = None,
             state_root: Path | None = None) -> int:
    """Convenience: discover + register."""
    return registry().discover(paths, state_root=state_root)


def activate(plugin_id: str) -> PluginManifest:
    return registry().activate(plugin_id)


def deactivate(plugin_id: str) -> None:
    registry().deactivate(plugin_id)


def dispatch(event: str, **kwargs: Any) -> list[Any]:
    return registry().dispatch(event, **kwargs)


def list_plugins() -> list[PluginManifest]:
    return registry().list()


__all__ = [
    "PluginManifest", "PluginError",
    "load_manifest", "default_search_paths",
    "registry", "discover", "activate", "deactivate", "dispatch",
    "list_plugins",
]