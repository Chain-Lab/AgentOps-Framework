"""DagExecutor tests for Phase 16.2 lease backend support."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import InMemoryWorkflowLeaseBackend
from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode, NodeType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_dag(name: str = "test-dag") -> DagWorkflow:
    """Create a simple sequential DAG with one agent node."""
    return DagWorkflow(
        name=name,
        nodes=[
            DagNode(
                id="node-1",
                type=NodeType.AGENT,
                agent="test-agent",
                input="hello",
            )
        ],
        edges=[],
        execution_mode="sequential",
    )


def _make_registries():
    """Create minimal registries for testing."""
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry
    return AgentRegistry(), ToolRegistry(), WorkflowRegistry()


def _run(coro):
    return asyncio.run(coro)


class FakeLeaseBackend:
    """Fake lease backend that tracks calls."""

    def __init__(self):
        self.acquire_calls = []
        self.renew_calls = []
        self.release_calls = []
        self._leases: dict[str, WorkflowRunLease] = {}

    async def acquire_run_lease(self, run_id, worker, policy=None):
        self.acquire_calls.append((run_id, worker.worker_id))
        policy = policy or LeasePolicy()
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        lease = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=now,
            expires_at=now + __import__("datetime").timedelta(seconds=policy.ttl_seconds),
        )
        self._leases[run_id] = lease
        return LeaseAcquireResult(
            acquired=True, run_id=run_id,
            owner_id=worker.worker_id, lease=lease,
        )

    async def renew_run_lease(self, run_id, worker, policy=None):
        self.renew_calls.append((run_id, worker.worker_id))
        existing = self._leases.get(run_id)
        if existing is None:
            raise KeyError("No lease")
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=now + __import__("datetime").timedelta(seconds=300),
            renewed_at=now,
            version=existing.version + 1,
        )
        self._leases[run_id] = renewed
        return renewed

    async def release_run_lease(self, run_id, worker):
        self.release_calls.append((run_id, worker.worker_id))
        existing = self._leases.get(run_id)
        if existing is None:
            raise KeyError("No lease")
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        released = WorkflowRunLease(
            run_id=run_id,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=existing.expires_at,
            renewed_at=existing.renewed_at,
            released_at=now,
            version=existing.version,
        )
        self._leases[run_id] = released
        return released

    async def get_run_lease(self, run_id):
        lease = self._leases.get(run_id)
        if lease is None or lease.released_at is not None:
            return None
        return lease

    async def list_expired_leases(self, before=None):
        cutoff = before or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        return [
            l for l in self._leases.values()
            if l.released_at is None and l.expires_at <= cutoff
        ]


class MockBackend:
    """Minimal mock backend that returns completed results."""

    async def execute(self, request):
        class _Resp:
            status = "completed"
            output = "done"
        return _Resp()

    async def handle_handoff(self, request):
        pass


# ===========================================================================
# DagExecutor lease backend tests
# ===========================================================================


class TestDagExecutorLeaseBackend:
    """Tests for DagExecutor with explicit lease_backend."""

    def test_explicit_lease_backend_takes_precedence(self):
        """Explicit lease_backend is used instead of state_store."""
        fake_backend = FakeLeaseBackend()
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=None,
            lease_backend=fake_backend,
        )
        assert executor._lease_backend is fake_backend

    def test_no_lease_backend_no_state_store_keeps_old_behavior(self):
        """Without lease_backend and state_store, no lease operations."""
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=None,
            lease_backend=None,
        )
        assert executor._get_lease_backend() is None

    def test_lease_acquire_denied_returns_stable_error(self):
        """When lease acquisition is denied, DagError is raised."""
        from agent_app.workflows.dag import DagError

        class DenyingBackend:
            async def acquire_run_lease(self, run_id, worker, policy=None):
                return LeaseAcquireResult(
                    acquired=False, run_id=run_id,
                    owner_id=worker.worker_id,
                    reason="Already leased by someone else",
                )
            async def renew_run_lease(self, *a, **kw):
                raise KeyError("No lease")
            async def release_run_lease(self, *a, **kw):
                raise KeyError("No lease")
            async def get_run_lease(self, run_id):
                return None
            async def list_expired_leases(self, before=None):
                return []

        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=None,
            lease_backend=DenyingBackend(),
            run_id="run-1",
        )
        with pytest.raises(DagError, match="Cannot execute workflow run"):
            _run(executor._acquire_lease())

    def test_lease_release_failure_does_not_raise(self):
        """Lease release failure is caught silently (KeyError = no lease)."""
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=None,
            lease_backend=None,
            run_id="run-1",
        )
        # Should not raise — no lease backend configured
        _run(executor._release_lease())

    def test_lease_backend_stored_in_executor(self):
        """Lease backend is stored and accessible."""
        fake = FakeLeaseBackend()
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            lease_backend=fake,
        )
        assert executor._lease_backend is fake

    def test_lease_policy_stored_in_executor(self):
        """Lease policy is stored and accessible."""
        policy = LeasePolicy(ttl_seconds=600)
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            lease_policy=policy,
        )
        assert executor._lease_policy is policy

    def test_get_lease_backend_fallback_to_state_store(self):
        """_get_lease_backend falls back to state_store when no explicit backend."""
        ar, tr, wr = _make_registries()
        mock_store = object()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=mock_store,
        )
        # Should return the state_store as fallback
        assert executor._get_lease_backend() is mock_store

    def test_get_lease_backend_explicit_wins(self):
        """Explicit lease_backend takes precedence over state_store."""
        fake = FakeLeaseBackend()
        ar, tr, wr = _make_registries()
        executor = DagExecutor(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            state_store=object(),
            lease_backend=fake,
        )
        assert executor._get_lease_backend() is fake
