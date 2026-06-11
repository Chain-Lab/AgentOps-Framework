"""Tests for policy replay background runner."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.runtime.policy_replay_jobs import (
    InMemoryPolicyReplayJobStore,
    PolicyReplayJob,
    PolicyReplayJobStatus,
    PolicyReplayJobStore,
)
from agent_app.governance.policy_replay import (
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
    PolicyReplayDecisionChange,
)
from agent_app.runtime.policy_replay_background import (
    PolicyReplayBackgroundRunner,
)


def _make_job(
    job_id: str = "job_1",
    status: str = PolicyReplayJobStatus.QUEUED,
    limit: int | None = None,
    tenant_id: str | None = None,
    tool_name: str | None = None,
    rule_id: str | None = None,
    requested_by: str | None = None,
) -> PolicyReplayJob:
    """Create a test replay job."""
    return PolicyReplayJob(
        job_id=job_id,
        status=status,
        limit=limit,
        tenant_id=tenant_id,
        tool_name=tool_name,
        rule_id=rule_id,
        requested_by=requested_by,
        created_at=datetime.now(timezone.utc),
    )


def _make_replay_result(replay_id: str = "replay_1") -> PolicyReplayResult:
    """Create a test replay result."""
    run = PolicyReplayRun(
        replay_id=replay_id,
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=2,
        changed_count=1,
        unchanged_count=1,
        failed_count=0,
        created_at=datetime.now(timezone.utc),
    )
    changes = [
        PolicyReplayDecisionChange(
            decision_id="dec_1",
            original_action="allow",
            replayed_action="allow",
            changed=False,
        ),
        PolicyReplayDecisionChange(
            decision_id="dec_2",
            original_action="allow",
            replayed_action="deny",
            changed=True,
            reason="new policy",
        ),
    ]
    return PolicyReplayResult(replay=run, changes=changes)


class _FakeReplayRunner:
    """Fake replay runner for testing the background runner."""

    def __init__(self, result: PolicyReplayResult | None = None, fail: bool = False):
        self._result = result or _make_replay_result()
        self._fail = fail
        self.calls: list[dict] = []

    async def run_replay(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("Policy engine not configured")
        return self._result


class TestPolicyReplayBackgroundRunner:
    """Tests for PolicyReplayBackgroundRunner."""

    async def test_submit_creates_queued_job(self):
        """submit() creates a job with queued status."""
        job_store = InMemoryPolicyReplayJobStore()
        fake_runner = _FakeReplayRunner()
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
        )

        job = await runner.submit(
            limit=50,
            tenant_id="tenant_a",
            tool_name="refund.request",
            requested_by="admin",
        )

        assert job.status == PolicyReplayJobStatus.QUEUED
        assert job.limit == 50
        assert job.tenant_id == "tenant_a"
        assert job.tool_name == "refund.request"
        assert job.requested_by == "admin"
        assert job.replay_id is None

        # Verify stored
        stored = await job_store.get(job.job_id)
        assert stored is not None
        assert stored.status == PolicyReplayJobStatus.QUEUED

    async def test_run_job_transitions_to_completed(self):
        """run_job() transitions queued -> running -> completed."""
        job_store = InMemoryPolicyReplayJobStore()
        replay_store = None  # No persistence needed for this test
        result = _make_replay_result("replay_bg_1")
        fake_runner = _FakeReplayRunner(result=result)
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
            replay_store=replay_store,
        )

        # Submit a job
        job = await runner.submit(limit=10)
        assert job.status == PolicyReplayJobStatus.QUEUED

        # Run it
        completed = await runner.run_job(job.job_id)

        assert completed.status == PolicyReplayJobStatus.COMPLETED
        assert completed.replay_id == "replay_bg_1"
        assert completed.started_at is not None
        assert completed.completed_at is not None

    async def test_run_job_transitions_to_failed_on_error(self):
        """run_job() transitions to failed when replay raises."""
        job_store = InMemoryPolicyReplayJobStore()
        fake_runner = _FakeReplayRunner(fail=True)
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
        )

        job = await runner.submit(limit=10)
        failed = await runner.run_job(job.job_id)

        assert failed.status == PolicyReplayJobStatus.FAILED
        assert failed.error is not None
        assert "Policy engine not configured" in failed.error.get("message", "")

    async def test_run_job_passes_filters_to_replay_runner(self):
        """run_job() passes filter parameters to the replay runner."""
        job_store = InMemoryPolicyReplayJobStore()
        fake_runner = _FakeReplayRunner()
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
        )

        job = await runner.submit(
            limit=25,
            tenant_id="tenant_x",
            tool_name="special.tool",
            rule_id="old_rule",
        )
        await runner.run_job(job.job_id)

        assert len(fake_runner.calls) == 1
        call_kwargs = fake_runner.calls[0]
        assert call_kwargs["limit"] == 25
        assert call_kwargs["tenant_id"] == "tenant_x"
        assert call_kwargs["tool_name"] == "special.tool"
        assert call_kwargs["rule_id"] == "old_rule"

    async def test_run_job_missing_job_raises(self):
        """run_job() raises KeyError for missing job."""
        job_store = InMemoryPolicyReplayJobStore()
        fake_runner = _FakeReplayRunner()
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
        )

        with pytest.raises(KeyError, match="not found"):
            await runner.run_job("nonexistent_job")

    async def test_list_jobs(self):
        """list() returns recent jobs."""
        job_store = InMemoryPolicyReplayJobStore()
        fake_runner = _FakeReplayRunner()
        runner = PolicyReplayBackgroundRunner(
            replay_runner=fake_runner,
            job_store=job_store,
        )

        await runner.submit()
        await runner.submit()
        await runner.submit()

        jobs = await runner.list_jobs()
        assert len(jobs) == 3
