"""Background replay runner — lightweight job execution model.

Phase 28: provides submit/run_job pattern for background replay execution
without external task queues.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_replay import PolicyReplayResult
from agent_app.runtime.policy_replay_jobs import (
    PolicyReplayJob,
    PolicyReplayJobStatus,
    PolicyReplayJobStore,
)


class PolicyReplayBackgroundRunner:
    """Lightweight background replay runner.

    Submits replay jobs and executes them, updating job status through
    the lifecycle: queued -> running -> completed/failed.

    Does NOT use external task queues. Jobs are executed by calling
    run_job() explicitly (e.g., from CLI or FastAPI background tasks).

    Args:
        replay_runner: The PolicyReplayRunner to execute replays with.
        job_store: Store for persisting job state.
        replay_store: Optional store for persisting replay results.
    """

    def __init__(
        self,
        replay_runner: Any,
        job_store: PolicyReplayJobStore,
        replay_store: Any = None,
    ) -> None:
        self._replay_runner = replay_runner
        self._job_store = job_store
        self._replay_store = replay_store

    async def submit(
        self,
        limit: int | None = None,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        rule_id: str | None = None,
        requested_by: str | None = None,
    ) -> PolicyReplayJob:
        """Submit a new replay job.

        Creates a queued job and persists it. The job must be executed
        separately via run_job().

        Args:
            limit: Max decisions to replay.
            tenant_id: Filter by tenant.
            tool_name: Filter by tool name.
            rule_id: Filter by original rule name.
            requested_by: Identity of who requested this.

        Returns:
            The created PolicyReplayJob (status: queued).
        """
        job = PolicyReplayJob(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            status=PolicyReplayJobStatus.QUEUED,
            limit=limit,
            tenant_id=tenant_id,
            tool_name=tool_name,
            rule_id=rule_id,
            requested_by=requested_by,
            created_at=datetime.now(timezone.utc),
        )
        return await self._job_store.create(job)

    async def run_job(self, job_id: str) -> PolicyReplayJob:
        """Execute a queued replay job.

        Transitions the job through: queued -> running -> completed/failed.
        If the replay succeeds, the replay_id is stored on the job.

        Args:
            job_id: The job ID to execute.

        Returns:
            The updated PolicyReplayJob.

        Raises:
            KeyError: If job_id not found in the job store.
        """
        job = await self._job_store.get(job_id)
        if job is None:
            raise KeyError(f"Job '{job_id}' not found in replay job store.")

        # Transition to running
        job.status = PolicyReplayJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        await self._job_store.update(job)

        try:
            result = await self._replay_runner.run_replay(
                limit=job.limit,
                tenant_id=job.tenant_id,
                tool_name=job.tool_name,
                rule_id=job.rule_id,
            )

            # Persist replay result if store available
            if self._replay_store is not None:
                await self._replay_store.save(result)

            job.status = PolicyReplayJobStatus.COMPLETED
            job.replay_id = result.replay.replay_id
            job.completed_at = datetime.now(timezone.utc)

        except Exception as exc:
            job.status = PolicyReplayJobStatus.FAILED
            job.error = {"message": str(exc)}
            job.completed_at = datetime.now(timezone.utc)

        return await self._job_store.update(job)

    async def list_jobs(self, limit: int = 50) -> list[PolicyReplayJob]:
        """List recent jobs, most recent first."""
        return await self._job_store.list(limit=limit)
