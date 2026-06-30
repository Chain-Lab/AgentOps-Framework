"""Control HTTP server for daemon operator control.

Phase 63: Persistent Approval / Control Plane — stdlib-based HTTP server
that exposes control commands, approvals, and audit events via REST API.
Uses only Python standard library (http.server + threading).
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class _ControlHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for control plane endpoints."""

    control_server: "_ControlHTTPServer | None" = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        pass

    def _json_response(self, status: int, data: Any) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error_response(self, status: int, error: str, message: str) -> None:
        self._json_response(status, {"error": error, "message": message})

    def _authenticate(self) -> bool:
        """Check bearer token auth if configured."""
        cs = type(self).control_server
        if cs is None or cs._auth_token is None:
            return True
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._error_response(401, "unauthorized", "Missing or invalid Authorization header")
            return False
        token = auth_header[7:]
        if token != cs._auth_token:
            self._error_response(403, "forbidden", "Invalid token")
            return False
        return True

    def _read_json_body(self) -> Any | None:
        """Parse JSON request body, return None on error."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            body = self.rfile.read(length)
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._error_response(400, "bad_request", "Invalid JSON body")
            return None

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._authenticate():
            return
        path = self.path.split("?")[0]

        if path == "/control/status":
            self._handle_control_status()
        elif path == "/control/commands":
            self._handle_list_commands()
        elif path.startswith("/control/commands/"):
            self._handle_get_command(path.split("/")[-1])
        elif path == "/approvals":
            self._handle_list_approvals()
        elif path.startswith("/approvals/") and path.endswith("/approve"):
            self._handle_approve(path.split("/")[-2])
        elif path.startswith("/approvals/") and path.endswith("/reject"):
            self._handle_reject(path.split("/")[-2])
        elif path == "/audit/events":
            self._handle_audit_events()
        else:
            self._error_response(404, "not_found", f"Unknown path: {self.path}")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authenticate():
            return
        path = self.path.split("?")[0]

        if path == "/control/commands":
            self._handle_create_command()
        elif path.startswith("/approvals"):
            pass
        else:
            self._error_response(404, "not_found", f"Unknown path: {self.path}")

    def handle_one_request(self) -> None:  # noqa: D401
        """Override to return 405 for unsupported HTTP methods."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self._error_response(414, "uri_too_long", "Request URI too long")
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                # parse_request already sent an error response
                return
            # Return 405 for unsupported methods (instead of 501)
            if self.command not in ("GET", "POST", "HEAD"):
                self._error_response(405, "method_not_allowed", f"Method {self.command} not allowed")
                return
            # Dispatch to do_GET / do_POST / do_HEAD
            mname = "do_" + self.command
            if not hasattr(self, mname):
                self._error_response(501, "not_implemented", f"Unsupported method ({self.command!r})")
                return
            method = getattr(self, mname)
            method()
            self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Control command handlers
    # ------------------------------------------------------------------

    def _handle_control_status(self) -> None:
        cs = type(self).control_server
        status_fn = cs._status_fn if cs is not None else None
        if status_fn is None:
            self._json_response(200, {"control_plane_enabled": True, "paused": False})
            return
        try:
            status = status_fn()
            self._json_response(200, status)
        except Exception as exc:  # noqa: BLE001
            self._json_response(200, {
                "control_plane_enabled": True,
                "error": str(exc),
            })

    def _handle_create_command(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        cs = type(self).control_server
        create_fn = cs._create_command_fn if cs is not None else None
        if create_fn is None:
            self._error_response(501, "not_implemented", "Command creation not configured")
            return
        try:
            result = create_fn(body)
            self._json_response(201, result)
        except KeyError as exc:
            self._error_response(404, "not_found", str(exc))
        except ValueError as exc:
            self._error_response(400, "bad_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error_response(500, "internal_error", str(exc))

    def _handle_list_commands(self) -> None:
        cs = type(self).control_server
        list_fn = cs._list_commands_fn if cs is not None else None
        if list_fn is None:
            self._json_response(200, [])
            return
        try:
            commands = list_fn()
            self._json_response(200, commands)
        except Exception as exc:  # noqa: BLE001
            self._json_response(200, {"error": str(exc)})

    def _handle_get_command(self, command_id: str) -> None:
        cs = type(self).control_server
        get_fn = cs._get_command_fn if cs is not None else None
        if get_fn is None:
            self._error_response(501, "not_implemented", "Not configured")
            return
        try:
            result = get_fn(command_id)
            if result is None:
                self._error_response(404, "not_found", f"Command {command_id} not found")
                return
            self._json_response(200, result)
        except Exception as exc:  # noqa: BLE001
            self._json_response(500, {"error": str(exc)})

    # ------------------------------------------------------------------
    # Approval handlers
    # ------------------------------------------------------------------

    def _handle_list_approvals(self) -> None:
        cs = type(self).control_server
        list_fn = cs._list_approvals_fn if cs is not None else None
        if list_fn is None:
            self._json_response(200, [])
            return
        try:
            approvals = list_fn()
            self._json_response(200, approvals)
        except Exception as exc:  # noqa: BLE001
            self._json_response(200, {"error": str(exc)})

    def _handle_approve(self, approval_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        cs = type(self).control_server
        approve_fn = cs._approve_fn if cs is not None else None
        if approve_fn is None:
            self._error_response(501, "not_implemented", "Approve not configured")
            return
        try:
            result = approve_fn(approval_id, body)
            self._json_response(200, result)
        except KeyError as exc:
            self._error_response(404, "not_found", str(exc))
        except ValueError as exc:
            self._error_response(400, "bad_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error_response(500, "internal_error", str(exc))

    def _handle_reject(self, approval_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        cs = type(self).control_server
        reject_fn = cs._reject_fn if cs is not None else None
        if reject_fn is None:
            self._error_response(501, "not_implemented", "Reject not configured")
            return
        try:
            result = reject_fn(approval_id, body)
            self._json_response(200, result)
        except KeyError as exc:
            self._error_response(404, "not_found", str(exc))
        except ValueError as exc:
            self._error_response(400, "bad_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error_response(500, "internal_error", str(exc))

    # ------------------------------------------------------------------
    # Audit handlers
    # ------------------------------------------------------------------

    def _handle_audit_events(self) -> None:
        cs = type(self).control_server
        audit_fn = cs._audit_fn if cs is not None else None
        if audit_fn is None:
            self._json_response(200, [])
            return
        try:
            events = audit_fn()
            self._json_response(200, events)
        except Exception as exc:  # noqa: BLE001
            self._json_response(200, {"error": str(exc)})


class _ControlHTTPServer:
    """Control HTTP server for daemon operator control plane.

    Wraps stdlib HTTPServer in a thread with start/stop lifecycle.
    """

    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str | None = None,
        status_fn: Callable[[], dict[str, Any]] | None = None,
        create_command_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        list_commands_fn: Callable[[], list[dict[str, Any]]] | None = None,
        get_command_fn: Callable[[str], dict[str, Any] | None] | None = None,
        list_approvals_fn: Callable[[], list[dict[str, Any]]] | None = None,
        approve_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        reject_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        audit_fn: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._status_fn = status_fn
        self._create_command_fn = create_command_fn
        self._list_commands_fn = list_commands_fn
        self._get_command_fn = get_command_fn
        self._list_approvals_fn = list_approvals_fn
        self._approve_fn = approve_fn
        self._reject_fn = reject_fn
        self._audit_fn = audit_fn
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the HTTP server in a background thread."""
        if self._httpd is not None:
            return
        handler = _ControlHTTPHandler
        handler.control_server = self
        self._httpd = HTTPServer((self._host, self._port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            if self._thread is not None:
                self._thread.join(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._httpd = None
            self._thread = None

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is not None:
            return self._httpd.server_address
        return (self._host, self._port)
