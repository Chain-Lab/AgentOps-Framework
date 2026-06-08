"""Tests for DAG snapshot models and store implementations (Phase 16.0)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_app.runtime.dag_snapshot import (
    DagNodeSnapshot,
    DagRunSnapshot,
    DagSnapshotStatus,
    SnapshotCorruptionError,
    SnapshotUnsupportedVersionError,
    SnapshotWriteError,
    snapshot_status_is_resumable,
    _new_snapshot_id,
    _now,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_node_snapshot(
    node_id: str = "node-1",
    status: str = "completed",
    attempts: int = 1,
    output: Any = None,
) -> DagNodeSnapshot:
    return DagNodeSnapshot(
        node_id=node_id,
        status=status,
        attempts=attempts,
        output=output,
    )


def _make_snapshot(
    run_id: str = "run-1",
    status: str = "running",
    schema_version: int = 1,
    completed_node_ids: list[str] | None = None,
    failed_node_ids: list[str] | None = None,
    current_node_ids: list[str] | None = None,
    nodes: dict[str, DagNodeSnapshot] | None = None,
) -> DagRunSnapshot:
    return DagRunSnapshot(
        snapshot_id=_new_snapshot_id(),
        run_id=run_id,
        workflow_name="test-workflow",
        status=status,
        schema_version=schema_version,
        completed_node_ids=completed_node_ids or [],
        failed_node_ids=failed_node_ids or [],
        current_node_ids=current_node_ids or [],
        nodes=nodes or {},
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestDagSnapshotStatus:
    """Tests for DagSnapshotStatus enum."""

    def test_status_values(self) -> None:
        assert DagSnapshotStatus.RUNNING == "running"
        assert DagSnapshotStatus.COMPLETED == "completed"
        assert DagSnapshotStatus.FAILED == "failed"
        assert DagSnapshotStatus.PARTIAL == "partial"
        assert DagSnapshotStatus.INTERRUPTED == "interrupted"


class TestSnapshotResumable:
    """Tests for snapshot_status_is_resumable helper."""

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("running", True),
            ("partial", True),
            ("failed", True),
            ("interrupted", True),
            ("completed", False),
        ],
    )
    def test_resumable_statuses(self, status: str, expected: bool) -> None:
        assert snapshot_status_is_resumable(status) is expected


class TestDagNodeSnapshot:
    """Tests for DagNodeSnapshot model."""

    def test_create_node_snapshot(self) -> None:
        node = _make_node_snapshot(node_id="n1", status="completed", output={"result": "ok"})
        assert node.node_id == "n1"
        assert node.status == "completed"
        assert node.attempts == 1
        assert node.output == {"result": "ok"}
        assert node.error is None

    def test_node_snapshot_with_error(self) -> None:
        node = DagNodeSnapshot(
            node_id="n1",
            status="failed",
            error={"type": "timeout", "message": "timed out"},
        )
        assert node.error is not None
        assert node.error["type"] == "timeout"

    def test_node_snapshot_serialization(self) -> None:
        node = _make_node_snapshot(output=[1, 2, 3])
        data = node.model_dump(mode="json")
        assert data["output"] == [1, 2, 3]


class TestDagRunSnapshotModel:
    """Tests for DagRunSnapshot model creation and defaults."""

    def test_create_snapshot_defaults(self) -> None:
        snap = _make_snapshot()
        assert snap.schema_version == 1
        assert snap.status == "running"
        assert snap.completed_node_ids == []
        assert snap.failed_node_ids == []
        assert snap.current_node_ids == []
        assert snap.pending_approvals == []
        assert snap.compensation_state is None
        assert snap.workflow_name == "test-workflow"

    def test_create_snapshot_with_nodes(self) -> None:
        nodes = {
            "n1": _make_node_snapshot("n1", "completed", output="result1"),
            "n2": _make_node_snapshot("n2", "running"),
        }
        snap = _make_snapshot(nodes=nodes)
        assert len(snap.nodes) == 2
        assert snap.nodes["n1"].output == "result1"
        assert snap.nodes["n2"].status == "running"

    def test_snapshot_id_is_unique(self) -> None:
        s1 = _make_snapshot()
        s2 = _make_snapshot()
        assert s1.snapshot_id != s2.snapshot_id


class TestDagRunSnapshotSerialization:
    """Tests for DagRunSnapshot JSON serialization."""

    def test_to_json_and_back(self) -> None:
        snap = _make_snapshot(
            run_id="run-xyz",
            status="completed",
            completed_node_ids=["n1", "n2"],
            nodes={
                "n1": _make_node_snapshot("n1", "completed", output={"key": "value"}),
            },
        )
        json_str = snap.to_json()
        assert isinstance(json_str, str)

        parsed = json.loads(json_str)
        assert parsed["run_id"] == "run-xyz"
        assert parsed["status"] == "completed"
        assert parsed["schema_version"] == 1
        assert parsed["completed_node_ids"] == ["n1", "n2"]

        # Round-trip
        restored = DagRunSnapshot.from_json(json_str)
        assert restored.run_id == "run-xyz"
        assert restored.status == "completed"
        assert restored.schema_version == 1
        assert restored.nodes["n1"].output == {"key": "value"}

    def test_to_json_with_datetime(self) -> None:
        snap = _make_snapshot()
        json_str = snap.to_json()
        # datetime should be serialized as ISO string
        parsed = json.loads(json_str)
        assert "created_at" in parsed
        assert "T" in parsed["created_at"]  # ISO format

    def test_from_json_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid snapshot JSON"):
            DagRunSnapshot.from_json("not valid json {{{")

    def test_from_json_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid snapshot data"):
            DagRunSnapshot.from_json('{"snapshot_id": "x"}')

    def test_schema_version_default_is_1(self) -> None:
        snap = _make_snapshot()
        assert snap.schema_version == 1


class TestSnapshotErrors:
    """Tests for snapshot error types."""

    def test_snapshot_write_error(self) -> None:
        err = SnapshotWriteError(run_id="run-1", message="disk full")
        assert err.run_id == "run-1"
        assert err.message == "disk full"
        d = err.to_dict()
        assert d["type"] == "snapshot_write_error"
        assert d["run_id"] == "run-1"

    def test_snapshot_unsupported_version_error(self) -> None:
        err = SnapshotUnsupportedVersionError(run_id="run-1", version=99)
        assert err.version == 99
        d = err.to_dict()
        assert d["type"] == "snapshot_unsupported_version"
        assert d["version"] == 99

    def test_snapshot_corruption_error(self) -> None:
        err = SnapshotCorruptionError(run_id="run-1", message="bad json")
        assert err.run_id == "run-1"
        d = err.to_dict()
        assert d["type"] == "snapshot_corruption"


# ---------------------------------------------------------------------------
# InMemory snapshot store tests
# ---------------------------------------------------------------------------


class TestInMemorySnapshotStore:
    """Tests for InMemoryWorkflowStateStore snapshot methods."""

    @pytest.fixture()
    def store(self) -> Any:
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore

        return InMemoryWorkflowStateStore()

    @pytest.mark.asyncio
    async def test_save_and_get_latest(self, store: Any) -> None:
        snap = _make_snapshot(run_id="run-1")
        saved = await store.save_run_snapshot(snap)
        assert saved.snapshot_id == snap.snapshot_id

        latest = await store.get_latest_run_snapshot("run-1")
        assert latest is not None
        assert latest.snapshot_id == snap.snapshot_id

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_when_empty(self, store: Any) -> None:
        latest = await store.get_latest_run_snapshot("nonexistent")
        assert latest is None

    @pytest.mark.asyncio
    async def test_list_snapshots_ordered_by_updated_at(self, store: Any) -> None:
        snap1 = _make_snapshot(run_id="run-1")
        snap2 = _make_snapshot(run_id="run-1")
        # Ensure different updated_at times
        snap1.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        snap2.updated_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

        await store.save_run_snapshot(snap1)
        await store.save_run_snapshot(snap2)

        listed = await store.list_run_snapshots("run-1")
        assert len(listed) == 2
        # Should be ordered by updated_at ascending
        assert listed[0].updated_at <= listed[1].updated_at

    @pytest.mark.asyncio
    async def test_different_run_id_isolation(self, store: Any) -> None:
        snap1 = _make_snapshot(run_id="run-a")
        snap2 = _make_snapshot(run_id="run-b")
        await store.save_run_snapshot(snap1)
        await store.save_run_snapshot(snap2)

        latest_a = await store.get_latest_run_snapshot("run-a")
        latest_b = await store.get_latest_run_snapshot("run-b")
        assert latest_a.snapshot_id == snap1.snapshot_id
        assert latest_b.snapshot_id == snap2.snapshot_id

    @pytest.mark.asyncio
    async def test_overwrite_same_snapshot_id(self, store: Any) -> None:
        snap = _make_snapshot(run_id="run-1")
        await store.save_run_snapshot(snap)

        # Modify and save again
        snap.status = "completed"
        snap.completed_node_ids = ["n1"]
        await store.save_run_snapshot(snap)

        # Should still be exactly 1 snapshot with updated data
        listed = await store.list_run_snapshots("run-1")
        assert len(listed) == 1
        assert listed[0].status == "completed"
        assert listed[0].completed_node_ids == ["n1"]

    @pytest.mark.asyncio
    async def test_delete_run_snapshots(self, store: Any) -> None:
        snap = _make_snapshot(run_id="run-1")
        await store.save_run_snapshot(snap)

        await store.delete_run_snapshots("run-1")
        latest = await store.get_latest_run_snapshot("run-1")
        assert latest is None


# ---------------------------------------------------------------------------
# SQLite snapshot store tests
# ---------------------------------------------------------------------------


class TestSQLiteSnapshotStore:
    """Tests for SQLiteWorkflowStateStore snapshot methods."""

    @pytest.fixture()
    def db_path(self, tmp_path: Any) -> str:
        return str(tmp_path / "test_snapshots.db")

    @pytest.fixture()
    def store(self, db_path: str) -> Any:
        from agent_app.runtime.dag_state_store import SQLiteWorkflowStateStore

        return SQLiteWorkflowStateStore(db_path=db_path)

    @pytest.mark.asyncio
    async def test_auto_creates_table(self, store: Any, db_path: str) -> None:
        import sqlite3

        # The table should exist after store init
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dag_run_snapshots'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    @pytest.mark.asyncio
    async def test_save_and_get_latest(self, store: Any) -> None:
        snap = _make_snapshot(run_id="run-1")
        saved = await store.save_run_snapshot(snap)
        assert saved.snapshot_id == snap.snapshot_id

        latest = await store.get_latest_run_snapshot("run-1")
        assert latest is not None
        assert latest.run_id == "run-1"
        assert latest.schema_version == 1

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_when_empty(self, store: Any) -> None:
        latest = await store.get_latest_run_snapshot("nonexistent")
        assert latest is None

    @pytest.mark.asyncio
    async def test_list_snapshots_ordered_by_updated_at(self, store: Any) -> None:
        snap1 = _make_snapshot(run_id="run-1")
        snap2 = _make_snapshot(run_id="run-1")
        snap1.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        snap2.updated_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

        await store.save_run_snapshot(snap1)
        await store.save_run_snapshot(snap2)

        listed = await store.list_run_snapshots("run-1")
        assert len(listed) == 2
        assert listed[0].updated_at <= listed[1].updated_at

    @pytest.mark.asyncio
    async def test_persists_across_store_instances(self, db_path: str) -> None:
        from agent_app.runtime.dag_state_store import SQLiteWorkflowStateStore

        snap = _make_snapshot(run_id="run-persist")
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        await store1.save_run_snapshot(snap)

        # New instance should see the snapshot
        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        latest = await store2.get_latest_run_snapshot("run-persist")
        assert latest is not None
        assert latest.snapshot_id == snap.snapshot_id

    @pytest.mark.asyncio
    async def test_different_run_id_isolation(self, store: Any) -> None:
        snap_a = _make_snapshot(run_id="run-a")
        snap_b = _make_snapshot(run_id="run-b")
        await store.save_run_snapshot(snap_a)
        await store.save_run_snapshot(snap_b)

        latest_a = await store.get_latest_run_snapshot("run-a")
        latest_b = await store.get_latest_run_snapshot("run-b")
        assert latest_a.snapshot_id == snap_a.snapshot_id
        assert latest_b.snapshot_id == snap_b.snapshot_id

    @pytest.mark.asyncio
    async def test_corrupted_json_returns_stable_error(self, store: Any, db_path: str) -> None:
        import sqlite3

        # Insert corrupted JSON directly into the DB
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO dag_run_snapshots
                (snapshot_id, run_id, workflow_name, status, schema_version,
                 snapshot_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad-snap",
                "run-bad",
                "wf",
                "running",
                1,
                "NOT VALID JSON {{{",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        with pytest.raises(SnapshotCorruptionError, match="Failed to deserialize"):
            await store.get_latest_run_snapshot("run-bad")

    @pytest.mark.asyncio
    async def test_delete_run_snapshots(self, store: Any) -> None:
        snap = _make_snapshot(run_id="run-1")
        await store.save_run_snapshot(snap)

        await store.delete_run_snapshots("run-1")
        latest = await store.get_latest_run_snapshot("run-1")
        assert latest is None


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestDagSnapshotConfig:
    """Tests for DagSnapshotConfig model."""

    def test_default_values(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        cfg = DagSnapshotConfig()
        assert cfg.enabled is True
        assert cfg.store == "memory"
        assert cfg.path is None
        assert cfg.save_on_node_start is True
        assert cfg.save_on_node_complete is True
        assert cfg.save_on_interrupt is True
        assert cfg.save_on_failure is True

    def test_explicit_enabled_true(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        cfg = DagSnapshotConfig(enabled=True, store="sqlite", path="/tmp/test.db")
        assert cfg.enabled is True
        assert cfg.store == "sqlite"
        assert cfg.path == "/tmp/test.db"

    def test_explicit_enabled_false(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        cfg = DagSnapshotConfig(enabled=False)
        assert cfg.enabled is False

    def test_invalid_store_raises(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        with pytest.raises(Exception):  # ValidationError
            DagSnapshotConfig(store="redis")

    def test_save_flags_defaults(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        cfg = DagSnapshotConfig()
        assert cfg.save_on_node_start is True
        assert cfg.save_on_node_complete is True

    def test_disable_selective_save_flags(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        cfg = DagSnapshotConfig(
            save_on_node_start=False,
            save_on_node_complete=False,
        )
        assert cfg.save_on_node_start is False
        assert cfg.save_on_node_complete is False


class TestRuntimeConfigSnapshotNormalization:
    """Tests for RuntimeConfig snapshot normalization."""

    def test_default_snapshot_config_is_none(self) -> None:
        from agent_app.config.schema import RuntimeConfig

        cfg = RuntimeConfig()
        assert cfg.dag_snapshot_config is None

    def test_nested_dag_snapshot_normalization(self) -> None:
        from agent_app.config.schema import RuntimeConfig

        raw = {
            "dag_snapshot": {
                "enabled": True,
                "store": "sqlite",
                "path": "/tmp/test.db",
            }
        }
        cfg = RuntimeConfig.model_validate(raw)
        assert cfg.dag_snapshot_config is not None
        assert cfg.dag_snapshot_config.enabled is True
        assert cfg.dag_snapshot_config.store == "sqlite"
        assert cfg.dag_snapshot_config.path == "/tmp/test.db"

    def test_flat_dag_snapshot_type(self) -> None:
        from agent_app.config.schema import RuntimeConfig

        raw = {"dag_snapshot_type": "memory"}
        cfg = RuntimeConfig.model_validate(raw)
        # Should not have dag_snapshot_config unless explicitly provided
        assert cfg.dag_snapshot_config is None
