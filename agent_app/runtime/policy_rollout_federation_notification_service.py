"""Federation notification service — enqueue and dispatch federation approval notifications.

Phase 49: Federation Notification Service.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDelivery,
    FederationNotificationDispatchResult,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationPolicy,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_adapters import (
    FederationNotificationAdapter,
)
from agent_app.runtime.policy_rollout_federation_notification_store import (
    FederationNotificationStore,
)

logger = logging.getLogger(__name__)


class FederationNotificationService:
    """Service for enqueuing and dispatching federation approval notifications."""

    def __init__(
        self,
        notification_store: FederationNotificationStore,
        adapters: dict[FederationNotificationChannel, FederationNotificationAdapter],
        notification_policy: FederationNotificationPolicy,
        audit_logger: Any | None = None,
        change_event_store: Any | None = None,
        history_recorder: Any | None = None,
    ) -> None:
        self._store = notification_store
        self._adapters = adapters
        self._policy = notification_policy
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store
        self._history_recorder = history_recorder

    # ------------------------------------------------------------------
    # Public enqueue methods
    # ------------------------------------------------------------------

    async def enqueue_for_approval_created(
        self,
        *,
        approval_id: str,
        federation_id: str | None = None,
        action: str,
        requested_by: str,
        recipients: list[str] | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        region: str | None = None,
        ring: str | None = None,
    ) -> list[FederationNotificationMessage]:
        """Enqueue notifications for a newly created approval request."""
        messages: list[FederationNotificationMessage] = []
        for channel in self._policy.default_channels:
            msg = self._build_message(
                approval_id=approval_id,
                federation_id=federation_id,
                event_type=FederationNotificationEventType.APPROVAL_CREATED,
                channel=channel,
                recipients=recipients,
                subject=f"Federation Approval Required: {action}",
                body=(
                    f"A federation approval request has been created for action '{action}' "
                    f"by '{requested_by}'. Approval ID: {approval_id}."
                ),
                payload={
                    "approval_id": approval_id,
                    "federation_id": federation_id,
                    "action": action,
                    "requested_by": requested_by,
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "region": region,
                    "ring": ring,
                },
            )
            created = await self._store.create(msg)
            messages.append(created)
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_CREATED.value,
            )
            self._record_change_event(
                event_type="notification.enqueued",
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type="notification.enqueued",
                details={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
        return messages

    async def enqueue_for_approval_approved(
        self,
        *,
        approval_id: str,
        federation_id: str | None = None,
        action: str,
        approved_by: str,
        recipients: list[str] | None = None,
    ) -> list[FederationNotificationMessage]:
        """Enqueue notifications for an approved approval request."""
        messages: list[FederationNotificationMessage] = []
        for channel in self._policy.default_channels:
            msg = self._build_message(
                approval_id=approval_id,
                federation_id=federation_id,
                event_type=FederationNotificationEventType.APPROVAL_APPROVED,
                channel=channel,
                recipients=recipients,
                subject=f"Federation Approval Granted: {action}",
                body=(
                    f"The federation approval request for action '{action}' "
                    f"has been approved by '{approved_by}'. Approval ID: {approval_id}."
                ),
                payload={
                    "approval_id": approval_id,
                    "federation_id": federation_id,
                    "action": action,
                    "approved_by": approved_by,
                },
            )
            created = await self._store.create(msg)
            messages.append(created)
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_APPROVED.value,
            )
            self._record_change_event(
                event_type="notification.enqueued",
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type="notification.enqueued",
                details={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
        return messages

    async def enqueue_for_approval_rejected(
        self,
        *,
        approval_id: str,
        federation_id: str | None = None,
        action: str,
        rejected_by: str,
        recipients: list[str] | None = None,
    ) -> list[FederationNotificationMessage]:
        """Enqueue notifications for a rejected approval request."""
        messages: list[FederationNotificationMessage] = []
        for channel in self._policy.default_channels:
            msg = self._build_message(
                approval_id=approval_id,
                federation_id=federation_id,
                event_type=FederationNotificationEventType.APPROVAL_REJECTED,
                channel=channel,
                recipients=recipients,
                subject=f"Federation Approval Rejected: {action}",
                body=(
                    f"The federation approval request for action '{action}' "
                    f"has been rejected by '{rejected_by}'. Approval ID: {approval_id}."
                ),
                payload={
                    "approval_id": approval_id,
                    "federation_id": federation_id,
                    "action": action,
                    "rejected_by": rejected_by,
                },
            )
            created = await self._store.create(msg)
            messages.append(created)
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_REJECTED.value,
            )
            self._record_change_event(
                event_type="notification.enqueued",
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type="notification.enqueued",
                details={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
        return messages

    async def enqueue_for_approval_escalated(
        self,
        *,
        approval_id: str,
        federation_id: str | None = None,
        action: str,
        escalated_by: str | None = None,
        escalation_level: int = 1,
        recipients: list[str] | None = None,
    ) -> list[FederationNotificationMessage]:
        """Enqueue notifications for an escalated approval request."""
        messages: list[FederationNotificationMessage] = []
        for channel in self._policy.default_channels:
            msg = self._build_message(
                approval_id=approval_id,
                federation_id=federation_id,
                event_type=FederationNotificationEventType.APPROVAL_ESCALATED,
                channel=channel,
                recipients=recipients,
                subject=f"Federation Approval Escalated (Level {escalation_level}): {action}",
                body=(
                    f"The federation approval request for action '{action}' "
                    f"has been escalated to level {escalation_level}"
                    f"{f' by {escalated_by}' if escalated_by else ''}. "
                    f"Approval ID: {approval_id}."
                ),
                payload={
                    "approval_id": approval_id,
                    "federation_id": federation_id,
                    "action": action,
                    "escalated_by": escalated_by,
                    "escalation_level": escalation_level,
                },
            )
            created = await self._store.create(msg)
            messages.append(created)
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_ESCALATED.value,
            )
            self._record_change_event(
                event_type="notification.enqueued",
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type="notification.enqueued",
                details={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
        return messages

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_pending(self, limit: int = 100) -> FederationNotificationDispatchResult:
        """Dispatch pending notifications — single tick, no infinite loop."""
        pending = await self._store.list_pending(limit=limit)

        total_dispatched = 0
        total_sent = 0
        total_failed = 0
        total_skipped = 0
        errors: list[str] = []

        for message in pending:
            total_dispatched += 1
            adapter = self._adapters.get(message.channel)

            if adapter is None:
                await self._store.mark_failed(
                    message.notification_id,
                    error=f"No adapter for channel: {message.channel.value}",
                )
                total_failed += 1
                errors.append(f"No adapter for channel: {message.channel.value}")
                continue

            try:
                delivery = await adapter.send(message)
            except Exception as exc:  # noqa: BLE001 — never crash on adapter failure
                delivery = FederationNotificationDelivery(
                    notification_id=message.notification_id,
                    channel=message.channel,
                    status=FederationNotificationStatus.FAILED,
                    error=str(exc),
                )

            if delivery.status == FederationNotificationStatus.SENT:
                await self._store.mark_sent(message.notification_id)
                total_sent += 1
            elif delivery.status == FederationNotificationStatus.FAILED:
                error_msg = delivery.error or "Unknown delivery failure"
                if message.attempt_count + 1 < message.max_attempts:
                    next_attempt = datetime.now(timezone.utc) + timedelta(
                        seconds=self._policy.backoff_seconds,
                    )
                    await self._store.mark_failed(
                        message.notification_id,
                        error=error_msg,
                        next_attempt_at=next_attempt,
                    )
                else:
                    await self._store.mark_failed(
                        message.notification_id,
                        error=error_msg,
                    )
                total_failed += 1
                errors.append(error_msg)
            else:
                total_skipped += 1

        return FederationNotificationDispatchResult(
            total_dispatched=total_dispatched,
            total_sent=total_sent,
            total_failed=total_failed,
            total_skipped=total_skipped,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_message(
        self,
        *,
        approval_id: str,
        federation_id: str | None,
        event_type: FederationNotificationEventType,
        channel: FederationNotificationChannel,
        recipients: list[str] | None,
        subject: str,
        body: str,
        payload: dict[str, Any],
    ) -> FederationNotificationMessage:
        """Build a FederationNotificationMessage with sensible defaults."""
        resolved_recipients = recipients or self._policy.recipients_by_channel.get(
            channel.value, [],
        )
        return FederationNotificationMessage(
            notification_id=f"fn_{uuid.uuid4().hex}",
            approval_id=approval_id,
            federation_id=federation_id,
            event_type=event_type,
            channel=channel,
            recipients=resolved_recipients,
            subject=subject,
            body=body,
            payload=payload,
            status=FederationNotificationStatus.PENDING,
            attempt_count=0,
            max_attempts=self._policy.max_attempts,
            created_at=datetime.now(timezone.utc),
        )

    def _record_audit(
        self,
        *,
        event: str,
        notification_id: str,
        approval_id: str,
        channel: str,
        event_type: str,
    ) -> None:
        """Best-effort audit logging — never break the caller on failure."""
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log(
                event=event,
                notification_id=notification_id,
                approval_id=approval_id,
                channel=channel,
                event_type=event_type,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.debug("Audit logging failed for notification %s", notification_id, exc_info=True)

    def _record_change_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Best-effort change event recording — never break the caller on failure."""
        if self._change_event_store is None:
            return
        try:
            self._change_event_store.record(
                event_type=event_type,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.debug("Change event recording failed for %s", event_type, exc_info=True)

    def _record_history(
        self,
        *,
        federation_id: str | None,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        """Best-effort federation history recording — never break the caller on failure."""
        if self._history_recorder is None or federation_id is None:
            return
        try:
            self._history_recorder.record(
                federation_id=federation_id,
                event_type=event_type,
                details=details,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.debug("History recording failed for federation %s", federation_id, exc_info=True)
