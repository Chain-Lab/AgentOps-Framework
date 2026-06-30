"""Tests for Phase 63 ControlPlaneStore.

Phase 63: Persistent Approval / Control Plane — SQLite-backed control
command store with state transitions, idempotency, and expiration.
"""
from __future__ import annotations

import pytest

from agent_app.runtime.policy_rollout_federation_notification_control_plane import (
    ControlCommand,
    ControlCommandStatus,
    ControlCommandType,
    ControlPlaneStore,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh ControlPlaneStore in a temporary directory."""
    db = str(tmp_path / "control.db")
    s = ControlPlaneStore(db)
    yield s
    s.close()


class TestControlPlaneStoreCreate:
    def test_create_command(self, store):
        cmd = store.create_command(
            command_id="cmd_001",
            command_type=ControlCommandType.PAUSE,
        )
        assert cmd.command_id == "cmd_001"
        assert cmd.command_type == ControlCommandType.PAUSE
        assert cmd.status == ControlCommandStatus.PENDING
        assert cmd.payload == {}
        assert cmd.requested_by is None
        assert cmd.reason is None

    def test_create_with_metadata(self, store):
        cmd = store.create_command(
            command_id="cmd_002",
            command_type=ControlCommandType.SHUTDOWN,
            requested_by="operator",
            reason="maintenance",
            payload={"force": True},
            idempotency_key="idem_001",
        )
        assert cmd.requested_by == "operator"
        assert cmd.reason == "maintenance"
        assert cmd.payload == {"force": True}
        assert cmd.idempotency_key == "idem_001"

    def test_command_id_prefix(self, store):
        cmd = store.create_command(
            command_id="cmd_003",
            command_type=ControlCommandType.RESUME,
        )
        assert cmd.command_id.startswith("cmd_")


class TestControlPlaneStoreGet:
    def test_get_existing(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        cmd = store.get_command("cmd_001")
        assert cmd is not None
        assert cmd.command_type == ControlCommandType.PAUSE

    def test_get_missing(self, store):
        cmd = store.get_command("cmd_nonexistent")
        assert cmd is None


class TestControlPlaneStoreList:
    def test_list_all(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.create_command("cmd_002", ControlCommandType.RESUME)
        cmds = store.list_commands()
        assert len(cmds) == 2

    def test_list_by_status(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.create_command("cmd_002", ControlCommandType.RESUME)
        store.mark_accepted("cmd_001")
        pending = store.list_commands(status="pending")
        assert len(pending) == 1
        assert pending[0].command_id == "cmd_002"

    def test_list_pending(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.create_command("cmd_002", ControlCommandType.SHUTDOWN)
        store.mark_accepted("cmd_001")
        pending = store.list_pending_commands()
        assert len(pending) == 1
        assert pending[0].command_id == "cmd_002"

    def test_list_ordered_by_created_at_desc(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.create_command("cmd_002", ControlCommandType.RESUME)
        cmds = store.list_commands()
        assert cmds[0].command_id == "cmd_002"


class TestControlPlaneStoreTransitions:
    def test_mark_accepted(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        cmd = store.mark_accepted("cmd_001")
        assert cmd.status == ControlCommandStatus.ACCEPTED
        assert cmd.accepted_at is not None

    def test_mark_running(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.mark_accepted("cmd_001")
        cmd = store.mark_running("cmd_001")
        assert cmd.status == ControlCommandStatus.RUNNING

    def test_mark_completed(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.mark_accepted("cmd_001")
        store.mark_running("cmd_001")
        cmd = store.mark_completed("cmd_001")
        assert cmd.status == ControlCommandStatus.COMPLETED
        assert cmd.completed_at is not None

    def test_mark_failed(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.mark_accepted("cmd_001")
        store.mark_running("cmd_001")
        cmd = store.mark_failed("cmd_001", {"error": "timeout"})
        assert cmd.status == ControlCommandStatus.FAILED
        assert cmd.error == {"error": "timeout"}

    def test_cannot_complete_terminal_twice(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.mark_accepted("cmd_001")
        store.mark_running("cmd_001")
        store.mark_completed("cmd_001")
        with pytest.raises(ValueError, match="terminal state"):
            store.mark_completed("cmd_001")

    def test_cannot_transition_from_wrong_state(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        with pytest.raises(ValueError, match="expected one of"):
            store.mark_running("cmd_001")

    def test_missing_command_raises_key_error(self, store):
        with pytest.raises(KeyError, match="not found"):
            store.mark_accepted("cmd_nonexistent")


class TestControlPlaneStoreExpire:
    def test_expire_old_commands(self, store):
        from datetime import datetime, timezone, timedelta
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        # Manually backdate the command
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        store._conn.execute(
            "UPDATE control_commands SET created_at = ? WHERE command_id = ?",
            (old_time, "cmd_001"),
        )
        store._conn.commit()
        count = store.expire_old_commands(max_age_seconds=3600)
        assert count == 1
        cmd = store.get_command("cmd_001")
        assert cmd.status == ControlCommandStatus.EXPIRED

    def test_expire_does_not_affect_completed(self, store):
        store.create_command("cmd_001", ControlCommandType.PAUSE)
        store.mark_accepted("cmd_001")
        store.mark_running("cmd_001")
        store.mark_completed("cmd_001")
        count = store.expire_old_commands(max_age_seconds=1)
        assert count == 0


class TestControlPlaneStorePersistence:
    def test_sqlite_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "control.db")
        # Write in first instance
        s1 = ControlPlaneStore(db)
        s1.create_command("cmd_001", ControlCommandType.PAUSE)
        s1.create_command("cmd_002", ControlCommandType.RESUME)
        s1.close()

        # Read in second instance
        s2 = ControlPlaneStore(db)
        cmds = s2.list_commands()
        assert len(cmds) == 2
        cmd_ids = {c.command_id for c in cmds}
        assert "cmd_001" in cmd_ids
        assert "cmd_002" in cmd_ids
        s2.close()
