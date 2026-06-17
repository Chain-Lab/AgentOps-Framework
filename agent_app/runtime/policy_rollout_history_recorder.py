"""Rollout history recorder — creates and appends normalized rollout history events."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)
from agent_app.runtime.policy_rollout_history_store import RolloutHistoryStore

logger = logging.getLogger(__name__)


class RolloutHistoryRecorder:
    """Creates and appends normalized rollout history events.

    Should be safe to call from services. Missing recorder should not break
    existing behavior. Errors in recording should be audited but should not
    break rollout execution.
    """

    def __init__(
        self,
        history_store: RolloutHistoryStore,
        audit_logger: Any | None = None,
    ) -> None:
        self._history_store = history_store
        self._audit_logger = audit_logger

    async def record(
        self,
        rollout_id: str,
        event_type: RolloutHistoryEventType,
        step_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        actor_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RolloutHistoryEvent:
        """Create and append a normalized history event.

        Optionally writes audit event: policy.rollout.history.recorded
        """
        event = RolloutHistoryEvent(
            history_event_id=f"rhe_{uuid.uuid4().hex[:12]}",
            rollout_id=rollout_id,
            event_type=event_type,
            step_id=step_id,
            environment=environment,
            ring_name=ring_name,
            actor_id=actor_id,
            source_type=source_type,
            source_id=source_id,
            message=message,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        result = await self._history_store.append(event)

        # Best-effort audit
        if self._audit_logger is not None:
            try:
                from agent_app.governance.audit import AuditEvent
                audit_event = AuditEvent(
                    event_id=f"ae_{uuid.uuid4().hex[:12]}",
                    event_type="policy.rollout.history.recorded",
                    user_id=actor_id,
                    data={
                        "history_event_id": result.history_event_id,
                        "rollout_id": rollout_id,
                        "event_type": event_type.value,
                        "step_id": step_id,
                    },
                )
                await self._audit_logger.log(audit_event)
            except Exception:
                logger.debug("Audit log failed for rollout history recording", exc_info=True)

        return result
