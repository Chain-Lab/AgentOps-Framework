"""Tests for policy replay job store."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from datetime import datetime, timezone

from agent_app.runtime.policy_replay_jobs import (
    InMemoryPolicyReplayJobStore,
    SQLitePolicyReplayJobStore,
    PolicyReplayJob,
    PolicyReplayJobStatus,
)


def _make_job(
    job_id: str = "job_1",
    status: str = PolicyReplayJobStatus.QUEUED,
    replay_id: str | None = None,
    limit: int | None = None,
    tenant_id: str | None = None,
    tool_name: str | None = None,
    rule_id: str | None = None,
    requested_by: str | None = None,
) -> PolicyReplayJob:
    """Create a test replay job."""
    return PolicyReplayJob(
        job_id=job_id,
        replay_id=replay_id,
        status=status,
        limit=limit,
        tenant_id=tenant_id,
        tool_name=tool_name,
        rule_id=rule_id,
        requested_by=requested_by,
        created_at=datetime.now(timezone.utc),
    )


def _make_db_path(tmp_path):
    """Create a temp db path."""
    return str(tmp_path / "test_jobs.db")


class TestInMemoryPolicyReplayJobStore:
    """Tests for InMemoryPolicyReplayJobStore."""

    async def test_create_and_get(self):
        """Create and retrieve a job."""
        store = InMemoryPolicyReplayJobStore()
        job = _make_job("job_1")
        saved = await store.create(job)
        assert saved.job_id == "job_1"

        fetched = await store.get("job_1")
        assert fetched is not None
        assert fetched.status == PolicyReplayJobStatus.QUEUED

    async def test_get_missing_returns_none(self):
        """Getting a non-existent job returns None."""
        store = InMemoryPolicyReplayJobStore()
        result = await store.get("nonexistent")
        assert result is None

    async def test_list_empty(self):
        """List returns empty list for new store."""
        store = InMemoryPolicyReplayJobStore()
        jobs = await store.list()
        assert jobs == []

    async def test_list_returns_jobs_most_recent_first(self):
        """List returns jobs ordered by creation time descending."""
        store = InMemoryPolicyReplayJobStore()
        await store.create(_make_job("job_1"))
        await store.create(_make_job("job_2"))
        await store.create(_make_job("job_3"))

        jobs = await store.list()
        assert len(jobs) == 3
        assert jobs[0].job_id == "job_3"
        assert jobs[1].job_id == "job_2"
        assert jobs[2].job_id == "job_1"

    async def test_update_job(self):
        """Update a job's status and fields."""
        store = InMemoryPolicyReplayJobStore()
        job = _make_job("job_1")
        await store.create(job)

        # Update to running
        job.status = PolicyReplayJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        updated = await store.update(job)
        assert updated.status == PolicyReplayJobStatus.RUNNING

        # Verify via get
        fetched = await store.get("job_1")
        assert fetched.status == PolicyReplayJobStatus.RUNNING

    async def test_list_respects_limit(self):
        """List respects the limit parameter."""
        store = InMemoryPolicyReplayJobStore()
        for i in range(10):
            await store.create(_make_job(f"job_{i}"))

        jobs = await store.list(limit=3)
        assert len(jobs) == 3
        assert jobs[0].job_id == "job_9"
        assert jobs[2].job_id == "job_7"


class TestSQLitePolicyReplayJobStore:
    """Tests for SQLitePolicyReplayJobStore."""

    async def test_create_and_get(self, tmp_path):
        """Create and retrieve a job."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        job = _make_job("job_1", limit=50, tenant_id="t1", requested_by="admin")
        saved = await store.create(job)
        assert saved.job_id == "job_1"

        fetched = await store.get("job_1")
        assert fetched is not None
        assert fetched.limit == 50
        assert fetched.tenant_id == "t1"
        assert fetched.requested_by == "admin"
        store.close()

    async def test_get_missing_returns_none(self, tmp_path):
        """Getting a non-existent job returns None."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        result = await store.get("nonexistent")
        assert result is None
        store.close()

    async def test_list_empty(self, tmp_path):
        """List returns empty list for new store."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        jobs = await store.list()
        assert jobs == []
        store.close()

    async def test_list_returns_jobs_most_recent_first(self, tmp_path):
        """List returns jobs ordered by creation time descending."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        await store.create(_make_job("job_1"))
        await store.create(_make_job("job_2"))
        await store.create(_make_job("job_3"))

        jobs = await store.list()
        assert len(jobs) == 3
        assert jobs[0].job_id == "job_3"
        assert jobs[1].job_id == "job_2"
        assert jobs[2].job_id == "job_1"
        store.close()

    async def test_update_job(self, tmp_path):
        """Update a job with new status and fields."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        job = _make_job("job_1")
        await store.create(job)

        # Update to completed
        job.status = PolicyReplayJobStatus.COMPLETED
        job.replay_id = "replay_abc"
        job.completed_at = datetime.now(timezone.utc)
        updated = await store.update(job)
        assert updated.status == PolicyReplayJobStatus.COMPLETED
        assert updated.replay_id == "replay_abc"

        # Verify via get
        fetched = await store.get("job_1")
        assert fetched.status == PolicyReplayJobStatus.COMPLETED
        assert fetched.replay_id == "replay_abc"
        store.close()

    async def test_update_failed_job_with_error(self, tmp_path):
        """Update a failed job with error details."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        job = _make_job("job_1", status=PolicyReplayJobStatus.RUNNING)
        await store.create(job)

        job.status = PolicyReplayJobStatus.FAILED
        job.error = {"message": "Policy engine not configured"}
        job.completed_at = datetime.now(timezone.utc)
        await store.update(job)

        fetched = await store.get("job_1")
        assert fetched.error is not None
        assert fetched.error["message"] == "Policy engine not configured"
        store.close()

    async def test_persists_across_instances(self, tmp_path):
        """Data persists when store is re-opened."""
        db_path = _make_db_path(tmp_path)
        store1 = SQLitePolicyReplayJobStore(db_path=db_path)
        await store1.create(_make_job("job_persist", limit=100))
        store1.close()

        # Re-open
        store2 = SQLitePolicyReplayJobStore(db_path=db_path)
        fetched = await store2.get("job_persist")
        assert fetched is not None
        assert fetched.limit == 100
        store2.close()

    async def test_list_respects_limit(self, tmp_path):
        """List respects the limit parameter."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        for i in range(10):
            await store.create(_make_job(f"job_{i}"))

        jobs = await store.list(limit=3)
        assert len(jobs) == 3
        assert jobs[0].job_id == "job_9"
        assert jobs[2].job_id == "job_7"
        store.close()

    async def test_status_transitions(self, tmp_path):
        """Job status transitions are correctly stored."""
        store = SQLitePolicyReplayJobStore(db_path=_make_db_path(tmp_path))
        job = _make_job("job_1", status=PolicyReplayJobStatus.QUEUED)
        await store.create(job)

        # Queued -> Running
        job.status = PolicyReplayJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        await store.update(job)

        # Running -> Completed
        job.status = PolicyReplayJobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        await store.update(job)

        fetched = await store.get("job_1")
        assert fetched.status == PolicyReplayJobStatus.COMPLETED
        assert fetched.started_at is not None
        assert fetched.completed_at is not None
        store.close()
