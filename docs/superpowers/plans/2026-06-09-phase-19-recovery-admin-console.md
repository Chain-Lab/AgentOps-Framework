# Phase 19 Recovery Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight, server-rendered Recovery Admin Console on top of the Phase 18 recovery admin APIs with secure-by-default access and explicit live-recovery confirmation.

**Architecture:** Add a new optional FastAPI router module, `agent_app/adapters/recovery_ui.py`, that lazy-imports FastAPI inside `create_recovery_ui_router()`. The router renders HTML with small helper functions, delegates all recovery behavior to existing `AgentApp` methods, and gates live recovery behind an HMAC confirmation token plus an explicit checkbox/field. Existing JSON admin router semantics remain unchanged because `agent_app/adapters/recovery_admin.py` is not modified.

**Tech Stack:** Python 3.10+, FastAPI/Starlette optional dependency, `HTMLResponse`, `pytest`, `fastapi.testclient.TestClient`, Pydantic recovery models, no template engine, no frontend build tooling.

---

## File Structure

- Create: `agent_app/adapters/recovery_ui.py`
  - Optional FastAPI UI router factory.
  - Lazy FastAPI imports only inside `create_recovery_ui_router()`.
  - Router-level admin dependency with deny-by-default fallback.
  - HMAC confirmation token helpers.
  - Plain HTML rendering helpers and route handlers.
  - Generic error pages with server-side logging.

- Create: `tests/unit/test_recovery_ui.py`
  - Import/lazy-dependency tests.
  - Authorization tests.
  - HTML route rendering tests.
  - Dry-run scan and live-scan rejection tests.
  - Manual recovery confirmation flow tests.
  - Missing candidate and exception-hygiene tests.

- Create: `docs/recovery_admin_console.md`
  - Mounting instructions.
  - Security requirements for `admin_dependency`.
  - Safety defaults and confirmation flow.
  - Current limitations.

- Modify: `README.md`
  - Add a short Recovery Admin Console section linking to the dedicated doc.

- Modify: `CHANGELOG.md`
  - Add Phase 19 unreleased entry.

- Modify: `docs/release_checklist_v0.10.md`
  - Add Phase 19 console verification items.

---

## Task 1: Add lazy import and secure-by-default tests

**Files:**
- Create: `tests/unit/test_recovery_ui.py`

- [ ] **Step 1: Write failing tests for lazy imports and auth defaults**

Create `tests/unit/test_recovery_ui.py` with this initial content:

