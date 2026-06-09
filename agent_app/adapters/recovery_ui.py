"""Optional FastAPI HTML recovery admin console.

This module is intentionally import-light: importing it must not require FastAPI.
Call ``create_recovery_ui_router`` only when the optional API dependencies are
installed.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def _escape(value: Any) -> str:
    """HTML-escape a value for safe display."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _enum_value(value: Any) -> str:
    """Return the plain value for enums and enum-like objects."""
    return str(getattr(value, "value", value))


def _badge(label: str, ok: bool) -> str:
    """Render a compact status badge."""
    class_name = "ok" if ok else "warn"
    return f'<span class="badge {class_name}">{_escape(label)}</span>'


def _html_page(title: str, body: str, status_code: int = 200) -> Any:
    """Render a complete HTML page response."""
    from fastapi.responses import HTMLResponse

    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_escape(title)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #172033; }}
    nav {{ margin-bottom: 1.5rem; }}
    nav a {{ margin-right: 1rem; color: #1d4ed8; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #d8dee8; padding: 0.6rem 0.75rem; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7fb; width: 14rem; }}
    .badge {{ border-radius: 999px; display: inline-block; font-size: 0.85rem; font-weight: 600; padding: 0.15rem 0.55rem; }}
    .ok {{ background: #dcfce7; color: #166534; }}
    .warn {{ background: #fee2e2; color: #991b1b; }}
    .safety {{ background: #fffbeb; border-left: 4px solid #d97706; margin: 1rem 0; padding: 0.75rem 1rem; }}
    .error {{ background: #fef2f2; border-left: 4px solid #dc2626; margin: 1rem 0; padding: 0.75rem 1rem; }}
    code {{ background: #f5f7fb; padding: 0.1rem 0.25rem; }}
  </style>
</head>
<body>
<nav>
  <a href="/admin/recovery">Dashboard</a>
  <a href="/admin/recovery/candidates">Candidates</a>
  <a href="/admin/recovery/history">History</a>
</nav>
{body}
</body>
</html>"""
    return HTMLResponse(content=content, status_code=status_code)


def _error_page(title: str, message: str, status_code: int) -> Any:
    """Render a clean error page without leaking implementation details."""
    return _html_page(
        title,
        f"""
<h1>{_escape(title)}</h1>
<div class="error"><p>{_escape(message)}</p></div>
<p><a href="/admin/recovery">Back to Recovery Admin Console</a></p>
""",
        status_code=status_code,
    )


def _safety_box() -> str:
    """Render shared recovery safety statements."""
    return """
<div class="safety">
  <strong>Safety:</strong>
  The recovery console does not start the daemon or perform recovery on page load.
  Dry-run default mode is shown before any live action.
  Live recovery requires explicit confirmation. Recovery is best-effort; leases are not exactly-once guarantees.
</div>
"""


def _make_confirmation_token(secret: bytes, run_id: str) -> str:
    """Create an HMAC token for future live-recovery confirmation flows."""
    return hmac.new(secret, f"recovery-confirm:{run_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _valid_confirmation_token(secret: bytes, run_id: str, token: str) -> bool:
    """Validate a confirmation token in constant time."""
    expected = _make_confirmation_token(secret, run_id)
    return hmac.compare_digest(expected, token)


def _status_bool(status: Any, attr: str) -> bool:
    return bool(getattr(status, attr, False))


def _render_dashboard(status: Any) -> str:
    """Render the recovery dashboard body."""
    enabled = _status_bool(status, "enabled")
    dry_run = _status_bool(status, "dry_run")
    daemon_configured = _status_bool(status, "daemon_configured")
    scanner_available = _status_bool(status, "scanner_available")
    service_available = _status_bool(status, "recovery_service_available")
    last_tick_at = getattr(status, "last_tick_at", None)
    last_tick = last_tick_at.isoformat() if hasattr(last_tick_at, "isoformat") else "No tick recorded"
    policy = getattr(status, "policy", None)
    policy_mode = _enum_value(getattr(policy, "mode", "default")) if policy is not None else "default"

    return f"""
<h1>Recovery Admin Console</h1>
{_safety_box()}
<table aria-label="Recovery system status">
  <tr><th>Daemon</th><td>{_badge("Enabled" if enabled else "Disabled", enabled)}</td></tr>
  <tr><th>Daemon configured</th><td>{_badge("Configured" if daemon_configured else "Not configured", daemon_configured)}</td></tr>
  <tr><th>Dry-run default</th><td>{_badge("Enabled" if dry_run else "Disabled", dry_run)}</td></tr>
  <tr><th>Scanner</th><td>{_badge("Available" if scanner_available else "Unavailable", scanner_available)}</td></tr>
  <tr><th>Recovery service</th><td>{_badge("Available" if service_available else "Unavailable", service_available)}</td></tr>
  <tr><th>Last daemon tick</th><td>{_escape(last_tick)}</td></tr>
  <tr><th>Policy mode</th><td>{_escape(policy_mode)}</td></tr>
</table>
<ul>
  <li><a href="/admin/recovery/candidates">Review recovery candidates</a></li>
  <li><a href="/admin/recovery/history">Review recovery history</a></li>
</ul>
"""


def create_recovery_ui_router(app: Any, admin_dependency: Any | None = None) -> Any:
    """Create the optional FastAPI router for the recovery admin UI.

    The router is deny-by-default. If *admin_dependency* is not supplied, every
    route raises HTTP 403 so newly registered admin paths cannot be reached
    without an explicit authorization dependency.

    Raises:
        ImportError: If FastAPI is not installed. Install the optional API extra
            with ``agent-app-framework[api]``.
    """
    try:
        from fastapi import APIRouter, Depends, HTTPException
    except ImportError as exc:
        raise ImportError(
            "FastAPI is required for the recovery admin console. "
            "Install optional API dependencies with agent-app-framework[api]."
        ) from exc

    async def _deny_by_default() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    def _server_error() -> Any:
        logger.exception("Recovery admin console request failed")
        return _error_page(
            "Recovery Admin Console Error",
            "Recovery admin operation failed. Check server logs for details.",
            500,
        )

    def _not_implemented() -> Any:
        return _error_page(
            "Not Implemented",
            "This recovery console page is not implemented yet.",
            404,
        )

    auth_dependency = admin_dependency or _deny_by_default
    router = APIRouter(
        prefix="/admin/recovery",
        tags=["recovery-ui"],
        dependencies=[Depends(auth_dependency)],
    )
    confirmation_secret = secrets.token_bytes(32)
    _ = confirmation_secret

    @router.get("")
    async def dashboard() -> Any:
        try:
            status = app.get_recovery_system_status()
            return _html_page("Recovery Admin Console", _render_dashboard(status))
        except Exception:
            return _server_error()

    @router.get("/candidates")
    async def candidates() -> Any:
        return _not_implemented()

    @router.get("/candidates/{run_id}")
    async def candidate_detail(run_id: str) -> Any:
        _ = run_id
        return _not_implemented()

    @router.get("/history")
    async def history() -> Any:
        return _not_implemented()

    @router.post("/scan")
    async def scan() -> Any:
        return _not_implemented()

    @router.post("/candidates/{run_id}/confirm")
    async def confirm_candidate(run_id: str) -> Any:
        _ = run_id
        return _not_implemented()

    @router.post("/candidates/{run_id}/recover")
    async def recover_candidate(run_id: str) -> Any:
        _ = run_id
        return _not_implemented()

    return router
