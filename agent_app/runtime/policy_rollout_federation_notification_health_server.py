"""Health HTTP server for daemon Phase 62 production hardening.

Serves ``/health`` (liveness) and ``/ready`` (readiness) endpoints using
only the standard library (http.server + threading).  No external HTTP
framework is required.
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable


class _HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler that delegates to callable factories."""

    health_fn: Callable[[], dict[str, Any]] | None = None
    ready_fn: Callable[[], dict[str, Any]] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Suppress default request logging
        pass

    def _json_response(self, data: dict[str, Any], status: int) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_ready(self, data: dict[str, Any]) -> int:
        """Return HTTP status for readiness probe."""
        state = data.get("state", "unknown")
        if state in ("healthy", "degraded"):
            running = data.get("running", False)
            if not running:
                return 503
            draining = data.get("draining", False)
            if draining:
                return 503
            leader_mode = data.get("leader_mode", False)
            if leader_mode:
                return 200
            return 200
        return 503

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health" or self.path.startswith("/health?"):
            health_fn = type(self).health_fn
            if health_fn is None:
                self._json_response({"error": "health not configured"}, 503)
                return
            data = health_fn()
            status = 200 if data.get("state") != "unhealthy" else 503
            self._json_response(data, status)
        elif self.path == "/ready" or self.path.startswith("/ready?"):
            ready_fn = type(self).ready_fn
            if ready_fn is None:
                self._json_response({"error": "ready not configured"}, 503)
                return
            data = ready_fn()
            status = self._check_ready(data)
            self._json_response(data, status)
        else:
            self.send_response(404)
            self.end_headers()


class HealthHTTPServer:
    """Lightweight HTTP server exposing daemon health endpoints.

    Runs on a background thread so it does not block the event loop.
    """

    def __init__(
        self,
        host: str,
        port: int,
        health_fn: Callable[[], dict[str, Any]],
        ready_fn: Callable[[], dict[str, Any]],
    ) -> None:
        self._host = host
        self._port = port
        self._health_fn = health_fn
        self._ready_fn = ready_fn
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the HTTP server on a background thread."""
        _HealthHandler.health_fn = self._health_fn
        _HealthHandler.ready_fn = self._ready_fn
        self._server = HTTPServer(
            (self._host, self._port),
            _HealthHandler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()
