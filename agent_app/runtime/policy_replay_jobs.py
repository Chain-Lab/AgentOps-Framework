"""Policy replay job models and stores.

Phase 28: background replay job execution.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agent_app.governance.policy_replay import PolicyReplayResult


# ---------------------------------------------------------------------------
# Job Models
# ---------------------------------------------------------------------------

class PolicyReplayJobStatus(str, Enum):
    """Status of a replay job."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PolicyReplayJob(BaseModel):
    """A replay job that can be queued and executed in the background.

    Attributes:
        job_id: Unique identifier for this job.
        replay_id: Associated replay result ID (set after completion).
        status: Current job status.
        limit: Max decisions to replay.
        tenant_id: Filter by tenant.
        tool_name: Filter by tool name.
        rule_id: Filter by original rule name.
        requested_by: Identity of who requested the replay.
        error: Error details if job failed.
        created_at: When the job was created.
        started_at: When the job started running.
        completed_at: When the job finished.
        metadata: Arbitrary metadata.
    """
    job_id: str = Field(..., description="Unique job identifier")
    replay_id: str | None = Field(default=None, description="Associated replay ID")
    status: str = Field(..., description="Job status")
    limit: int | None = Field(default=None, description="Max decisions to replay")
    tenant_id: str | None = Field(default=None, description="Tenant filter")
    tool_name: str | None = Field(default=None, description="Tool name filter")
    rule_id: str | None = Field(default=None, description="Rule name filter")
    requested_by: str | None = Field(default=None, description="Who requested this")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Job creation time",
    )
    started_at: datetime | None = Field(default=None, description="When job started")
    completed_at: datetime | None = Field(default=None, description="When job finished")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary metadata"
    )


# ---------------------------------------------------------------------------
# Job Store Protocol
# ---------------------------------------------------------------------------

class PolicyReplayJobStore(Protocol):
    """Protocol for persisting replay jobs."""

    async def create(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Create a new job. Returns the created job."""
        ...

    async def get(self, job_id: str) -> PolicyReplayJob | None:
        """Retrieve a job by ID. Returns None if not found."""
        ...

    async def update(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Update an existing job. Returns the updated job."""
        ...

    async def list(self, limit: int = 50) -> list[PolicyReplayJob]:
        """List recent jobs, most recent first."""
        ...


# ---------------------------------------------------------------------------
# InMemoryPolicyReplayJobStore
# ---------------------------------------------------------------------------

class InMemoryPolicyReplayJobStore:
    """In-memory policy replay job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, PolicyReplayJob] = {}
        self._order: list[str] = []

    async def create(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Create a new job."""
        self._jobs[job.job_id] = job
        self._order.append(job.job_id)
        return job

    async def get(self, job_id: str) -> PolicyReplayJob | None:
        """Retrieve a job by ID."""
        return self._jobs.get(job_id)

    async def update(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Update an existing job."""
        if job.job_id in self._jobs:
            self._jobs[job.job_id] = job
        return job

    async def list(self, limit: int = 50) -> list[PolicyReplayJob]:
        """List recent jobs, most recent first."""
        ids = list(reversed(self._order[-limit:]))
        return [self._jobs[rid] for rid in ids if rid in self._jobs]


# ---------------------------------------------------------------------------
# SQLitePolicyReplayJobStore
# ---------------------------------------------------------------------------

class SQLitePolicyReplayJobStore:
    """SQLite-backed policy replay job store.

    Persists replay jobs to a SQLite database file.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_replay_jobs.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_replay_jobs (
                job_id TEXT PRIMARY KEY,
                replay_id TEXT,
                status TEXT NOT NULL,
                limit_value INTEGER,
                tenant_id TEXT,
                tool_name TEXT,
                rule_id TEXT,
                requested_by TEXT,
                error_json TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                metadata_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_jobs_created "
            "ON policy_replay_jobs(created_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_jobs_status "
            "ON policy_replay_jobs(status)"
        )
        self._conn.commit()

    async def create(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Create a new job."""
        self._conn.execute(
            """
            INSERT INTO policy_replay_jobs
                (job_id, replay_id, status, limit_value, tenant_id,
                 tool_name, rule_id, requested_by, error_json,
                 created_at, started_at, completed_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                job.replay_id,
                job.status,
                job.limit,
                job.tenant_id,
                job.tool_name,
                job.rule_id,
                job.requested_by,
                json.dumps(job.error) if job.error else None,
                job.created_at.isoformat(),
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                json.dumps(job.metadata),
            ),
        )
        self._conn.commit()
        return job

    async def get(self, job_id: str) -> PolicyReplayJob | None:
        """Retrieve a job by ID. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM policy_replay_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    async def update(self, job: PolicyReplayJob) -> PolicyReplayJob:
        """Update an existing job."""
        self._conn.execute(
            """
            UPDATE policy_replay_jobs SET
                replay_id = ?,
                status = ?,
                limit_value = ?,
                tenant_id = ?,
                tool_name = ?,
                rule_id = ?,
                requested_by = ?,
                error_json = ?,
                started_at = ?,
                completed_at = ?,
                metadata_json = ?
            WHERE job_id = ?
            """,
            (
                job.replay_id,
                job.status,
                job.limit,
                job.tenant_id,
                job.tool_name,
                job.rule_id,
                job.requested_by,
                json.dumps(job.error) if job.error else None,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                json.dumps(job.metadata),
                job.job_id,
            ),
        )
        self._conn.commit()
        return job

    async def list(self, limit: int = 50) -> list[PolicyReplayJob]:
        """List recent jobs, most recent first."""
        rows = self._conn.execute(
            "SELECT * FROM policy_replay_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row: sqlite3.Row) -> PolicyReplayJob:
        """Convert a database row to PolicyReplayJob."""
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
        data["error"] = json.loads(data["error_json"]) if data.get("error_json") else None
        data["limit"] = data.pop("limit_value")
        data["rule_id"] = data.pop("rule_id")
        for ts_field in ("created_at", "started_at", "completed_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PolicyReplayJob(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def create_replay_job_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyReplayJobStore:
    """Factory function to create a PolicyReplayJobStore.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A PolicyReplayJobStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryPolicyReplayJobStore()
    if store_type == "sqlite":
        return SQLitePolicyReplayJobStore(db_path=db_path or ".agent_app/policy_replay_jobs.db")
    raise ValueError(
        f"Unknown replay job store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )
