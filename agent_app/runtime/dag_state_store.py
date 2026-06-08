"""WorkflowStateStore implementations — InMemory and SQLite.

Phase 14.0: Persists DAG execution state (workflow runs, node executions,
events, and compensation handlers) for crash inspection and recovery planning.

Reuses Phase 9 storage conventions (JSON serialization, ISO datetime,
stdlib sqlite3) while providing a DAG-specific store protocol.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_app.runtime.dag_snapshot import (
    DagRunSnapshot,
    SnapshotCorruptionError,
    SnapshotUnsupportedVersionError,
    SnapshotWriteError,
    snapshot_status_is_resumable,
)

from agent_app.runtime.dag_run_state import (
    CompensationExecutionState,
    CompensationRunStatus,
    IdempotencyRecord,
    LeaseAcquireResult,
    LeasePolicy,
    LeaseStatus,
    NodeExecutionState,
    NodeResumeDecision,
    NodeRunStatus,
    ResumePlan,
    ResumePolicy,
    RecoveryPlan,
    WorkflowEventState,
    WorkflowRunLease,
    WorkflowRunState,
    WorkflowRunStatus,
    WorkflowStateStore,
    WorkerIdentity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a unique event/workflow identifier."""
    return str(uuid.uuid4())


def _timedelta(seconds: int) -> Any:
    """Create a timedelta for datetime arithmetic."""
    import datetime as _dt
    return _dt.timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# InMemoryWorkflowStateStore
# ---------------------------------------------------------------------------