```python
"""Tests for Phase 19 Recovery Admin Console UI router."""

from __future__ import annotations

import builtins
import importlib
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.governance.audit import AuditEvent
from agent_app.runtime.recovery_models import (
    AutoRecoveryPolicy,
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryDaemonTickResult,
    RecoveryRecommendation,
)


def _make_status(**overrides: Any) -> MagicMock:
    status = MagicMock()
    status.enabled = overrides.get("enabled", False)
    status.dry_run = overrides.get("dry_run", True)
    status.daemon_configured = overrides.get("daemon_configured", False)
    status.scanner_available = overrides.get("scanner_available", False)
    status.recovery_service_available = overrides.get("recovery_service_available", False)
    status.last_tick_at = overrides.get("last_tick_at", None)
    status.policy = overrides.get("policy", AutoRecoveryPolicy())
    return status


def _make_candidate(run_id: str = "run-1") -> RecoveryCandidate:
    return RecoveryCandidate(
        run_id=run_id,
        workflow_name="customer_support",
        status="failed",
        updated_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
        age_seconds=120.0,
        reasons=[RecoveryCandidateReason.NODE_FAILED],
        recommendation=RecoveryRecommendation.RESUME,
        lease_present=False,
        resumable=True,
        plan_summary={"next_action": "resume failed node"},
    )


def _make_mock_app() -> MagicMock:
    app = MagicMock()
    app.get_recovery_system_status = MagicMock(return_value=_make_status())
    app.run_recovery_scan_once = AsyncMock(return_value=RecoveryDaemonTickResult(dry_run=True))
    app.inspect_recovery_candidate = AsyncMock(return_value=_make_candidate())
    app.get_recovery_history = AsyncMock(return_value=[])
    result = MagicMock()
    result.run_id = "run-1"
    result.attempted = True
    result.recovered = True
    result.status = "completed"
    result.error = None
    app.recover_run = AsyncMock(return_value=result)
    return app


def _install_ui_app(mock_app: MagicMock | None = None, admin_dependency: Any | None = None):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from agent_app.adapters.recovery_ui import create_recovery_ui_router

    async def allow_admin() -> None:
        return None

    api = fastapi.FastAPI()
    api.include_router(
        create_recovery_ui_router(
            mock_app or _make_mock_app(),
            admin_dependency=admin_dependency if admin_dependency is not None else allow_admin,
        )
    )
    return testclient.TestClient(api)


def test_import_agent_app_does_not_require_fastapi(monkeypatch):
    """Importing agent_app must not import or require FastAPI."""
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("blocked fastapi import")
        return original_import(name, *args, **kwargs)

    sys.modules.pop("agent_app", None)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("agent_app")

    assert module is not None


def test_import_recovery_ui_module_does_not_require_fastapi(monkeypatch):
    """Importing recovery_ui must not require FastAPI until the factory is called."""
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("blocked fastapi import")
        return original_import(name, *args, **kwargs)

    sys.modules.pop("agent_app.adapters.recovery_ui", None)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("agent_app.adapters.recovery_ui")

    assert hasattr(module, "create_recovery_ui_router")


def test_factory_requires_fastapi_at_call_time(monkeypatch):
    """Factory call raises a helpful ImportError if FastAPI is missing."""
    from agent_app.adapters import recovery_ui

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("blocked fastapi import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(ImportError, match="agent-app-framework\[api\]"):
        recovery_ui.create_recovery_ui_router(_make_mock_app())


def test_router_without_dependency_returns_403_for_all_routes():
    """Every UI route denies by default if no admin_dependency is supplied."""
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from agent_app.adapters.recovery_ui import create_recovery_ui_router

    api = fastapi.FastAPI()
    api.include_router(create_recovery_ui_router(_make_mock_app()))
    client = testclient.TestClient(api)

    requests = [
        ("get", "/admin/recovery", {}),
        ("get", "/admin/recovery/candidates", {}),
        ("get", "/admin/recovery/candidates/run-1", {}),
        ("get", "/admin/recovery/history", {}),
        ("post", "/admin/recovery/scan", {}),
        ("post", "/admin/recovery/candidates/run-1/confirm", {}),
        ("post", "/admin/recovery/candidates/run-1/recover", {}),
    ]

    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, path


def test_router_with_deny_dependency_returns_403():
    """A caller-supplied deny dependency is honored by every route."""
    fastapi = pytest.importorskip("fastapi")
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from agent_app.adapters.recovery_ui import create_recovery_ui_router

    async def deny_admin() -> None:
        raise HTTPException(status_code=403, detail="denied")

    api = fastapi.FastAPI()
    api.include_router(create_recovery_ui_router(_make_mock_app(), admin_dependency=deny_admin))
    client = TestClient(api)

    response = client.get("/admin/recovery")

    assert response.status_code == 403
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'agent_app.adapters.recovery_ui'`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/unit/test_recovery_ui.py
git commit -m "test: add recovery UI security import tests"
```

---

## Task 2: Implement router factory, auth dependency, and dashboard page

**Files:**
- Create: `agent_app/adapters/recovery_ui.py`
- Modify: `tests/unit/test_recovery_ui.py`

- [ ] **Step 1: Add a failing dashboard rendering test**

Append this test to `tests/unit/test_recovery_ui.py`:

```python
def test_dashboard_renders_status_and_safety_text():
    """Dashboard renders recovery status and safety statements without side effects."""
    mock_app = _make_mock_app()
    mock_app.get_recovery_system_status.return_value = _make_status(
        enabled=False,
        dry_run=True,
        daemon_configured=True,
        scanner_available=True,
        recovery_service_available=True,
    )
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery")

    assert response.status_code == 200
    assert "Recovery Admin Console" in response.text
    assert "Daemon" in response.text
    assert "Disabled" in response.text
    assert "Dry-run default" in response.text
    assert "Live recovery requires explicit confirmation" in response.text
    assert "Recovery is best-effort" in response.text
    mock_app.get_recovery_system_status.assert_called_once_with()
    mock_app.run_recovery_scan_once.assert_not_called()
    mock_app.recover_run.assert_not_called()
```

- [ ] **Step 2: Run the dashboard test and verify it fails**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py::test_dashboard_renders_status_and_safety_text -q
```

Expected: FAIL because `/admin/recovery` does not exist or `create_recovery_ui_router` is not implemented.

- [ ] **Step 3: Write minimal implementation for imports, auth, helpers, and dashboard**

Create `agent_app/adapters/recovery_ui.py` with this content:

