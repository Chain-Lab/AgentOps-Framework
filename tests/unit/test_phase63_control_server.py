"""Tests for Phase 63 Control HTTP Server.

Phase 63: Persistent Approval / Control Plane — stdlib HTTP server for
control commands, approvals, and audit events with token auth.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from agent_app.runtime.policy_rollout_federation_notification_control_server import (
    _ControlHTTPServer,
)


def _wait_for_server(host, port, timeout=3.0):
    """Wait for HTTP server to become available."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _http_get(host, port, path, token=None):
    """Make HTTP GET request, return (status, body)."""
    import http.client
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection(host, port, timeout=2)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        return resp.status, json.loads(body) if body else {}
    finally:
        conn.close()


def _http_post(host, port, path, data, token=None):
    """Make HTTP POST request, return (status, body)."""
    import http.client
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection(host, port, timeout=2)
    try:
        body = json.dumps(data, default=str)
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode()
        return resp.status, json.loads(resp_body) if resp_body else {}
    finally:
        conn.close()


class TestControlHTTPServerLifecycle:
    def test_server_starts_and_stops(self, tmp_path):
        server = _ControlHTTPServer("127.0.0.1", 0)
        server.start()
        assert _wait_for_server("127.0.0.1", server.address[1])
        server.stop()
        # After stop, server should not accept connections
        import socket
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", server.address[1]), timeout=1):
                pass

    def test_double_start_is_idempotent(self, tmp_path):
        server = _ControlHTTPServer("127.0.0.1", 0)
        server.start()
        server.start()  # Should not raise
        server.stop()


class TestControlHTTPServerEndpoints:
    @pytest.fixture
    def server(self):
        server = _ControlHTTPServer("127.0.0.1", 0)
        server.start()
        yield server
        server.stop()

    def test_get_control_status(self, server):
        host, port = server.address
        status, body = _http_get(host, port, "/control/status")
        assert status == 200
        assert "control_plane_enabled" in body

    def test_get_control_commands_empty(self, server):
        host, port = server.address
        status, body = _http_get(host, port, "/control/commands")
        assert status == 200

    def test_get_control_command_not_found(self, server):
        host, port = server.address
        # Provide a get_command function that returns None for unknown IDs
        server._get_command_fn = lambda cmd_id: None
        status, body = _http_get(host, port, f"/control/commands/cmd_nonexistent")
        assert status == 404

    def test_post_control_commands(self, server):
        host, port = server.address

        def create_fn(body):
            return {"command_id": "cmd_test", "status": "pending", **body}

        server._create_command_fn = create_fn
        status, body = _http_post(host, port, "/control/commands", {"command_type": "pause"})
        assert status == 201
        assert body["command_id"] == "cmd_test"

    def test_post_control_commands_invalid_json(self, server):
        import http.client
        host, port = server.address
        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("POST", "/control/commands", body="not json", headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 400
        finally:
            conn.close()

    def test_unknown_route_returns_404(self, server):
        host, port = server.address
        status, body = _http_get(host, port, "/unknown/path")
        assert status == 404

    def test_method_not_allowed(self, server):
        import http.client
        host, port = server.address
        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("DELETE", "/control/status")
            resp = conn.getresponse()
            assert resp.status == 405  # Method not allowed
        finally:
            conn.close()

    def test_get_approvals(self, server):
        host, port = server.address
        status, body = _http_get(host, port, "/approvals")
        assert status == 200
        assert isinstance(body, list)

    def test_get_audit_events(self, server):
        host, port = server.address
        status, body = _http_get(host, port, "/audit/events")
        assert status == 200
        assert isinstance(body, list)


class TestControlHTTPServerTokenAuth:
    @pytest.fixture
    def auth_server(self):
        server = _ControlHTTPServer("127.0.0.1", 0, auth_token="secret123")
        server.start()
        yield server
        server.stop()

    def test_unauthorized_without_token(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_get(host, port, "/control/status")
        assert status == 401

    def test_forbidden_with_wrong_token(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_get(host, port, "/control/status", token="wrong")
        assert status == 403

    def test_authorized_with_correct_token(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_get(host, port, "/control/status", token="secret123")
        assert status == 200

    def test_post_requires_auth(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_post(host, port, "/control/commands", {"command_type": "pause"})
        assert status == 401

    def test_approve_requires_auth(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_post(host, port, "/approvals/appr_001/approve", {"resolved_by": "op"})
        assert status == 401

    def test_reject_requires_auth(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_post(host, port, "/approvals/appr_001/reject", {"resolved_by": "op"})
        assert status == 401

    def test_audit_requires_auth(self, auth_server):
        host, port = auth_server.address
        status, _ = _http_get(host, port, "/audit/events")
        assert status == 401
