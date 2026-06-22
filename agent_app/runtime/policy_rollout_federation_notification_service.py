"""Federation notification service — enqueue and dispatch federation approval notifications.

Phase 49: Federation Notification Service.
Phase 50: DLQ Integration + Retry Policy.
Phase 51: Template rendering, preference checks, webhook signing, replay.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
)
from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDeadLetter,
    FederationNotificationDelivery,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
    FederationNotificationDispatchResult,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationPolicy,
    FederationNotificationRetryPolicy,
    FederationNotificationStatus,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
)
from agent_app.governance.policy_rollout_federation_webhook import (
    FederationWebhookReplayResult,
    FederationWebhookRequestSnapshot,
)
from agent_app.runtime.policy_rollout_federation_notification_adapters import (
    FederationNotificationAdapter,
)
from agent_app.runtime.policy_rollout_federation_notification_dlq_store import (
    FederationNotificationDLQStore,
)
from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
    NotificationObservabilityStore,
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
        dlq_store: FederationNotificationDLQStore | None = None,
        retry_policy: FederationNotificationRetryPolicy | None = None,
        by_channel_retry_policy: dict[str, FederationNotificationRetryPolicy] | None = None,
        template_service: Any | None = None,  # FederationNotificationTemplateService
        preference_service: Any | None = None,  # FederationNotificationPreferenceService
        webhook_signature_service: Any | None = None,  # FederationWebhookSignatureService
        observability_store: NotificationObservabilityStore | None = None,
    ) -> None:
        self._store = notification_store
        self._adapters = adapters
        self._policy = notification_policy
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store
        self._history_recorder = history_recorder
        self._dlq_store = dlq_store
        self._retry_policy = retry_policy
        self._by_channel_retry_policy = by_channel_retry_policy
        self._template_service = template_service
        self._preference_service = preference_service
        self._webhook_signature_service = webhook_signature_service
        self._observability_store = observability_store

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
            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.CREATED,
                notification_id=msg.notification_id,
                federation_id=federation_id,
                channel=channel.value,
            )
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_CREATED.value,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_CREATED,
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type=FederationHistoryEventType.NOTIFICATION_CREATED,
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
            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.CREATED,
                notification_id=msg.notification_id,
                federation_id=federation_id,
                channel=channel.value,
            )
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_APPROVED.value,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_CREATED,
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type=FederationHistoryEventType.NOTIFICATION_CREATED,
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
            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.CREATED,
                notification_id=msg.notification_id,
                federation_id=federation_id,
                channel=channel.value,
            )
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_REJECTED.value,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_CREATED,
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type=FederationHistoryEventType.NOTIFICATION_CREATED,
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
            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.CREATED,
                notification_id=msg.notification_id,
                federation_id=federation_id,
                channel=channel.value,
            )
            self._record_audit(
                event="notification.enqueued",
                notification_id=msg.notification_id,
                approval_id=approval_id,
                channel=channel.value,
                event_type=FederationNotificationEventType.APPROVAL_ESCALATED.value,
            )
            self._record_change_event(
                event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_CREATED,
                payload={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
            self._record_history(
                federation_id=federation_id,
                event_type=FederationHistoryEventType.NOTIFICATION_CREATED,
                details={"notification_id": msg.notification_id, "approval_id": approval_id},
            )
        return messages

    # ------------------------------------------------------------------
    # Retry policy
    # ------------------------------------------------------------------

    def get_retry_policy_for_channel(self, channel: FederationNotificationChannel) -> FederationNotificationRetryPolicy | None:
        """Return the retry policy for a specific channel, falling back to default.

        Returns None when no retry policy is configured (neither default nor
        channel-specific), so that callers can fall back to the notification
        policy's max_attempts and backoff_seconds for backward compatibility.
        """
        if self._by_channel_retry_policy:
            override = self._by_channel_retry_policy.get(channel.value)
            if override is not None:
                return override
        return self._retry_policy

    # ------------------------------------------------------------------
    # Observability event recording (Phase 52 Task 10)
    # ------------------------------------------------------------------

    def _record_observability_event(
        self,
        *,
        event_type: PolicyChangeEventType,
        history_event_type: FederationHistoryEventType,
        federation_id: str | None,
        notification_id: str | None,
        detail: str,
    ) -> None:
        """Record both a policy change event and a federation history event for
        a key observability milestone. Best-effort — never breaks the caller."""
        payload: dict[str, Any] = {
            "notification_id": notification_id,
            "detail": detail,
        }
        self._record_change_event(
            event_type=event_type,
            payload=payload,
        )
        self._record_history(
            federation_id=federation_id,
            event_type=history_event_type,
            details=payload,
        )

    def record_sla_violation(
        self,
        *,
        federation_id: str | None,
        notification_id: str | None,
        channel: str | None,
        metric: str,
        observed_value: float,
        threshold: float,
        severity: str,
    ) -> None:
        """Record an SLA violation as a change event and history event."""
        self._record_observability_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_SLA_VIOLATION_DETECTED,
            history_event_type=FederationHistoryEventType.NOTIFICATION_SLA_VIOLATION_DETECTED,
            federation_id=federation_id,
            notification_id=notification_id,
            detail=f"sla_violation:{metric}:{severity}",
        )

    def record_alert_created(
        self,
        *,
        federation_id: str | None,
        alert_id: str,
        rule_id: str,
        severity: str,
    ) -> None:
        """Record an alert creation as a change event and history event."""
        self._record_observability_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_CREATED,
            history_event_type=FederationHistoryEventType.NOTIFICATION_ALERT_CREATED,
            federation_id=federation_id,
            notification_id=None,
            detail=f"alert_created:{alert_id}:{rule_id}:{severity}",
        )

    def record_alert_acknowledged(
        self,
        *,
        federation_id: str | None,
        alert_id: str,
        acknowledged_by: str | None = None,
    ) -> None:
        """Record an alert acknowledgment as a change event and history event."""
        self._record_observability_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_ACKNOWLEDGED,
            history_event_type=FederationHistoryEventType.NOTIFICATION_ALERT_ACKNOWLEDGED,
            federation_id=federation_id,
            notification_id=None,
            detail=f"alert_acknowledged:{alert_id}",
        )

    def record_alert_resolved(
        self,
        *,
        federation_id: str | None,
        alert_id: str,
        resolved_by: str | None = None,
    ) -> None:
        """Record an alert resolution as a change event and history event."""
        self._record_observability_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_ALERT_RESOLVED,
            history_event_type=FederationHistoryEventType.NOTIFICATION_ALERT_RESOLVED,
            federation_id=federation_id,
            notification_id=None,
            detail=f"alert_resolved:{alert_id}",
        )

    def record_report_exported(
        self,
        *,
        federation_id: str | None,
        report_type: str,
        format: str,
    ) -> None:
        """Record a report export as a change event and history event."""
        self._record_observability_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_REPORT_EXPORTED,
            history_event_type=FederationHistoryEventType.NOTIFICATION_OBSERVABILITY_REPORT_EXPORTED,
            federation_id=federation_id,
            notification_id=None,
            detail=f"report_exported:{report_type}:{format}",
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_pending(self, limit: int = 100) -> FederationNotificationDispatchResult:
        """Dispatch pending notifications — single tick, no infinite loop.

        Phase 51 integration order:
        1. Preference check — SUPPRESSED if not deliverable
        2. Template rendering — TEMPLATE_FAILED if rendering fails
        3. Webhook signing — SIGNATURE_FAILED if signing fails
        4. Adapter send (existing flow)
        """
        pending = await self._store.list_pending(limit=limit)

        total_dispatched = 0
        total_sent = 0
        total_failed = 0
        total_skipped = 0
        errors: list[str] = []

        for message in pending:
            total_dispatched += 1
            adapter = self._adapters.get(message.channel)
            channel_retry_policy = self.get_retry_policy_for_channel(message.channel)

            # Determine max_attempts and backoff: use channel retry policy if
            # configured, otherwise fall back to notification policy fields on
            # the message itself for backward compatibility.
            if channel_retry_policy is not None:
                max_attempts = channel_retry_policy.max_attempts
                backoff_seconds = channel_retry_policy.backoff_seconds
                send_to_dlq = channel_retry_policy.send_to_dlq
            else:
                max_attempts = message.max_attempts
                backoff_seconds = self._policy.backoff_seconds
                send_to_dlq = False  # No DLQ when no retry policy configured

            # --- Phase 51 Step 1: Preference check ---
            if self._preference_service is not None:
                try:
                    should_deliver = await self._preference_service.should_deliver(
                        subject_type="notification",
                        subject_id=message.notification_id,
                        event_type=message.event_type.value,
                        channel=message.channel.value,
                        federation_id=message.federation_id,
                        approval_id=message.approval_id,
                    )
                except Exception:  # noqa: BLE001 — never crash on preference check
                    logger.debug(
                        "Preference check failed for notification %s",
                        message.notification_id,
                        exc_info=True,
                    )
                    should_deliver = True  # fail-open

                if not should_deliver:
                    await self._store.mark_failed(
                        message.notification_id,
                        error="Suppressed by preference (opt-out)",
                    )
                    # Overwrite status to SUPPRESSED
                    msg_in_store = await self._store.get(message.notification_id)
                    if msg_in_store is not None:
                        msg_in_store.status = FederationNotificationStatus.SUPPRESSED
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.SUPPRESSED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        preference_decision="opt_out",
                    )
                    self._record_observability_event(
                        event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_DELIVERY_EVENT_RECORDED,
                        history_event_type=FederationHistoryEventType.NOTIFICATION_DELIVERY_EVENT_RECORDED,
                        federation_id=message.federation_id,
                        notification_id=message.notification_id,
                        detail="delivery_suppressed",
                    )
                    self._record_audit(
                        event="notification.suppressed",
                        notification_id=message.notification_id,
                        approval_id=message.approval_id,
                        channel=message.channel.value,
                        event_type=message.event_type.value,
                    )
                    total_skipped += 1
                    continue

            # --- Phase 51 Step 2: Template rendering ---
            if self._template_service is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_notification_template import (
                        FederationNotificationTemplateError,
                    )

                    rendered = await self._template_service.render(
                        event_type=message.event_type.value,
                        channel=message.channel.value,
                        federation_id=message.federation_id,
                        context=message.payload,
                    )
                    if rendered.subject is not None:
                        message.subject = rendered.subject
                    message.body = rendered.body
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.RENDERED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        template_id=rendered.template_id,
                    )
                except FederationNotificationTemplateError as exc:
                    await self._store.mark_failed(
                        message.notification_id,
                        error=f"Template rendering failed: {exc}",
                    )
                    # Overwrite status to TEMPLATE_FAILED
                    msg_in_store = await self._store.get(message.notification_id)
                    if msg_in_store is not None:
                        msg_in_store.status = FederationNotificationStatus.TEMPLATE_FAILED
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.TEMPLATE_FAILED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        error_message=str(exc),
                    )
                    self._record_audit(
                        event="notification.template_failed",
                        notification_id=message.notification_id,
                        approval_id=message.approval_id,
                        channel=message.channel.value,
                        event_type=message.event_type.value,
                    )
                    total_failed += 1
                    errors.append(f"Template rendering failed: {exc}")
                    continue
                except Exception as exc:  # noqa: BLE001 — unexpected template error
                    await self._store.mark_failed(
                        message.notification_id,
                        error=f"Template rendering failed: {exc}",
                    )
                    msg_in_store = await self._store.get(message.notification_id)
                    if msg_in_store is not None:
                        msg_in_store.status = FederationNotificationStatus.TEMPLATE_FAILED
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.TEMPLATE_FAILED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        error_message=str(exc),
                    )
                    self._record_audit(
                        event="notification.template_failed",
                        notification_id=message.notification_id,
                        approval_id=message.approval_id,
                        channel=message.channel.value,
                        event_type=message.event_type.value,
                    )
                    total_failed += 1
                    errors.append(f"Template rendering failed: {exc}")
                    continue

            # --- Phase 51 Step 3: Webhook signing ---
            signature_headers: dict[str, str] | None = None
            if message.channel == FederationNotificationChannel.WEBHOOK and self._webhook_signature_service is not None:
                try:
                    body_str = json.dumps(message.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                    signature_headers = self._webhook_signature_service.sign(body_str)
                    # Save request snapshot
                    now = datetime.now(timezone.utc)
                    snapshot = FederationWebhookRequestSnapshot(
                        request_id=f"fwr_{uuid.uuid4().hex}",
                        notification_id=message.notification_id,
                        url=self._policy.webhook_url or "",
                        headers=signature_headers,
                        body=body_str,
                        nonce=signature_headers.get("X-AgentApp-Signature-Nonce", ""),
                        timestamp=now,
                        payload_digest=self._webhook_signature_service.compute_digest(body_str),
                        created_at=now,
                    )
                    # Store snapshot via change event (best-effort)
                    self._record_change_event(
                        event_type="federation.notification.webhook_snapshot",
                        payload={
                            "request_id": snapshot.request_id,
                            "notification_id": message.notification_id,
                        },
                    )
                except Exception as exc:  # noqa: BLE001 — never crash on signing
                    await self._store.mark_failed(
                        message.notification_id,
                        error=f"Webhook signing failed: {exc}",
                    )
                    msg_in_store = await self._store.get(message.notification_id)
                    if msg_in_store is not None:
                        msg_in_store.status = FederationNotificationStatus.SIGNATURE_FAILED
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.WEBHOOK_SIGNATURE_FAILED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        error_message=str(exc),
                    )
                    self._record_audit(
                        event="notification.signature_failed",
                        notification_id=message.notification_id,
                        approval_id=message.approval_id,
                        channel=message.channel.value,
                        event_type=message.event_type.value,
                    )
                    total_failed += 1
                    errors.append(f"Webhook signing failed: {exc}")
                    continue

            # --- Existing adapter dispatch ---
            if adapter is None:
                no_adapter_error = f"No adapter for channel: {message.channel.value}"
                # Check if we should send to DLQ
                if (
                    message.attempt_count + 1 >= max_attempts
                    and send_to_dlq
                    and self._dlq_store is not None
                ):
                    await self._create_dlq_entry(
                        message=message,
                        reason=FederationNotificationDLQReason.ADAPTER_ERROR,
                        error=no_adapter_error,
                    )
                await self._store.mark_failed(
                    message.notification_id,
                    error=no_adapter_error,
                )
                await self._record_delivery_event(
                    event_type=NotificationDeliveryEventType.FAILED,
                    notification_id=message.notification_id,
                    federation_id=message.federation_id,
                    channel=message.channel.value,
                    adapter_name=None,
                    attempt=message.attempt_count + 1,
                    error_message=no_adapter_error,
                    error_code="no_adapter",
                )
                total_failed += 1
                errors.append(no_adapter_error)
                continue

            # Inject signature headers into message payload for webhook adapter
            if signature_headers is not None:
                message.payload["_signature_headers"] = signature_headers

            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.SEND_ATTEMPTED,
                notification_id=message.notification_id,
                federation_id=message.federation_id,
                channel=message.channel.value,
                adapter_name=adapter_name if isinstance(adapter_name := getattr(adapter, "name", None), str) else None,
                attempt=message.attempt_count + 1,
            )
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
                await self._record_delivery_event(
                    event_type=NotificationDeliveryEventType.SENT,
                    notification_id=message.notification_id,
                    federation_id=message.federation_id,
                    channel=message.channel.value,
                    adapter_name=adapter_name if isinstance(adapter_name := getattr(adapter, "name", None), str) else None,
                )
                self._record_observability_event(
                    event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_DELIVERY_EVENT_RECORDED,
                    history_event_type=FederationHistoryEventType.NOTIFICATION_DELIVERY_EVENT_RECORDED,
                    federation_id=message.federation_id,
                    notification_id=message.notification_id,
                    detail="delivery_succeeded",
                )
                total_sent += 1
            elif delivery.status == FederationNotificationStatus.FAILED:
                error_msg = delivery.error or "Unknown delivery failure"
                attempt_num = message.attempt_count + 1
                await self._record_delivery_event(
                    event_type=NotificationDeliveryEventType.FAILED,
                    notification_id=message.notification_id,
                    federation_id=message.federation_id,
                    channel=message.channel.value,
                    adapter_name=adapter_name if isinstance(adapter_name := getattr(adapter, "name", None), str) else None,
                    attempt=attempt_num,
                    error_message=error_msg,
                    error_code="delivery_failure",
                )
                if attempt_num < max_attempts:
                    next_attempt = datetime.now(timezone.utc) + timedelta(
                        seconds=backoff_seconds,
                    )
                    await self._store.mark_failed(
                        message.notification_id,
                        error=error_msg,
                        next_attempt_at=next_attempt,
                    )
                    await self._record_delivery_event(
                        event_type=NotificationDeliveryEventType.RETRY_SCHEDULED,
                        notification_id=message.notification_id,
                        federation_id=message.federation_id,
                        channel=message.channel.value,
                        attempt=attempt_num,
                    )
                else:
                    # Max retries exceeded — check DLQ eligibility
                    if send_to_dlq and self._dlq_store is not None:
                        await self._create_dlq_entry(
                            message=message,
                            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
                            error=error_msg,
                        )
                    await self._store.mark_failed(
                        message.notification_id,
                        error=error_msg,
                    )
                    self._record_observability_event(
                        event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_DELIVERY_EVENT_RECORDED,
                        history_event_type=FederationHistoryEventType.NOTIFICATION_DELIVERY_EVENT_RECORDED,
                        federation_id=message.federation_id,
                        notification_id=message.notification_id,
                        detail="delivery_failed_max_retries",
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
    # Replay (Phase 51)
    # ------------------------------------------------------------------

    async def replay_original(
        self,
        dlq_id: str,
        dlq_store: Any,  # FederationNotificationDLQStore
        *,
        dry_run: bool = False,
        target_url: str | None = None,
        max_replays: int = 3,
    ) -> FederationWebhookReplayResult:
        """Replay a webhook notification from DLQ using original payload.

        Uses the original body (not re-rendered), generates new
        signature/timestamp/nonce.
        """
        # 1. Get DLQ entry
        entry = await dlq_store.get(dlq_id)
        if entry is None:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id="",
                success=False,
                error=f"DLQ entry '{dlq_id}' not found",
            )

        # 2. Verify it's a webhook channel entry
        if entry.channel != FederationNotificationChannel.WEBHOOK.value:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id=entry.notification_id,
                success=False,
                error=f"Cannot replay non-webhook channel: {entry.channel}",
            )

        # 3. Check replay count
        replay_count = entry.metadata.get("replay_count", 0)
        if replay_count >= max_replays:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id=entry.notification_id,
                success=False,
                replay_count=replay_count,
                error=f"Max replays exceeded ({replay_count}/{max_replays})",
            )

        # 4. Dry run — return result without sending
        if dry_run:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id=entry.notification_id,
                success=True,
                replay_count=replay_count,
            )

        # 5. Build original body and generate new signature
        try:
            original_body = json.dumps(entry.payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id=entry.notification_id,
                success=False,
                replay_count=replay_count,
                error=f"Failed to serialize original payload: {exc}",
            )

        signature_headers: dict[str, str] | None = None
        if self._webhook_signature_service is not None:
            try:
                signature_headers = self._webhook_signature_service.sign(original_body)
            except Exception as exc:  # noqa: BLE001
                return FederationWebhookReplayResult(
                    replay_id=f"fwrp_{uuid.uuid4().hex}",
                    dlq_id=dlq_id,
                    notification_id=entry.notification_id,
                    success=False,
                    replay_count=replay_count,
                    error=f"Webhook signing failed during replay: {exc}",
                )

        # 6. Send via webhook adapter
        url = target_url or self._policy.webhook_url or ""
        adapter = self._adapters.get(FederationNotificationChannel.WEBHOOK)

        if adapter is None:
            return FederationWebhookReplayResult(
                replay_id=f"fwrp_{uuid.uuid4().hex}",
                dlq_id=dlq_id,
                notification_id=entry.notification_id,
                success=False,
                replay_count=replay_count,
                error="No webhook adapter configured",
            )

        # Build a message from the DLQ entry for the adapter
        replay_message = FederationNotificationMessage(
            notification_id=entry.notification_id,
            approval_id=entry.approval_id or "",
            federation_id=entry.federation_id,
            event_type=FederationNotificationEventType(
                entry.metadata.get("event_type", "approval.created"),
            ),
            channel=FederationNotificationChannel.WEBHOOK,
            recipients=[entry.recipient] if entry.recipient else [],
            subject=entry.metadata.get("subject"),
            body=original_body,
            payload={**entry.payload, "_signature_headers": signature_headers} if signature_headers else entry.payload,
            status=FederationNotificationStatus.PENDING,
            attempt_count=0,
            max_attempts=1,
            created_at=datetime.now(timezone.utc),
        )

        try:
            delivery = await adapter.send(replay_message)
            success = delivery.status == FederationNotificationStatus.SENT
        except Exception as exc:  # noqa: BLE001
            success = False
            delivery_error = str(exc)

        # 7. Record replay audit event
        now = datetime.now(timezone.utc)
        new_replay_count = replay_count + 1
        if success:
            await self._record_delivery_event(
                event_type=NotificationDeliveryEventType.DLQ_REPLAYED,
                notification_id=entry.notification_id,
                federation_id=entry.federation_id,
                channel=FederationNotificationChannel.WEBHOOK.value,
            )
        self._record_audit(
            event="notification.replay_original",
            notification_id=entry.notification_id,
            approval_id=entry.approval_id or "",
            channel=FederationNotificationChannel.WEBHOOK.value,
            event_type=entry.metadata.get("event_type", ""),
        )
        self._record_change_event(
            event_type=PolicyChangeEventType.FEDERATION_WEBHOOK_REPLAY_REQUESTED,
            payload={
                "dlq_id": dlq_id,
                "notification_id": entry.notification_id,
                "replay_count": new_replay_count,
                "success": success,
            },
        )

        # 8. Update DLQ entry replay metadata
        entry.metadata["replay_count"] = new_replay_count
        entry.metadata["last_replay_at"] = now.isoformat()
        entry.updated_at = now
        if success:
            try:
                await dlq_store.mark_retried(dlq_id)
            except Exception:  # noqa: BLE001 — best effort
                logger.debug("Failed to mark DLQ entry %s as retried", dlq_id, exc_info=True)

        # 9. Return result
        return FederationWebhookReplayResult(
            replay_id=f"fwrp_{uuid.uuid4().hex}",
            dlq_id=dlq_id,
            notification_id=entry.notification_id,
            success=success,
            replay_count=new_replay_count,
            last_replay_at=now,
            error=None if success else (delivery.error if hasattr(delivery, "error") and delivery.error else delivery_error),
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
        event_type: PolicyChangeEventType,
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
        event_type: FederationHistoryEventType,
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

    async def _record_delivery_event(
        self,
        *,
        event_type: NotificationDeliveryEventType,
        notification_id: str,
        federation_id: str | None = None,
        channel: str | None = None,
        approval_id: str | None = None,
        status: str | None = None,
        attempt: int | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        adapter_name: str | None = None,
        template_id: str | None = None,
        preference_decision: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort observability event recording — never break the caller on failure."""
        if self._observability_store is None:
            return
        try:
            event = NotificationDeliveryEvent(
                event_id=f"nde_{uuid.uuid4().hex}",
                notification_id=notification_id,
                approval_id=approval_id,
                federation_id=federation_id,
                channel=channel,
                event_type=event_type,
                status=status,
                attempt=attempt,
                latency_ms=latency_ms,
                error_code=error_code,
                error_message=error_message,
                adapter_name=adapter_name,
                template_id=template_id,
                preference_decision=preference_decision,
                metadata=metadata or {},
                created_at=datetime.now(timezone.utc),
            )
            await self._observability_store.record_event(event)
        except Exception:  # noqa: BLE001 — best-effort, never break notification flow
            logger.debug("Failed to record delivery event: %s", exc_info=True)

    async def _create_dlq_entry(
        self,
        *,
        message: FederationNotificationMessage,
        reason: FederationNotificationDLQReason,
        error: str,
    ) -> None:
        """Create a dead-letter queue entry for a notification that exceeded retries."""
        now = datetime.now(timezone.utc)
        dlq_item = FederationNotificationDeadLetter(
            dlq_id=f"fdlq_{uuid.uuid4().hex}",
            notification_id=message.notification_id,
            approval_id=message.approval_id,
            federation_id=message.federation_id,
            channel=message.channel.value,
            adapter=None,
            recipient=message.recipients[0] if message.recipients else None,
            reason=reason,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=message.attempt_count + 1,
            last_error=error,
            payload=message.payload,
            metadata={
                "event_type": message.event_type.value,
                "subject": message.subject,
            },
            created_at=now,
            updated_at=now,
        )
        try:
            await self._dlq_store.create(dlq_item)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — best-effort, never crash dispatch
            logger.debug("DLQ creation failed for notification %s", message.notification_id, exc_info=True)
            return

        await self._record_delivery_event(
            event_type=NotificationDeliveryEventType.DLQ_CREATED,
            notification_id=message.notification_id,
            federation_id=message.federation_id,
            channel=message.channel.value,
        )
        self._record_change_event(
            event_type=PolicyChangeEventType.FEDERATION_NOTIFICATION_DLQ_CREATED,
            payload={
                "dlq_id": dlq_item.dlq_id,
                "notification_id": message.notification_id,
                "channel": message.channel.value,
                "reason": reason.value,
            },
        )
        self._record_history(
            federation_id=message.federation_id,
            event_type=FederationHistoryEventType.NOTIFICATION_DLQ_CREATED,
            details={
                "dlq_id": dlq_item.dlq_id,
                "notification_id": message.notification_id,
                "reason": reason.value,
            },
        )
