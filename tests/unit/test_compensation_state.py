"""Tests for compensation state models (Phase 16.1)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_app.runtime.compensation_state import (
    CompensationActionState,
    CompensationActionStatus,
    CompensationExecutionState,
    CompensationRunStatus,
    deserialize_compensation_state,
    serialize_compensation_state,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_action(
    action_id: str = "action_1",
    run_id: str = "run-1",
    node_id: str = "node-1",
    compensating_for_node_id: str = "node-1",
    status: str = CompensationActionStatus.PENDING.value,
    attempts: int = 0,
    max_attempts: int = 1,
) -> CompensationActionState:
    return CompensationActionState(
        action_id=action_id,
        run_id=run_id,
        node_id=node_id,
        compensating_for_node_id=compensating_for_node_id,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def _make_state(
    run_id: str = "run-1",
    status: str = CompensationRunStatus.PENDING.value,
    actions: dict[str, CompensationActionState] | None = None,
) -> CompensationExecutionState:
    if actions is None:
        actions = {
            "action_1": _make_action(run_id=run_id),
            "action_2": _make_action(
                action_id="action_2", run_id=run_id, node_id="node-2"
            ),
        }
    return CompensationExecutionState(
        run_id=run_id,
        workflow_name="test-workflow",
        status=status,
        actions=actions,
    )


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestCompensationActionStatus:
    def test_status_values(self) -> None:
        assert CompensationActionStatus.PENDING == "pending"
        assert CompensationActionStatus.RUNNING == "running"
        assert CompensationActionStatus.COMPLETED == "completed"
        assert CompensationActionStatus.FAILED == "failed"
        assert CompensationActionStatus.SKIPPED == "skipped"


class TestCompensationRunStatus:
    def test_status_values(self) -> None:
        assert CompensationRunStatus.NOT_REQUIRED == "not_required"
        assert CompensationRunStatus.PENDING == "pending"
        assert CompensationRunStatus.RUNNING == "running"
        assert CompensationRunStatus.COMPLETED == "completed"
        assert CompensationRunStatus.PARTIAL_FAILED == "partial_failed"
        assert CompensationRunStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# CompensationActionState tests
# ---------------------------------------------------------------------------

class TestCompensationActionState:
    def test_create_action_defaults(self) -> None:
        action = _make_action()
        assert action.action_id == "action_1"
        assert action.run_id == "run-1"
        assert action.node_id == "node-1"
        assert action.compensating_for_node_id == "node-1"
        assert action.status == CompensationActionStatus.PENDING.value
        assert action.attempts == 0
        assert action.max_attempts == 1
        assert action.output is None
        assert action.error is None
        assert action.idempotency_key is None
        assert action.started_at is None
        assert action.completed_at is None

    def test_action_id_auto_generated(self) -> None:
        action = CompensationActionState(
            run_id="run-1",
            node_id="node-1",
        )
        assert action.action_id is not None
        assert action.action_id.startswith("action_")

    def test_action_id_explicit(self) -> None:
        action = CompensationActionState(
            action_id="custom_id",
            run_id="run-1",
            node_id="node-1",
        )
        assert action.action_id == "custom_id"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(Exception):  # ValidationError
            CompensationActionState(
                action_id="a1",
                run_id="r1",
                node_id="n1",
                status="invalid_status",
            )

    def test_mark_running(self) -> None:
        action = _make_action(attempts=0)
        action.mark_running()
        assert action.status == CompensationActionStatus.RUNNING.value
        assert action.attempts == 1
        assert action.started_at is not None

    def test_mark_running_increments_attempts(self) -> None:
        action = _make_action(attempts=1)
        action.mark_running()
        assert action.attempts == 2

    def test_mark_completed(self) -> None:
        action = _make_action(status=CompensationActionStatus.RUNNING.value)
        action.mark_completed(output={"result": "ok"})
        assert action.status == CompensationActionStatus.COMPLETED.value
        assert action.output == {"result": "ok"}
        assert action.completed_at is not None

    def test_mark_failed_with_exception(self) -> None:
        action = _make_action(status=CompensationActionStatus.RUNNING.value)
        action.mark_failed(RuntimeError("handler failed"))
        assert action.status == CompensationActionStatus.FAILED.value
        assert action.error is not None
        assert action.error["type"] == "RuntimeError"
        assert action.error["message"] == "handler failed"
        assert action.completed_at is not None

    def test_mark_failed_with_dict(self) -> None:
        action = _make_action(status=CompensationActionStatus.RUNNING.value)
        action.mark_failed({"type": "timeout", "message": "timed out"})
        assert action.error == {"type": "timeout", "message": "timed out"}

    def test_mark_skipped(self) -> None:
        action = _make_action()
        action.mark_skipped(reason="node not found")
        assert action.status == CompensationActionStatus.SKIPPED.value
        assert action.error is not None
        assert action.error["type"] == "skipped"

    def test_mark_skipped_no_reason(self) -> None:
        action = _make_action()
        action.mark_skipped()
        assert action.status == CompensationActionStatus.SKIPPED.value

    def test_can_retry_pending(self) -> None:
        action = _make_action(status=CompensationActionStatus.PENDING.value)
        assert action.can_retry() is False

    def test_can_retry_failed_with_attempts_remaining(self) -> None:
        action = _make_action(
            status=CompensationActionStatus.FAILED.value,
            attempts=1,
            max_attempts=3,
        )
        assert action.can_retry() is True

    def test_can_retry_failed_exhausted(self) -> None:
        action = _make_action(
            status=CompensationActionStatus.FAILED.value,
            attempts=3,
            max_attempts=3,
        )
        assert action.can_retry() is False

    def test_can_retry_completed(self) -> None:
        action = _make_action(status=CompensationActionStatus.COMPLETED.value)
        assert action.can_retry() is False

    def test_serialization(self) -> None:
        action = _make_action(status=CompensationActionStatus.RUNNING.value)
        action.mark_running()  # increments attempts to 1
        action.mark_completed(output=[1, 2, 3])
        data = action.model_dump(mode="json")
        assert data["status"] == "completed"
        assert data["output"] == [1, 2, 3]
        assert data["attempts"] == 1

    def test_timezone_aware_datetime(self) -> None:
        action = _make_action()
        now = datetime.now(timezone.utc)
        action.started_at = now
        action.completed_at = now
        data = action.model_dump(mode="json")
        assert "T" in data["started_at"]  # ISO format
        assert data["started_at"].endswith("+00:00") or data["started_at"].endswith("Z")


# ---------------------------------------------------------------------------
# CompensationExecutionState tests
# ---------------------------------------------------------------------------

class TestCompensationExecutionState:
    def test_create_state_defaults(self) -> None:
        state = _make_state()
        assert state.schema_version == 1
        assert state.status == CompensationRunStatus.PENDING.value
        assert len(state.actions) == 2
        assert len(state.action_order) == 2

    def test_compensation_id_auto_generated(self) -> None:
        state = _make_state()
        assert state.compensation_id is not None
        assert state.compensation_id.startswith("comp_")

    def test_compensation_id_explicit(self) -> None:
        state = CompensationExecutionState(
            compensation_id="custom_comp",
            run_id="run-1",
        )
        assert state.compensation_id == "custom_comp"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(Exception):  # ValidationError
            CompensationExecutionState(
                run_id="run-1",
                status="invalid_status",
            )

    def test_add_action(self) -> None:
        state = CompensationExecutionState(run_id="run-1")
        action = _make_action(action_id="a3", node_id="node-3")
        state.add_action(action)
        assert "a3" in state.actions
        assert "a3" in state.action_order

    def test_add_action_does_not_duplicate(self) -> None:
        state = _make_state()
        initial_count = len(state.actions)
        action = _make_action(action_id="action_1")  # same ID
        state.add_action(action)
        assert len(state.actions) == initial_count  # no duplicate

    def test_get_action(self) -> None:
        state = _make_state()
        action = state.get_action("action_1")
        assert action is not None
        assert action.node_id == "node-1"

    def test_get_action_missing(self) -> None:
        state = _make_state()
        assert state.get_action("nonexistent") is None

    def test_get_pending_actions(self) -> None:
        state = _make_state()
        state.actions["action_1"].status = CompensationActionStatus.RUNNING.value
        pending = state.get_pending_actions()
        assert len(pending) == 1
        assert pending[0].action_id == "action_2"

    def test_get_completed_actions(self) -> None:
        state = _make_state()
        state.actions["action_1"].status = CompensationActionStatus.COMPLETED.value
        completed = state.get_completed_actions()
        assert len(completed) == 1
        assert completed[0].action_id == "action_1"

    def test_get_failed_retryable_actions(self) -> None:
        state = _make_state()
        state.actions["action_1"].status = CompensationActionStatus.FAILED.value
        state.actions["action_1"].attempts = 1
        state.actions["action_1"].max_attempts = 3
        retryable = state.get_failed_retryable_actions()
        assert len(retryable) == 1

    def test_mark_running(self) -> None:
        state = _make_state()
        state.mark_running()
        assert state.status == CompensationRunStatus.RUNNING.value
        assert state.started_at is not None

    def test_mark_completed(self) -> None:
        state = _make_state(status=CompensationRunStatus.RUNNING.value)
        state.mark_completed()
        assert state.status == CompensationRunStatus.COMPLETED.value
        assert state.completed_at is not None

    def test_mark_partial_failed(self) -> None:
        state = _make_state()
        state.mark_partial_failed()
        assert state.status == CompensationRunStatus.PARTIAL_FAILED.value
        assert state.completed_at is not None

    def test_mark_failed(self) -> None:
        state = _make_state()
        state.mark_failed()
        assert state.status == CompensationRunStatus.FAILED.value
        assert state.completed_at is not None

    def test_action_order_sync(self) -> None:
        """action_order should track actions automatically."""
        state = CompensationExecutionState(run_id="run-1")
        a1 = _make_action(action_id="a1", run_id="run-1")
        a2 = _make_action(action_id="a2", run_id="run-1", node_id="node-2")
        state.add_action(a1)
        state.add_action(a2)
        assert state.action_order == ["a1", "a2"]

    def test_action_order_filter_invalid(self) -> None:
        """action_order should filter out IDs not in actions."""
        state = CompensationExecutionState(
            run_id="run-1",
            action_order=["a1", "nonexistent", "a2"],
            actions={
                "a1": _make_action(action_id="a1", run_id="run-1"),
                "a2": _make_action(action_id="a2", run_id="run-1", node_id="node-2"),
            },
        )
        assert "nonexistent" not in state.action_order
        assert state.action_order == ["a1", "a2"]


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestCompensationStateSerialization:
    def test_serialize_and_deserialize(self) -> None:
        state = _make_state()
        state.actions["action_1"].status = CompensationActionStatus.COMPLETED.value
        state.actions["action_1"].output = {"result": "ok"}

        json_str = serialize_compensation_state(state)
        assert isinstance(json_str, str)

        restored = deserialize_compensation_state(json_str)
        assert restored.run_id == "run-1"
        assert restored.status == CompensationRunStatus.PENDING.value
        assert restored.schema_version == 1
        assert "action_1" in restored.actions
        assert restored.actions["action_1"].status == "completed"
        assert restored.actions["action_1"].output == {"result": "ok"}

    def test_serialize_with_datetime(self) -> None:
        state = _make_state()
        now = datetime.now(timezone.utc)
        state.started_at = now
        state.completed_at = now
        state.actions["action_1"].started_at = now
        state.actions["action_1"].completed_at = now

        json_str = serialize_compensation_state(state)
        parsed = json.loads(json_str)
        assert "T" in parsed["started_at"]
        assert "T" in parsed["actions"]["action_1"]["started_at"]

    def test_deserialize_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid compensation state JSON"):
            deserialize_compensation_state("not valid json {{{")

    def test_deserialize_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid compensation state data"):
            deserialize_compensation_state('{}')

    def test_round_trip_preserves_action_order(self) -> None:
        state = _make_state()
        json_str = serialize_compensation_state(state)
        restored = deserialize_compensation_state(json_str)
        assert restored.action_order == ["action_1", "action_2"]

    def test_non_serializable_input_gives_error(self) -> None:
        """Non-JSON-serializable output raises PydanticSerializationError."""
        from pydantic_core import PydanticSerializationError
        action = _make_action()
        action.output = lambda x: x  # Non-serializable
        with pytest.raises(PydanticSerializationError):
            action.model_dump(mode="json")
