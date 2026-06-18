"""Phase 47: Federation history recorder — creates and appends normalized federation history events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
)
from agent_app.runtime.policy_rollout_federation_history_store import FederationHistoryStore


class FederationHistoryRecorder:
    """Creates and appends normalized federation history events.

    Should be safe to call from services. Missing recorder should not break
    existing behavior. Errors in recording should be audited but should not
    break federation execution.
    """

    def __init__(
        self,
        history_store: FederationHistoryStore,
        audit_logger: Any | None = None,
    ) -> None:
        self._store = history_store
        self._audit_logger = audit_logger

    async def record(
        self,
        event_type: FederationHistoryEventType,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        region: str | None = None,
        actor_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FederationHistoryEvent:
        """Create and append a normalized federation history event.

        Optionally writes audit event: policy.federation.history.recorded
        """
        event = FederationHistoryEvent(
            history_event_id=f"fhe_{uuid.uuid4().hex[:16]}",
            federation_id=federation_id,
            target_id=target_id,
            rollout_id=rollout_id,
            wave_id=wave_id,
            event_type=event_type,
            tenant_id=tenant_id,
            environment=environment,
            ring_name=ring_name,
            region=region,
            actor_id=actor_id,
            source_type=source_type,
            source_id=source_id,
            message=message,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        result = await self._store.append(event)

        # Best-effort audit (never raises)
        if self._audit_logger is not None:
            try:
                from agent_app.governance.audit import AuditEvent
                audit_event = AuditEvent(
                    event_id=f"ae_{uuid.uuid4().hex[:12]}",
                    event_type="policy.federation.history.recorded",
                    user_id=actor_id,
                    data={
                        "history_event_id": result.history_event_id,
                        "federation_id": federation_id,
                        "event_type": event_type.value,
                        "target_id": target_id,
                    },
                )
                await self._audit_logger.log(audit_event)
            except Exception:
                pass

        return result
