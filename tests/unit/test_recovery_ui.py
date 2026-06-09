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
        resume_plan_summary={"next_action": "resume failed node"},
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

    with pytest.raises(ImportError, match=r"agent-app-framework\[api\]"):
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


def test_candidate_list_runs_dry_run_scan_and_renders_links():
    """Candidate list runs a dry-run scan and links selected candidates."""
    mock_app = _make_mock_app()
    mock_app.run_recovery_scan_once.return_value = RecoveryDaemonTickResult(
        scanned_count=3,
        selected_count=1,
        recovered_count=0,
        skipped_count=1,
        failed_count=1,
        dry_run=True,
        selected_run_ids=["run-selected"],
        skipped=[{"run_id": "run-skip", "reason": "lease_active"}],
        failures=[{"run_id": "run-fail", "error": "boom"}],
    )
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates")

    assert response.status_code == 200
    assert "Recovery Candidates" in response.text
    assert "Dry-run" in response.text
    assert "3" in response.text
    assert "1" in response.text
    assert "/admin/recovery/candidates/run-selected" in response.text
    assert "run-selected" in response.text
    assert "run-skip" in response.text
    assert "lease_active" in response.text
    assert "run-fail" in response.text
    assert "boom" in response.text
    mock_app.run_recovery_scan_once.assert_called_once_with()
    mock_app.recover_run.assert_not_called()


def test_candidate_inspect_renders_details_and_confirm_form():
    """Candidate inspect renders details and a future confirmation form."""
    mock_app = _make_mock_app()
    mock_app.inspect_recovery_candidate.return_value = _make_candidate("run-inspect")
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates/run-inspect")

    assert response.status_code == 200
    assert "Recovery Candidate" in response.text
    assert "run-inspect" in response.text
    assert "customer_support" in response.text
    assert "failed" in response.text
    assert "node_failed" in response.text
    assert "resume" in response.text
    assert "resume failed node" in response.text
    assert 'action="/admin/recovery/candidates/run-inspect/confirm"' in response.text
    mock_app.inspect_recovery_candidate.assert_called_once_with("run-inspect")
    mock_app.recover_run.assert_not_called()


def test_candidate_inspect_missing_candidate_returns_clean_404():
    """Missing candidate inspect returns a clean 404 without leaking internals."""
    mock_app = _make_mock_app()
    mock_app.inspect_recovery_candidate.side_effect = KeyError("secret-token-123")
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/candidates/run-missing")

    assert response.status_code == 404
    assert "Candidate Not Found" in response.text
    assert "run-missing" in response.text
    assert "secret-token-123" not in response.text
    assert "Traceback" not in response.text


def test_history_without_run_id_renders_form_and_message():
    """History page without run_id renders a run-scoped lookup form."""
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/history")

    assert response.status_code == 200
    assert "Recovery History" in response.text
    assert 'method="get"' in response.text
    assert 'name="run_id"' in response.text
    assert "Enter a run ID" in response.text
    mock_app.get_recovery_history.assert_not_called()


def test_history_with_run_id_renders_events():
    """History page with run_id renders run-scoped audit events."""
    mock_app = _make_mock_app()
    event = AuditEvent(
        event_id="event-1",
        run_id="run-hist",
        event_type="recovery.manual",
        user_id="admin-user",
        tenant_id="tenant-1",
        tool_name="recovery",
        data={"action": "inspect", "status": "ok"},
        created_at=datetime(2026, 6, 9, 12, 5, tzinfo=timezone.utc),
    )
    mock_app.get_recovery_history.return_value = [event]
    client = _install_ui_app(mock_app)

    response = client.get("/admin/recovery/history?run_id=run-hist")

    assert response.status_code == 200
    assert "Recovery History" in response.text
    assert "run-hist" in response.text
    assert "event-1" in response.text
    assert "recovery.manual" in response.text
    assert "admin-user" in response.text
    assert "tenant-1" in response.text
    assert "inspect" in response.text
    assert "ok" in response.text
    mock_app.get_recovery_history.assert_called_once_with("run-hist")


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


def test_post_scan_rejects_dry_run_false_without_multipart_parser(monkeypatch):
    """URL-encoded scan forms do not require Starlette's multipart parser."""
    pytest.importorskip("fastapi")
    from starlette.requests import Request

    async def fail_if_form_parser_is_used(self):
        _ = self
        raise RuntimeError("python-multipart is not installed")

    monkeypatch.setattr(Request, "form", fail_if_form_parser_is_used)
    mock_app = _make_mock_app()
    client = _install_ui_app(mock_app)

    response = client.post("/admin/recovery/scan", data={"dry_run": "false"})

    assert response.status_code == 400
    assert "Invalid Scan Request" in response.text
    assert "Traceback" not in response.text
    mock_app.run_recovery_scan_once.assert_not_called()
    mock_app.recover_run.assert_not_called()
