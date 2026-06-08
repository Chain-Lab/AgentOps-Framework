"""Lease backend abstraction — pluggable lease coordination layer.

Phase 16.2: Introduces a ``WorkflowLeaseBackend`` protocol that decouples
lease coordination from ``WorkflowStateStore``.  The default backend is a
``StateStoreLeaseBackend`` adapter that delegates to the existing state
store, preserving full backward compatibility.  Standalone ``InMemory``
and ``SQLite`` backends are also provided.

This is NOT a distributed lock service, NOT Redis, NOT etcd, and does NOT
provide exactly-once execution.  It is a best-effort coordination layer.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
    WorkflowRunStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return current UTC datetime with tzinfo."""
    return datetime.now(timezone.utc)


def _timedelta_from_seconds(seconds: int) -> timedelta:
    """Create a timedelta from seconds."""
    return timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WorkflowLeaseBackend(Protocol):
    """Protocol for pluggable workflow lease coordination.

    Implementations manage lease acquire / renew / release / query
    operations independently of any particular state store.  The
    ``StateStoreLeaseBackend`` adapter wraps an existing
    ``WorkflowStateStore`` to satisfy this protocol.
    """

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Attempt to acquire a lease on a workflow run.

        Args:
            run_id: The workflow run to lease.
            worker: The worker requesting the lease.
            policy: Optional lease policy (TTL, steal-expired, etc.).

        Returns:
            LeaseAcquireResult indicating success or denial.
        """
        ...  # pragma: no cover

    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Renew an existing lease held by the same worker.

        Args:
            run_id: The workflow run to renew the lease for.
            worker: The worker requesting renewal.
            policy: Optional lease policy (uses existing TTL if not provided).

        Returns:
            The renewed WorkflowRunLease.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        ...  # pragma: no cover

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a held lease.

        Args:
            run_id: The workflow run to release the lease for.
            worker: The worker releasing the lease.

        Returns:
            The released WorkflowRunLease.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        ...  # pragma: no cover

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current lease for a workflow run.

        Args:
            run_id: The workflow run to query.

        Returns:
            WorkflowRunLease if an active (non-released) lease exists,
            None otherwise.
        """
        ...  # pragma: no cover

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List leases that have expired.

        Args:
            before: Optional cutoff datetime.  Defaults to now.

        Returns:
            List of expired WorkflowRunLease objects (not yet released).
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# StateStore adapter
# ---------------------------------------------------------------------------


class StateStoreLeaseBackend:
    """Adapter that wraps a ``WorkflowStateStore`` as a ``WorkflowLeaseBackend``.

    This adapter delegates all lease operations to the underlying state
    store, preserving backward compatibility with code that previously
    called ``state_store.acquire_run_lease()`` directly.

    Usage::

        backend = StateStoreLeaseBackend(state_store)
        result = await backend.acquire_run_lease(run_id, worker, policy)
    """

    def __init__(self, state_store: object) -> None:
        """Initialize with a state store that implements lease methods.

        Args:
            state_store: A ``WorkflowStateStore`` instance (or any object
                with ``acquire_run_lease``, ``renew_run_lease``,
                ``release_run_lease``, ``get_run_lease``, and
                ``list_expired_leases`` methods).
        """
        self._state_store = state_store

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Delegate to ``state_store.acquire_run_lease``."""
        return await self._state_store.acquire_run_lease(run_id, worker, policy)

    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Delegate to ``state_store.renew_run_lease``."""
        return await self._state_store.renew_run_lease(run_id, worker, policy)

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Delegate to ``state_store.release_run_lease``."""
        return await self._state_store.release_run_lease(run_id, worker)

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Delegate to ``state_store.get_run_lease``."""
        return await self._state_store.get_run_lease(run_id)

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """Delegate to ``state_store.list_expired_leases``."""
        return await self._state_store.list_expired_leases(before)


# ---------------------------------------------------------------------------
# InMemory lease backend
# ---------------------------------------------------------------------------


