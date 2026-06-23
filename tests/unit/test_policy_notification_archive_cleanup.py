"""Tests for Phase 55 Task 6 — Archive Checkpoint and Resumable Cleanup."""
from __future__ import annotations

import os
import tempfile
import time

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.runtime.policy_rollout_federation_notification_archive_cleanup import (
    ArchiveCheckpoint,
    ArchiveCleanupPolicy,
    ArchiveCleanupResult,
    InMemoryArchiveCheckpointStore,
    SQLiteArchiveCheckpointStore,
    create_archive_checkpoint_store,
)
from agent_app.runtime.policy_rollout_federation_notification_archive_cleanup_service import (
    ResumableArchiveCleanup,
)
from agent_app.runtime.policy_rollout_federation_notification_rollup import (
    NotificationMetricsRollup,
)
from agent_app.governance.policy_change_event import PolicyChangeEventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint(
    checkpoint_id: str = "acp_rollup_20260101000000",
    data_type: str = "rollup",
    is_complete: bool = False,
    records_processed: int = 0,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> ArchiveCheckpoint:
    now = created_at or datetime.now(timezone.utc)
    return ArchiveCheckpoint(
        checkpoint_id=checkpoint_id,
        data_type=data_type,
        last_processed_id="nru_001",
        last_processed_at=now,
        records_processed=records_processed,
        batch_size=500,
        is_complete=is_complete,
        created_at=now,
        updated_at=updated_at or now,
    )


def _make_rollup(
    rollup_id: str = "nru_001",
    window_end: datetime | None = None,
) -> NotificationMetricsRollup:
    if window_end is None:
        window_end = datetime.now(timezone.utc) - timedelta(days=30)
    return NotificationMetricsRollup(
        rollup_id=rollup_id,
        granularity="daily",
        window_start=window_end - timedelta(days=1),
        window_end=window_end,
        total=100,
        sent=90,
        failed=5,
        suppressed=3,
        dlq=2,
        retry_scheduled=0,
        success_rate=0.9,
        failure_rate=0.05,
        dlq_rate=0.02,
        created_at=window_end,
    )


# ---------------------------------------------------------------------------
# InMemory store
# ---------------------------------------------------------------------------


class TestInMemoryArchiveCheckpointStore:
    @pytest.mark.asyncio
    async def test_record_and_get(self):
        store = InMemoryArchiveCheckpointStore()
        cp = _make_checkpoint()
        await store.record_checkpoint(cp)
        fetched = await store.get_checkpoint(cp.checkpoint_id)
        assert fetched is not None
        assert fetched.checkpoint_id == cp.checkpoint_id
        assert fetched.data_type == "rollup"

    @pytest.mark.asyncio
    async def test_list_by_data_type(self):
        store = InMemoryArchiveCheckpointStore()
        await store.record_checkpoint(_make_checkpoint(data_type="rollup"))
        await store.record_checkpoint(_make_checkpoint(data_type="event", checkpoint_id="acp_event_001"))
        rollups = await store.list_checkpoints(data_type="rollup")
        assert len(rollups) == 1
        assert rollups[0].data_type == "rollup"

    @pytest.mark.asyncio
    async def test_get_latest(self):
        store = InMemoryArchiveCheckpointStore()
        old = _make_checkpoint(
            checkpoint_id="acp_old",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        new = _make_checkpoint(
            checkpoint_id="acp_new",
            updated_at=datetime.now(timezone.utc),
        )
        await store.record_checkpoint(old)
        await store.record_checkpoint(new)
        latest = await store.get_latest_checkpoint("rollup")
        assert latest is not None
        assert latest.checkpoint_id == "acp_new"

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_when_empty(self):
        store = InMemoryArchiveCheckpointStore()
        assert await store.get_latest_checkpoint("rollup") is None

    @pytest.mark.asyncio
    async def test_delete_checkpoint(self):
        store = InMemoryArchiveCheckpointStore()
        cp = _make_checkpoint()
        await store.record_checkpoint(cp)
        await store.delete_checkpoint(cp.checkpoint_id)
        assert await store.get_checkpoint(cp.checkpoint_id) is None

    @pytest.mark.asyncio
    async def test_prune_old_checkpoints(self):
        store = InMemoryArchiveCheckpointStore()
        old_cp = _make_checkpoint(
            checkpoint_id="acp_old",
            updated_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        new_cp = _make_checkpoint(
            checkpoint_id="acp_new",
            updated_at=datetime.now(timezone.utc),
        )
        await store.record_checkpoint(old_cp)
        await store.record_checkpoint(new_cp)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        pruned = await store.prune_old_checkpoints(cutoff)
        assert pruned == 1
        assert await store.get_checkpoint("acp_old") is None
        assert await store.get_checkpoint("acp_new") is not None

    @pytest.mark.asyncio
    async def test_checkpoint_id_prefix_validation(self):
        with pytest.raises(ValueError, match="must start with 'acp_'"):
            _make_checkpoint(checkpoint_id="bad_id")

    @pytest.mark.asyncio
    async def test_complete_checkpoint(self):
        store = InMemoryArchiveCheckpointStore()
        cp = _make_checkpoint(is_complete=True, records_processed=100)
        await store.record_checkpoint(cp)
        fetched = await store.get_checkpoint(cp.checkpoint_id)
        assert fetched is not None
        assert fetched.is_complete is True
        assert fetched.records_processed == 100


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class TestSQLiteArchiveCheckpointStore:
    @pytest.fixture
    def tmp_db(self, tmp_path):
        db = str(tmp_path / "archive_checkpoints.db")
        yield db

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_db):
        store = SQLiteArchiveCheckpointStore(tmp_db)
        cp = _make_checkpoint()
        await store.record_checkpoint(cp)
        store.close()

        store2 = SQLiteArchiveCheckpointStore(tmp_db)
        fetched = await store2.get_checkpoint(cp.checkpoint_id)
        assert fetched is not None
        assert fetched.data_type == "rollup"
        store2.close()

    @pytest.mark.asyncio
    async def test_list_and_prune(self, tmp_db):
        store = SQLiteArchiveCheckpointStore(tmp_db)
        await store.record_checkpoint(_make_checkpoint(
            checkpoint_id="acp_old_001",
            updated_at=datetime.now(timezone.utc) - timedelta(days=60),
        ))
        await store.record_checkpoint(_make_checkpoint(
            checkpoint_id="acp_new_001",
            updated_at=datetime.now(timezone.utc),
        ))
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        pruned = await store.prune_old_checkpoints(cutoff)
        assert pruned == 1
        store.close()


# ---------------------------------------------------------------------------
# ResumableArchiveCleanup
# ---------------------------------------------------------------------------


class FakeRollupStore:
    """Fake rollup store for testing cleanup."""

    def __init__(self, rollups: list[NotificationMetricsRollup]) -> None:
        self.rollups = rollups
        self.deleted: list[str] = []

    async def list_rollups(self, limit: int = 500, offset: int = 0) -> list[NotificationMetricsRollup]:
        return self.rollups[offset: offset + limit]


class FakeAuditLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


class TestResumableArchiveCleanup:
    @pytest.mark.asyncio
    async def test_run_cleanup_archives_old_rollups(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            store = SQLiteArchiveCheckpointStore(db_path)
            now = datetime.now(timezone.utc)
            old_rollups = [
                _make_rollup(rollup_id="nru_001", window_end=now - timedelta(days=100)),
                _make_rollup(rollup_id="nru_002", window_end=now - timedelta(days=95)),
            ]
            fake_rollup_store = FakeRollupStore(old_rollups)

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(
                    rollup_retention_days=30,
                    archive_dir=tmpdir,
                ),
                rollup_store=fake_rollup_store,
            )

            result = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
            assert result.records_processed == 2
            assert result.records_archived == 2
            assert len(result.archive_files) == 1
            assert result.is_complete is True
            store.close()

    @pytest.mark.asyncio
    async def test_run_cleanup_dry_run(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            store = SQLiteArchiveCheckpointStore(db_path)
            now = datetime.now(timezone.utc)
            old_rollups = [
                _make_rollup(rollup_id="nru_001", window_end=now - timedelta(days=100)),
            ]
            fake_rollup_store = FakeRollupStore(old_rollups)

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(
                    rollup_retention_days=30,
                    archive_dir=tmpdir,
                ),
                rollup_store=fake_rollup_store,
            )

            result = await cleanup.run_cleanup(data_type="rollup", dry_run=True, now=now)
            assert result.records_processed == 1
            assert result.records_archived == 1
            assert len(result.archive_files) == 0  # dry run doesn't create files
            assert result.is_complete is True
            store.close()

    @pytest.mark.asyncio
    async def test_skips_recent_rollups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = InMemoryArchiveCheckpointStore()
            now = datetime.now(timezone.utc)
            recent = _make_rollup(rollup_id="nru_recent", window_end=now - timedelta(days=5))
            fake_rollup_store = FakeRollupStore([recent])

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(rollup_retention_days=30),
                rollup_store=fake_rollup_store,
            )

            result = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
            assert result.records_processed == 0
            assert result.is_complete is True

    @pytest.mark.asyncio
    async def test_resumes_from_checkpoint(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            store = SQLiteArchiveCheckpointStore(db_path)
            now = datetime.now(timezone.utc)
            # First batch processes nru_001
            old_rollups = [
                _make_rollup(rollup_id="nru_001", window_end=now - timedelta(days=100)),
                _make_rollup(rollup_id="nru_002", window_end=now - timedelta(days=95)),
            ]
            fake_rollup_store = FakeRollupStore(old_rollups)

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(
                    rollup_retention_days=30,
                    batch_size=1,
                    archive_dir=tmpdir,
                ),
                rollup_store=fake_rollup_store,
            )

            result1 = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
            assert result1.records_processed == 1  # Only first batch
            assert result1.checkpoint_id is not None
            assert result1.is_complete is True
            store.close()

    @pytest.mark.asyncio
    async def test_disabled_policy_skips_cleanup(self):
        store = InMemoryArchiveCheckpointStore()
        cleanup = ResumableArchiveCleanup(
            checkpoint_store=store,
            policy=ArchiveCleanupPolicy(enabled=False),
        )

        result = await cleanup.run_cleanup(data_type="rollup", dry_run=False)
        assert result.records_processed == 0
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_audit_logger_records_events(self):
        store = InMemoryArchiveCheckpointStore()
        audit = FakeAuditLogger()
        now = datetime.now(timezone.utc)
        old_rollups = [_make_rollup(window_end=now - timedelta(days=100))]
        fake_rollup_store = FakeRollupStore(old_rollups)

        cleanup = ResumableArchiveCleanup(
            checkpoint_store=store,
            rollup_store=fake_rollup_store,
            audit_logger=audit,
        )

        await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
        event_types = [e["event_type"] for e in audit.events]
        assert "archive_cleanup_complete" in event_types
        assert "archive_cleanup_batch" in event_types

    @pytest.mark.asyncio
    async def test_prune_old_checkpoints(self):
        store = InMemoryArchiveCheckpointStore()
        old_cp = _make_checkpoint(
            checkpoint_id="acp_old_prune",
            updated_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        await store.record_checkpoint(old_cp)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        cleanup = ResumableArchiveCleanup(checkpoint_store=store)
        pruned = await cleanup.prune_old_checkpoints(now=cutoff + timedelta(days=1))
        assert pruned == 1

    @pytest.mark.asyncio
    async def test_unknown_data_type_returns_error(self):
        store = InMemoryArchiveCheckpointStore()
        cleanup = ResumableArchiveCleanup(checkpoint_store=store)
        result = await cleanup.run_cleanup(data_type="unknown")
        assert result.error is not None
        assert "Unknown data type" in result.error


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class FakeChangeEventStore:
    """Fake change event store for testing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: Any, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


class TestResumableArchiveCleanupChangeEvents:
    @pytest.mark.asyncio
    async def test_run_cleanup_records_started_event(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            store = SQLiteArchiveCheckpointStore(db_path)
            now = datetime.now(timezone.utc)
            old_rollups = [
                _make_rollup(rollup_id="nru_001", window_end=now - timedelta(days=100)),
            ]
            fake_rollup_store = FakeRollupStore(old_rollups)
            change_event_store = FakeChangeEventStore()

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(
                    rollup_retention_days=30,
                    archive_dir=tmpdir,
                ),
                rollup_store=fake_rollup_store,
                change_event_store=change_event_store,
            )

            result = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
            assert result.records_processed == 1
            event_types = [e["event_type"] for e in change_event_store.events]
            assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_STARTED in event_types
            store.close()

    @pytest.mark.asyncio
    async def test_run_cleanup_records_completed_event(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            store = SQLiteArchiveCheckpointStore(db_path)
            now = datetime.now(timezone.utc)
            old_rollups = [
                _make_rollup(rollup_id="nru_001", window_end=now - timedelta(days=100)),
            ]
            fake_rollup_store = FakeRollupStore(old_rollups)
            change_event_store = FakeChangeEventStore()

            cleanup = ResumableArchiveCleanup(
                checkpoint_store=store,
                policy=ArchiveCleanupPolicy(
                    rollup_retention_days=30,
                    archive_dir=tmpdir,
                ),
                rollup_store=fake_rollup_store,
                change_event_store=change_event_store,
            )

            result = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
            assert result.is_complete is True
            event_types = [e["event_type"] for e in change_event_store.events]
            assert PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_COMPLETED in event_types
            completed_events = [
                e for e in change_event_store.events
                if e["event_type"] == PolicyChangeEventType.FEDERATION_NOTIFICATION_ARCHIVE_CLEANUP_COMPLETED
            ]
            assert len(completed_events) == 1
            assert completed_events[0]["payload"]["is_complete"] is True
            store.close()

    @pytest.mark.asyncio
    async def test_run_cleanup_without_change_event_store_no_error(self):
        """Cleanup should work fine without a change_event_store."""
        store = InMemoryArchiveCheckpointStore()
        now = datetime.now(timezone.utc)
        old_rollups = [_make_rollup(window_end=now - timedelta(days=100))]
        fake_rollup_store = FakeRollupStore(old_rollups)

        cleanup = ResumableArchiveCleanup(
            checkpoint_store=store,
            rollup_store=fake_rollup_store,
            change_event_store=None,
        )

        result = await cleanup.run_cleanup(data_type="rollup", dry_run=False, now=now)
        assert result.records_processed == 1


class TestFactory:
    def test_create_memory(self):
        store = create_archive_checkpoint_store("memory")
        assert isinstance(store, InMemoryArchiveCheckpointStore)

    def test_create_sqlite(self, tmp_path):
        db = str(tmp_path / "checkpoints.db")
        store = create_archive_checkpoint_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteArchiveCheckpointStore)
        store.close()

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_archive_checkpoint_store("unknown")
