"""rollout-shield runtime package.

A Python 3 stdlib-only implementation of the rollout-shield runtime:
- CLI (`bin/rollout-shield`)
- Persistent state on disk
- Monitoring daemon
- Web dashboard (HTTP server + JSON API)

Composes with the spec docs in `protocol/`, `agent/`, `rollout/`,
`hardware/`, and the existing scripts in `tools/`.
"""

__version__ = "0.1.0"
__all__ = ["cli", "state", "monitor_daemon", "http_server", "health_checks", "alerter"]
