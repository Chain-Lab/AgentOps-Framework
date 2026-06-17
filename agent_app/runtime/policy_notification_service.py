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

logger = logging.getLogger(__name__)


class PolicyNotificationService:
    """Service for creating and delivering policy notifications."""

    def __init__(
        self,
        notification_store: PolicyNotificationStore,
        rule_store: PolicyNotificationRuleStore,
        channels: dict[str, Any] | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._store = notification_store
        self._rule_store = rule_store
        self._channels = channels or {}
        self._audit_logger = audit_logger

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
