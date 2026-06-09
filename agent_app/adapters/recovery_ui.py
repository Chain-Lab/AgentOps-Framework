"""Optional FastAPI HTML recovery admin console.

This module is intentionally import-light: importing it must not require FastAPI.
Call ``create_recovery_ui_router`` only when the optional API dependencies are
installed.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
from typing import Any
from urllib.parse import parse_qsl

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


def _format_time(value: Any) -> str:
    """Return an ISO timestamp for datetime-like values."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _json_summary(value: Any) -> str:
    """Render a compact escaped JSON-like summary."""
    if not value:
        return ""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _dict_rows(items: list[Any], key_name: str, value_name: str) -> str:
    """Render simple dictionaries as list items."""
    if not items:
        return "<p>None</p>"
    rows = []
    for item in items:
        if isinstance(item, dict):
            key = item.get(key_name, "")
            value = item.get(value_name, "")
        else:
            key = getattr(item, key_name, "")
            value = getattr(item, value_name, "")
        rows.append(f"<li><code>{_escape(key)}</code>: {_escape(value)}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def _render_scan_result(result: Any) -> str:
    """Render the result of a dry-run recovery candidate scan."""
    selected_run_ids = list(getattr(result, "selected_run_ids", []) or [])
    selected_links = (
        "<ul>"
        + "".join(
            f'<li><a href="/admin/recovery/candidates/{_escape(run_id)}">{_escape(run_id)}</a></li>'
            for run_id in selected_run_ids
        )
        + "</ul>"
        if selected_run_ids
        else "<p>No candidates selected.</p>"
    )
    mode = "Dry-run" if bool(getattr(result, "dry_run", True)) else "Live"

    return f"""
<h1>Recovery Candidates</h1>
{_safety_box()}
<p>{_escape(mode)} scan completed. No recovery is performed by this page.</p>
<table aria-label="Recovery candidate scan summary">
  <tr><th>Scanned</th><td>{_escape(getattr(result, "scanned_count", 0))}</td></tr>
  <tr><th>Selected</th><td>{_escape(getattr(result, "selected_count", len(selected_run_ids)))}</td></tr>
  <tr><th>Recovered</th><td>{_escape(getattr(result, "recovered_count", 0))}</td></tr>
  <tr><th>Skipped</th><td>{_escape(getattr(result, "skipped_count", 0))}</td></tr>
  <tr><th>Failed</th><td>{_escape(getattr(result, "failed_count", 0))}</td></tr>
</table>
<h2>Selected candidates</h2>
{selected_links}
<h2>Skipped</h2>
{_dict_rows(list(getattr(result, "skipped", []) or []), "run_id", "reason")}
<h2>Failures</h2>
{_dict_rows(list(getattr(result, "failures", []) or []), "run_id", "error")}
"""


def _render_candidate(candidate: Any) -> str:
    """Render one recovery candidate inspection page."""
    run_id = getattr(candidate, "run_id", "")
    reasons = ", ".join(_enum_value(reason) for reason in getattr(candidate, "reasons", []) or [])
    recommendation = _enum_value(getattr(candidate, "recommendation", ""))
    resume_plan = getattr(candidate, "resume_plan_summary", None)
    recovery_plan = getattr(candidate, "recovery_plan_summary", None)
    plan_summary = resume_plan or recovery_plan or getattr(candidate, "plan_summary", None)
    error = getattr(candidate, "error", None)

    return f"""
<h1>Recovery Candidate</h1>
{_safety_box()}
<table aria-label="Recovery candidate details">
  <tr><th>Run ID</th><td><code>{_escape(run_id)}</code></td></tr>
  <tr><th>Workflow</th><td>{_escape(getattr(candidate, "workflow_name", ""))}</td></tr>
  <tr><th>Status</th><td>{_escape(getattr(candidate, "status", ""))}</td></tr>
  <tr><th>Updated at</th><td>{_escape(_format_time(getattr(candidate, "updated_at", None)))}</td></tr>
  <tr><th>Age seconds</th><td>{_escape(getattr(candidate, "age_seconds", ""))}</td></tr>
  <tr><th>Reasons</th><td>{_escape(reasons)}</td></tr>
  <tr><th>Recommendation</th><td>{_escape(recommendation)}</td></tr>
  <tr><th>Lease present</th><td>{_escape(getattr(candidate, "lease_present", False))}</td></tr>
  <tr><th>Lease owner</th><td>{_escape(getattr(candidate, "lease_owner", ""))}</td></tr>
  <tr><th>Lease expires at</th><td>{_escape(_format_time(getattr(candidate, "lease_expires_at", None)))}</td></tr>
  <tr><th>Lease expired</th><td>{_escape(getattr(candidate, "lease_expired", ""))}</td></tr>
  <tr><th>Resumable</th><td>{_escape(getattr(candidate, "resumable", ""))}</td></tr>
  <tr><th>Plan summary</th><td><code>{_escape(_json_summary(plan_summary))}</code></td></tr>
  <tr><th>Error</th><td><code>{_escape(_json_summary(error))}</code></td></tr>
</table>
<form method="post" action="/admin/recovery/candidates/{_escape(run_id)}/confirm">
  <button type="submit">Review confirmation</button>
</form>
<p><a href="/admin/recovery/history?run_id={_escape(run_id)}">View recovery history for this run</a></p>
"""


def _render_confirm_recovery(candidate: Any, token: str) -> str:
    """Render live recovery confirmation form."""
    run_id = _escape(candidate.run_id)
    return f"""
<h1>Confirm Live Recovery</h1>
{_safety_box()}
<p>You are about to run live recovery for <code>{run_id}</code>.</p>
<table>
  <tr><th>Workflow</th><td>{_escape(candidate.workflow_name)}</td></tr>
  <tr><th>Status</th><td>{_escape(candidate.status)}</td></tr>
  <tr><th>Recommendation</th><td>{_escape(_enum_value(candidate.recommendation))}</td></tr>
  <tr><th>Resumable</th><td>{_badge('Yes' if candidate.resumable else 'No', bool(candidate.resumable))}</td></tr>
</table>
<form method="post" action="/admin/recovery/candidates/{run_id}/recover">
  <input type="hidden" name="confirmation_token" value="{_escape(token)}">
  <label>
    <input type="checkbox" name="confirm_no_dry_run" value="true">
    I understand this performs live recovery with dry_run=False.
  </label>
  <p><button type="submit">Run live recovery</button></p>
</form>
"""


def _render_recovery_result(run_id: str, result: Any) -> str:
    """Render manual recovery result."""
    error = getattr(result, "error", None)
    return f"""
<h1>Recovery Result</h1>
{_safety_box()}
<table>
  <tr><th>Run ID</th><td><code>{_escape(run_id)}</code></td></tr>
  <tr><th>Attempted</th><td>{_badge('Yes' if result.attempted else 'No', bool(result.attempted))}</td></tr>
  <tr><th>Recovered</th><td>{_badge('Yes' if result.recovered else 'No', bool(result.recovered))}</td></tr>
  <tr><th>Status</th><td>{_escape(result.status)}</td></tr>
  <tr><th>Error</th><td>{_escape(error) if error else 'None'}</td></tr>
</table>
<p><a href="/admin/recovery/candidates/{_escape(run_id)}">Back to candidate</a></p>
"""


def _render_history_form() -> str:
    """Render the recovery history lookup form."""
    return """
<h1>Recovery History</h1>
<p>Enter a run ID to view run-scoped recovery history.</p>
<form method="get" action="/admin/recovery/history">
  <label for="run_id">Run ID</label>
  <input id="run_id" name="run_id" type="text" required>
  <button type="submit">View history</button>
</form>
"""


def _render_history(run_id: str, events: list[Any]) -> str:
    """Render recovery audit history for a run."""
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            f"<td>{_escape(_format_time(getattr(event, 'created_at', None)))}</td>"
            f"<td><code>{_escape(getattr(event, 'event_id', ''))}</code></td>"
            f"<td>{_escape(getattr(event, 'event_type', ''))}</td>"
            f"<td>{_escape(getattr(event, 'user_id', ''))}</td>"
            f"<td>{_escape(getattr(event, 'tenant_id', ''))}</td>"
            f"<td>{_escape(getattr(event, 'tool_name', ''))}</td>"
            f"<td><code>{_escape(_json_summary(getattr(event, 'data', {})))}</code></td>"
            "</tr>"
        )
    body = (
        "".join(rows)
        if rows
        else '<tr><td colspan="7">No recovery history events found for this run.</td></tr>'
    )
    return f"""
<h1>Recovery History</h1>
<p>Showing run-scoped recovery history for <code>{_escape(run_id)}</code>.</p>
<form method="get" action="/admin/recovery/history">
  <label for="run_id">Run ID</label>
  <input id="run_id" name="run_id" type="text" value="{_escape(run_id)}" required>
  <button type="submit">View history</button>
</form>
<table aria-label="Recovery history events">
  <tr><th>Created at</th><th>Event ID</th><th>Event type</th><th>User</th><th>Tenant</th><th>Tool</th><th>Data</th></tr>
  {body}
</table>
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
        from fastapi import APIRouter, Depends, HTTPException, Request
    except ImportError as exc:
        raise ImportError(
            "FastAPI is required for the recovery admin console. "
            "Install optional API dependencies with agent-app-framework[api]."
        ) from exc
    globals()["Request"] = Request

    async def _deny_by_default() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    def _server_error() -> Any:
        logger.exception("Recovery admin console request failed")
        return _error_page(
            "Recovery Admin Console Error",
            "Recovery admin operation failed. Check server logs for details.",
            500,
        )

    async def _form_data(request: Request) -> dict[str, str]:
        body = (await request.body()).decode("utf-8")
        if not body:
            return {}
        return {str(key): str(value) for key, value in parse_qsl(body, keep_blank_values=True)}

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
    secret = secrets.token_bytes(32)

    @router.get("")
    async def dashboard() -> Any:
        try:
            status = app.get_recovery_system_status()
            return _html_page("Recovery Admin Console", _render_dashboard(status))
        except Exception:
            return _server_error()

    @router.get("/candidates")
    async def candidates() -> Any:
        try:
            result = await app.run_recovery_scan_once()
            return _html_page("Recovery Candidates", _render_scan_result(result))
        except Exception:
            logger.exception("Recovery UI candidate list failed")
            return _server_error()

    @router.get("/candidates/{run_id}")
    async def inspect_candidate(run_id: str) -> Any:
        try:
            candidate = await app.inspect_recovery_candidate(run_id)
            return _html_page("Recovery Candidate", _render_candidate(candidate))
        except KeyError:
            return _error_page("Candidate Not Found", f"Recovery candidate '{run_id}' was not found.", 404)
        except Exception:
            logger.exception("Recovery UI candidate inspect failed")
            return _server_error()

    @router.get("/history")
    async def history(run_id: str | None = None) -> Any:
        try:
            if not run_id:
                return _html_page("Recovery History", _render_history_form())
            events = await app.get_recovery_history(run_id)
            return _html_page("Recovery History", _render_history(run_id, events))
        except Exception:
            logger.exception("Recovery UI history failed")
            return _server_error()

    @router.post("/scan")
    async def scan(request: Request) -> Any:
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy

        form = await _form_data(request)
        if form.get("dry_run", "true").lower() in {"0", "false", "no", "off"}:
            return _error_page(
                "Invalid Scan Request",
                "The Recovery Admin Console only supports dry-run scans.",
                400,
            )
        try:
            result = await app.run_recovery_scan_once(policy=AutoRecoveryPolicy(dry_run=True))
            return _html_page("Recovery Candidates", _render_scan_result(result))
        except Exception:
            logger.exception("Recovery UI dry-run scan failed")
            return _server_error()

    @router.post("/candidates/{run_id}/confirm")
    async def confirm_recovery(run_id: str) -> Any:
        try:
            candidate = await app.inspect_recovery_candidate(run_id)
            token = _make_confirmation_token(secret, run_id)
            return _html_page("Confirm Live Recovery", _render_confirm_recovery(candidate, token))
        except KeyError:
            return _error_page("Candidate Not Found", f"Recovery candidate '{run_id}' was not found.", 404)
        except Exception:
            logger.exception("Recovery UI confirm recovery failed")
            return _server_error()

    @router.post("/candidates/{run_id}/recover")
    async def recover(run_id: str, request: Request) -> Any:
        form = await _form_data(request)
        token = form.get("confirmation_token", "")
        confirm_no_dry_run = form.get("confirm_no_dry_run", "").lower() == "true"
        if not token or not _valid_confirmation_token(secret, run_id, token):
            return _error_page("Invalid Recovery Confirmation", "A valid confirmation token is required before live recovery.", 400)
        if not confirm_no_dry_run:
            return _error_page("Invalid Recovery Confirmation", "Live recovery requires explicit confirmation that dry_run=False will be used.", 400)
        try:
            result = await app.recover_run(run_id=run_id, dry_run=False)
            return _html_page("Recovery Result", _render_recovery_result(run_id, result))
        except Exception:
            logger.exception("Recovery UI live recovery failed")
            return _server_error()

    return router
