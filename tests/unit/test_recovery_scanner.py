"""Tests for Phase 16.5 RecoveryScanner.

Tests cover:
  - stale running run becomes candidate
  - active lease leads to WAIT_FOR_ACTIVE_LEASE
  - expired lease leads to RESUME recommendation
  - failed resumable run leads to RESUME recommendation
  - compensation started leads to MANUAL_REVIEW
  - completed run excluded by default
  - completed run included when include_completed=True
  - scan respects limit
  - inspect_run returns candidate
  - inspect missing run returns clear error
  - scanner with no lease backend
  - scanner is read-only
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.runtime.dag_run_state import (
    CompensationExecutionState,
    CompensationRunStatus,
    LeasePolicy,
    LeaseStatus,
    NodeExecutionState,
    NodeRunStatus,
    RecoveryPlan,
    WorkerIdentity,
    WorkflowRunLease,
    WorkflowRunState,
    WorkflowRunStatus,
)
from agent_app.runtime.lease_backend import WorkflowLeaseBackend
from agent_app.runtime.recovery_models import (
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryRecommendation,
    RecoveryScanConfig,
    RecoveryScanResult,
)
from agent_app.runtime.recovery_scanner import RecoveryScanner, _now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store():
    from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore
    return InMemoryWorkflowStateStore()


def _make_run(
    run_id: str = "wr-1",
    status: str = WorkflowRunStatus.RUNNING.value,
    workflow_name: str = "test_dag",
    updated_at: datetime | None = None,
) -> WorkflowRunState:
    now = updated_at or _now()
    return WorkflowRunState(
        run_id=run_id,
        workflow_name=workflow_name,
        status=status,
        input="test",
        started_at=now - timedelta(hours=1),
        updated_at=now,
    )


def _make_node(
    run_id: str,
    node_id: str,
    status: str = NodeRunStatus.COMPLETED.value,
) -> NodeExecutionState:
    return NodeExecutionState(
        run_id=run_id,
        node_id=node_id,
        node_type="agent",
        status=status,
        input={},
        output="done" if status == NodeRunStatus.COMPLETED.value else None,
    )


def _make_compensation(
    run_id: str,
    node_id: str,
    status: str = CompensationRunStatus.COMPLETED.value,
) -> CompensationExecutionState:
    return CompensationExecutionState(
        run_id=run_id,
        node_id=node_id,
        handler_name="rollback",
        status=status,
    )


def _make_lease(
    run_id: str,
    owner_id: str = "worker-1",
    expired: bool = False,
) -> WorkflowRunLease:
    now = _now()
    if expired:
        expires_at = now - timedelta(minutes=5)
    else:
        expires_at = now + timedelta(minutes=30)
    return WorkflowRunLease(
        run_id=run_id,
        owner_id=owner_id,
        acquired_at=now - timedelta(minutes=10),
        expires_at=expires_at,
        released_at=None,
    )


class FakeLeaseBackend:
    """Fake lease backend for scanner tests."""

    def __init__(self, leases: dict[str, WorkflowRunLease | None] | None = None):
        self._leases = leases or {}

    async def acquire_run_lease(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def renew_run_lease(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def release_run_lease(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def get_run_lease(self, run_id: str) -> WorkflowRunLease | None:
        return self._leases.get(run_id)

    async def list_expired_leases(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def health_check(self) -> Any:
        return None

    def diagnostics(self) -> Any:
        return None


# ---------------------------------------------------------------------------
# Scanner tests
# ---------------------------------------------------------------------------


class TestScannerStaleRunning:
    """A stale running run (no lease) should become a RESUME candidate."""

    @pytest.mark.asyncio
    async def test_stale_running_no_lease(self):
        store = _make_store()
        # Create a run that was updated 600s ago (stale threshold is 300s)
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-stale", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.run_id == "wr-stale"
        assert c.age_seconds is not None
        assert c.age_seconds > 300
        assert RecoveryCandidateReason.RUN_STALE in c.reasons
        assert c.recommendation == RecoveryRecommendation.RESUME

    @pytest.mark.asyncio
    async def test_fresh_running_not_stale(self):
        store = _make_store()
        run = _make_run("wr-fresh", WorkflowRunStatus.RUNNING.value)
        await store.create_run(run)

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        # Fresh running run should NOT be a candidate
        assert result.candidate_count == 0


class TestScannerActiveLease:
    """Active lease should block recovery (WAIT_FOR_ACTIVE_LEASE)."""

    @pytest.mark.asyncio
    async def test_active_lease_blocks_recovery(self):
        store = _make_store()
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-lease", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        lease = _make_lease("wr-lease", owner_id="worker-active", expired=False)
        lease_backend = FakeLeaseBackend({"wr-lease": lease})

        scanner = RecoveryScanner(store, lease_backend=lease_backend)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.lease_present is True
        assert c.lease_owner == "worker-active"
        assert c.lease_expired is False
        assert c.recommendation == RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE


class TestScannerExpiredLease:
    """Expired lease should allow RESUME."""

    @pytest.mark.asyncio
    async def test_expired_lease_allows_resume(self):
        store = _make_store()
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-expired", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        lease = _make_lease("wr-expired", owner_id="worker-old", expired=True)
        lease_backend = FakeLeaseBackend({"wr-expired": lease})

        scanner = RecoveryScanner(store, lease_backend=lease_backend)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.lease_expired is True
        assert RecoveryCandidateReason.LEASE_EXPIRED in c.reasons
        assert c.recommendation == RecoveryRecommendation.RESUME


class TestScannerFailedResumable:
    """Failed runs with failed nodes are not resumable (require manual review)."""

    @pytest.mark.asyncio
    async def test_failed_with_failed_nodes_not_resumable(self):
        store = _make_store()
        run = _make_run("wr-failed", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)
        # Add a failed node
        await store.upsert_node(_make_node("wr-failed", "n1", NodeRunStatus.FAILED.value))

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan()

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.status == "failed"
        assert RecoveryCandidateReason.NODE_FAILED in c.reasons
        # Failed nodes make the run not resumable
        assert c.resumable is False
        assert c.recommendation == RecoveryRecommendation.DO_NOT_RESUME


class TestScannerCompensationStarted:
    """Compensation started should lead to MANUAL_REVIEW."""

    @pytest.mark.asyncio
    async def test_compensation_incomplete(self):
        store = _make_store()
        run = _make_run("wr-comp", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)
        await store.upsert_node(_make_node("wr-comp", "n1", NodeRunStatus.FAILED.value))
        # Active compensation
        await store.upsert_compensation(
            _make_compensation("wr-comp", "n1", CompensationRunStatus.RUNNING.value)
        )

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan()

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert RecoveryCandidateReason.COMPENSATION_INCOMPLETE in c.reasons
        assert c.recommendation == RecoveryRecommendation.MANUAL_REVIEW


class TestScannerCompleted:
    """Completed runs are excluded by default, included when configured."""

    @pytest.mark.asyncio
    async def test_completed_excluded_by_default(self):
        store = _make_store()
        run = _make_run("wr-done", WorkflowRunStatus.COMPLETED.value)
        await store.create_run(run)

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan()

        assert result.candidate_count == 0

    @pytest.mark.asyncio
    async def test_completed_included_when_configured(self):
        store = _make_store()
        run = _make_run("wr-done", WorkflowRunStatus.COMPLETED.value)
        await store.create_run(run)

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan(
            RecoveryScanConfig(include_completed=True)
        )

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.status == "completed"
        assert c.recommendation == RecoveryRecommendation.INSPECT_ONLY


class TestScannerLimit:
    """Scan respects the limit parameter."""

    @pytest.mark.asyncio
    async def test_limit(self):
        store = _make_store()
        # Create 10 failed runs
        for i in range(10):
            run = _make_run(f"wr-{i:03d}", WorkflowRunStatus.FAILED.value)
            await store.create_run(run)
            await store.upsert_node(
                _make_node(f"wr-{i:03d}", "n1", NodeRunStatus.FAILED.value)
            )

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan(RecoveryScanConfig(limit=3))

        assert result.candidate_count == 3
        assert result.total_scanned == 10


class TestScannerInspectRun:
    """inspect_run returns a candidate for a single run."""

    @pytest.mark.asyncio
    async def test_inspect_existing_run(self):
        store = _make_store()
        run = _make_run("wr-ins", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)
        await store.upsert_node(_make_node("wr-ins", "n1", NodeRunStatus.FAILED.value))

        scanner = RecoveryScanner(store, lease_backend=None)
        candidate = await scanner.inspect_run("wr-ins")

        assert candidate.run_id == "wr-ins"
        assert candidate.status == "failed"

    @pytest.mark.asyncio
    async def test_inspect_missing_run_raises(self):
        store = _make_store()
        scanner = RecoveryScanner(store, lease_backend=None)

        with pytest.raises(KeyError):
            await scanner.inspect_run("nonexistent")


class TestScannerReadOnly:
    """Scanner does not modify run state."""

    @pytest.mark.asyncio
    async def test_scan_is_read_only(self):
        store = _make_store()
        run = _make_run("wr-ro", WorkflowRunStatus.RUNNING.value)
        original_updated = run.updated_at
        await store.create_run(run)

        scanner = RecoveryScanner(store, lease_backend=None)
        await scanner.scan()

        # Verify the run was not modified
        stored = await store.get_run("wr-ro")
        assert stored.updated_at == original_updated


class TestScannerNoLeaseBackend:
    """Scanner works without a lease backend."""

    @pytest.mark.asyncio
    async def test_no_lease_backend(self):
        store = _make_store()
        run = _make_run("wr-nolb", WorkflowRunStatus.FAILED.value)
        await store.create_run(run)
        await store.upsert_node(
            _make_node("wr-nolb", "n1", NodeRunStatus.FAILED.value)
        )

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan()

        # Should still find the candidate
        assert result.candidate_count == 1
        assert result.candidates[0].lease_present is False
        assert result.candidates[0].lease_owner is None


class TestScannerRedisLease:
    """Scanner reads Redis lease via backend.get_run_lease."""

    @pytest.mark.asyncio
    async def test_redis_lease_active(self):
        store = _make_store()
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-redis", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        lease = _make_lease("wr-redis", owner_id="redis-worker", expired=False)
        lease_backend = FakeLeaseBackend({"wr-redis": lease})

        scanner = RecoveryScanner(store, lease_backend=lease_backend)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.lease_present is True
        assert c.lease_owner == "redis-worker"
        assert c.recommendation == RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE

    @pytest.mark.asyncio
    async def test_redis_lease_expired(self):
        store = _make_store()
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-redis2", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        lease = _make_lease("wr-redis2", owner_id="redis-worker", expired=True)
        lease_backend = FakeLeaseBackend({"wr-redis2": lease})

        scanner = RecoveryScanner(store, lease_backend=lease_backend)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        assert result.candidate_count == 1
        c = result.candidates[0]
        assert c.lease_expired is True
        assert c.recommendation == RecoveryRecommendation.RESUME

    @pytest.mark.asyncio
    async def test_redis_lease_exception_does_not_crash(self):
        """Redis exception during lease lookup produces candidate error but does not crash."""
        store = _make_store()
        old_time = _now() - timedelta(seconds=600)
        run = _make_run("wr-redis3", WorkflowRunStatus.RUNNING.value, updated_at=old_time)
        await store.create_run(run)

        # Lease backend that raises on get_run_lease
        class BrokenLeaseBackend(FakeLeaseBackend):
            async def get_run_lease(self, run_id: str) -> None:
                raise RuntimeError("Redis connection lost")

        lease_backend = BrokenLeaseBackend()
        scanner = RecoveryScanner(store, lease_backend=lease_backend)
        result = await scanner.scan(RecoveryScanConfig(stale_after_seconds=300))

        # Should still produce a candidate, just without lease info
        assert result.candidate_count == 1
        assert result.candidates[0].lease_present is False


class TestScannerWorkflowFilter:
    """Scanner respects workflow_name filter."""

    @pytest.mark.asyncio
    async def test_filter_by_workflow_name(self):
        store = _make_store()
        for i in range(3):
            run = _make_run(f"wr-a-{i}", WorkflowRunStatus.FAILED.value, workflow_name="wf_a")
            await store.create_run(run)
            await store.upsert_node(_make_node(f"wr-a-{i}", "n1", NodeRunStatus.FAILED.value))
        for i in range(3):
            run = _make_run(f"wr-b-{i}", WorkflowRunStatus.FAILED.value, workflow_name="wf_b")
            await store.create_run(run)
            await store.upsert_node(_make_node(f"wr-b-{i}", "n1", NodeRunStatus.FAILED.value))

        scanner = RecoveryScanner(store, lease_backend=None)
        result = await scanner.scan(RecoveryScanConfig(workflow_name="wf_a"))

        assert result.candidate_count == 3
        for c in result.candidates:
            assert c.workflow_name == "wf_a"
