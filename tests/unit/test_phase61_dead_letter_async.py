"""Phase 61 Task 6: Async dead-letter evaluation wrapper tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
)
from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
    DeadLetterPolicyResult,
    evaluate_async,
    InMemoryDeadLetterPolicyStore,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(attempt: int = 6, attempt_id: str = "nda_001") -> AlertPriorityQueueItem:
    return AlertPriorityQueueItem(
        attempt_id=attempt_id,
        alert_id="alert-001",
        target_id="target-001",
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status=AlertPriorityQueueItemStatus.CLAIMED.value,
        priority=0,
        created_at=datetime.now(timezone.utc),
        attempt=attempt,
        metadata_json="{}",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvaluateAsync:
    """Tests for evaluate_async wrapper."""

    def test_returns_dead_letter_result(self):
        """evaluate_async returns DeadLetterPolicyResult."""
        store = InMemoryDeadLetterPolicyStore()
        item = _make_item()
        result = asyncio.run(evaluate_async(store, item))
        assert isinstance(result, DeadLetterPolicyResult)

    def test_delegates_to_store_evaluate(self):
        """evaluate_async delegates to store.evaluate."""
        store = InMemoryDeadLetterPolicyStore()
        item = _make_item()
        with patch.object(store, "evaluate", return_value=DeadLetterPolicyResult(is_dead_letter=False)) as mock_eval:
            result = asyncio.run(evaluate_async(store, item))
            mock_eval.assert_called_once_with(item)

    def test_dead_letter_when_exceeds_max_retries(self):
        """evaluate_async identifies dead letter when attempt > max_retries."""
        store = InMemoryDeadLetterPolicyStore()
        # Default max_retries=5, attempt=6 → dead letter
        item = _make_item(attempt=6, attempt_id="nda_dead_001")
        result = asyncio.run(evaluate_async(store, item))
        assert result.is_dead_letter is True
        assert result.reason == "max_retries_exceeded"

    def test_not_dead_letter_within_retry_limit(self):
        """evaluate_async returns False for items within retry limit."""
        store = InMemoryDeadLetterPolicyStore()
        item = _make_item(attempt=3, attempt_id="nda_ok_001")
        result = asyncio.run(evaluate_async(store, item))
        assert result.is_dead_letter is False
        assert result.reason is None

    def test_thread_safety_no_blocking(self):
        """evaluate_async works correctly (no event loop blocking)."""
        store = InMemoryDeadLetterPolicyStore()
        item = _make_item()
        # Should not raise RuntimeError about event loop
        result = asyncio.run(evaluate_async(store, item))
        assert isinstance(result, DeadLetterPolicyResult)

    def test_dead_letter_has_record(self):
        """evaluate_async returns DeadLetterRecord when applicable."""
        store = InMemoryDeadLetterPolicyStore()
        item = _make_item(attempt=6, attempt_id="nda_rec_001")
        result = asyncio.run(evaluate_async(store, item))
        assert result.is_dead_letter is True
        assert result.record is not None
        assert result.record.attempt_id == "nda_rec_001"
        assert result.record.alert_id == "alert-001"
        assert result.record.target_id == "target-001"
        assert result.record.attempt_count == 6
