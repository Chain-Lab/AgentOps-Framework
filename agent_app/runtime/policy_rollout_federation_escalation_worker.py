"""Federation approval escalation worker — auto-escalates timed-out pending approvals.

Phase 49: Single-tick escalation worker. The caller is responsible for
scheduling repeated calls (e.g. via a cron-like scheduler).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalStatus,
)
from agent_app.runtime.policy_rollout_federation_approval_service import (
    FederationApprovalService,
)
from agent_app.runtime.policy_rollout_federation_approval_store import (
    FederationApprovalStore,
)
from agent_app.runtime.policy_rollout_federation_notification_service import (
    FederationNotificationService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class FederationApprovalEscalationWorkerResult(BaseModel):
    """Result of a single escalation worker tick."""

    scanned_count: int = 0
    escalated_count: int = 0
    skipped_count: int = 0
    errors: list[str] = []


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class FederationApprovalEscalationWorker:
    """Auto-escalates federation approval requests that have been pending
    beyond the configured timeout.
    """

    def __init__(
        self,
        approval_store: FederationApprovalStore,
        approval_service: FederationApprovalService,
        notification_service: FederationNotificationService | None = None,
        distributed_lock: Any | None = None,
        escalation_after_minutes: int = 60,
        dry_run: bool = False,
    ) -> None:
        self._store = approval_store
        self._service = approval_service
        self._notification_service = notification_service
        self._lock = distributed_lock
        self._escalation_after_minutes = escalation_after_minutes
        self._dry_run = dry_run

    async def tick(
        self,
        now: datetime | None = None,
    ) -> FederationApprovalEscalationWorkerResult:
        """Perform a single escalation tick.

        Scans all PENDING approvals and escalates those that have exceeded
        the configured ``escalation_after_minutes`` timeout.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        result = FederationApprovalEscalationWorkerResult()

        # Acquire distributed lock if provided
        lock_owner: str | None = None
        lock_acquired = False
        if self._lock is not None:
            lock_owner = f"esc-worker-{uuid.uuid4().hex[:8]}"
            try:
                lock_acquired = await self._lock.acquire(
                    lock_name="federation:escalation:worker",
                    owner_id=lock_owner,
                    ttl_seconds=300,
                )
            except Exception as exc:  # noqa: BLE001 — never crash on lock failure
                result.skipped_count = 1
                result.errors.append(f"Lock acquisition error: {exc}")
                return result

            if not lock_acquired:
                result.scanned_count = 0
                result.skipped_count = 1
                result.errors.append("Lock unavailable")
                return result

        try:
            pending = await self._store.list(status=FederationApprovalStatus.PENDING)

            for approval in pending:
                result.scanned_count += 1

                timeout = approval.created_at + timedelta(
                    minutes=self._escalation_after_minutes,
                )

                if timeout <= now:
                    # Approval has timed out — escalate
                    if not self._dry_run:
                        try:
                            escalated = await self._service.escalate(
                                approval.approval_id,
                                escalated_by="escalation_worker",
                                reason="Auto-escalated: approval timeout",
                            )
                        except Exception as exc:  # noqa: BLE001 — catch per-item errors
                            result.errors.append(
                                f"Escalation failed for {approval.approval_id}: {exc}"
                            )
                            result.escalated_count += 1
                            continue
                    else:
                        escalated = None

                    # Best-effort notification
                    if self._notification_service is not None:
                        try:
                            await self._notification_service.enqueue_for_approval_escalated(
                                approval_id=approval.approval_id,
                                federation_id=approval.federation_id,
                                action=approval.action,
                                escalated_by="escalation_worker",
                                escalation_level=(
                                    escalated.escalation_level
                                    if escalated is not None
                                    else approval.escalation_level + 1
                                ),
                            )
                        except Exception:  # noqa: BLE001 — best-effort
                            logger.debug(
                                "Notification failed for escalation of %s",
                                approval.approval_id,
                                exc_info=True,
                            )

                    result.escalated_count += 1
                else:
                    # Not yet due
                    result.skipped_count += 1
        finally:
            # Release lock if we acquired it
            if lock_acquired and self._lock is not None:
                try:
                    await self._lock.release(
                        lock_name="federation:escalation:worker",
                        owner_id=lock_owner,
                    )
                except Exception:  # noqa: BLE001 — best-effort
                    logger.debug("Lock release failed", exc_info=True)

        return result