```python
"""Optional FastAPI HTML recovery admin console.

This module is an optional dependency. Install with:

    pip install 'agent-app-framework[api]'

The router is created lazily — importing this module does NOT require FastAPI.
Call ``create_recovery_ui_router(app, admin_dependency=...)`` only when FastAPI is installed.
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
    """Return the display value for enums and plain values."""
    return str(getattr(value, "value", value))


def _badge(label: str, ok: bool) -> str:
    """Render a small status badge."""
    cls = "ok" if ok else "warn"
    return f'<span class="badge {cls}">{_escape(label)}</span>'


def _html_page(title: str, body: str, status_code: int = 200) -> Any:
    """Render a complete HTML response."""
    from fastapi.responses import HTMLResponse

    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
    nav a {{ margin-right: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .badge {{ border-radius: 0.5rem; padding: 0.15rem 0.45rem; font-size: 0.85rem; }}
    .ok {{ background: #d1fae5; color: #065f46; }}
    .warn {{ background: #fee2e2; color: #991b1b; }}
    .safety {{ border-left: 4px solid #d97706; background: #fffbeb; padding: 0.75rem 1rem; }}
    .error {{ border-left: 4px solid #dc2626; background: #fef2f2; padding: 0.75rem 1rem; }}
    code {{ background: #f6f8fa; padding: 0.1rem 0.25rem; }}
    button {{ padding: 0.4rem 0.75rem; }}
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
    """Render a clean error page without exposing raw exception details."""
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
    """Render recovery safety guidance shared by multiple pages."""
    return """
<div class="safety">
  <strong>Safety:</strong>
  The recovery daemon does not auto-start from this console. Dry-run is the default.
  Live recovery requires explicit confirmation. Recovery is best-effort; leases are not exactly-once guarantees.
</div>
"""


def _make_confirmation_token(secret: bytes, run_id: str) -> str:
    """Create a process-local HMAC token for live recovery confirmation."""
    return hmac.new(secret, f"recover:{run_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _valid_confirmation_token(secret: bytes, run_id: str, token: str) -> bool:
    """Validate a process-local HMAC confirmation token."""
    expected = _make_confirmation_token(secret, run_id)
    return hmac.compare_digest(expected, token)


def _render_dashboard(status: Any) -> str:
    """Render recovery system dashboard status."""
    daemon_label = "Enabled" if status.enabled else "Disabled"
    dry_run_label = "Enabled" if status.dry_run else "Disabled"
    scanner_label = "Available" if status.scanner_available else "Unavailable"
    service_label = "Available" if status.recovery_service_available else "Unavailable"
    last_tick = status.last_tick_at.isoformat() if getattr(status, "last_tick_at", None) else "No tick recorded"
    return f"""
<h1>Recovery Admin Console</h1>
{_safety_box()}
<table>
  <tr><th>Daemon</th><td>{_badge(daemon_label, bool(status.enabled))}</td></tr>
  <tr><th>Dry-run default</th><td>{_badge(dry_run_label, bool(status.dry_run))}</td></tr>
  <tr><th>Scanner</th><td>{_badge(scanner_label, bool(status.scanner_available))}</td></tr>
  <tr><th>Recovery service</th><td>{_badge(service_label, bool(status.recovery_service_available))}</td></tr>
  <tr><th>Last tick</th><td>{_escape(last_tick)}</td></tr>
</table>
<ul>
  <li><a href="/admin/recovery/candidates">View dry-run recovery candidates</a></li>
  <li><a href="/admin/recovery/history">View recovery history for a run</a></li>
</ul>
"""


def create_recovery_ui_router(app: Any, admin_dependency: Any | None = None) -> Any:
    """Create an optional FastAPI HTML router for recovery administration.

    The router is secure-by-default: if no *admin_dependency* is supplied,
    all endpoints return HTTP 403. Applications should pass a FastAPI
    dependency that authenticates and authorizes recovery administrators.
    """
    try:
        from fastapi import APIRouter, Depends, HTTPException
    except ImportError as e:
        raise ImportError(
            "FastAPI dependencies are not installed. "
            "Install with: pip install 'agent-app-framework[api]'"
        ) from e

    async def _deny_by_default() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    auth_dependency = admin_dependency or _deny_by_default
    router = APIRouter(
        prefix="/admin/recovery",
        tags=["recovery-ui"],
        dependencies=[Depends(auth_dependency)],
    )
    secret = secrets.token_bytes(32)

    def _server_error() -> Any:
        return _error_page(
            "Recovery Admin Console Error",
            "Recovery admin operation failed. Check server logs for details.",
            500,
        )

    @router.get("")
    async def dashboard() -> Any:
        try:
            status = app.get_recovery_system_status()
            return _html_page("Recovery Admin Console", _render_dashboard(status))
        except Exception:
            logger.exception("Recovery UI dashboard failed")
            return _server_error()

    return router
```

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS for import/auth/dashboard tests.

- [ ] **Step 5: Commit**

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py
git commit -m "feat: add secure recovery UI dashboard"
```

---

## Task 3: Add candidate list, candidate inspect, and history pages

**Files:**
- Modify: `tests/unit/test_recovery_ui.py`
- Modify: `agent_app/adapters/recovery_ui.py`

- [ ] **Step 1: Add failing page rendering tests**

Append these tests to `tests/unit/test_recovery_ui.py`:

```python
def test_candidate_list_runs_dry_run_scan_and_renders_links():
    """Candidate list runs a dry-run scan and links selected run IDs."""
    mock_app = _make_mock_app()
    mock_app.run_recovery_scan_once.return_value = RecoveryDaemonTickResult(
        scanned_count=5,
        selected_count=1,
        recovered_count=0,
        skipped_count=1,
        failed_count=1,
        dry_run=True,
        selected_run_ids=["run-1"],
        skipped=[{"run_id": "run-2", "reason": "inspect_only"}],
        failures=[{"run_id": "run-3", "error": "scan issue"}],
    )
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates")

    assert response.status_code == 200
    assert "Recovery Candidates" in response.text
    assert "Scanned" in response.text
    assert "run-1" in response.text
    assert "/admin/recovery/candidates/run-1" in response.text
    assert "inspect_only" in response.text
    assert "scan issue" in response.text
    assert "Dry-run" in response.text
    mock_app.run_recovery_scan_once.assert_called_once_with()
    mock_app.recover_run.assert_not_called()


def test_candidate_inspect_renders_details_and_confirm_form():
    """Candidate inspect renders details and the confirmation-step form."""
    mock_app = _make_mock_app()
    mock_app.inspect_recovery_candidate.return_value = _make_candidate("run-inspect")
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates/run-inspect")

    assert response.status_code == 200
    assert "run-inspect" in response.text
    assert "customer_support" in response.text
    assert "failed" in response.text
    assert "node_failed" in response.text
    assert "resume" in response.text
    assert "Confirm live recovery" in response.text
    assert "action=\"/admin/recovery/candidates/run-inspect/confirm\"" in response.text
    mock_app.inspect_recovery_candidate.assert_called_once_with("run-inspect")
    mock_app.recover_run.assert_not_called()


def test_candidate_inspect_missing_candidate_returns_clean_404():
    """Missing candidate renders a clean 404 page."""
    mock_app = _make_mock_app()
    mock_app.inspect_recovery_candidate.side_effect = KeyError("secret missing details")
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates/missing-run")

    assert response.status_code == 404
    assert "Candidate Not Found" in response.text
    assert "missing-run" in response.text
    assert "secret missing details" not in response.text
    assert "Traceback" not in response.text


