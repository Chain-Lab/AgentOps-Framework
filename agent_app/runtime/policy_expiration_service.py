"""Policy expiration service — sweeps expired rollout approvals and gate requirements.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_expiration import (
    PolicyExpirationAction,
    PolicyExpirationResult,
    PolicyExpirationSweepReport,
    PolicyExpirationTargetType,
)
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

logger = logging.getLogger(__name__)


class PolicyExpirationService:
    """Service that sweeps and expires stale rollout approvals and gate requirements."""

    def __init__(
        self,
        rollout_approval_store: Any | None = None,
        release_gate_requirement_store: Any | None = None,
        notification_service: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._approval_store = rollout_approval_store
        self._gate_store = release_gate_requirement_store
        self._notification_service = notification_service
        self._audit_logger = audit_logger
        self._event_store = event_store

    async def sweep(self, now: datetime | None = None) -> PolicyExpirationSweepReport:
        """Run a full expiration sweep across both stores.

        Returns a sweep report with all results. Errors are captured as ERROR
        results rather than crashing.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        report = PolicyExpirationSweepReport(
            sweep_id=f"pes_{uuid.uuid4().hex[:12]}",
            started_at=now,
        )

        # Expire rollout approvals
        try:
            approval_results = await self.expire_rollout_approvals(now)
            report.results.extend(approval_results)
        except Exception as exc:
            report.results.append(
                PolicyExpirationResult(
                    result_id=f"per_{uuid.uuid4().hex[:12]}",
                    target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                    target_id="sweep_error",
                    action=PolicyExpirationAction.ERROR,
                    reason="Error expiring rollout approvals",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    created_at=now,
                )
            )

        # Expire gate requirements
        try:
            gate_results = await self.expire_gate_requirements(now)
            report.results.extend(gate_results)
        except Exception as exc:
            report.results.append(
                PolicyExpirationResult(
                    result_id=f"per_{uuid.uuid4().hex[:12]}",
                    target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
                    target_id="sweep_error",
                    action=PolicyExpirationAction.ERROR,
                    reason="Error expiring gate requirements",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    created_at=now,
                )
            )

        report.completed_at = datetime.now(timezone.utc)

        # Emit audit events (best-effort)
        await self._emit_audit_events(report)

        # Emit change events (best-effort)
        await self._emit_change_events(report)

        return report

    async def expire_rollout_approvals(
        self, now: datetime | None = None
    ) -> list[PolicyExpirationResult]:
        """Expire pending rollout approvals past their expires_at timestamp.

        Missing approval store is skipped (returns empty list).
        """
        if self._approval_store is None:
            return []

        if now is None:
            now = datetime.now(timezone.utc)

        expired_approvals = await self._approval_store.expire_pending(now)

        results: list[PolicyExpirationResult] = []
        for approval in expired_approvals:
            result = PolicyExpirationResult(
                result_id=f"per_{uuid.uuid4().hex[:12]}",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id=approval.approval_id,
                action=PolicyExpirationAction.EXPIRED,
                reason="Approval expired past expires_at",
                created_at=now,
            )
            results.append(result)

            # Notify via notification service if available
            if self._notification_service is not None:
                try:
                    await self._notification_service.notify_event(
                        event_type="policy.rollout.approval.expired",
                        data={
                            "approval_id": approval.approval_id,
                            "rollout_id": approval.rollout_id,
                            "step_id": approval.step_id,
                        },
                        source_type="rollout_approval",
                        source_id=approval.approval_id,
                    )
                except Exception:
                    pass  # Best-effort notification

        return results

    async def expire_gate_requirements(
        self, now: datetime | None = None
    ) -> list[PolicyExpirationResult]:
        """Expire gate requirements that have exceeded their max_age_seconds.

        A gate requirement is expired when:
        1. Its status is REQUIRED
        2. It has max_age_seconds set
        3. The age (based on satisfied_at or created_at) exceeds max_age_seconds

        Missing gate store is skipped (returns empty list).
        """
        if self._gate_store is None:
            return []

        if now is None:
            now = datetime.now(timezone.utc)

        required = await self._gate_store.list(status=ReleaseGateRequirementStatus.REQUIRED)

        results: list[PolicyExpirationResult] = []
        for req in required:
            if req.max_age_seconds is None:
                continue

            # Compute age from satisfied_at or created_at
            reference_time = req.satisfied_at or req.created_at
            age_seconds = (now - reference_time).total_seconds()

            if age_seconds > req.max_age_seconds:
                # Update status to EXPIRED
                req.status = ReleaseGateRequirementStatus.EXPIRED
                await self._gate_store.update(req)

                target_type = (
                    PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT
                    if req.source_type == "promotion"
                    else PolicyExpirationTargetType.ROLLOUT_GATE_REQUIREMENT
                )

                result = PolicyExpirationResult(
                    result_id=f"per_{uuid.uuid4().hex[:12]}",
                    target_type=target_type,
                    target_id=req.requirement_id,
                    action=PolicyExpirationAction.EXPIRED,
                    reason=f"Gate requirement exceeded max_age_seconds={req.max_age_seconds}",
                    created_at=now,
                )
                results.append(result)

                # Notify via notification service if available
                if self._notification_service is not None:
                    try:
                        await self._notification_service.notify_event(
                            event_type="policy.promotion.gate.expired",
                            data={
                                "requirement_id": req.requirement_id,
                                "source_type": req.source_type,
                                "source_id": req.source_id,
                                "max_age_seconds": req.max_age_seconds,
                                "age_seconds": age_seconds,
                            },
                            source_type="gate_requirement",
                            source_id=req.requirement_id,
                        )
                    except Exception:
                        pass  # Best-effort notification

        return results

    async def _emit_audit_events(self, report: PolicyExpirationSweepReport) -> None:
        """Emit audit events for sweep results (best-effort)."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent

            for result in report.results:
                if result.action == PolicyExpirationAction.EXPIRED:
                    event = AuditEvent(
                        event_id=f"ae_{uuid.uuid4().hex[:12]}",
                        event_type=f"policy.expiration.{result.target_type.value}",
                        data={
                            "sweep_id": report.sweep_id,
                            "target_id": result.target_id,
                            "reason": result.reason,
                        },
                    )
                    await self._audit_logger.log(event)
        except Exception:
            pass  # Best-effort

    async def _emit_change_events(self, report: PolicyExpirationSweepReport) -> None:
        """Emit change events for sweep results (best-effort)."""
        if self._event_store is None:
            return
        try:
            from agent_app.governance.policy_change_event import (
                PolicyChangeEvent,
                PolicyChangeEventType,
            )

            for result in report.results:
                if result.action == PolicyExpirationAction.EXPIRED:
                    # Map target type to change event type
                    if result.target_type == PolicyExpirationTargetType.ROLLOUT_APPROVAL:
                        event_type = PolicyChangeEventType.ROLLOUT_APPROVAL_EXPIRED
                    elif result.target_type == PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT:
                        event_type = PolicyChangeEventType.PROMOTION_GATE_EXPIRED
                    else:
                        # Use rollout approval expired as fallback for rollout gate
                        event_type = PolicyChangeEventType.ROLLOUT_APPROVAL_EXPIRED

                    event = PolicyChangeEvent(
                        event_id=f"pce_{uuid.uuid4().hex[:12]}",
                        event_type=event_type,
                        reason=result.reason,
                        data={
                            "sweep_id": report.sweep_id,
                            "target_id": result.target_id,
                        },
                        created_at=result.created_at,
                    )
                    await self._event_store.append(event)
        except Exception:
            pass  # Best-effort
