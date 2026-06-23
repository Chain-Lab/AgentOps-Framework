"""Archive cleanup checkpoint models and store.

Phase 55 Task 6: Resumable archive cleanup with checkpoint tracking.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ArchiveCleanupPolicy(BaseModel):
    """Policy for archive cleanup operations."""

    enabled: bool = Field(default=True, description="Whether archive cleanup is active")
    batch_size: int = Field(default=500, description="Records per cleanup batch")
    rollup_retention_days: int = Field(default=90, description="Days to retain rollup data")
    checkpoint_retention_days: int = Field(default=30, description="Days to retain checkpoints")
    archive_dir: str = Field(
        default=".agent_app/archives/federation_notifications",
        description="Directory for archive files",
    )
    archive_format: str = Field(default="jsonl", description="Archive format")


class ArchiveCheckpoint(BaseModel):
    """Checkpoint record for resumable archive cleanup."""

    checkpoint_id: str = Field(..., description="Unique checkpoint ID (acp_ prefix)")
    data_type: str = Field(..., description="Data type: rollup, event, alert, attempt")
    last_processed_id: str | None = Field(default=None, description="Last processed record ID")
    last_processed_at: datetime | None = Field(default=None, description="Timestamp of last processed record")
    records_processed: int = Field(default=0, description="Total records processed in this run")
    batch_size: int = Field(default=500, description="Batch size used")
    is_complete: bool = Field(default=False, description="Whether cleanup is fully complete")
    created_at: datetime = Field(..., description="Checkpoint creation time")
    updated_at: datetime = Field(..., description="Last update time")

    @field_validator("checkpoint_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("acp_"):
            raise ValueError(f"checkpoint_id must start with 'acp_', got '{v}'")
        return v


class ArchiveCleanupResult(BaseModel):
    """Result from an archive cleanup run."""

    dry_run: bool = Field(default=False)
    data_type: str = Field(default="")
    records_processed: int = Field(default=0)
    records_archived: int = Field(default=0)
    records_deleted: int = Field(default=0)
    checkpoint_id: str | None = Field(default=None)
    is_complete: bool = Field(default=False)
    archive_files: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Store Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ArchiveCheckpointStore(Protocol):
    """Protocol for archive checkpoint persistence."""

    async def record_checkpoint(self, checkpoint: ArchiveCheckpoint) -> ArchiveCheckpoint: ...
    async def get_checkpoint(self, checkpoint_id: str) -> ArchiveCheckpoint | None: ...
    async def list_checkpoints(self, data_type: str | None = None) -> list[ArchiveCheckpoint]: ...
    async def get_latest_checkpoint(self, data_type: str) -> ArchiveCheckpoint | None: ...
    async def delete_checkpoint(self, checkpoint_id: str) -> None: ...
    async def prune_old_checkpoints(self, older_than: datetime) -> int: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryArchiveCheckpointStore:
    """In-memory archive checkpoint store."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, ArchiveCheckpoint] = {}
        self._by_type: dict[str, list[str]] = {}

    async def record_checkpoint(self, checkpoint: ArchiveCheckpoint) -> ArchiveCheckpoint:
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        dtype = checkpoint.data_type
        if dtype not in self._by_type:
            self._by_type[dtype] = []
        if checkpoint.checkpoint_id not in self._by_type[dtype]:
            self._by_type[dtype].append(checkpoint.checkpoint_id)
        return checkpoint

    async def get_checkpoint(self, checkpoint_id: str) -> ArchiveCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    async def list_checkpoints(self, data_type: str | None = None) -> list[ArchiveCheckpoint]:
        if data_type is not None:
            ids = self._by_type.get(data_type, [])
            return [self._checkpoints[i] for i in ids if i in self._checkpoints]
        return list(self._checkpoints.values())

    async def get_latest_checkpoint(self, data_type: str) -> ArchiveCheckpoint | None:
        checkpoints = await self.list_checkpoints(data_type=data_type)
        if not checkpoints:
            return None
        return max(checkpoints, key=lambda c: c.updated_at)

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        checkpoint = self._checkpoints.pop(checkpoint_id, None)
        if checkpoint is not None:
            dtype = checkpoint.data_type
            if dtype in self._by_type:
                self._by_type[dtype] = [i for i in self._by_type[dtype] if i != checkpoint_id]

    async def prune_old_checkpoints(self, older_than: datetime) -> int:
        to_delete = [
            cid for cid, c in self._checkpoints.items()
            if c.updated_at < older_than
        ]
        for cid in to_delete:
            await self.delete_checkpoint(cid)
        return len(to_delete)


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteArchiveCheckpointStore:
    """SQLite-backed archive checkpoint store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS archive_cleanup_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                data_type TEXT NOT NULL,
                last_processed_id TEXT,
                last_processed_at TEXT,
                records_processed INTEGER NOT NULL DEFAULT 0,
                batch_size INTEGER NOT NULL DEFAULT 500,
                is_complete INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_acp_data_type
                ON archive_cleanup_checkpoints(data_type);
        """)
        self._conn.commit()

    async def record_checkpoint(self, checkpoint: ArchiveCheckpoint) -> ArchiveCheckpoint:
        self._conn.execute(
            """INSERT OR REPLACE INTO archive_cleanup_checkpoints
               (checkpoint_id, data_type, last_processed_id, last_processed_at,
                records_processed, batch_size, is_complete, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                checkpoint.checkpoint_id,
                checkpoint.data_type,
                checkpoint.last_processed_id,
                checkpoint.last_processed_at.isoformat() if checkpoint.last_processed_at else None,
                checkpoint.records_processed,
                checkpoint.batch_size,
                1 if checkpoint.is_complete else 0,
                checkpoint.created_at.isoformat(),
                checkpoint.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return checkpoint

    async def get_checkpoint(self, checkpoint_id: str) -> ArchiveCheckpoint | None:
        row = self._conn.execute(
            "SELECT * FROM archive_cleanup_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def list_checkpoints(self, data_type: str | None = None) -> list[ArchiveCheckpoint]:
        if data_type is not None:
            rows = self._conn.execute(
                "SELECT * FROM archive_cleanup_checkpoints WHERE data_type=? ORDER BY updated_at DESC",
                (data_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM archive_cleanup_checkpoints ORDER BY updated_at DESC",
            ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    async def get_latest_checkpoint(self, data_type: str) -> ArchiveCheckpoint | None:
        row = self._conn.execute(
            "SELECT * FROM archive_cleanup_checkpoints WHERE data_type=? ORDER BY updated_at DESC LIMIT 1",
            (data_type,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        self._conn.execute(
            "DELETE FROM archive_cleanup_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        )
        self._conn.commit()

    async def prune_old_checkpoints(self, older_than: datetime) -> int:
        cursor = self._conn.execute(
            "SELECT checkpoint_id FROM archive_cleanup_checkpoints WHERE updated_at < ?",
            (older_than.isoformat(),),
        )
        to_delete = [row[0] for row in cursor.fetchall()]
        for cid in to_delete:
            self._conn.execute(
                "DELETE FROM archive_cleanup_checkpoints WHERE checkpoint_id=?",
                (cid,),
            )
        self._conn.commit()
        return len(to_delete)

    def _row_to_checkpoint(self, row: sqlite3.Row) -> ArchiveCheckpoint:
        data = dict(row)
        data["last_processed_at"] = (
            datetime.fromisoformat(data["last_processed_at"])
            if data["last_processed_at"] is not None
            else None
        )
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        data["is_complete"] = bool(data.pop("is_complete"))
        data["records_processed"] = data.pop("records_processed")
        data["batch_size"] = data.pop("batch_size")
        return ArchiveCheckpoint(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_archive_checkpoint_store(backend: str = "memory", **kwargs: Any) -> ArchiveCheckpointStore:
    """Create an archive checkpoint store."""
    if backend == "memory":
        return InMemoryArchiveCheckpointStore()
    if backend == "sqlite":
        db_path = kwargs.get("db_path", ".agent_app/archives/archive_checkpoints.db")
        return SQLiteArchiveCheckpointStore(db_path)
    raise ValueError(f"Unknown archive checkpoint store backend: {backend}")
