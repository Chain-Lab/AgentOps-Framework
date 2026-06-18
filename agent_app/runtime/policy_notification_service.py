"""Notification service — match rules, create, send, and list notifications.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)
from agent_app.runtime.policy_notification_store import PolicyNotificationStore
from agent_app.runtime.policy_notification_rule_store import PolicyNotificationRuleStore
from agent_app.governance.policy_rollout_history import RolloutHistoryEventType

logger = logging.getLogger(__name__)


class PolicyNotificationService:
    """Service for creating and delivering policy notifications."""

    def __init__(
        self,
        notification_store: PolicyNotificationStore,
        rule_store: PolicyNotificationRuleStore,
        channels: dict[str, Any] | None = None,
        audit_logger: Any | None = None,
        history_recorder: Any | None = None,
        federation_recorder: Any | None = None,
    ) -> None:
        self._store = notification_store
        self._rule_store = rule_store
        self._channels = channels or {}
        self._audit_logger = audit_logger
        self._history_recorder = history_recorder
        self._federation_recorder = federation_recorder

    async def notify_event(
        self,
        event_type: str,
        data: dict[str, Any],
        source_type: str | None = None,
        source_id: str | None = None,
        actor_id: str | None = None,
    ) -> list[PolicyNotificationMessage]:
        """Match enabled rules against event, create and send notifications."""
        rules = await self._rule_store.list(status=PolicyNotificationRuleStatus.ENABLED)
        matching = [
            r for r in rules
            if event_type in r.event_types
            and (not r.source_types or source_type in r.source_types)
        ]

        messages: list[PolicyNotificationMessage] = []
        for rule in matching:
            # Render templates
            title = rule.title_template
            if title:
                try:
                    title = title.format(**data)
                except (KeyError, IndexError):
                    pass  # Keep template as-is on error
            else:
                title = f"{event_type}"

            body = rule.body_template
            if body:
                try:
                    body = body.format(**data)
                except (KeyError, IndexError):
                    pass
            else:
                body = str(data)

            msg = PolicyNotificationMessage(
                notification_id=f"pn_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                severity=rule.severity,
                title=title,
                body=body,
                source_type=source_type,
                source_id=source_id,
                actor_id=actor_id,
                metadata=data,
                created_at=datetime.now(timezone.utc),
            )

            # Store as PENDING
            await self._store.create(msg)

            # Record NOTIFICATION_CREATED if rollout-related
            rollout_id_for_history = data.get("rollout_id")
            if rollout_id_for_history is not None:
                await self._record_history(
                    rollout_id_for_history,
                    RolloutHistoryEventType.NOTIFICATION_CREATED,
                    source_type=source_type,
                    source_id=source_id,
                )

            # Send through channels
            all_ok = True
            channel_errors: list[dict[str, Any]] = []
            for ch_name in rule.channels:
                ch = self._channels.get(ch_name)
                if ch is None:
                    all_ok = False
                    channel_errors.append({"channel": ch_name, "error": "unknown channel"})
                    continue
                try:
                    result = await ch.send(msg)
                    if result.status == PolicyNotificationStatus.FAILED:
                        all_ok = False
                        channel_errors.append({
                            "channel": ch_name,
                            "error": result.error or "channel returned failed",
                        })
                except Exception as exc:
                    all_ok = False
                    channel_errors.append({
                        "channel": ch_name,
                        "error": str(exc),
                    })

            # Update status
            if all_ok:
                msg.status = PolicyNotificationStatus.SENT
                msg.sent_at = datetime.now(timezone.utc)
            else:
                msg.status = PolicyNotificationStatus.FAILED
                msg.error = {"channel_errors": channel_errors}
            await self._store.update(msg)

            # Record NOTIFICATION_SENT or NOTIFICATION_FAILED if rollout-related
            rollout_id_for_history = data.get("rollout_id")
            if rollout_id_for_history is not None:
                history_event_type = (
                    RolloutHistoryEventType.NOTIFICATION_SENT
                    if all_ok
                    else RolloutHistoryEventType.NOTIFICATION_FAILED
                )
                await self._record_history(
                    rollout_id_for_history,
                    history_event_type,
                    source_type=source_type,
                    source_id=source_id,
                )

            # Best-effort federation recorder for federation-related notifications
            federation_id_for_recorder = data.get("federation_id")
            is_federation_source = (
                source_type is not None and source_type.startswith("federation")
            )
            if self._federation_recorder is not None and (federation_id_for_recorder is not None or is_federation_source):
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    fed_event_type = (
                        FederationHistoryEventType.NOTIFICATION_SENT
                        if all_ok
                        else FederationHistoryEventType.NOTIFICATION_FAILED
                    )
                    await self._federation_recorder.record(
                        event_type=fed_event_type,
                        federation_id=federation_id_for_recorder,
                        source_type=source_type,
                        source_id=source_id,
                        message=f"Notification {'sent' if all_ok else 'failed'}: {msg.title}",
                        metadata={
                            "notification_id": msg.notification_id,
                            "rule_id": rule.rule_id,
                            "event_type": event_type,
                        },
                    )
                except Exception:
                    pass

            # Audit (best-effort)
            await self._audit(
                f"policy.notification.{'sent' if all_ok else 'failed'}",
                {"notification_id": msg.notification_id, "rule_id": rule.rule_id},
            )

            messages.append(msg)

        return messages

    async def send_pending(
        self,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        """Send all pending notifications through log channel."""
        pending = await self._store.list(status=PolicyNotificationStatus.PENDING, limit=limit)
        log_ch = self._channels.get("log")
        sent: list[PolicyNotificationMessage] = []
        for msg in pending:
            if log_ch is not None:
                try:
                    result = await log_ch.send(msg)
                    msg.status = result.status
                    msg.sent_at = result.sent_at
                except Exception as exc:
                    msg.status = PolicyNotificationStatus.FAILED
                    msg.error = {"type": "send_error", "message": str(exc)}
            else:
                msg.status = PolicyNotificationStatus.SENT
                msg.sent_at = datetime.now(timezone.utc)
            await self._store.update(msg)
            sent.append(msg)
        return sent

    async def list_notifications(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        """List notifications with optional filters."""
        return await self._store.list(status=status, event_type=event_type, limit=limit)

    async def _audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Record audit event (best-effort)."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent
            event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                data=data,
            )
            await self._audit_logger.log(event)
        except Exception:
            pass

    async def _record_history(
        self,
        rollout_id: str,
        event_type: Any,  # RolloutHistoryEventType
        **kwargs: Any,
    ) -> None:
        """Record a rollout history event (best-effort, never raises)."""
        if self._history_recorder is None:
            return
        try:
            await self._history_recorder.record(rollout_id=rollout_id, event_type=event_type, **kwargs)
        except Exception:
            pass  # History recording failure must not break notification