def test_history_without_run_id_renders_form_and_message():
    """History page without run_id explains that history is run-scoped."""
    client = _install_ui_app()

    response = client.get("/admin/recovery/history")

    assert response.status_code == 200
    assert "Recovery History" in response.text
    assert "run-scoped" in response.text
    assert "name=\"run_id\"" in response.text


def test_history_with_run_id_renders_events():
    """History page with run_id renders chronological audit events."""
    mock_app = _make_mock_app()
    mock_app.get_recovery_history.return_value = [
        AuditEvent(
            event_id="evt-1",
            run_id="run-hist",
            event_type="recovery.scan_started",
            created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            user_id="admin",
            tenant_id="default",
            data={"count": 1},
        )
    ]
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/history?run_id=run-hist")

    assert response.status_code == 200
    assert "run-hist" in response.text
    assert "recovery.scan_started" in response.text
    assert "evt-1" in response.text
    assert "admin" in response.text
    mock_app.get_recovery_history.assert_called_once_with("run-hist")
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py::test_candidate_list_runs_dry_run_scan_and_renders_links tests/unit/test_recovery_ui.py::test_candidate_inspect_renders_details_and_confirm_form tests/unit/test_recovery_ui.py::test_candidate_inspect_missing_candidate_returns_clean_404 tests/unit/test_recovery_ui.py::test_history_without_run_id_renders_form_and_message tests/unit/test_recovery_ui.py::test_history_with_run_id_renders_events -q
```

Expected: FAIL with 404 responses for the new routes.

- [ ] **Step 3: Add rendering helpers and GET routes**

Modify `agent_app/adapters/recovery_ui.py` by adding these helper functions after `_render_dashboard()`:

```python
def _render_scan_result(result: Any) -> str:
    """Render a dry-run scan result and selected candidates."""
    selected_links = "".join(
        f'<li><a href="/admin/recovery/candidates/{_escape(run_id)}"><code>{_escape(run_id)}</code></a></li>'
        for run_id in result.selected_run_ids
    ) or "<li>No selected candidates.</li>"
    skipped = "".join(
        f"<li><code>{_escape(item.get('run_id', ''))}</code>: {_escape(item.get('reason', item))}</li>"
        for item in result.skipped
    ) or "<li>No skipped candidates.</li>"
    failures = "".join(
        f"<li><code>{_escape(item.get('run_id', ''))}</code>: {_escape(item.get('error', item))}</li>"
        for item in result.failures
    ) or "<li>No scan failures.</li>"
    return f"""
<h1>Recovery Candidates</h1>
{_safety_box()}
<p><strong>Dry-run scan result:</strong> no recovery execution was performed.</p>
<table>
  <tr><th>Scanned</th><td>{_escape(result.scanned_count)}</td></tr>
  <tr><th>Selected</th><td>{_escape(result.selected_count)}</td></tr>
  <tr><th>Recovered</th><td>{_escape(result.recovered_count)}</td></tr>
  <tr><th>Skipped</th><td>{_escape(result.skipped_count)}</td></tr>
  <tr><th>Failed</th><td>{_escape(result.failed_count)}</td></tr>
  <tr><th>Dry-run</th><td>{_badge('Enabled', bool(result.dry_run))}</td></tr>
</table>
<h2>Selected run IDs</h2>
<ul>{selected_links}</ul>
<h2>Skipped</h2>
<ul>{skipped}</ul>
<h2>Failures</h2>
<ul>{failures}</ul>
<form method="post" action="/admin/recovery/scan">
  <button type="submit">Run dry-run scan</button>
</form>
"""


def _render_candidate(candidate: Any) -> str:
    """Render one recovery candidate inspection page."""
    reasons = "".join(
        f"<li>{_escape(_enum_value(reason))}</li>" for reason in candidate.reasons
    ) or "<li>No reasons recorded.</li>"
    plan_summary = getattr(candidate, "plan_summary", None)
    plan_rows = ""
    if plan_summary:
        plan_rows = "".join(
            f"<tr><th>{_escape(key)}</th><td>{_escape(value)}</td></tr>"
            for key, value in plan_summary.items()
        )
    else:
        plan_rows = "<tr><td colspan=\"2\">No plan summary available.</td></tr>"
    run_id = _escape(candidate.run_id)
    return f"""
<h1>Recovery Candidate <code>{run_id}</code></h1>
{_safety_box()}
<table>
  <tr><th>Run ID</th><td><code>{run_id}</code></td></tr>
  <tr><th>Workflow</th><td>{_escape(candidate.workflow_name)}</td></tr>
  <tr><th>Status</th><td>{_escape(candidate.status)}</td></tr>
  <tr><th>Recommendation</th><td>{_escape(_enum_value(candidate.recommendation))}</td></tr>
  <tr><th>Lease present</th><td>{_badge('Yes' if candidate.lease_present else 'No', not candidate.lease_present)}</td></tr>
  <tr><th>Resumable</th><td>{_badge('Yes' if candidate.resumable else 'No', bool(candidate.resumable))}</td></tr>
</table>
<h2>Reasons</h2>
<ul>{reasons}</ul>
<h2>Plan summary</h2>
<table>{plan_rows}</table>
<form method="post" action="/admin/recovery/candidates/{run_id}/confirm">
  <button type="submit">Confirm live recovery</button>
</form>
"""


