"""Unit tests for RolloutHistoryRecorder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.policy_rollout_history_recorder import RolloutHistoryRecorder
from agent_app.runtime.policy_rollout_history_store import InMemoryRolloutHistoryStore


@pytest.fixture
def history_store() -> InMemoryRolloutHistoryStore:
    return InMemoryRolloutHistoryStore()


@pytest.fixture
def audit_logger() -> InMemoryAuditLogger:
    return InMemoryAuditLogger()


@pytest.mark.asyncio
async def test_record_event(history_store: InMemoryRolloutHistoryStore) -> None:
    """Records and returns a history event."""
    recorder = RolloutHistoryRecorder(history_store=history_store)
    result = await recorder.record(
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
        message="Rollout created",
    )

    assert isinstance(result, RolloutHistoryEvent)
    assert result.rollout_id == "ro_001"
    assert result.event_type == RolloutHistoryEventType.ROLLOUT_CREATED
    assert result.message == "Rollout created"
    assert result.created_at.tzinfo is not None

    # Verify it was persisted
    fetched = await history_store.get(result.history_event_id)
    assert fetched is not None
    assert fetched.rollout_id == "ro_001"


@pytest.mark.asyncio
async def test_record_event_with_metadata(history_store: InMemoryRolloutHistoryStore) -> None:
    """Metadata is preserved in the recorded event."""
    recorder = RolloutHistoryRecorder(history_store=history_store)
    result = await recorder.record(
        rollout_id="ro_002",
        event_type=RolloutHistoryEventType.STEP_STARTED,
        step_id="step_1",
        metadata={"ring": "canary", "attempt": 1},
    )

    assert result.metadata == {"ring": "canary", "attempt": 1}
    assert result.step_id == "step_1"


@pytest.mark.asyncio
async def test_record_event_generates_id(history_store: InMemoryRolloutHistoryStore) -> None:
    """Auto-generates rhe_ prefix ID."""
    recorder = RolloutHistoryRecorder(history_store=history_store)
    result = await recorder.record(
        rollout_id="ro_003",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
    )

    assert result.history_event_id.startswith("rhe_")
    assert len(result.history_event_id) > 4  # rhe_ + hex chars

    # Each call generates a unique ID
    result2 = await recorder.record(
        rollout_id="ro_003",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
    )
    assert result.history_event_id != result2.history_event_id


@pytest.mark.asyncio
async def test_record_event_audit(
    history_store: InMemoryRolloutHistoryStore,
    audit_logger: InMemoryAuditLogger,
) -> None:
    """Audit event is emitted when audit_logger is provided."""
    recorder = RolloutHistoryRecorder(
        history_store=history_store,
        audit_logger=audit_logger,
    )
    result = await recorder.record(
        rollout_id="ro_004",
        event_type=RolloutHistoryEventType.GATE_SATISFIED,
        step_id="step_2",
        actor_id="user_alice",
    )

    # Verify audit event was logged
    audit_events = audit_logger.list_events(event_type="policy.rollout.history.recorded")
    assert len(audit_events) == 1
    audit_evt = audit_events[0]
    assert audit_evt.user_id == "user_alice"
    assert audit_evt.data["history_event_id"] == result.history_event_id
    assert audit_evt.data["rollout_id"] == "ro_004"
    assert audit_evt.data["event_type"] == "rollout.gate.satisfied"
    assert audit_evt.data["step_id"] == "step_2"


@pytest.mark.asyncio
async def test_record_event_no_audit(history_store: InMemoryRolloutHistoryStore) -> None:
    """No audit event when audit_logger is None."""
    recorder = RolloutHistoryRecorder(history_store=history_store)
    await recorder.record(
        rollout_id="ro_005",
        event_type=RolloutHistoryEventType.APPROVAL_REQUESTED,
    )

    # No audit_logger set, so no crash — recorder works fine
    events = await history_store.list(rollout_id="ro_005")
    assert len(events) == 1
