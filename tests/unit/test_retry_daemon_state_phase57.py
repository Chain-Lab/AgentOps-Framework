"""Tests for Phase 57: Daemon persistent state store.

Phase 57 Task 4: Persistent retry daemon state with SQLite support.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
    AlertDeliveryRetryDaemonState,
    InMemoryAlertDeliveryRetryDaemonStateStore,
    SQLiteAlertDeliveryRetryDaemonStateStore,
    create_retry_daemon_state_store,
    _redact_error_message,
    _redact_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(daemon_id="test-daemon", **kwargs):
    defaults = dict(
        daemon_id=daemon_id,
        enabled=True,
        desired_state="stopped",
        actual_state="stopped",
        started_at=None,
        stopped_at=None,
        last_run_at=None,
        last_success_at=None,
        last_error_at=None,
        last_error_message=None,
        consecutive_failures=0,
        last_result={},
    )
    defaults.update(kwargs)
    return AlertDeliveryRetryDaemonState(**defaults)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestDaemonStateModel:
    """Phase 57: Daemon state model tests."""

    def test_default_values(self):
        state = _make_state()
        assert state.daemon_id == "test-daemon"
        assert state.enabled is True
        assert state.desired_state == "stopped"
        assert state.actual_state == "stopped"
        assert state.consecutive_failures == 0

    def test_updated_at_auto_set(self):
        state = _make_state()
        assert state.updated_at is not None
        assert state.updated_at.tzinfo is not None

    def test_model_dump_with_dates(self):
        state = _make_state(
            started_at=datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc),
            last_run_at=datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
        )
        d = state.model_dump()
        assert d["started_at"] is not None
        assert d["last_run_at"] is not None


# ---------------------------------------------------------------------------
# InMemory store tests
# ---------------------------------------------------------------------------


class TestInMemoryDaemonStateStore:
    """Phase 57: In-memory daemon state store tests."""

    def test_save_and_get(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        state = _make_state(daemon_id="daemon-1")
        store.save(state)
        retrieved = store.get("daemon-1")
        assert retrieved is not None
        assert retrieved.daemon_id == "daemon-1"
        assert retrieved.enabled is True

    def test_get_missing_returns_none(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        assert store.get("nonexistent") is None

    def test_list_states(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        store.save(_make_state(daemon_id="d1"))
        store.save(_make_state(daemon_id="d2"))
        states = store.list_states()
        assert len(states) == 2

    def test_update_existing(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        state = _make_state(daemon_id="d1")
        store.save(state)
        # Update
        state.actual_state = "running"
        state.consecutive_failures = 1
        store.save(state)
        retrieved = store.get("d1")
        assert retrieved.actual_state == "running"
        assert retrieved.consecutive_failures == 1

    def test_error_redacted_on_save(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        state = _make_state(
            daemon_id="d1",
            last_error_message="Connection failed: token=abc123 and password=secret",
        )
        store.save(state)
        retrieved = store.get("d1")
        assert retrieved.last_error_message is not None
        assert "abc123" not in retrieved.last_error_message
        assert "secret" not in retrieved.last_error_message

    def test_last_result_redacted(self):
        store = InMemoryAlertDeliveryRetryDaemonStateStore()
        state = _make_state(
            daemon_id="d1",
            last_result={"body_preview": "secret_token=xyz", "headers": {"authorization": "Bearer xyz"}},
        )
        store.save(state)
        retrieved = store.get("d1")
        assert "secret_token" not in str(retrieved.last_result)
        assert "xyz" not in str(retrieved.last_result.get("headers", {}))


# ---------------------------------------------------------------------------
# SQLite store tests
# ---------------------------------------------------------------------------


class TestSQLiteDaemonStateStore:
    """Phase 57: SQLite daemon state store tests."""

    def test_save_and_get(self, tmp_path):
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        state = _make_state(daemon_id="d1")
        store.save(state)
        retrieved = store.get("d1")
        assert retrieved is not None
        assert retrieved.daemon_id == "d1"
        assert retrieved.enabled is True
        store.close()

    def test_get_missing_returns_none(self, tmp_path):
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        assert store.get("nonexistent") is None
        store.close()

    def test_list_states(self, tmp_path):
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        store.save(_make_state(daemon_id="d1"))
        store.save(_make_state(daemon_id="d2"))
        states = store.list_states()
        assert len(states) == 2
        store.close()

    def test_persists_across_instances(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store1 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)
        state = _make_state(daemon_id="d1", actual_state="running", consecutive_failures=2)
        store1.save(state)
        store1.close()

        store2 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)
        retrieved = store2.get("d1")
        assert retrieved is not None
        assert retrieved.actual_state == "running"
        assert retrieved.consecutive_failures == 2
        store2.close()

    def test_error_redacted_on_save(self, tmp_path):
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        state = _make_state(
            daemon_id="d1",
            last_error_message="token=abc123 failed",
        )
        store.save(state)
        retrieved = store.get("d1")
        assert "abc123" not in (retrieved.last_error_message or "")
        store.close()

    def test_close_behavior(self, tmp_path):
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        store.save(_make_state(daemon_id="d1"))
        store.close()
        # After close, operations should still work (new connection)
        store2 = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "test.db"))
        assert store2.get("d1") is not None
        store2.close()


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestCreateDaemonStateStore:
    """Phase 57: Factory function tests."""

    def test_create_memory(self):
        store = create_retry_daemon_state_store("memory")
        assert isinstance(store, InMemoryAlertDeliveryRetryDaemonStateStore)

    def test_create_sqlite(self, tmp_path):
        store = create_retry_daemon_state_store("sqlite", str(tmp_path / "test.db"))
        assert isinstance(store, SQLiteAlertDeliveryRetryDaemonStateStore)
        store.close()

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_retry_daemon_state_store("redis")


# ---------------------------------------------------------------------------
# Error redaction tests
# ---------------------------------------------------------------------------


class TestErrorRedaction:
    """Phase 57: Error message redaction tests."""

    def test_redact_token_in_message(self):
        msg = "Auth failed: token=abc123xyz"
        result = _redact_error_message(msg)
        assert "abc123xyz" not in result
        assert "[REDACTED]" in result

    def test_redact_secret_in_message(self):
        msg = "Invalid secret=supersecretvalue"
        result = _redact_error_message(msg)
        assert "supersecretvalue" not in result

    def test_redact_api_key_in_message(self):
        msg = "api_key=sk-1234567890abcdef rejected"
        result = _redact_error_message(msg)
        assert "sk-1234567890abcdef" not in result

    def test_clean_message_unchanged(self):
        msg = "Connection refused to host: port 443"
        result = _redact_error_message(msg)
        assert result == msg

    def test_none_input(self):
        assert _redact_error_message(None) is None

    def test_empty_input(self):
        assert _redact_error_message("") == ""

    def test_redact_result_dict(self):
        result = _redact_result({
            "error_message": "token=abc123 failed",
            "body_preview": "secret data here",
            "headers": {"authorization": "Bearer xyz", "content-type": "application/json"},
            "count": 42,
        })
        assert "abc123" not in str(result.get("error_message", ""))
        assert "Bearer xyz" not in str(result.get("headers", {}).get("authorization", ""))
        assert result.get("count") == 42