def _render_history_form() -> str:
    """Render history page when no run ID is supplied."""
    return """
<h1>Recovery History</h1>
<p>The current recovery history API is run-scoped. Enter a run ID to inspect its events.</p>
<form method="get" action="/admin/recovery/history">
  <label>Run ID <input type="text" name="run_id"></label>
  <button type="submit">View history</button>
</form>
"""


def _render_history(run_id: str, events: list[Any]) -> str:
    """Render recovery audit history for a single run."""
    rows = "".join(
        "<tr>"
        f"<td>{_escape(event.created_at.isoformat() if event.created_at else '')}</td>"
        f"<td>{_escape(event.event_id)}</td>"
        f"<td>{_escape(event.event_type)}</td>"
        f"<td>{_escape(event.user_id)}</td>"
        f"<td>{_escape(event.tenant_id)}</td>"
        f"<td><code>{_escape(event.data)}</code></td>"
        "</tr>"
        for event in events
    ) or "<tr><td colspan=\"6\">No recovery history events found.</td></tr>"
    return f"""
<h1>Recovery History</h1>
<p>Run ID: <code>{_escape(run_id)}</code></p>
<table>
  <tr><th>Created at</th><th>Event ID</th><th>Type</th><th>User</th><th>Tenant</th><th>Data</th></tr>
  {rows}
</table>
"""
```

Then add these routes inside `create_recovery_ui_router()` after `dashboard()`:

```python
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
            return _error_page(
                "Candidate Not Found",
                f"Recovery candidate '{run_id}' was not found.",
                404,
            )
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
```

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS for all current recovery UI tests.

- [ ] **Step 5: Commit**

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py
git commit -m "feat: add recovery UI candidate and history pages"
```

---

## Task 4: Add dry-run scan POST and reject live scan attempts

**Files:**
- Modify: `tests/unit/test_recovery_ui.py`
- Modify: `agent_app/adapters/recovery_ui.py`

- [ ] **Step 1: Add failing POST scan tests**

Append these tests to `tests/unit/test_recovery_ui.py`:

```python
def test_post_scan_always_calls_scan_with_dry_run_policy():
    """POST scan always triggers a dry-run scan with dry_run=True."""
    mock_app = _make_mock_app()
    mock_app.run_recovery_scan_once.return_value = RecoveryDaemonTickResult(
        scanned_count=2,
        selected_count=1,
        dry_run=True,
        selected_run_ids=["run-1"],
    )
    client = _install_ui_app(mock_app)

    response = client.post("/admin/recovery/scan")

    assert response.status_code == 200
    assert "Recovery Candidates" in response.text
    policy = mock_app.run_recovery_scan_once.call_args.kwargs["policy"]
    assert isinstance(policy, AutoRecoveryPolicy)
    assert policy.dry_run is True
    mock_app.recover_run.assert_not_called()


def test_post_scan_rejects_dry_run_false_attempt():
    """UI scan rejects attempts to request live scan semantics."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)

    response = client.post("/admin/recovery/scan", data={"dry_run": "false"})

    assert response.status_code == 400
    assert "Invalid Scan Request" in response.text
    assert "dry-run" in response.text
    assert "Traceback" not in response.text
    mock_app.run_recovery_scan_once.assert_not_called()
    mock_app.recover_run.assert_not_called()
```

- [ ] **Step 2: Run the POST scan tests and verify they fail**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py::test_post_scan_always_calls_scan_with_dry_run_policy tests/unit/test_recovery_ui.py::test_post_scan_rejects_dry_run_false_attempt -q
```

Expected: FAIL with 405 or 404 because the POST route is not implemented.

- [ ] **Step 3: Add form parsing helper and POST scan route**

Modify the factory import inside `create_recovery_ui_router()`:

```python
        from fastapi import APIRouter, Depends, HTTPException, Request
```

Add this helper inside `create_recovery_ui_router()` after `_server_error()`:

```python
    async def _form_data(request: Request) -> dict[str, str]:
        form = await request.form()
        return {str(key): str(value) for key, value in form.items()}
```

Add this route inside `create_recovery_ui_router()` after the GET `/candidates` route:

```python
    @router.post("/scan")
    async def scan(request: Request) -> Any:
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy

        try:
            form = await _form_data(request)
            if form.get("dry_run", "true").lower() in {"0", "false", "no", "off"}:
                return _error_page(
                    "Invalid Scan Request",
                    "The Recovery Admin Console only supports dry-run scans.",
                    400,
                )
            result = await app.run_recovery_scan_once(policy=AutoRecoveryPolicy(dry_run=True))
            return _html_page("Recovery Candidates", _render_scan_result(result))
        except Exception:
            logger.exception("Recovery UI dry-run scan failed")
            return _server_error()
```

- [ ] **Step 4: Fix the invalid scan error path if needed**

If `test_post_scan_rejects_dry_run_false_attempt` returns 500, move the `dry_run=false` check outside the broad `except Exception` block by using this exact route body instead:

```python
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
```

- [ ] **Step 5: Run the full UI test file and verify it passes**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py
git commit -m "feat: add recovery UI dry-run scan action"
```

---

## Task 5: Add live recovery confirmation token flow

**Files:**
- Modify: `tests/unit/test_recovery_ui.py`
- Modify: `agent_app/adapters/recovery_ui.py`

- [ ] **Step 1: Add failing confirmation flow tests**

Append these tests to `tests/unit/test_recovery_ui.py`:

