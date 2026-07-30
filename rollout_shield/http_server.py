"""HTTP server for the rollout-shield web dashboard.

Serves two things on the same port:

- ``/`` and ``/static/*`` → static dashboard files (HTML + JS + CSS)
  from ``rollout_shield/interface/``
- ``/api/*`` → JSON API for the dashboard to consume

API endpoints:

- ``GET /api/health``        — latest health-check summary
- ``GET /api/claims``        — recent claims (default 50)
- ``GET /api/alerts``        — recent alerts (default 50)
- ``GET /api/reputation``    — full reputation index
- ``GET /api/status``        — system summary (state, agents, counts)
- ``GET /api/keys``          — registered agent keys (no private material)

The server is intentionally minimal — single-threaded by default, but
configurable via ``--host`` / ``--port``. For production use behind a
reverse proxy, run the dashboard as a sidecar.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .state import State


INTERFACE_DIR = Path(__file__).parent / "interface"


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "rollout-shield/0.1.0"
    state: State  # set by `make_handler`

    # ---------- response helpers ----------

    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json(404, {"error": "not_found", "path": str(path)})
            return
        ctype, _enc = mimetypes.guess_type(path.name)
        if ctype is None:
            ctype = "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- routing ----------

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        # API
        if path.startswith("/api/"):
            self._route_api(path, query)
            return

        # Static
        if path == "/" or path == "":
            self._send_file(INTERFACE_DIR / "index.html")
            return
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            self._send_file(INTERFACE_DIR / rel)
            return
        # Fallback: SPA-style serve index.html for unknown paths under /
        if not path.startswith("/api/"):
            self._send_file(INTERFACE_DIR / "index.html")
            return
        self._send_json(404, {"error": "not_found", "path": path})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — http.server API
        sys.stderr.write("[dashboard] " + (format % args) + "\n")

    # ---------- API handlers ----------

    def _route_api(self, path: str, query: dict) -> None:
        try:
            if path == "/api/health":
                latest = self.state.latest_health()
                self._send_json(200, latest or {"status": "unknown", "ts": None})
            elif path == "/api/claims":
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(200, {"claims": self.state.recent_claims(limit)})
            elif path == "/api/alerts":
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(200, {"alerts": self.state.recent_alerts(limit)})
            elif path == "/api/reputation":
                self._send_json(200, self.state.load_reputation())
            elif path == "/api/status":
                self._send_json(200, self.state.summary())
            elif path == "/api/keys":
                keys = self.state.list_keys()
                # never expose private key material — strip before returning
                sanitized = []
                for k in keys:
                    sanitized.append({kk: vv for kk, vv in k.items()
                                      if kk not in ("private_key_pem", "private_key")})
                self._send_json(200, {"keys": sanitized})
            else:
                self._send_json(404, {"error": "unknown_endpoint", "path": path})
        except Exception as exc:  # noqa: BLE001 — never expose stack traces to the browser
            self._send_json(500, {"error": "internal", "message": repr(exc)})


def make_handler(state: State):
    """Create a request handler class bound to the given state."""
    class _Bound(DashboardHandler):
        pass
    _Bound.state = state
    return _Bound


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rollout-shield dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--open", action="store_true",
                        help="also open the dashboard URL in the default browser")
    parser.add_argument("--i-know-bind-is-public", dest="public_confirm",
                        action="store_true",
                        help="REQUIRED when --host is 0.0.0.0 / public (acknowledgment)")
    args = parser.parse_args(argv)

    # Pop-off #5: refuse public bind without explicit confirmation
    if args.host in ("0.0.0.0", "::", "") and not args.public_confirm:
        print(
            f"REFUSED: binding to {args.host!r} exposes the dashboard on all\n"
            f"network interfaces. Pass --i-know-bind-is-public to confirm\n"
            f"you understand this leaks state (claims, reputation, alerts)\n"
            f"to anyone reachable on the network.",
            file=sys.stderr,
        )
        return 2

    state = State(root=args.state_root)
    handler_cls = make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)

    url = f"http://{args.host}:{args.port}/"
    print(f"[dashboard] serving on {url}", file=sys.stderr)
    print(f"[dashboard] state root: {state.root}", file=sys.stderr)
    print(f"[dashboard] endpoints:", file=sys.stderr)
    print(f"  GET /                  → dashboard HTML", file=sys.stderr)
    print(f"  GET /api/health        → latest health summary", file=sys.stderr)
    print(f"  GET /api/claims        → recent claims (?limit=N)", file=sys.stderr)
    print(f"  GET /api/alerts        → recent alerts (?limit=N)", file=sys.stderr)
    print(f"  GET /api/reputation    → reputation index", file=sys.stderr)
    print(f"  GET /api/status        → system summary", file=sys.stderr)
    print(f"  GET /api/keys          → registered keys (no private material)", file=sys.stderr)

    if args.open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[dashboard] could not open browser: {exc}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down", file=sys.stderr)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
