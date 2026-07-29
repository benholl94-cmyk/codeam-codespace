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

    def _send_log_stream(self, query: dict) -> None:
        """Server-Sent Events stream of recent structured log lines.

        The dashboard JS subscribes via ``new EventSource('/api/log/stream')``
        and renders a live tail. The stream polls the on-disk log file
        (or the JSONL fallback) every second; falls back to the live
        Python logging queue when no logfile is configured.
        """
        # minimal SSE: 100 historical lines + heartbeat every 15s
        try:
            log_path = (self.state.root / "daemon.log")
            if not log_path.exists():
                # synthetic stream: a heartbeat every 15s, no history
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                while True:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    import time as _t; _t.sleep(15.0)
                return
            with log_path.open("rb") as fh:
                # last 100 lines
                try:
                    fh.seek(0, 2)
                    size = fh.tell()
                    fh.seek(max(0, size - 8192))
                    tail = fh.read().splitlines()[-100:]
                except OSError:
                    tail = []
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for line in tail:
                try:
                    decoded = line.decode("utf-8", errors="replace")
                except Exception:
                    continue
                self.wfile.write(f"data: {decoded}\n\n".encode("utf-8"))
            self.wfile.flush()
            # heartbeat to keep connection alive
            self.wfile.write(b": end\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    # ---------- routing ----------

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        import time as _time
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        # metrics: count + time every API request
        from . import metrics as _metrics
        t0 = _time.perf_counter()
        status_holder = {"code": 200}

        def _finish() -> None:
            elapsed = _time.perf_counter() - t0
            _metrics.http_requests_total.inc(
                labels=(self.command, path, str(status_holder["code"])))
            _metrics.http_request_duration_seconds.observe(
                elapsed, labels=(self.command, path))

        # API
        if path.startswith("/api/"):
            try:
                self._route_api(path, query)
                status_holder["code"] = 200
            except Exception:  # noqa: BLE001
                status_holder["code"] = 500
                self._send_json(500, {"error": "internal"})
                raise
            finally:
                _finish()
            return

        # Static
        if path == "/" or path == "":
            self._send_file(INTERFACE_DIR / "index.html")
            return
        if path == "/ai-assistance.html":
            self._send_file(INTERFACE_DIR / "ai-assistance.html")
            return
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            self._send_file(INTERFACE_DIR / rel)
            return
        # Fallback: SPA-style serve index.html for unknown paths under /
        if not path.startswith("/api/"):
            self._send_file(INTERFACE_DIR / "index.html")
            return
        status_holder["code"] = 404
        self._send_json(404, {"error": "not_found", "path": path})
        _finish()

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
            elif path == "/api/metrics":
                from . import metrics
                self._send_text(200, metrics.render(),
                                ctype="text/plain; version=0.0.4; charset=utf-8")
            elif path == "/api/routing":
                from . import routing
                self._send_json(200, routing.manifest())
            elif path == "/api/log/stream":
                # Server-Sent Events — log tail (last 100 lines, polling)
                # Operator UI subscribes via EventSource and renders
                # a live log view.
                self._send_log_stream(query)
            elif path == "/api/ai/leaderboard":
                from .ai.leaderboard import (latest_per_model_benchmark,
                                             aggregate_scores, top_model)
                entries = latest_per_model_benchmark(self.state)
                scores = aggregate_scores(self.state)
                best = top_model(self.state)
                self._send_json(200, {
                    "best": {"model_id": best[0], "score": best[1]} if best else None,
                    "scores": scores,
                    "entries": [e.to_dict() for e in entries],
                })
            elif path == "/api/ai/cycles":
                from .ai.self_cycle import iter_cycles
                limit = int(query.get("limit", ["50"])[0])
                cycles = iter_cycles(self.state, limit=limit)
                self._send_json(200, {
                    "cycles": [c.to_dict() for c in cycles],
                })
            elif path == "/api/ai/first-of-kind":
                from .ai.generator import iter_artifacts
                limit = int(query.get("limit", ["50"])[0])
                kind = query.get("kind", [None])[0]
                artifacts = iter_artifacts(self.state, limit=limit, kind=kind)
                self._send_json(200, {
                    "artifacts": [a.to_dict() for a in artifacts],
                })
            elif path == "/api/ai/models":
                from .ai.models import list_models
                models = list_models()
                self._send_json(200, {
                    "models": [
                        {"id": m.id, "name": m.name, "family": m.family,
                         "description": m.description}
                        for m in models
                    ],
                })
            elif path == "/api/webhooks/targets":
                from .webhook_delivery import list_targets
                self._send_json(200, {
                    "targets": [t.to_dict() for t in list_targets(self.state)],
                })
            elif path == "/api/webhooks/deliveries":
                from .webhook_delivery import list_deliveries, DeliveryStatus
                status = query.get("status", [None])[0]
                target = query.get("target", [None])[0]
                limit = int(query.get("limit", ["100"])[0])
                st = DeliveryStatus(status) if status else None
                self._send_json(200, {
                    "deliveries": [r.to_dict()
                                   for r in list_deliveries(self.state,
                                                            status=st,
                                                            target=target,
                                                            limit=limit)],
                })
            elif path == "/api/webhooks/stats":
                from .webhook_delivery import stats as webhook_stats
                self._send_json(200, webhook_stats(self.state))
            elif path == "/api/webhooks/health":
                from .webhook_delivery import stats as webhook_stats
                s = webhook_stats(self.state)
                self._send_json(200, {
                    "ok": True,
                    "outbox_depth": s.get("outbox_depth", 0),
                    "dlq_depth": s.get("dlq_depth", 0),
                    "targets_count": s.get("targets_count", 0),
                })
            elif path.startswith("/api/webhooks/deliveries/"):
                # /api/webhooks/deliveries/<id>  OR  /api/webhooks/deliveries/<id>/replay
                from .webhook_delivery import get_delivery
                rest = path[len("/api/webhooks/deliveries/"):]
                parts = rest.split("/")
                delivery_id = parts[0]
                rec = get_delivery(self.state, delivery_id)
                if rec is None:
                    self._send_json(404, {"error": "unknown_delivery",
                                          "delivery_id": delivery_id})
                    return
                self._send_json(200, rec.to_dict())
            else:
                self._send_json(404, {"error": "unknown_endpoint", "path": path})
        except Exception as exc:  # noqa: BLE001 — never expose stack traces to the browser
            self._send_json(500, {"error": "internal", "message": repr(exc)})

    # ---------- POST routing (webhook delivery enqueue + replay) ----------

    def _read_body_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return None
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        import time as _time
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        from . import metrics as _metrics
        t0 = _time.perf_counter()
        status_holder = {"code": 200}

        def _finish() -> None:
            elapsed = _time.perf_counter() - t0
            _metrics.http_requests_total.inc(
                labels=("POST", path, str(status_holder["code"])))
            _metrics.http_request_duration_seconds.observe(
                elapsed, labels=("POST", path))

        try:
            if path == "/api/webhooks/deliver":
                body = self._read_body_json()
                if body is None:
                    status_holder["code"] = 400
                    self._send_json(400, {"error": "invalid_json"})
                    return
                target = body.get("target")
                payload = body.get("payload")
                idem = body.get("idempotency_key")
                if not target or not isinstance(payload, dict):
                    status_holder["code"] = 400
                    self._send_json(400, {"error": "missing target or payload"})
                    return
                from .webhook_delivery import enqueue
                try:
                    rec = enqueue(self.state, target_name=target,
                                  payload=payload, idempotency_key=idem)
                    self._send_json(201, rec.to_dict())
                except Exception as exc:
                    status_holder["code"] = 400
                    self._send_json(400, {"error": repr(exc)})
                return
            if path.startswith("/api/webhooks/deliveries/") and path.endswith("/replay"):
                rest = path[len("/api/webhooks/deliveries/"):-len("/replay")]
                if not rest:
                    status_holder["code"] = 400
                    self._send_json(400, {"error": "missing delivery_id"})
                    return
                from .webhook_delivery import replay as webhook_replay
                try:
                    rec = webhook_replay(self.state, rest)
                    self._send_json(200, rec.to_dict())
                except Exception as exc:
                    status_holder["code"] = 400
                    self._send_json(400, {"error": repr(exc)})
                return
            status_holder["code"] = 404
            self._send_json(404, {"error": "unknown_endpoint", "path": path})
        finally:
            _finish()


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
    args = parser.parse_args(argv)

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
