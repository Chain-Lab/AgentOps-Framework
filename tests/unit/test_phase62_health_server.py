"""Phase 62 Task 5: Health HTTP server tests."""
from __future__ import annotations

import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_app.runtime.policy_rollout_federation_notification_health_server import (
    HealthHTTPServer,
)


def _free_port() -> int:
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fetch(port: int, path: str) -> tuple[int, str]:
    """HTTP GET and return (status, body)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
        s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        response = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
    raw = response.decode("utf-8", errors="replace")
    # Split headers from body
    parts = raw.split("\r\n\r\n", 1)
    body = parts[1] if len(parts) > 1 else ""
    header_lines = raw.split("\r\n")
    status_line = header_lines[0] if header_lines else ""
    try:
        status = int(status_line.split(" ")[1])
    except (IndexError, ValueError):
        status = 0
    return status, body


class TestHealthHTTPServer:
    """Tests for HealthHTTPServer."""

    def test_server_starts_and_serves_health(self):
        """Server responds to /health with 200 when healthy."""
        port = _free_port()
        health_fn = lambda: {"state": "healthy", "running": True}
        ready_fn = lambda: {"state": "healthy", "running": True, "leader_mode": True}
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        try:
            assert server.running is True
            status, body = _fetch(port, "/health")
            assert status == 200
            assert "healthy" in body
        finally:
            server.stop()

    def test_server_returns_503_when_unhealthy(self):
        """Server returns 503 for /health when state is unhealthy."""
        port = _free_port()
        health_fn = lambda: {"state": "unhealthy", "running": True}
        ready_fn = lambda: {"state": "unhealthy", "running": True}
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        try:
            status, body = _fetch(port, "/health")
            assert status == 503
        finally:
            server.stop()

    def test_server_returns_404_for_unknown_path(self):
        """Server returns 404 for unknown paths."""
        port = _free_port()
        health_fn = lambda: {"state": "healthy", "running": True}
        ready_fn = lambda: {"state": "healthy", "running": True}
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        try:
            status, _ = _fetch(port, "/unknown")
            assert status == 404
        finally:
            server.stop()

    def test_server_returns_503_when_health_not_configured(self):
        """Server returns 503 for /health when health_fn is None."""
        port = _free_port()
        server = HealthHTTPServer("127.0.0.1", port, None, None)
        server.start()
        try:
            status, body = _fetch(port, "/health")
            assert status == 503
            assert "not configured" in body
        finally:
            server.stop()

    def test_server_returns_503_when_ready_not_configured(self):
        """Server returns 503 for /ready when ready_fn is None."""
        port = _free_port()
        server = HealthHTTPServer("127.0.0.1", port, None, None)
        server.start()
        try:
            status, body = _fetch(port, "/ready")
            assert status == 503
            assert "not configured" in body
        finally:
            server.stop()

    def test_server_ready_returns_200_when_healthy_and_running(self):
        """Server /ready returns 200 when healthy, running, leader."""
        port = _free_port()
        health_fn = lambda: {"state": "healthy", "running": True}
        ready_fn = lambda: {
            "state": "healthy",
            "running": True,
            "leader_mode": True,
        }
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        try:
            status, _ = _fetch(port, "/ready")
            assert status == 200
        finally:
            server.stop()

    def test_server_ready_returns_503_when_draining(self):
        """Server /ready returns 503 when draining."""
        port = _free_port()
        health_fn = lambda: {"state": "healthy", "running": True}
        ready_fn = lambda: {
            "state": "healthy",
            "running": True,
            "draining": True,
        }
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        try:
            status, _ = _fetch(port, "/ready")
            assert status == 503
        finally:
            server.stop()

    def test_server_stop_cleans_up(self):
        """Server stop() sets running=False and joins thread."""
        port = _free_port()
        health_fn = lambda: {"state": "healthy", "running": True}
        ready_fn = lambda: {"state": "healthy", "running": True}
        server = HealthHTTPServer("127.0.0.1", port, health_fn, ready_fn)
        server.start()
        assert server.running is True
        server.stop()
        assert server.running is False
        assert server._server is None
        assert server._thread is None

    def test_server_running_false_before_start(self):
        """Server running property is False before start()."""
        port = _free_port()
        server = HealthHTTPServer("127.0.0.1", port, None, None)
        assert server.running is False