```python
def _extract_confirmation_token(html: str) -> str:
    marker = 'name="confirmation_token" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def test_confirm_page_renders_confirmation_token_without_recovery():
    """Confirm step renders an HMAC token and does not execute recovery."""
    mock_app = _make_mock_app()
    mock_app.inspect_recovery_candidate.return_value = _make_candidate("run-confirm")
    client = _install_ui_app(mock_app)

    response = client.post("/admin/recovery/candidates/run-confirm/confirm")

    assert response.status_code == 200
    assert "Confirm Live Recovery" in response.text
    assert "confirmation_token" in response.text
    assert "confirm_no_dry_run" in response.text
    assert _extract_confirmation_token(response.text)
    mock_app.inspect_recovery_candidate.assert_called_once_with("run-confirm")
    mock_app.recover_run.assert_not_called()


def test_recover_without_token_is_rejected():
    """Recover step rejects missing confirmation tokens."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)

    response = client.post(
        "/admin/recovery/candidates/run-1/recover",
        data={"confirm_no_dry_run": "true"},
    )

    assert response.status_code == 400
    assert "Invalid Recovery Confirmation" in response.text
    mock_app.recover_run.assert_not_called()


def test_recover_with_invalid_token_is_rejected():
    """Recover step rejects invalid confirmation tokens."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)

    response = client.post(
        "/admin/recovery/candidates/run-1/recover",
        data={"confirmation_token": "bad-token", "confirm_no_dry_run": "true"},
    )

    assert response.status_code == 400
    assert "Invalid Recovery Confirmation" in response.text
    mock_app.recover_run.assert_not_called()


def test_recover_without_explicit_checkbox_is_rejected():
    """Recover step requires confirm_no_dry_run=true as an explicit live action."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)
    confirm_response = client.post("/admin/recovery/candidates/run-1/confirm")
    token = _extract_confirmation_token(confirm_response.text)

    response = client.post(
        "/admin/recovery/candidates/run-1/recover",
        data={"confirmation_token": token},
    )

    assert response.status_code == 400
    assert "explicit confirmation" in response.text
    mock_app.recover_run.assert_not_called()


def test_confirmed_recovery_calls_recover_run_no_dry_run():
    """Valid token plus checkbox calls app.recover_run with dry_run=False."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)
    confirm_response = client.post("/admin/recovery/candidates/run-1/confirm")
    token = _extract_confirmation_token(confirm_response.text)

    response = client.post(
        "/admin/recovery/candidates/run-1/recover",
        data={"confirmation_token": token, "confirm_no_dry_run": "true"},
    )

    assert response.status_code == 200
    assert "Recovery Result" in response.text
    assert "run-1" in response.text
    mock_app.recover_run.assert_called_once_with(run_id="run-1", dry_run=False)
```

- [ ] **Step 2: Run the confirmation flow tests and verify they fail**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py::test_confirm_page_renders_confirmation_token_without_recovery tests/unit/test_recovery_ui.py::test_recover_without_token_is_rejected tests/unit/test_recovery_ui.py::test_recover_with_invalid_token_is_rejected tests/unit/test_recovery_ui.py::test_recover_without_explicit_checkbox_is_rejected tests/unit/test_recovery_ui.py::test_confirmed_recovery_calls_recover_run_no_dry_run -q
```

Expected: FAIL with 404/405 responses because confirmation routes are not implemented.

- [ ] **Step 3: Add confirmation and recovery rendering helpers**

Add these helper functions after `_render_candidate()` in `agent_app/adapters/recovery_ui.py`:

```python
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
```

- [ ] **Step 4: Add confirm and recover routes**

Add these routes inside `create_recovery_ui_router()` after `inspect_candidate()`:

```python
    @router.post("/candidates/{run_id}/confirm")
    async def confirm_recovery(run_id: str) -> Any:
        try:
            candidate = await app.inspect_recovery_candidate(run_id)
            token = _make_confirmation_token(secret, run_id)
            return _html_page("Confirm Live Recovery", _render_confirm_recovery(candidate, token))
        except KeyError:
            return _error_page(
                "Candidate Not Found",
                f"Recovery candidate '{run_id}' was not found.",
                404,
            )
        except Exception:
            logger.exception("Recovery UI confirm recovery failed")
            return _server_error()

    @router.post("/candidates/{run_id}/recover")
    async def recover(run_id: str, request: Request) -> Any:
        form = await _form_data(request)
        token = form.get("confirmation_token", "")
        confirm_no_dry_run = form.get("confirm_no_dry_run", "").lower() == "true"
        if not token or not _valid_confirmation_token(secret, run_id, token):
            return _error_page(
                "Invalid Recovery Confirmation",
                "A valid confirmation token is required before live recovery.",
                400,
            )
        if not confirm_no_dry_run:
            return _error_page(
                "Invalid Recovery Confirmation",
                "Live recovery requires explicit confirmation that dry_run=False will be used.",
                400,
            )
        try:
            result = await app.recover_run(run_id=run_id, dry_run=False)
            return _html_page("Recovery Result", _render_recovery_result(run_id, result))
        except Exception:
            logger.exception("Recovery UI live recovery failed")
            return _server_error()
```

- [ ] **Step 5: Run the full UI test file and verify it passes**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py
git commit -m "feat: add recovery UI live confirmation flow"
```

---

## Task 6: Add generic 500 error hygiene regression coverage

**Files:**
- Modify: `tests/unit/test_recovery_ui.py`
- Modify: `agent_app/adapters/recovery_ui.py` only if the tests reveal leaked details

- [ ] **Step 1: Add failing exception hygiene tests**

