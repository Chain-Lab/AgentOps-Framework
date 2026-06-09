"""Tests for Phase 16.5 RecoveryService.

Tests cover:
  - recover_run acquires lease before resume
  - recover_run refuses active lease
  - recover_run calls app.resume_workflow_run
  - recover_run releases lease on success
  - recover_run releases lease on failure
  - recover_run records audit events
  - recover_run returns clear error when no state_store
  - recover_run returns clear error when no lease_backend
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.runtime.dag_run_state import (
    LeasePolicy,
    NodeRunStatus,
    RecoveryPlan,
    WorkflowRunLease,
    WorkflowRunState,
    WorkflowRunStatus,
)
from agent_app.runtime.lease_backend import (
    LeaseAcquireResult,
    WorkflowLeaseBackend,
)
from agent_app.runtime.recovery_models import (
    ManualRecoveryResult,
    RecoveryCandidate,
    RecoveryScanConfig,
    RecoveryRecommendation,
)
from agent_app.runtime.recovery_scanner import RecoveryScanner
from agent_app.runtime.recovery_service import RecoveryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store():
    from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore
    return InMemoryWorkflowStateStore()


def _make_run(
    run_id: str = "wr-1",
    status: str = WorkflowRunStatus.FAILED.value,
) -> WorkflowRunState:
    now = datetime.now(timezone.utc)
    return WorkflowRunState(
        run_id=run_id,
        workflow_name="test_dag",
        status=status,
        input="test",
        started_at=now - timedelta(hours=1),
        updated_at=now,
    )


class FakeLeaseBackend:
    """Fake lease backend for service tests."""

    def __init__(self):
        self._leases: dict[str, WorkflowRunLease] = {}
        self.acquire_calls: list[dict] = []
        self.release_calls: list[dict] = []
        self._acquire_deny: str | None = None  # If set, deny acquires with this owner

    def set_acquire_deny(self, owner: str) -> None:
        self._acquire_deny = owner

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: Any,
        policy: Any = None,
    ) -> Any:
        self.acquire_calls.append({"run_id": run_id, "worker_id": worker.worker_id})
        if self._acquire_deny is not None:
            return LeaseAcquireResult(
                acquired=False,
                run_id=run_id,
                owner_id=worker.worker_id,
                reason=f"Lease held by '{self._acquire_deny}'",
                current_owner_id=self._acquire_deny,
            )
        now = datetime.now(timezone.utc)
        lease = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=now,
            expires_at=now + timedelta(minutes=30),
        )
        self._leases[run_id] = lease
        return LeaseAcquireResult(
            acquired=True,
            run_id=run_id,
            owner_id=worker.worker_id,
            lease=lease,
        )

    async def renew_run_lease(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def release_run_lease(
        self,
        run_id: str,
        worker: Any,
    ) -> Any:
        self.release_calls.append({"run_id": run_id, "worker_id": worker.worker_id})
        lease = self._leases.get(run_id)
        if lease:
            lease.released_at = datetime.now(timezone.utc)
        return lease  # type: ignore[return-value]

    async def get_run_lease(self, run_id: str) -> WorkflowRunLease | None:
        return self._leases.get(run_id)

    async def list_expired_leases(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def health_check(self) -> Any:
        return None

    def diagnostics(self) -> Any:
        return None


class FakeAuditLogger:
    """Fake audit logger that records events."""

    def __init__(self):
        self.events: list[Any] = []

    async def log(self, event: Any) -> None:
        self.events.append(event)


def _make_app(state_store: Any, lease_backend: Any) -> MagicMock:
    """Create a mock AgentApp with the given state store and lease backend."""
    app = MagicMock()
    app._dag_state_store = state_store
    app._dag_lease_backend = lease_backend
    app._audit_logger = None

    # Mock resume_workflow_run to return a successful result
    mock_result = MagicMock()
    mock_result.status = "completed"
    app.resume_workflow_run = AsyncMock(return_value=mock_result)

    return app


# ---------------------------------------------------------------------------
# RecoveryService tests
# ---------------------------------------------------------------------------


class TestRecoveryServiceLeaseLifecycle:
    """recover_run acquires and releases lease."""

    @pytest.mark.asyncio
    async def test_acquire_lease_before_resume(self):
        store = _make_store()
        run = _make_run("wr-lease", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        app = _make_app(store, lease_backend)

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-lease",
            recovered_by="operator-1",
        )

        assert result.lease_acquired is True
        assert len(lease_backend.acquire_calls) == 1
        assert lease_backend.acquire_calls[0]["run_id"] == "wr-lease"
        assert lease_backend.acquire_calls[0]["worker_id"] == "operator-1"

    @pytest.mark.asyncio
    async def test_release_lease_on_success(self):
        store = _make_store()
        run = _make_run("wr-rel-s", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        app = _make_app(store, lease_backend)

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-rel-s",
            recovered_by="operator-1",
        )

        assert result.lease_released is True
        assert len(lease_backend.release_calls) == 1
        assert lease_backend.release_calls[0]["run_id"] == "wr-rel-s"

    @pytest.mark.asyncio
    async def test_release_lease_on_failure(self):
        store = _make_store()
        run = _make_run("wr-rel-f", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        # Make resume fail
        app = _make_app(store, lease_backend)
        app.resume_workflow_run = AsyncMock(side_effect=RuntimeError("resume boom"))

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-rel-f",
            recovered_by="operator-1",
        )

        assert result.attempted is True
        assert result.recovered is False
        # Lease should still be released
        assert result.lease_acquired is True
        assert len(lease_backend.release_calls) == 1


class TestRecoveryServiceActiveLease:
    """Active lease blocks recovery."""

    @pytest.mark.asyncio
    async def test_refuses_active_lease(self):
        store = _make_store()
        run = _make_run("wr-active", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        lease_backend.set_acquire_deny("other-worker")
        app = _make_app(store, lease_backend)

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-active",
            recovered_by="operator-1",
        )

        assert result.attempted is False
        assert result.lease_acquired is False
        # resume should NOT have been called
        app.resume_workflow_run.assert_not_called()
        assert result.error is not None
        assert "denied" in result.error.get("type", "") or "lease" in result.error.get("type", "")


class TestRecoveryServiceCallsResume:
    """recover_run calls app.resume_workflow_run."""

    @pytest.mark.asyncio
    async def test_calls_resume(self):
        store = _make_store()
        run = _make_run("wr-resume", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        app = _make_app(store, lease_backend)

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        await service.recover_run(
            workflow="test_dag",
            run_id="wr-resume",
            recovered_by="operator-1",
        )

        app.resume_workflow_run.assert_called_once()
        call_kwargs = app.resume_workflow_run.call_args.kwargs
        assert call_kwargs["workflow"] == "test_dag"
        assert call_kwargs["run_id"] == "wr-resume"


class TestRecoveryServiceAudit:
    """recover_run records audit events."""

    @pytest.mark.asyncio
    async def test_audit_events_recorded(self):
        from datetime import timedelta
        store = _make_store()
        # Use a stale running run — this is resumable and triggers full recovery
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        run = _make_run("wr-audit", WorkflowRunStatus.RUNNING.value)
        run.updated_at = old_time
        await store.create_run(run)

        lease_backend = FakeLeaseBackend()
        audit = FakeAuditLogger()
        app = _make_app(store, lease_backend)
        app._audit_logger = audit

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
            audit_logger=audit,
        )

        await service.recover_run(
            workflow="test_dag",
            run_id="wr-audit",
            recovered_by="operator-1",
        )

        event_types = [e.event_type for e in audit.events]
        assert "recovery.started" in event_types
        assert "recovery.completed" in event_types


class TestRecoveryServiceNoDeps:
    """recover_run returns clear errors when deps are missing."""

    @pytest.mark.asyncio
    async def test_no_state_store(self):
        from agent_app.runtime.recovery_service import RecoveryService

        lease_backend = FakeLeaseBackend()
        app = MagicMock()
        app._dag_state_store = None
        app._dag_lease_backend = lease_backend

        service = RecoveryService(
            app=app,
            state_store=None,  # type: ignore
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-x",
            recovered_by="op",
        )

        assert result.attempted is False
        assert result.error is not None
        assert "state store" in result.error.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_no_lease_backend(self):
        from agent_app.runtime.recovery_service import RecoveryService

        store = _make_store()
        app = MagicMock()
        app._dag_state_store = store
        app._dag_lease_backend = None

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=None,  # type: ignore
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="wr-x",
            recovered_by="op",
        )

        assert result.attempted is False
        assert result.error is not None
        assert "lease backend" in result.error.get("message", "").lower()


class TestRecoveryServiceMissingRun:
    """recover_run handles missing run gracefully."""

    @pytest.mark.asyncio
    async def test_missing_run(self):
        store = _make_store()
        lease_backend = FakeLeaseBackend()
        app = _make_app(store, lease_backend)

        service = RecoveryService(
            app=app,
            state_store=store,
            lease_backend=lease_backend,
        )

        result = await service.recover_run(
            workflow="test_dag",
            run_id="nonexistent",
            recovered_by="operator-1",
        )

        assert result.attempted is False
        assert result.error is not None
        assert "not found" in result.error.get("message", "").lower()
