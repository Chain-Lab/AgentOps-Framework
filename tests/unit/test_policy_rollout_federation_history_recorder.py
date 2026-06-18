"""Unit tests for FederationHistoryRecorder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEvent,
    FederationHistoryEventType,
)
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.policy_rollout_federation_history_recorder import FederationHistoryRecorder
from agent_app.runtime.policy_rollout_federation_history_store import InMemoryFederationHistoryStore


@pytest.fixture
def history_store() -> InMemoryFederationHistoryStore:
    return InMemoryFederationHistoryStore()


@pytest.fixture
def audit_logger() -> InMemoryAuditLogger:
    return InMemoryAuditLogger()


class TestFederationHistoryRecorder:

    @pytest.mark.asyncio
    async def test_record_creates_event(
        self,
        history_store: InMemoryFederationHistoryStore,
    ) -> None:
        """record() creates and appends an event, verify it is stored."""
        recorder = FederationHistoryRecorder(history_store=history_store)
        result = await recorder.record(
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            federation_id="fed_001",
            message="Federation created",
        )

        assert isinstance(result, FederationHistoryEvent)
        assert result.federation_id == "fed_001"
        assert result.event_type == FederationHistoryEventType.FEDERATION_CREATED
        assert result.message == "Federation created"

        # Verify it was persisted
        fetched = await history_store.get(result.history_event_id)
        assert fetched is not None
        assert fetched.federation_id == "fed_001"

    @pytest.mark.asyncio
    async def test_record_preserves_all_fields(
        self,
        history_store: InMemoryFederationHistoryStore,
    ) -> None:
        """All optional fields are preserved in the recorded event."""
        recorder = FederationHistoryRecorder(history_store=history_store)
        result = await recorder.record(
            event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="fed_002",
            target_id="tgt_001",
            rollout_id="ro_001",
            wave_id="wave_001",
            tenant_id="tenant_acme",
            environment="production",
            ring_name="canary",
            region="us-east-1",
            actor_id="user_alice",
            source_type="federation",
            source_id="src_001",
            message="Target execution started",
            metadata={"ring": "canary", "attempt": 1},
        )

        assert result.federation_id == "fed_002"
        assert result.target_id == "tgt_001"
        assert result.rollout_id == "ro_001"
        assert result.wave_id == "wave_001"
        assert result.event_type == FederationHistoryEventType.TARGET_EXECUTION_STARTED
        assert result.tenant_id == "tenant_acme"
        assert result.environment == "production"
        assert result.ring_name == "canary"
        assert result.region == "us-east-1"
        assert result.actor_id == "user_alice"
        assert result.source_type == "federation"
        assert result.source_id == "src_001"
        assert result.message == "Target execution started"
        assert result.metadata == {"ring": "canary", "attempt": 1}

    @pytest.mark.asyncio
    async def test_record_generates_unique_ids(
        self,
        history_store: InMemoryFederationHistoryStore,
    ) -> None:
        """Two records get different fhe_ IDs."""
        recorder = FederationHistoryRecorder(history_store=history_store)
        result1 = await recorder.record(
            event_type=FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="fed_003",
        )
        result2 = await recorder.record(
            event_type=FederationHistoryEventType.FEDERATION_COMPLETED,
            federation_id="fed_003",
        )

        assert result1.history_event_id.startswith("fhe_")
        assert result2.history_event_id.startswith("fhe_")
        assert result1.history_event_id != result2.history_event_id

    @pytest.mark.asyncio
    async def test_record_timezone_aware(
        self,
        history_store: InMemoryFederationHistoryStore,
    ) -> None:
        """created_at is timezone-aware."""
        recorder = FederationHistoryRecorder(history_store=history_store)
        result = await recorder.record(
            event_type=FederationHistoryEventType.WAVE_STARTED,
            federation_id="fed_004",
        )

        assert result.created_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_record_with_no_audit_logger(
        self,
        history_store: InMemoryFederationHistoryStore,
    ) -> None:
        """Works fine without audit_logger."""
        recorder = FederationHistoryRecorder(history_store=history_store)
        result = await recorder.record(
            event_type=FederationHistoryEventType.FEDERATION_CANCELLED,
            federation_id="fed_005",
        )

        # No audit_logger set, so no crash — recorder works fine
        events = await history_store.list(federation_id="fed_005")
        assert len(events) == 1
        assert events[0].history_event_id == result.history_event_id