Append these tests to `tests/unit/test_recovery_ui.py`:

```python
def test_dashboard_failure_returns_generic_500_without_traceback():
    """Dashboard exceptions are logged server-side and hidden from HTML."""
    mock_app = _make_mock_app()
    mock_app.get_recovery_system_status.side_effect = RuntimeError("secret database password")
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery")

    assert response.status_code == 500
    assert "Recovery admin operation failed" in response.text
    assert "secret database password" not in response.text
    assert "Traceback" not in response.text


def test_recovery_failure_returns_generic_500_without_traceback():
    """Live recovery exceptions do not leak raw exception strings."""
    mock_app = _make_mock_app()
    mock_app.recover_run.side_effect = RuntimeError("secret lease backend token")
    client = _install_ui_app(mock_app)
    confirm_response = client.post("/admin/recovery/candidates/run-1/confirm")
    token = _extract_confirmation_token(confirm_response.text)

    response = client.post(
        "/admin/recovery/candidates/run-1/recover",
        data={"confirmation_token": token, "confirm_no_dry_run": "true"},
    )

    assert response.status_code == 500
    assert "Recovery admin operation failed" in response.text
    assert "secret lease backend token" not in response.text
    assert "Traceback" not in response.text
```

- [ ] **Step 2: Run the exception hygiene tests and verify they pass or fail for the right reason**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py::test_dashboard_failure_returns_generic_500_without_traceback tests/unit/test_recovery_ui.py::test_recovery_failure_returns_generic_500_without_traceback -q
```

Expected: PASS if Task 2 and Task 5 already used `_server_error()` consistently. If either test fails because raw exception details appear in `response.text`, update that route to catch `Exception`, call `logger.exception(...)`, and return `_server_error()`.

- [ ] **Step 3: Run focused UI suite**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py
git commit -m "test: cover recovery UI error hygiene"
```

---

## Task 7: Document Recovery Admin Console usage and limitations

**Files:**
- Create: `docs/recovery_admin_console.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/release_checklist_v0.10.md`

- [ ] **Step 1: Create dedicated documentation**

Create `docs/recovery_admin_console.md`:

```markdown
# Recovery Admin Console

Phase 19 adds an optional, server-rendered HTML console for recovery observability and carefully gated manual recovery actions.

## Requirements

The console is part of the optional FastAPI integration:

```bash
pip install 'agent-app-framework[api]'
```

Importing `agent_app` and importing `agent_app.adapters.recovery_ui` do not require FastAPI. FastAPI is imported only when `create_recovery_ui_router()` is called.

## Mounting the UI router

```python
from fastapi import Depends, FastAPI, HTTPException

from agent_app.adapters.recovery_ui import create_recovery_ui_router
from agent_app.config.loader import build_app

agent_app = build_app("agentapp.yaml")
api = FastAPI()

async def require_recovery_admin() -> None:
    # Replace this example with your real authentication and authorization check.
    authorized = False
    if not authorized:
        raise HTTPException(status_code=403, detail="Forbidden")

api.include_router(
    create_recovery_ui_router(
        agent_app,
        admin_dependency=require_recovery_admin,
    )
)
```

`admin_dependency` is required for access. If it is omitted, every UI route returns `403 Forbidden` by default.

## Routes

```http
GET  /admin/recovery
GET  /admin/recovery/candidates
GET  /admin/recovery/candidates/{run_id}
GET  /admin/recovery/history
POST /admin/recovery/scan
POST /admin/recovery/candidates/{run_id}/confirm
POST /admin/recovery/candidates/{run_id}/recover
```

GET routes are read-only. No GET route performs recovery or any other mutating operation.

## Safety model

- The recovery daemon remains default-off and is not started by the UI router.
- Dry-run remains the default for recovery scans.
- `POST /admin/recovery/scan` always uses `AutoRecoveryPolicy(dry_run=True)`.
- Requests that attempt `dry_run=false` for UI scans return `400 Bad Request`.
- Live recovery requires two POSTs:
  1. `POST /admin/recovery/candidates/{run_id}/confirm` renders the candidate and a process-local confirmation token.
  2. `POST /admin/recovery/candidates/{run_id}/recover` requires the token and `confirm_no_dry_run=true` before calling `app.recover_run(run_id=run_id, dry_run=False)`.

The confirmation token is an HMAC over `recover:{run_id}` using a secret generated when the router is created. Tokens are valid for the lifetime of that router process. Authorization remains the responsibility of `admin_dependency`.

## Error handling

The console renders clean error pages:

- `403 Forbidden` is handled by the FastAPI dependency.
- Missing candidates render `404 Candidate Not Found`.
- Invalid scan or recovery confirmation requests render `400` pages.
- Unexpected failures render a generic `500` page and log details server-side.

Raw exception strings and tracebacks are not returned in HTML responses.

## Limitations

- The console stores no UI state in a database.
- There is no React, Vite, Node, template engine, or frontend build pipeline.
- Recovery is best-effort; leases are not exactly-once guarantees.
- Recovery history is currently run-scoped. The history page asks for a run ID before rendering events.
- The JSON admin router and UI router are mounted explicitly by applications. Phase 19 does not auto-mount either router.
```

- [ ] **Step 2: Update README**

Add this section near the recovery/admin documentation area in `README.md`:

```markdown
### Recovery Admin Console

The optional FastAPI integration includes a lightweight server-rendered Recovery Admin Console for recovery visibility and gated manual recovery actions.

```python
from agent_app.adapters.recovery_ui import create_recovery_ui_router

