"""Tests for Phase 63 PersistentAuditStore.

Phase 63: Persistent Approval / Control Plane — SQLite-backed audit
event store with filtering by event_type, command_id, approval_id,
and time-ordered listing.
"""
from __future__ import annotations

import pytest

from agent_app.runtime.policy_rollout_federation_notification_audit_store import (
    PersistentAuditEvent,
    PersistentAuditStore,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh PersistentAuditStore in a temporary directory."""
    db = str(tmp_path / "audit.db")
    s = PersistentAuditStore(db)
    yield s
    s.close()


class TestPersistentAuditStoreAppend:
    def test_append_event(self, store):
        event = store.append(
            event_id="evt_001",
            event_type="control.command.created",
            data={"command_type": "pause"},
        )
        assert event.event_id == "evt_001"
        assert event.event_type == "control.command.created"
        assert event.data == {"command_type": "pause"}

    def test_append_with_references(self, store):
        event = store.append(
            event_id="evt_002",
            event_type="control.command.accepted",
            command_id="cmd_001",
            approval_id="appr_001",
            daemon_id="daemon-1",
            actor="operator",
            data={"status": "accepted"},
        )
        assert event.command_id == "cmd_001"
        assert event.approval_id == "appr_001"
        assert event.daemon_id == "daemon-1"
        assert event.actor == "operator"

    def test_event_has_timestamp(self, store):
        event = store.append("evt_001", "test.event")
        assert event.created_at is not None
        assert event.created_at.tzinfo is not None


class TestPersistentAuditStoreGet:
    def test_get_existing(self, store):
        store.append("evt_001", "control.command.created")
        event = store.get("evt_001")
        assert event is not None
        assert event.event_type == "control.command.created"

    def test_get_missing(self, store):
        event = store.get("evt_nonexistent")
        assert event is None


class TestPersistentAuditStoreList:
    def test_list_recent(self, store):
        store.append("evt_001", "control.command.created")
        store.append("evt_002", "control.command.accepted")
        events = store.list_recent(limit=10)
        assert len(events) == 2

    def test_list_ordered_desc(self, store):
        store.append("evt_001", "event.a")
        store.append("evt_002", "event.b")
        events = store.list_recent()
        assert events[0].event_id == "evt_002"

    def test_filter_by_event_type(self, store):
        store.append("evt_001", "control.command.created", command_id="cmd_001")
        store.append("evt_002", "control.command.completed", command_id="cmd_001")
        store.append("evt_003", "approval.created")
        events = store.list_by_event_type("control.command.created")
        assert len(events) == 1
        assert events[0].event_id == "evt_001"

    def test_filter_by_command_id(self, store):
        store.append("evt_001", "cmd.created", command_id="cmd_001")
        store.append("evt_002", "cmd.accepted", command_id="cmd_001")
        store.append("evt_003", "cmd.completed", command_id="cmd_002")
        events = store.list_by_command_id("cmd_001")
        assert len(events) == 2

    def test_filter_by_approval_id(self, store):
        store.append("evt_001", "appr.created", approval_id="appr_001")
        store.append("evt_002", "appr.approved", approval_id="appr_001")
        store.append("evt_003", "appr.created", approval_id="appr_002")
        events = store.list_by_approval_id("appr_001")
        assert len(events) == 2

    def test_filter_multiple_combined(self, store):
        store.append("evt_001", "cmd.created", command_id="cmd_001", daemon_id="d1")
        store.append("evt_002", "cmd.accepted", command_id="cmd_001", daemon_id="d1")
        store.append("evt_003", "cmd.completed", command_id="cmd_002", daemon_id="d1")
        events = store.list_recent(command_id="cmd_001", event_type="cmd.created")
        assert len(events) == 1
        assert events[0].event_id == "evt_001"

    def test_limit(self, store):
        for i in range(10):
            store.append(f"evt_{i:03d}", "test.event")
        events = store.list_recent(limit=5)
        assert len(events) == 5


class TestPersistentAuditStoreJSONRoundTrip:
    def test_json_roundtrip(self, store):
        data = {
            "nested": {"key": "value", "num": 42},
            "list": [1, 2, 3],
            "bool": True,
            "null": None,
        }
        event = store.append(
            "evt_001",
            "control.command.created",
            data=data,
        )
        retrieved = store.get("evt_001")
        assert retrieved.data == data

    def test_empty_data(self, store):
        event = store.append("evt_001", "test.event")
        assert event.data == {}


class TestPersistentAuditStorePersistence:
    def test_sqlite_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "audit.db")
        # Write in first instance
        s1 = PersistentAuditStore(db)
        s1.append(
            "evt_001",
            "control.command.created",
            command_id="cmd_001",
            actor="operator",
            data={"command_type": "pause"},
        )
        s1.close()

        # Read in second instance
        s2 = PersistentAuditStore(db)
        events = s2.list_recent()
        assert len(events) == 1
        assert events[0].command_id == "cmd_001"
        assert events[0].actor == "operator"
        s2.close()