class InMemoryWorkflowStateStore:
    """In-memory DAG execution state store.

    Suitable for development and testing. State is lost when the process
    exits. All operations are in-memory dict lookups with O(1) access.
    """

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunState] = {}
        self._nodes: dict[str, dict[str, NodeExecutionState]] = {}  # run_id -> {node_id -> state}
        self._events: dict[str, list[WorkflowEventState]] = {}  # run_id -> [events]
        self._compensations: dict[str, list[CompensationExecutionState]] = {}  # run_id -> [states]
        self._leases: dict[str, WorkflowRunLease] = {}  # run_id -> lease
        self._idempotency: dict[str, IdempotencyRecord] = {}  # key -> record
        self._snapshots: dict[str, list[DagRunSnapshot]] = {}  # run_id -> [snapshots]

    # -- Workflow runs --

    async def create_run(self, state: WorkflowRunState) -> None:
        """Create a new workflow run record.

        Args:
            state: The initial workflow run state.
        """
        self._runs[state.run_id] = state
        self._nodes.setdefault(state.run_id, {})
        self._events.setdefault(state.run_id, [])
        self._compensations.setdefault(state.run_id, [])

    async def update_run(self, run_id: str, **updates: Any) -> None:
        """Update fields on an existing workflow run.

        Args:
            run_id: The run identifier.
            **updates: Field names and values to update.

        Raises:
            KeyError: If the run_id is not found.
        """
        if run_id not in self._runs:
            raise KeyError(f"Workflow run '{run_id}' not found.")
        run = self._runs[run_id]
        for key, value in updates.items():
            if hasattr(run, key):
                setattr(run, key, value)
        run.updated_at = _now()

    async def get_run(self, run_id: str) -> WorkflowRunState:
        """Retrieve a workflow run by ID.

        Args:
            run_id: The run identifier.

        Returns:
            The WorkflowRunState.

        Raises:
            KeyError: If the run_id is not found.
        """
        if run_id not in self._runs:
            raise KeyError(f"Workflow run '{run_id}' not found.")
        return self._runs[run_id]

    async def list_runs(self) -> list[WorkflowRunState]:
        """List all workflow runs.

        Returns:
            List of all WorkflowRunState objects.
        """
        return list(self._runs.values())

    # -- Node execution states --

    async def upsert_node(self, state: NodeExecutionState) -> None:
        """Create or update a node execution state.

        Args:
            state: The node execution state to persist.
        """
        nodes = self._nodes.setdefault(state.run_id, {})
        nodes[state.node_id] = state

    async def get_node(self, run_id: str, node_id: str) -> NodeExecutionState | None:
        """Retrieve a specific node execution state.

        Args:
            run_id: Parent workflow run ID.
            node_id: Node identifier.

        Returns:
            The NodeExecutionState, or None if not found.
        """
        return self._nodes.get(run_id, {}).get(node_id)

    async def list_nodes(self, run_id: str) -> list[NodeExecutionState]:
        """List all node execution states for a workflow run.

        Args:
            run_id: Parent workflow run ID.

        Returns:
            List of NodeExecutionState objects.
        """
        return list(self._nodes.get(run_id, {}).values())

    # -- Events --

    async def append_event(self, event: WorkflowEventState) -> None:
        """Append an event to the workflow event log.

        Args:
            event: The event to record.
        """
        self._events.setdefault(event.run_id, []).append(event)

    async def list_events(self, run_id: str) -> list[WorkflowEventState]:
        """List all events for a workflow run.

        Args:
            run_id: Parent workflow run ID.

        Returns:
            Chronological list of WorkflowEventState objects.
        """
        return list(self._events.get(run_id, []))

    # -- Compensation states --

    async def upsert_compensation(
        self, state: CompensationExecutionState
    ) -> None:
        """Create or update a compensation execution state.

        Args:
            state: The compensation state to persist.
        """
        compensations = self._compensations.setdefault(state.run_id, [])
        for i, existing in enumerate(compensations):
            if existing.node_id == state.node_id:
                compensations[i] = state
                return
        compensations.append(state)

    async def list_compensations(
        self, run_id: str
    ) -> list[CompensationExecutionState]:
        """List all compensation states for a workflow run.

        Args:
            run_id: Parent workflow run ID.

        Returns:
            List of CompensationExecutionState objects.
        """
        return list(self._compensations.get(run_id, []))

    # -- Recovery helpers (in-memory) --

    async def build_recovery_plan(self, run_id: str) -> RecoveryPlan:
        """Build a recovery plan from in-memory state.

        Args:
            run_id: The workflow run to assess.

        Returns:
            A RecoveryPlan with resumability assessment.
        """
        nodes = await self.list_nodes(run_id)
        compensations = await self.list_compensations(run_id)
        return _build_recovery_plan(run_id, nodes, compensations)

    async def get_node_outputs(self, run_id: str) -> dict[str, Any]:
        """Get outputs from completed nodes for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            Dict mapping node_id -> output for completed nodes.
            Nodes without output or not completed are excluded.
        """
        outputs: dict[str, Any] = {}
        for node in await self.list_nodes(run_id):
            if node.status == NodeRunStatus.COMPLETED.value and node.output is not None:
                outputs[node.node_id] = node.output
        return outputs

    async def build_resume_plan(
        self, run_id: str, policy: ResumePolicy | None = None
    ) -> ResumePlan:
        """Build a resume plan from in-memory state.

        Args:
            run_id: The workflow run to assess.
            policy: Optional resume policy. Uses defaults if not provided.

        Returns:
            A ResumePlan with per-node decisions.
        """
        policy = policy or ResumePolicy()
        nodes = await self.list_nodes(run_id)
        compensations = await self.list_compensations(run_id)
        run = await self.get_run(run_id)
        return _build_resume_plan(run_id, run, nodes, compensations, policy)

    # -- Lease management (Phase 15) --

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
            policy: Optional lease policy.

        Returns:
            LeaseAcquireResult indicating success or denial.
        """
        policy = policy or LeasePolicy()
        now = _now()

        # Check if run exists
        if run_id not in self._runs:
            return LeaseAcquireResult(
                acquired=False,
                run_id=run_id,
                owner_id=worker.worker_id,
                reason=f"Workflow run '{run_id}' not found.",
            )

        existing = self._leases.get(run_id)

        # No existing lease — acquire succeeds
        if existing is None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta(policy.ttl_seconds),
            )
            self._leases[run_id] = lease
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing lease — check if it's released
        if existing.released_at is not None:
            # Released — allow new acquire
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta(policy.ttl_seconds),
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
                    expires_at=now + _timedelta(policy.ttl_seconds),
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

        # Active, non-expired lease — deny
        if existing.owner_id == worker.worker_id:
            # Same owner re-acquiring — refresh
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=existing.acquired_at,
                expires_at=now + _timedelta(policy.ttl_seconds),
                renewed_at=now,
                version=existing.version + 1,
            )
            self._leases[run_id] = lease
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        return LeaseAcquireResult(
            acquired=False,
            run_id=run_id,
            owner_id=worker.worker_id,
            reason=f"Run is currently leased by '{existing.owner_id}' until {existing.expires_at.isoformat()}.",
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

        Args:
            run_id: The workflow run to renew the lease for.
            worker: The worker requesting renewal.
            policy: Optional lease policy (uses existing lease TTL if not provided).

        Returns:
            The renewed WorkflowRunLease.

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

        now = _now()
        if now >= existing.expires_at:
            raise KeyError(
                f"Lease for workflow run '{run_id}' has expired "
                f"(expired at {existing.expires_at.isoformat()})."
            )

        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=existing.acquired_at,
            expires_at=now + _timedelta(policy.ttl_seconds),
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

        Args:
            run_id: The workflow run to release the lease for.
            worker: The worker releasing the lease.

        Returns:
            The released WorkflowRunLease.

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

        now = _now()
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
        """Get the current lease for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            The WorkflowRunLease, or None if no lease exists.
        """
        lease = self._leases.get(run_id)
        if lease is None or lease.released_at is not None:
            return None
        return lease

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List all expired, unreleased leases.

        Args:
            before: Only return leases that expired before this time.
                Defaults to now.

        Returns:
            List of expired WorkflowRunLease objects.
        """
        cutoff = before or _now()
        expired = []
        for lease in self._leases.values():
            if (
                lease.released_at is None
                and lease.expires_at <= cutoff
            ):
                expired.append(lease)
        return expired

    # -- Idempotency (Phase 15) --

    async def put_idempotency_record(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord:
        """Store an idempotency record.

        Overwrites any existing record with the same key.

        Args:
            record: The idempotency record to store.

        Returns:
            The stored IdempotencyRecord.
        """
        self._idempotency[record.key] = record
        return record

    async def get_idempotency_record(
        self,
        key: str,
    ) -> IdempotencyRecord | None:
        """Retrieve an idempotency record by key.

        Args:
            key: The idempotency key.

        Returns:
            The IdempotencyRecord, or None if not found.
        """
        return self._idempotency.get(key)

    async def reserve_idempotency_key(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord:
        """Atomically reserve an idempotency key (Phase 15.1).

        If the key does not exist, creates and returns the record.
        If the key exists with the same fingerprint, raises
        ``DuplicateIdempotencyKeyError``.
        If the key exists with a different fingerprint, raises
        ``IdempotencyKeyMismatchError``.

        In the in-memory store this is effectively atomic because the
        CPython GIL serialises dict operations within a single process.

        Args:
            record: The IdempotencyRecord with scope and request_fingerprint set.

        Returns:
            The created IdempotencyRecord.

        Raises:
            DuplicateIdempotencyKeyError: Key already used with same fingerprint.
            IdempotencyKeyMismatchError: Key already used with different fingerprint.
        """
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,
            IdempotencyKeyMismatchError,
        )

        scope = record.scope or ""
        # Use composite key (scope:key) for proper scope isolation
        composite_key = f"{scope}:{record.key}"

        existing = self._idempotency.get(composite_key)
        if existing is not None:
            if existing.request_fingerprint == record.request_fingerprint:
                raise DuplicateIdempotencyKeyError(
                    idempotency_key=record.key,
                    scope=scope,
                    operation=record.operation,
                    existing_run_id=existing.run_id,
                )
            raise IdempotencyKeyMismatchError(
                idempotency_key=record.key,
                scope=scope,
                operation=record.operation,
                existing_run_id=existing.run_id,
            )
        # Key does not exist — create it
        self._idempotency[composite_key] = record
        return record

    # -- Snapshots (Phase 16.0) --

    async def save_run_snapshot(
        self,
        snapshot: DagRunSnapshot,
    ) -> DagRunSnapshot:
        """Save a DAG execution snapshot.

        Overwrites any existing snapshot with the same snapshot_id
        (idempotent save).

        Args:
            snapshot: The snapshot to persist.

        Returns:
            The saved DagRunSnapshot.
        """
        snapshots = self._snapshots.setdefault(snapshot.run_id, [])
        # Update existing if same snapshot_id, otherwise append
        for i, existing in enumerate(snapshots):
            if existing.snapshot_id == snapshot.snapshot_id:
                snapshots[i] = snapshot
                break
        else:
            snapshots.append(snapshot)
        return snapshot

    async def get_latest_run_snapshot(
        self,
        run_id: str,
    ) -> DagRunSnapshot | None:
        """Get the most recent snapshot for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            The latest DagRunSnapshot, or None if no snapshots exist.
        """
        snapshots = self._snapshots.get(run_id, [])
        if not snapshots:
            return None
        return max(snapshots, key=lambda s: s.updated_at)

    async def list_run_snapshots(
        self,
        run_id: str,
    ) -> list[DagRunSnapshot]:
        """List all snapshots for a workflow run, ordered by updated_at ascending.

        Args:
            run_id: The workflow run ID.

        Returns:
            List of DagRunSnapshot instances in chronological order.
        """
        snapshots = self._snapshots.get(run_id, [])
        return sorted(snapshots, key=lambda s: s.updated_at)

    async def delete_run_snapshots(
        self,
        run_id: str,
    ) -> None:
        """Delete all snapshots for a workflow run.

        Args:
            run_id: The workflow run ID.
        """
        self._snapshots.pop(run_id, None)


# ---------------------------------------------------------------------------
# SQLiteWorkflowStateStore
# ---------------------------------------------------------------------------


class SQLiteWorkflowStateStore:
    """SQLite-backed DAG execution state store.

    Persists workflow execution state to a SQLite database file. Survives
    process restarts and can be shared across instances. Uses stdlib
    ``sqlite3`` — no ORM dependency.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/workflow_state.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        # In-memory caches for lease/idempotency (consistent with InMemory store)
        self._leases: dict[str, WorkflowRunLease] = {}
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._sync_leases_from_db()
        self._sync_idempotency_from_db()

    def _sync_leases_from_db(self) -> None:
        """Load existing leases from DB into the in-memory cache."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM workflow_run_leases WHERE released_at IS NULL"
            ).fetchall()
            self._leases = {row["run_id"]: _row_to_lease(row) for row in rows}
        except sqlite3.OperationalError:
            # Table doesn't exist yet (old DB schema)
            self._leases = {}

    def _sync_idempotency_from_db(self) -> None:
        """Load existing idempotency records from DB into the in-memory cache."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM workflow_idempotency"
            ).fetchall()
            self._idempotency = {row["key"]: _row_to_idempotency(row) for row in rows}
        except sqlite3.OperationalError:
            # Table doesn't exist yet (old DB schema)
            self._idempotency = {}

    def _init_db(self) -> None:
        """Create tables if they don't exist.

        Phase 15.1: The idempotency table uses UNIQUE(scope, key) for
        atomic enforcement.  Old databases without the scope column are
        migrated by _add_idempotency_columns().
        """
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id       TEXT PRIMARY KEY,
                workflow_name TEXT,
                status       TEXT NOT NULL,
                input_json   TEXT,
                output_json  TEXT,
                error_json   TEXT,
                started_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                completed_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS workflow_nodes (
                run_id       TEXT NOT NULL,
                node_id      TEXT NOT NULL,
                node_type    TEXT NOT NULL,
                status       TEXT NOT NULL,
                input_json   TEXT,
                output_json  TEXT,
                error_json   TEXT,
                started_at   TEXT,
                completed_at TEXT,
                attempts     INTEGER DEFAULT 0,
                metadata_json TEXT DEFAULT '{}',
                PRIMARY KEY (run_id, node_id)
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                event_id    TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                node_id     TEXT,
                event_type  TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_compensations (
                run_id        TEXT NOT NULL,
                node_id       TEXT NOT NULL,
                handler_name  TEXT,
                status        TEXT NOT NULL,
                error_json    TEXT,
                started_at    TEXT,
                completed_at  TEXT,
                metadata_json TEXT DEFAULT '{}',
                PRIMARY KEY (run_id, node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_wf_nodes_run
                ON workflow_nodes(run_id);
            CREATE INDEX IF NOT EXISTS idx_wf_events_run
                ON workflow_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_wf_events_type
                ON workflow_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_wf_comp_run
                ON workflow_compensations(run_id);

            -- Phase 15: Lease table
            CREATE TABLE IF NOT EXISTS workflow_run_leases (
                run_id       TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                acquired_at  TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                renewed_at   TEXT,
                released_at  TEXT,
                version      INTEGER NOT NULL DEFAULT 1
            );

            -- Phase 15.1: Idempotency table with UNIQUE(scope, key) for atomic enforcement
            CREATE TABLE IF NOT EXISTS workflow_idempotency (
                key          TEXT NOT NULL,
                run_id       TEXT NOT NULL,
                operation    TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                result_ref   TEXT,
                scope        TEXT NOT NULL DEFAULT '',
                request_fingerprint TEXT,
                PRIMARY KEY (scope, key)
            );

            CREATE INDEX IF NOT EXISTS idx_lease_expires
                ON workflow_run_leases(expires_at);
            CREATE INDEX IF NOT EXISTS idx_idempotency_run
                ON workflow_idempotency(run_id);

            -- Phase 16.0: DAG execution snapshot table
            CREATE TABLE IF NOT EXISTS dag_run_snapshots (
                snapshot_id    TEXT PRIMARY KEY,
                run_id         TEXT NOT NULL,
                workflow_name  TEXT,
                status         TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                snapshot_json  TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dag_run_snapshots_run_updated
                ON dag_run_snapshots(run_id, updated_at);
            """
        )
        self._conn.commit()
        # Phase 15.1: Migrate old idempotency table if it lacks scope column
        self._add_idempotency_columns()

    def _add_idempotency_columns(self) -> None:
        """Add scope and request_fingerprint columns to old idempotency tables.

        Old Phase 15 databases used PRIMARY KEY (key) without scope.
        SQLite does not support DROP CONSTRAINT, so we:
        1. Check if the scope column already exists.
        2. If not, create a new table with the correct schema.
        3. Copy data across (defaulting scope to empty string for old records).
        4. Drop the old table and rename the new one.
        """
        try:
            # Check if scope column already exists
            cols = self._conn.execute("PRAGMA table_info(workflow_idempotency)").fetchall()
            col_names = {c[1] for c in cols}  # column name is index 1
            if "scope" in col_names and "request_fingerprint" in col_names:
                return  # Already migrated
            if "scope" not in col_names:
                # Old schema — migrate
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_idempotency_new (
                        key          TEXT NOT NULL,
                        run_id       TEXT NOT NULL,
                        operation    TEXT NOT NULL,
                        created_at   TEXT NOT NULL,
                        result_ref   TEXT,
                        scope        TEXT NOT NULL DEFAULT '',
                        request_fingerprint TEXT,
                        PRIMARY KEY (scope, key)
                    );
                    INSERT OR IGNORE INTO workflow_idempotency_new
                        (key, run_id, operation, created_at, result_ref, scope, request_fingerprint)
                    SELECT key, run_id, operation, created_at, result_ref, '', NULL
                    FROM workflow_idempotency;
                    DROP TABLE IF EXISTS workflow_idempotency;
                    ALTER TABLE workflow_idempotency_new RENAME TO workflow_idempotency;
                    CREATE INDEX IF NOT EXISTS idx_idempotency_run
                        ON workflow_idempotency(run_id);
                    """
                )
                self._conn.commit()
        except sqlite3.OperationalError:
            # Table doesn't exist or migration failed — not critical
            pass

    # -- Workflow runs --

    async def create_run(self, state: WorkflowRunState) -> None:
        """Create a new workflow run record (INSERT, no-op if exists)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO workflow_runs
                (run_id, workflow_name, status, input_json, output_json,
                 error_json, started_at, updated_at, completed_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.run_id,
                state.workflow_name,
                state.status,
                _json(state.input),
                _json(state.output),
                _json(state.error),
                state.started_at.isoformat(),
                state.updated_at.isoformat(),
                state.completed_at.isoformat() if state.completed_at else None,
                _json(state.metadata),
            ),
        )
        self._conn.commit()

    async def update_run(self, run_id: str, **updates: Any) -> None:
        """Update specific fields on a workflow run.

        Maps Pydantic model field names to database column names.
        """
        if not updates:
            return

        # Map model field names to DB column names
        db_column_map = {
            "status": "status",
            "output": "output_json",
            "error": "error_json",
            "completed_at": "completed_at",
            "input": "input_json",
            "metadata": "metadata_json",
        }

        db_updates: dict[str, Any] = {}
        for key, value in updates.items():
            col = db_column_map.get(key, key)
            if key in ("output", "error", "input", "metadata"):
                db_updates[col] = _json(value)
            elif key == "completed_at" and isinstance(value, datetime):
                db_updates[col] = value.isoformat()
            else:
                db_updates[col] = value

        db_updates["updated_at"] = _now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in db_updates)
        values = list(db_updates.values())
        values.append(run_id)
        self._conn.execute(
            f"UPDATE workflow_runs SET {set_clause} WHERE run_id = ?",
            values,
        )
        self._conn.commit()

    async def get_run(self, run_id: str) -> WorkflowRunState:
        """Retrieve a workflow run by ID.

        Raises:
            KeyError: If the run_id is not found.
        """
        row = self._conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Workflow run '{run_id}' not found.")
        return _row_to_run(row)

    async def list_runs(self) -> list[WorkflowRunState]:
        """List all workflow runs."""
        rows = self._conn.execute("SELECT * FROM workflow_runs").fetchall()
        return [_row_to_run(row) for row in rows]

    # -- Node execution states --

    async def upsert_node(self, state: NodeExecutionState) -> None:
        """Create or update a node execution state (UPSERT)."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO workflow_nodes
                (run_id, node_id, node_type, status, input_json, output_json,
                 error_json, started_at, completed_at, attempts, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.run_id,
                state.node_id,
                state.node_type,
                state.status,
                _json(state.input),
                _json(state.output),
                _json(state.error),
                state.started_at.isoformat() if state.started_at else None,
                state.completed_at.isoformat() if state.completed_at else None,
                state.attempts,
                _json(state.metadata),
            ),
        )
        self._conn.commit()

    async def get_node(
        self, run_id: str, node_id: str
    ) -> NodeExecutionState | None:
        """Retrieve a specific node execution state."""
        row = self._conn.execute(
            "SELECT * FROM workflow_nodes WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_node(row)

    async def list_nodes(self, run_id: str) -> list[NodeExecutionState]:
        """List all node execution states for a workflow run."""
        rows = self._conn.execute(
            "SELECT * FROM workflow_nodes WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [_row_to_node(row) for row in rows]

    # -- Events --

    async def append_event(self, event: WorkflowEventState) -> None:
        """Append an event to the workflow event log."""
        self._conn.execute(
            """
            INSERT INTO workflow_events
                (event_id, run_id, node_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.run_id,
                event.node_id,
                event.event_type,
                _json(event.payload),
                event.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    async def list_events(self, run_id: str) -> list[WorkflowEventState]:
        """List all events for a workflow run (chronological order)."""
        rows = self._conn.execute(
            "SELECT * FROM workflow_events WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    # -- Compensation states --

    async def upsert_compensation(
        self, state: CompensationExecutionState
    ) -> None:
        """Create or update a compensation execution state."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO workflow_compensations
                (run_id, node_id, handler_name, status, error_json,
                 started_at, completed_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.run_id,
                state.node_id,
                state.handler_name,
                state.status,
                _json(state.error),
                state.started_at.isoformat() if state.started_at else None,
                state.completed_at.isoformat() if state.completed_at else None,
                _json(state.metadata),
            ),
        )
        self._conn.commit()

    async def list_compensations(
        self, run_id: str
    ) -> list[CompensationExecutionState]:
        """List all compensation states for a workflow run."""
        rows = self._conn.execute(
            "SELECT * FROM workflow_compensations WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return [_row_to_compensation(row) for row in rows]

    # -- Recovery helpers (SQLite) --

    async def build_recovery_plan(self, run_id: str) -> RecoveryPlan:
        """Build a recovery plan from persisted SQLite state.

        Args:
            run_id: The workflow run to assess.

        Returns:
            A RecoveryPlan with resumability assessment.
        """
        nodes = await self.list_nodes(run_id)
        compensations = await self.list_compensations(run_id)
        return _build_recovery_plan(run_id, nodes, compensations)

    async def get_node_outputs(self, run_id: str) -> dict[str, Any]:
        """Get outputs from completed nodes for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            Dict mapping node_id -> output for completed nodes.
        """
        outputs: dict[str, Any] = {}
        for node in await self.list_nodes(run_id):
            if node.status == NodeRunStatus.COMPLETED.value and node.output is not None:
                outputs[node.node_id] = node.output
        return outputs

    async def build_resume_plan(
        self, run_id: str, policy: ResumePolicy | None = None
    ) -> ResumePlan:
        """Build a resume plan from persisted SQLite state.

        Args:
            run_id: The workflow run to assess.
            policy: Optional resume policy. Uses defaults if not provided.

        Returns:
            A ResumePlan with per-node decisions.
        """
        policy = policy or ResumePolicy()
        nodes = await self.list_nodes(run_id)
        compensations = await self.list_compensations(run_id)
        try:
            run = await self.get_run(run_id)
        except KeyError:
            run = WorkflowRunState(run_id=run_id)
        return _build_resume_plan(run_id, run, nodes, compensations, policy)

    # -- Lease management (Phase 15) --

    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Attempt to acquire a lease on a workflow run (SQLite-backed).

        Uses the in-memory cache as the source of truth for current state,
        then persists to SQLite.

        Args:
            run_id: The workflow run to lease.
            worker: The worker requesting the lease.
            policy: Optional lease policy.

        Returns:
            LeaseAcquireResult indicating success or denial.
        """
        policy = policy or LeasePolicy()
        now = _now()

        # Check if run exists
        try:
            await self.get_run(run_id)
        except KeyError:
            return LeaseAcquireResult(
                acquired=False,
                run_id=run_id,
                owner_id=worker.worker_id,
                reason=f"Workflow run '{run_id}' not found.",
            )

        existing = self._leases.get(run_id)

        # No existing lease — acquire succeeds
        if existing is None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta(policy.ttl_seconds),
            )
            self._leases[run_id] = lease
            self._conn.execute(
                """
                INSERT INTO workflow_run_leases
                    (run_id, owner_id, acquired_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    worker.worker_id,
                    lease.acquired_at.isoformat(),
                    lease.expires_at.isoformat(),
                ),
            )
            self._conn.commit()
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing lease — check if released
        if existing.released_at is not None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + _timedelta(policy.ttl_seconds),
            )
            self._leases[run_id] = lease
            self._conn.execute(
                """
                INSERT OR REPLACE INTO workflow_run_leases
                    (run_id, owner_id, acquired_at, expires_at, renewed_at, released_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    worker.worker_id,
                    lease.acquired_at.isoformat(),
                    lease.expires_at.isoformat(),
                    None,
                    None,
                    lease.version,
                ),
            )
            self._conn.commit()
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

        # Existing active lease — check expiry
        if now >= existing.expires_at:
            if policy.allow_steal_expired:
                lease = WorkflowRunLease(
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    acquired_at=now,
                    expires_at=now + _timedelta(policy.ttl_seconds),
                    version=existing.version + 1,
                )
                self._leases[run_id] = lease
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO workflow_run_leases
                        (run_id, owner_id, acquired_at, expires_at, renewed_at, released_at, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        worker.worker_id,
                        lease.acquired_at.isoformat(),
                        lease.expires_at.isoformat(),
                        None,
                        None,
                        lease.version,
                    ),
                )
                self._conn.commit()
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

        # Active, non-expired lease
        if existing.owner_id == worker.worker_id:
            # Same owner re-acquiring — refresh
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=existing.acquired_at,
                expires_at=now + _timedelta(policy.ttl_seconds),
                renewed_at=now,
                version=existing.version + 1,
            )
            self._leases[run_id] = lease
            self._conn.execute(
                """
                UPDATE workflow_run_leases
                SET expires_at = ?, renewed_at = ?, version = ?
                WHERE run_id = ?
                """,
                (
                    lease.expires_at.isoformat(),
                    lease.renewed_at.isoformat(),
                    lease.version,
                    run_id,
                ),
            )
            self._conn.commit()
            return LeaseAcquireResult(
                acquired=True,
                run_id=run_id,
                owner_id=worker.worker_id,
                lease=lease,
            )

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
        """Renew an existing lease (SQLite-backed).

        Args:
            run_id: The workflow run to renew the lease for.
            worker: The worker requesting renewal.
            policy: Optional lease policy.

        Returns:
            The renewed WorkflowRunLease.

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

        now = _now()
        if now >= existing.expires_at:
            raise KeyError(
                f"Lease for workflow run '{run_id}' has expired "
                f"(expired at {existing.expires_at.isoformat()})."
            )

        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=existing.acquired_at,
            expires_at=now + _timedelta(policy.ttl_seconds),
            renewed_at=now,
            version=existing.version + 1,
        )
        self._leases[run_id] = renewed
        self._conn.execute(
            """
            UPDATE workflow_run_leases
            SET expires_at = ?, renewed_at = ?, version = ?
            WHERE run_id = ?
            """,
            (
                renewed.expires_at.isoformat(),
                renewed.renewed_at.isoformat(),
                renewed.version,
                run_id,
            ),
        )
        self._conn.commit()
        return renewed

    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a held lease (SQLite-backed).

        Args:
            run_id: The workflow run to release the lease for.
            worker: The worker releasing the lease.

        Returns:
            The released WorkflowRunLease.

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

        now = _now()
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
        self._conn.execute(
            """
            UPDATE workflow_run_leases
            SET released_at = ?
            WHERE run_id = ?
            """,
            (released.released_at.isoformat(), run_id),
        )
        self._conn.commit()
        return released

    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current lease for a workflow run (SQLite-backed).

        Args:
            run_id: The workflow run ID.

        Returns:
            The WorkflowRunLease, or None if no active lease exists.
        """
        lease = self._leases.get(run_id)
        if lease is None or lease.released_at is not None:
            return None
        # Re-sync from DB to catch changes from other instances
        row = self._conn.execute(
            "SELECT * FROM workflow_run_leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        lease = _row_to_lease(row)
        if lease.released_at is not None:
            self._leases.pop(run_id, None)
            return None
        self._leases[run_id] = lease
        return lease

    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List all expired, unreleased leases (SQLite-backed).

        Args:
            before: Only return leases that expired before this time.
                Defaults to now.

        Returns:
            List of expired WorkflowRunLease objects.
        """
        cutoff = before or _now()
        cutoff_str = cutoff.isoformat()
        rows = self._conn.execute(
            "SELECT * FROM workflow_run_leases WHERE released_at IS NULL AND expires_at <= ?",
            (cutoff_str,),
        ).fetchall()
        return [_row_to_lease(row) for row in rows]

    # -- Idempotency (Phase 15) --

    async def put_idempotency_record(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord:
        """Store an idempotency record (SQLite-backed).

        Uses INSERT OR REPLACE for upsert semantics.

        Args:
            record: The idempotency record to store.

        Returns:
            The stored IdempotencyRecord.
        """
        self._idempotency[record.key] = record
        self._conn.execute(
            """
            INSERT OR REPLACE INTO workflow_idempotency
                (key, run_id, operation, created_at, result_ref, scope, request_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.key,
                record.run_id,
                record.operation,
                record.created_at.isoformat(),
                record.result_ref,
                record.scope or "",
                record.request_fingerprint,
            ),
        )
        self._conn.commit()
        return record

    async def get_idempotency_record(
        self,
        key: str,
    ) -> IdempotencyRecord | None:
        """Retrieve an idempotency record by key (SQLite-backed).

        Args:
            key: The idempotency key.

        Returns:
            The IdempotencyRecord, or None if not found.
        """
        # Check cache first
        cached = self._idempotency.get(key)
        if cached is not None:
            return cached
        # Fall back to DB
        row = self._conn.execute(
            "SELECT * FROM workflow_idempotency WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        record = _row_to_idempotency(row)
        self._idempotency[key] = record
        return record

    async def reserve_idempotency_key(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord:
        """Atomically reserve an idempotency key (Phase 15.1, SQLite-backed).

        Uses a transaction + UNIQUE(scope, key) constraint to prevent
        race conditions between concurrent callers.

        Behavior:
          * Key does not exist → INSERT succeeds, return record.
          * Key exists + same fingerprint → IntegrityError → Duplicate.
          * Key exists + different fingerprint → IntegrityError → Mismatch.

        Args:
            record: The IdempotencyRecord with scope and request_fingerprint set.

        Returns:
            The created IdempotencyRecord.

        Raises:
            DuplicateIdempotencyKeyError: Key already used with same fingerprint.
            IdempotencyKeyMismatchError: Key already used with different fingerprint.
        """
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,
            IdempotencyKeyMismatchError,
        )

        scope = record.scope or ""

        try:
            self._conn.execute("BEGIN")
            # Check for existing record with the same scope + key
            row = self._conn.execute(
                "SELECT * FROM workflow_idempotency WHERE scope = ? AND key = ?",
                (scope, record.key),
            ).fetchone()

            if row is not None:
                existing = _row_to_idempotency(row)
                if existing.request_fingerprint == record.request_fingerprint:
                    self._conn.execute("ROLLBACK")
                    raise DuplicateIdempotencyKeyError(
                        idempotency_key=record.key,
                        scope=scope,
                        operation=record.operation,
                        existing_run_id=existing.run_id,
                    )
                self._conn.execute("ROLLBACK")
                raise IdempotencyKeyMismatchError(
                    idempotency_key=record.key,
                    scope=scope,
                    operation=record.operation,
                    existing_run_id=existing.run_id,
                )

            # Key does not exist — INSERT (UNIQUE constraint guarantees atomicity)
            self._conn.execute(
                """
                INSERT INTO workflow_idempotency
                    (key, run_id, operation, created_at, result_ref, scope, request_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.key,
                    record.run_id,
                    record.operation,
                    record.created_at.isoformat(),
                    record.result_ref,
                    scope,
                    record.request_fingerprint,
                ),
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            # UNIQUE constraint violation — another caller inserted first
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            # Re-read to determine the exact conflict type
            row = self._conn.execute(
                "SELECT * FROM workflow_idempotency WHERE scope = ? AND key = ?",
                (scope, record.key),
            ).fetchone()
            if row is not None:
                existing = _row_to_idempotency(row)
                if existing.request_fingerprint == record.request_fingerprint:
                    raise DuplicateIdempotencyKeyError(
                        idempotency_key=record.key,
                        scope=scope,
                        operation=record.operation,
                        existing_run_id=existing.run_id,
                    )
                raise IdempotencyKeyMismatchError(
                    idempotency_key=record.key,
                    scope=scope,
                    operation=record.operation,
                    existing_run_id=existing.run_id,
                )
            # Should not reach here, but fall through to create
            self._idempotency[record.key] = record
            return record

        # Update in-memory cache
        self._idempotency[record.key] = record
        return record

    # -- Snapshots (Phase 16.0) --

    async def save_run_snapshot(
        self,
        snapshot: DagRunSnapshot,
    ) -> DagRunSnapshot:
        """Save a DAG execution snapshot (SQLite-backed).

        Upserts by snapshot_id.  Updates the ``updated_at`` timestamp
        on each save.

        Args:
            snapshot: The snapshot to persist.

        Returns:
            The saved DagRunSnapshot.
        """
        now = _now()
        snapshot.updated_at = now
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dag_run_snapshots
                (snapshot_id, run_id, workflow_name, status, schema_version,
                 snapshot_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.run_id,
                snapshot.workflow_name,
                snapshot.status,
                snapshot.schema_version,
                snapshot.to_json(),
                snapshot.created_at.isoformat(),
                snapshot.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return snapshot

    async def get_latest_run_snapshot(
        self,
        run_id: str,
    ) -> DagRunSnapshot | None:
        """Get the most recent snapshot for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            The latest DagRunSnapshot, or None if no snapshots exist.
        """
        row = self._conn.execute(
            """
            SELECT snapshot_json FROM dag_run_snapshots
            WHERE run_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return DagRunSnapshot.from_json(row["snapshot_json"])
        except (ValueError, Exception) as exc:
            raise SnapshotCorruptionError(
                run_id=run_id,
                message=f"Failed to deserialize snapshot: {exc}",
            ) from exc

    async def list_run_snapshots(
        self,
        run_id: str,
    ) -> list[DagRunSnapshot]:
        """List all snapshots for a workflow run, ordered by updated_at ascending.

        Args:
            run_id: The workflow run ID.

        Returns:
            List of DagRunSnapshot instances in chronological order.
        """
        rows = self._conn.execute(
            """
            SELECT snapshot_json FROM dag_run_snapshots
            WHERE run_id = ?
            ORDER BY updated_at ASC
            """,
            (run_id,),
        ).fetchall()
        snapshots: list[DagRunSnapshot] = []
        for row in rows:
            try:
                snapshots.append(DagRunSnapshot.from_json(row["snapshot_json"]))
            except (ValueError, Exception):
                # Skip corrupted snapshots in list view
                continue
        return snapshots

    async def delete_run_snapshots(
        self,
        run_id: str,
    ) -> None:
        """Delete all snapshots for a workflow run.

        Args:
            run_id: The workflow run ID.
        """
        self._conn.execute(
            "DELETE FROM dag_run_snapshots WHERE run_id = ?",
            (run_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_workflow_state_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> "WorkflowStateStore":
    """Create a WorkflowStateStore implementation.

    Args:
        store_type: "memory" or "sqlite".
        db_path: Path for SQLite store (ignored for memory).

    Returns:
        A WorkflowStateStore implementation.

    Raises:
        ValueError: If store_type is unknown.
    """
    if store_type == "memory":
        return InMemoryWorkflowStateStore()
    if store_type == "sqlite":
        return SQLiteWorkflowStateStore(db_path=db_path or ".agent_app/workflow_state.db")
    raise ValueError(
        f"Unknown workflow_state store type '{store_type}'. "
        "Supported: 'memory', 'sqlite'."
    )


# ---------------------------------------------------------------------------
# Recovery plan builder (shared by both stores)
# ---------------------------------------------------------------------------


def _build_recovery_plan(
    run_id: str,
    nodes: list[NodeExecutionState],
    compensations: list[CompensationExecutionState],
) -> RecoveryPlan:
    """Build a RecoveryPlan from node and compensation state lists.

    Phase 14.0 recovery semantics:
    - A run is resumable only if it was RUNNING and at least one node
      was interrupted (running without completed_at) and NO nodes failed
      and NO compensation was triggered.
    - If all nodes completed, the run already finished — not resumable.
    - If compensation started, the run is in a non-resumable terminal state.
    - If any node failed, the run needs manual intervention.

    Args:
        run_id: The workflow run ID.
        nodes: All node execution states for the run.
        compensations: All compensation states for the run.

    Returns:
        A RecoveryPlan instance.
    """
    completed: list[str] = []
    interrupted: list[str] = []
    failed: list[str] = []
    compensation_started = bool(compensations)

    for node in nodes:
        if node.status in (
            NodeRunStatus.COMPLETED.value,
            NodeRunStatus.COMPENSATED.value,
        ):
            completed.append(node.node_id)
        elif node.status == NodeRunStatus.RUNNING:
            # Running without completed_at — node was interrupted mid-execution
            interrupted.append(node.node_id)
        elif node.status in (
            NodeRunStatus.FAILED.value,
            NodeRunStatus.COMPENSATION_FAILED.value,
        ):
            failed.append(node.node_id)
        elif node.status == NodeRunStatus.CANCELLED.value:
            interrupted.append(node.node_id)

    # Determine resumability
    if compensation_started:
        reason = "Compensation has already started — cannot resume."
    elif failed:
        reason = f"{len(failed)} node(s) failed — manual intervention required."
    elif not interrupted:
        # All nodes completed or skipped — run already finished
        reason = "All nodes completed or skipped — run already finished."
    else:
        # Nodes were interrupted mid-execution, no failures, no compensation
        reason = None  # Resumable

    resumable = reason is None

    return RecoveryPlan(
        run_id=run_id,
        resumable=resumable,
        completed_nodes=completed,
        interrupted_nodes=interrupted,
        failed_nodes=failed,
        compensation_started=compensation_started,
        reason=reason,
    )


def _build_resume_plan(
    run_id: str,
    run: WorkflowRunState,
    nodes: list[NodeExecutionState],
    compensations: list[CompensationExecutionState],
    policy: ResumePolicy,
) -> ResumePlan:
    """Build a ResumePlan from persisted state and resume policy.

    Phase 14.1 resume semantics:
    - completed nodes with output → "skip" (reuse persisted output)
    - completed nodes without output → "blocked" (cannot safely resume)
    - interrupted nodes (running without completed_at) → "retry" if policy allows
    - failed nodes → "retry" if policy.retry_failed, else "blocked"
    - compensation started → resumable=False (unless policy allows, which Phase 14.1 doesn't implement)
    - nodes depend on blocked upstream → "blocked"

    Args:
        run_id: The workflow run ID.
        run: The WorkflowRunState.
        nodes: All node execution states for the run.
        compensations: All compensation states for the run.
        policy: Resume policy controlling retry/skip behavior.

    Returns:
        A ResumePlan instance with per-node decisions.
    """
    decisions: list[NodeResumeDecision] = []
    completed_nodes: list[str] = []
    skipped_nodes: list[str] = []
    retry_nodes: list[str] = []
    blocked_nodes: list[str] = []

    # Check if compensation has started
    compensation_started = bool(compensations)
    if compensation_started and not policy.allow_after_compensation_started:
        return ResumePlan(
            run_id=run_id,
            workflow_name=run.workflow_name,
            resumable=False,
            reason="Cannot resume workflow run because compensation has already started.",
            completed_nodes=[n.node_id for n in nodes if n.status == NodeRunStatus.COMPLETED.value],
            blocked_nodes=[n.node_id for n in nodes if n.status in (
                NodeRunStatus.FAILED.value,
                NodeRunStatus.RUNNING.value,
            )],
        )

    # Build node status map
    node_map = {n.node_id: n for n in nodes}

    # First pass: decide per-node action
    node_actions: dict[str, str] = {}  # node_id -> action
    for node in nodes:
        if node.status == NodeRunStatus.COMPLETED.value:
            if policy.skip_completed:
                if node.output is not None:
                    node_actions[node.node_id] = "skip"
                    completed_nodes.append(node.node_id)
                    decisions.append(NodeResumeDecision(
                        node_id=node.node_id,
                        action="skip",
                        reason="Node completed successfully, reusing persisted output.",
                    ))
                else:
                    node_actions[node.node_id] = "blocked"
                    blocked_nodes.append(node.node_id)
                    decisions.append(NodeResumeDecision(
                        node_id=node.node_id,
                        action="blocked",
                        reason="Completed node has no persisted output — cannot safely resume.",
                    ))
            else:
                node_actions[node.node_id] = "run"
                retry_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="run",
                    reason="skip_completed=False, re-executing completed node.",
                ))
        elif node.status == NodeRunStatus.RUNNING.value:
            # Interrupted: running without completed_at
            if policy.retry_interrupted:
                node_actions[node.node_id] = "retry"
                retry_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="retry",
                    reason="Node was interrupted mid-execution.",
                ))
            else:
                node_actions[node.node_id] = "blocked"
                blocked_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="blocked",
                    reason="retry_interrupted=False.",
                ))
        elif node.status == NodeRunStatus.FAILED.value:
            if policy.retry_failed:
                node_actions[node.node_id] = "retry"
                retry_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="retry",
                    reason="Node previously failed, retry_failed=True.",
                ))
            else:
                node_actions[node.node_id] = "blocked"
                blocked_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="blocked",
                    reason="Node previously failed, retry_failed=False.",
                ))
        elif node.status == NodeRunStatus.SKIPPED.value:
            node_actions[node.node_id] = "skip"
            skipped_nodes.append(node.node_id)
            decisions.append(NodeResumeDecision(
                node_id=node.node_id,
                action="skip",
                reason="Node was skipped (upstream failure or condition).",
            ))
        elif node.status == NodeRunStatus.CANCELLED.value:
            if policy.retry_interrupted:
                node_actions[node.node_id] = "retry"
                retry_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="retry",
                    reason="Node was cancelled, retry_interrupted=True.",
                ))
            else:
                node_actions[node.node_id] = "blocked"
                blocked_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="blocked",
                    reason="Node was cancelled, retry_interrupted=False.",
                ))
        else:
            # PENDING → run (never executed); COMPENSATING/COMPENSATED → skip;
            # COMPENSATION_FAILED → blocked
            if node.status == NodeRunStatus.PENDING.value:
                node_actions[node.node_id] = "run"
                retry_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="run",
                    reason="Node was pending, never executed.",
                ))
            elif node.status in (
                NodeRunStatus.COMPENSATING.value,
                NodeRunStatus.COMPENSATED.value,
            ):
                node_actions[node.node_id] = "skip"
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="skip",
                    reason=f"Node status '{node.status}' — skip.",
                ))
            else:
                # COMPENSATION_FAILED
                node_actions[node.node_id] = "blocked"
                blocked_nodes.append(node.node_id)
                decisions.append(NodeResumeDecision(
                    node_id=node.node_id,
                    action="blocked",
                    reason=f"Node status '{node.status}' — cannot resume.",
                ))

    # Determine overall resumability
    # A run is resumable unless compensation has already started.
    # Blocked nodes (failed with retry_failed=False, etc.) are handled
    # downstream by DagExecutor.resume() — they don't block the resume
    # process itself.
    reason = None
    resumable = True

    return ResumePlan(
        run_id=run_id,
        workflow_name=run.workflow_name,
        resumable=resumable,
        decisions=decisions,
        completed_nodes=completed_nodes,
        skipped_nodes=skipped_nodes,
        retry_nodes=retry_nodes,
        blocked_nodes=blocked_nodes,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _json(value: Any) -> str | None:
    """Serialize a value to JSON, or return None if None."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _row_to_run(row: sqlite3.Row) -> WorkflowRunState:
    """Convert a workflow_runs DB row to WorkflowRunState."""
    data = dict(row)
    data["input"] = _parse_json(data.pop("input_json"))
    data["output"] = _parse_json(data.pop("output_json"))
    data["error"] = _parse_json(data.pop("error_json"))
    data["metadata"] = _parse_json(data.pop("metadata_json")) or {}
    for field in ("started_at", "updated_at"):
        val = data.pop(field)
        data[field] = datetime.fromisoformat(val) if val else _now()
    data["completed_at"] = (
        datetime.fromisoformat(data["completed_at"])
        if data.get("completed_at")
        else None
    )
    return WorkflowRunState(**data)


def _row_to_node(row: sqlite3.Row) -> NodeExecutionState:
    """Convert a workflow_nodes DB row to NodeExecutionState."""
    data = dict(row)
    data["input"] = _parse_json(data.pop("input_json"))
    data["output"] = _parse_json(data.pop("output_json"))
    data["error"] = _parse_json(data.pop("error_json"))
    data["metadata"] = _parse_json(data.pop("metadata_json")) or {}
    for field in ("started_at", "completed_at"):
        val = data.pop(field)
        data[field] = datetime.fromisoformat(val) if val else None
    return NodeExecutionState(**data)


def _row_to_event(row: sqlite3.Row) -> WorkflowEventState:
    """Convert a workflow_events DB row to WorkflowEventState."""
    data = dict(row)
    data["payload"] = _parse_json(data.pop("payload_json")) or {}
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return WorkflowEventState(**data)


def _row_to_compensation(row: sqlite3.Row) -> CompensationExecutionState:
    """Convert a workflow_compensations DB row to CompensationExecutionState."""
    data = dict(row)
    data["error"] = _parse_json(data.pop("error_json"))
    data["metadata"] = _parse_json(data.pop("metadata_json")) or {}
    for field in ("started_at", "completed_at"):
        val = data.pop(field)
        data[field] = datetime.fromisoformat(val) if val else None
    return CompensationExecutionState(**data)


def _parse_json(value: str | None) -> Any:
    """Parse a JSON string, returning None for null/empty values."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_lease(row: sqlite3.Row) -> WorkflowRunLease:
    """Convert a workflow_run_leases DB row to WorkflowRunLease."""
    data = dict(row)
    for field in ("acquired_at", "expires_at"):
        val = data.pop(field)
        data[field] = datetime.fromisoformat(val)
    for field in ("renewed_at", "released_at"):
        val = data.pop(field)
        data[field] = datetime.fromisoformat(val) if val else None
    return WorkflowRunLease(**data)


def _row_to_idempotency(row: sqlite3.Row) -> IdempotencyRecord:
    """Convert a workflow_idempotency DB row to IdempotencyRecord.

    Handles both old schema (no scope/request_fingerprint) and new
    Phase 15.1 schema with UNIQUE(scope, key).
    """
    data = dict(row)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    # Phase 15.1: Provide defaults for old rows that lack these columns
    data.setdefault("scope", None)
    data.setdefault("request_fingerprint", None)
    return IdempotencyRecord(**data)