api.include_router(create_recovery_ui_router(app, admin_dependency=require_recovery_admin))
```

The router is secure by default: omitting `admin_dependency` returns `403 Forbidden` for every UI route. The console preserves recovery safety defaults: the daemon remains default-off, scans are dry-run by default, and live recovery requires a confirmation token plus an explicit `confirm_no_dry_run=true` field.

See [docs/recovery_admin_console.md](docs/recovery_admin_console.md) for mounting instructions, safety behavior, and limitations.
```

- [ ] **Step 3: Update CHANGELOG**

Add this bullet under the current unreleased/v0.10 section in `CHANGELOG.md`:

```markdown
- Added Phase 19 Recovery Admin Console: an optional server-rendered FastAPI UI router with secure-by-default admin dependency handling, dry-run candidate scans, run-scoped history views, and a two-step HMAC confirmation flow for live recovery.
```

- [ ] **Step 4: Update release checklist**

Add these checks to `docs/release_checklist_v0.10.md` under the Phase 19 or recovery verification section:

```markdown
- [ ] Verify `tests/unit/test_recovery_ui.py` passes.
- [ ] Verify importing `agent_app.adapters.recovery_ui` does not require FastAPI until `create_recovery_ui_router()` is called.
- [ ] Verify the Recovery Admin Console denies all routes when `admin_dependency` is omitted.
- [ ] Verify UI scans remain dry-run and reject `dry_run=false` attempts.
- [ ] Verify live recovery requires confirmation token plus `confirm_no_dry_run=true`.
- [ ] Verify `docs/recovery_admin_console.md` documents mounting, safety defaults, best-effort recovery, and current limitations.
```

- [ ] **Step 5: Run doc-adjacent tests**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md docs/release_checklist_v0.10.md docs/recovery_admin_console.md
git commit -m "docs: document recovery admin console"
```

---

## Task 8: Final regression verification

**Files:**
- No code changes unless verification exposes a defect.

- [ ] **Step 1: Run Phase 18 CLI baseline**

Run:

```bash
python -m pytest tests/unit/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run recovery admin/CLI/daemon baseline**

Run:

```bash
python -m pytest tests/unit/test_recovery_admin.py tests/unit/test_recovery_cli.py tests/unit/test_recovery_daemon.py -q
```

Expected: PASS.

- [ ] **Step 3: Run Phase 19 UI tests**

Run:

```bash
python -m pytest tests/unit/test_recovery_ui.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full test suite**

Run:

```bash
python -m pytest -q
```

Expected: PASS. If failures appear, classify each failure before fixing:

- Phase 19 regression caused by `recovery_ui.py` or tests.
- Pre-existing unrelated failure.
- Environment/optional dependency skip issue.

For Phase 19 regressions, write a failing regression test first, verify it fails, implement the minimal fix, and rerun the failing test plus the full suite.

- [ ] **Step 5: Inspect git diff for accidental JSON API changes**

Run:

```bash
git diff -- agent_app/adapters/recovery_admin.py agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py README.md CHANGELOG.md docs/release_checklist_v0.10.md docs/recovery_admin_console.md
```

Expected:

- `agent_app/adapters/recovery_admin.py` has no Phase 19 changes.
- `agent_app/adapters/recovery_ui.py` contains the new HTML router only.
- Tests and docs match the Phase 19 design.

- [ ] **Step 6: Commit final verification note if any files changed during fixes**

Only run this if Step 4 required code or doc fixes:

```bash
git add agent_app/adapters/recovery_ui.py tests/unit/test_recovery_ui.py README.md CHANGELOG.md docs/release_checklist_v0.10.md docs/recovery_admin_console.md
git commit -m "fix: finalize recovery admin console verification"
```

---

## Self-Review

### Spec coverage

- Optional `agent_app/adapters/recovery_ui.py` module: Tasks 2-5.
- Lazy FastAPI import: Task 1 tests and Task 2 implementation.
- Secure-by-default `admin_dependency`: Task 1 tests and Task 2 implementation.
- HTML route surface: Tasks 2-5.
- Dashboard status and safety statements: Task 2.
- Candidate list dry-run scan: Task 3 GET route and Task 4 POST route.
- Candidate inspect page and 404 handling: Task 3.
- Run-scoped history page: Task 3.
- Dry-run scan rejecting `dry_run=false`: Task 4.
- Two-step live recovery confirmation: Task 5.
- HMAC token with process-local secret: Task 2 helpers and Task 5 routes.
- Generic 500 pages without raw exceptions: Task 6.
- Documentation: Task 7.
- Regression commands: Task 8.

### Placeholder scan

This plan contains no `TBD`, no `TODO`, and no incomplete implementation steps. Each code-changing task includes concrete test code, implementation code, commands, and expected outcomes.

### Type and API consistency

- `create_recovery_ui_router(app: Any, admin_dependency: Any | None = None) -> Any` matches the optional FastAPI pattern used by `create_recovery_admin_router()`.
- UI routes call existing `AgentApp` methods: `get_recovery_system_status()`, `run_recovery_scan_once()`, `inspect_recovery_candidate()`, `get_recovery_history()`, and `recover_run()`.
- Live recovery calls `app.recover_run(run_id=run_id, dry_run=False)` exactly as required by the Phase 19 design.
- Scan POST calls `app.run_recovery_scan_once(policy=AutoRecoveryPolicy(dry_run=True))` exactly as required by the Phase 19 design.