class InMemoryWorkflowLeaseBackend:
    """Standalone in-memory lease backend.

    Stores leases in a plain dict, independent of any state store.
    Suitable for development, testing, and single-process deployments.

    Usage::

        backend = InMemoryWorkflowLeaseBackend()
        result = await backend.acquire_run_lease(run_id, worker, policy)
    """

    def __init__(self) -> None:
        self._leases: dict[str, WorkflowRunLease] = {}

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Attempt to acquire a lease on a workflow run.

        Five-path logic (mirrors InMemoryWorkflowStateStore):
        1. Run not found in state → denied (no run tracking here; always
           allow acquire for standalone lease backend).
        2. No existing lease → acquire succeeds.
        3. Existing released lease → acquire succeeds.
        4. Expired lease + allow_steal_expired → steal succeeds.
        5. Expired lease + !allow_steal_expired → denied.
        6. Active lease owned by same worker → refresh.
        7. Active lease owned by different worker → denied.
        """
        policy = policy or LeasePolicy()
        now = _utcnow()

        existing = self._leases.get(run_id)

        # No existing lease — acquire succeeds
        if existing is None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            )
            self._leases[run_id] = lease
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing released lease — allow new acquire
        if existing.released_at is not None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            )
            self._leases[run_id] = lease
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing active lease — check expiry
        if now >= existing.expires_at:
            # Expired — allow steal if policy permits
            if policy.allow_steal_expired:
                lease = WorkflowRunLease(
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    acquired_at=now,
                    expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
                    version=existing.version + 1,
                )
                self._leases[run_id] = lease
                return LeaseAcquireResult(
                    acquired=True,
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    lease=lease,
                )
            else:
                return LeaseAcquireResult(
                    acquired=False,
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    reason="Existing lease expired but allow_steal_expired=False.",
                    current_owner_id=existing.owner_id,
                    expires_at=existing.expires_at,
                )

        # Active lease — check ownership
        if existing.owner_id == worker.worker_id:
            # Same owner — refresh (extend TTL, bump version)
            refreshed = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=existing.acquired_at,
                expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
                renewed_at=now,
                version=existing.version + 1,
            )
            self._leases[run_id] = refreshed
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=refreshed,
            )
        else:
            # Different owner — deny
            return LeaseAcquireResult(
                acquired=False,
                run_id=run_id,
                owner_id=worker.worker_id,
                reason=(
                    f"Run is currently leased by '{existing.owner_id}' "
                    f"until {existing.expires_at.isoformat()}."
                ),
                current_owner_id=existing.owner_id,
                expires_at=existing.expires_at,
            )

    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Renew an existing lease held by the same worker.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        policy = policy or LeasePolicy()
        existing = self._leases.get(run_id)
        if existing is None:
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        if existing.owner_id != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing.owner_id}', not '{worker.worker_id}'."
            )
        if existing.released_at is not None:
            raise KeyError(f"Lease for workflow run '{run_id}' has been released.")

        now = _utcnow()
        if now >= existing.expires_at:
            raise KeyError(
                f"Lease for workflow run '{run_id}' has expired "
                f"(expired at {existing.expires_at.isoformat()})."
            )

        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=existing.acquired_at,
            expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            renewed_at=now,
            version=existing.version + 1,
        )
        self._leases[run_id] = renewed
        return renewed

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a held lease.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        existing = self._leases.get(run_id)
        if existing is None:
            raise KeyError(f"No active lease for workflow run '{run_id}'.")
        if existing.owner_id != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing.owner_id}', not '{worker.worker_id}'."
            )
        if existing.released_at is not None:
            raise KeyError(f"Lease for workflow run '{run_id}' has already been released.")

        now = _utcnow()
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

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current active (non-released) lease for a run."""
        lease = self._leases.get(run_id)
        if lease is None or lease.released_at is not None:
            return None
        return lease

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List leases that have expired and are not yet released."""
        cutoff = before or _utcnow()
        return [
            lease
            for lease in self._leases.values()
            if lease.released_at is None and lease.expires_at <= cutoff
        ]


# ---------------------------------------------------------------------------
# SQLite lease backend
# ---------------------------------------------------------------------------


class SQLiteWorkflowLeaseBackend:
    """SQLite-backed standalone lease backend.

    Persists leases in a SQLite table, making them visible across process
    instances.  Uses an in-memory cache as the source of truth for
    operations, re-syncing from the database on ``get_run_lease``.

    Usage::

        backend = SQLiteWorkflowLeaseBackend("/path/to/leases.db")
        result = await backend.acquire_run_lease(run_id, worker, policy)
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser().resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create the lease table and indexes if they don't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_run_leases (
                    run_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    renewed_at TEXT,
                    released_at TEXT,
                    version INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_wf_run_leases_expires
                ON workflow_run_leases(expires_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_wf_run_leases_owner
                ON workflow_run_leases(owner_id)
            """)
            conn.commit()
        finally:
            conn.close()

    def _row_to_lease(self, row: tuple) -> WorkflowRunLease:
        """Convert a DB row tuple to a WorkflowRunLease."""
        return WorkflowRunLease(
            run_id=row[0],
            owner_id=row[1],
            acquired_at=datetime.fromisoformat(row[2]),
            expires_at=datetime.fromisoformat(row[3]),
            renewed_at=(
                datetime.fromisoformat(row[4]) if row[4] else None
            ),
            released_at=(
                datetime.fromisoformat(row[5]) if row[5] else None
            ),
            version=row[6],
        )

    def _lease_to_row(self, lease: WorkflowRunLease) -> tuple:
        """Convert a WorkflowRunLease to a DB row tuple."""
        return (
            lease.run_id,
            lease.owner_id,
            lease.acquired_at.isoformat(),
            lease.expires_at.isoformat(),
            lease.renewed_at.isoformat() if lease.renewed_at else None,
            lease.released_at.isoformat() if lease.released_at else None,
            lease.version,
        )

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Attempt to acquire a lease on a workflow run.

        Five-path logic mirrors InMemoryWorkflowLeaseBackend, with
        persistence to SQLite.
        """
        policy = policy or LeasePolicy()
        now = _utcnow()

        existing = await self.get_run_lease(run_id)
        # Also check for released leases (get_run_lease returns None for released)
        released_lease = self._leases.get(run_id) if hasattr(self, "_leases") else None
        if released_lease is None:
            # Check DB for released lease
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workflow_run_leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row and row[5] is not None:
                    released_lease = self._row_to_lease(row)
            finally:
                conn.close()

        # No existing lease — acquire succeeds
        if existing is None and released_lease is None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            )
            self._leases = getattr(self, "_leases", {})
            self._leases[run_id] = lease
            self._persist_lease(lease)
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing released lease — allow new acquire
        if released_lease is not None and released_lease.released_at is not None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            )
            self._leases = getattr(self, "_leases", {})
            self._leases[run_id] = lease
            self._persist_lease(lease)
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # We have an existing active lease (from get_run_lease)
        if existing is not None:
            # Check expiry
            if now >= existing.expires_at:
                if policy.allow_steal_expired:
                    lease = WorkflowRunLease(
                        run_id=run_id,
                        owner_id=worker.worker_id,
                        acquired_at=now,
                        expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
                        version=existing.version + 1,
                    )
                    self._leases = getattr(self, "_leases", {})
                    self._leases[run_id] = lease
                    self._persist_lease(lease)
                    return LeaseAcquireResult(
                        acquired=True,
                        run_id=run_id,
                        owner_id=worker.worker_id,
                        lease=lease,
                    )
                else:
                    return LeaseAcquireResult(
                        acquired=False,
                        run_id=run_id,
                        owner_id=worker.worker_id,
                        reason="Existing lease expired but allow_steal_expired=False.",
                        current_owner_id=existing.owner_id,
                        expires_at=existing.expires_at,
                    )

            # Active lease — check ownership
            if existing.owner_id == worker.worker_id:
                refreshed = WorkflowRunLease(
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    acquired_at=existing.acquired_at,
                    expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
                    renewed_at=now,
                    version=existing.version + 1,
                )
                self._leases = getattr(self, "_leases", {})
                self._leases[run_id] = refreshed
                self._persist_lease(refreshed)
                return LeaseAcquireResult(
                    acquired=True,
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    lease=refreshed,
                )
            else:
                return LeaseAcquireResult(
                    acquired=False,
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    reason=(
                        f"Run is currently leased by '{existing.owner_id}' "
                        f"until {existing.expires_at.isoformat()}."
                    ),
                    current_owner_id=existing.owner_id,
                    expires_at=existing.expires_at,
                )

        # Fallback: shouldn't reach here
        return LeaseAcquireResult(
            acquired=False,
            run_id=run_id,
            owner_id=worker.worker_id,
            reason="Unexpected lease state.",
        )

    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Renew an existing lease held by the same worker.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        policy = policy or LeasePolicy()
        existing = self._leases.get(run_id) if hasattr(self, "_leases") else None
        if existing is None:
            # Try DB
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workflow_run_leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row:
                    existing = self._row_to_lease(row)
                    self._leases = getattr(self, "_leases", {})
                    self._leases[run_id] = existing
                else:
                    raise KeyError(f"No active lease for workflow run '{run_id}'.")
            finally:
                conn.close()
        if existing.owner_id != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing.owner_id}', not '{worker.worker_id}'."
            )
        if existing.released_at is not None:
            raise KeyError(f"Lease for workflow run '{run_id}' has been released.")

        now = _utcnow()
        if now >= existing.expires_at:
            raise KeyError(
                f"Lease for workflow run '{run_id}' has expired "
                f"(expired at {existing.expires_at.isoformat()})."
            )

        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=existing.acquired_at,
            expires_at=now + _timedelta_from_seconds(policy.ttl_seconds),
            renewed_at=now,
            version=existing.version + 1,
        )
        self._leases = getattr(self, "_leases", {})
        self._leases[run_id] = renewed
        self._persist_lease(renewed)
        return renewed

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a held lease.

        Raises:
            KeyError: If the run has no lease or is leased by a different worker.
        """
        existing = self._leases.get(run_id) if hasattr(self, "_leases") else None
        if existing is None:
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workflow_run_leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row:
                    existing = self._row_to_lease(row)
                    self._leases = getattr(self, "_leases", {})
                    self._leases[run_id] = existing
                else:
                    raise KeyError(f"No active lease for workflow run '{run_id}'.")
            finally:
                conn.close()
        if existing.owner_id != worker.worker_id:
            raise KeyError(
                f"Lease for workflow run '{run_id}' is held by "
                f"'{existing.owner_id}', not '{worker.worker_id}'."
            )
        if existing.released_at is not None:
            raise KeyError(f"Lease for workflow run '{run_id}' has already been released.")

        now = _utcnow()
        released = WorkflowRunLease(
            run_id=run_id,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=existing.expires_at,
            renewed_at=existing.renewed_at,
            released_at=now,
            version=existing.version,
        )
        self._leases = getattr(self, "_leases", {})
        self._leases[run_id] = released
        self._persist_lease(released)
        return released

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current active (non-released) lease for a run.

        Re-syncs from DB if not in the local cache.
        """
        # Check local cache first
        local = getattr(self, "_leases", {}).get(run_id)
        if local is not None:
            if local.released_at is not None:
                return None
            # Re-sync from DB to pick up changes from other instances
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workflow_run_leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row:
                    db_lease = self._row_to_lease(row)
                    self._leases = getattr(self, "_leases", {})
                    self._leases[run_id] = db_lease
                    if db_lease.released_at is not None:
                        return None
                    return db_lease
            finally:
                conn.close()
            return local

        # Not in cache — query DB
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM workflow_run_leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            lease = self._row_to_lease(row)
            self._leases = getattr(self, "_leases", {})
            self._leases[run_id] = lease
            if lease.released_at is not None:
                return None
            return lease
        finally:
            conn.close()

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List leases that have expired and are not yet released."""
        cutoff = before or _utcnow()
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM workflow_run_leases "
                "WHERE released_at IS NULL AND expires_at <= ?",
                (cutoff.isoformat(),),
            ).fetchall()
            return [self._row_to_lease(row) for row in rows]
        finally:
            conn.close()

    def _persist_lease(self, lease: WorkflowRunLease) -> None:
        """Persist a lease to SQLite (INSERT OR REPLACE)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_run_leases
                    (run_id, owner_id, acquired_at, expires_at, renewed_at,
                     released_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self._lease_to_row(lease),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_lease_backend(
    backend_type: str = "state_store",
    state_store: object | None = None,
    db_path: str | None = None,
) -> WorkflowLeaseBackend:
    """Create a lease backend.

    Args:
        backend_type: Backend type — ``"state_store"``, ``"memory"``,
            or ``"sqlite"``.
        state_store: Required when ``backend_type="state_store"``.
        db_path: Required when ``backend_type="sqlite"``.

    Returns:
        A ``WorkflowLeaseBackend`` implementation.

    Raises:
        ValueError: If ``backend_type`` is unknown or required args are missing.
    """
    if backend_type == "state_store":
        if state_store is None:
            raise ValueError(
                "state_store is required when backend_type='state_store'. "
                "Provide a WorkflowStateStore instance."
            )
        return StateStoreLeaseBackend(state_store)
    if backend_type == "memory":
        return InMemoryWorkflowLeaseBackend()
    if backend_type == "sqlite":
        if not db_path:
            raise ValueError(
                "db_path is required when backend_type='sqlite'. "
                "Provide a path like '.agent_app/workflow_leases.db'."
            )
        return SQLiteWorkflowLeaseBackend(db_path=db_path)
    raise ValueError(
        f"Unknown lease backend type '{backend_type}'. "
        "Supported: 'state_store', 'memory', 'sqlite'."
    )
