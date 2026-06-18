"""Federation service — orchestrates federated rollout targets, plans, and conflict detection.

Phase 46 Task 4: Create target, create plan, start plan, detect conflicts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.audit import AuditEvent
from agent_app.governance.policy_change_event import (
    PolicyChangeEvent,
    PolicyChangeEventType,
)
from agent_app.governance.policy_rbac import (
    _DEFAULT_ALLOWED,
    PolicyReleasePermission,
)
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_store import (
    FederatedRolloutPlanStore,
    FederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import RolloutPlanStore


class RolloutFederationService:
    """Orchestrates federated rollout targets, plans, and conflict detection.

    Creates targets, creates federated plans with per-target executions,
    detects conflicts before activation, and manages plan lifecycle.
    """

    def __init__(
        self,
        target_store: FederatedRolloutTargetStore,
        federation_store: FederatedRolloutPlanStore,
        rollout_store: RolloutPlanStore,
        rollout_service: Any = None,
        conflict_detector: RolloutConflictDetector | None = None,
        history_recorder: Any | None = None,
        notification_service: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        fail_on_error_conflicts: bool = True,
        warn_on_bundle_conflict: bool = True,
    ) -> None:
        self._target_store = target_store
        self._federation_store = federation_store
        self._rollout_store = rollout_store
        self._rollout_service = rollout_service
        self._conflict_detector = conflict_detector
        self._history_recorder = history_recorder
        self._notification_service = notification_service
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._fail_on_error_conflicts = fail_on_error_conflicts
        self._warn_on_bundle_conflict = warn_on_bundle_conflict

    # ------------------------------------------------------------------
    # Permission helpers
    # ------------------------------------------------------------------

    async def _check_permission(
        self,
        permission: PolicyReleasePermission,
        context: RunContext,
    ) -> None:
        """Check whether the context grants the required permission.

        Default-allowed permissions are always granted.  Otherwise the
        permission's value must appear in ``context.permissions``.
        """
        if permission in _DEFAULT_ALLOWED:
            return
        if permission.value in context.permissions:
            return
        raise PermissionError(
            f"Permission denied: {permission.value} required"
        )

    # ------------------------------------------------------------------
    # Audit / event helpers
    # ------------------------------------------------------------------

    async def _write_audit(
        self,
        event_type: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Best-effort audit logging (never raises)."""
        if self._audit_logger is None:
            return
        try:
            event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                user_id=user_id,
                tenant_id=tenant_id,
                data=data or {},
                created_at=datetime.now(timezone.utc),
            )
            await self._audit_logger.log(event)
        except Exception:
            pass

    async def _emit_change_event(
        self,
        event_type: PolicyChangeEventType,
        actor_id: str | None = None,
        bundle_id: str | None = None,
        reason: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Best-effort change event emission (never raises)."""
        if self._event_store is None:
            return
        try:
            event = PolicyChangeEvent(
                event_id=f"pce_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                actor_id=actor_id,
                bundle_id=bundle_id,
                reason=reason,
                data=data or {},
                created_at=datetime.now(timezone.utc),
            )
            await self._event_store.append(event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Conflict helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_error_conflicts(conflicts: list[RolloutConflict]) -> bool:
        """Return True if any conflict has ERROR severity."""
        return any(c.severity == RolloutConflictSeverity.ERROR for c in conflicts)

    @staticmethod
    def _conflict_summary(conflicts: list[RolloutConflict]) -> str:
        """Return a human-readable summary of conflicts."""
        return "; ".join(c.message for c in conflicts)

    @staticmethod
    def _effective_target_ids(
        target_ids: list[str],
        waves: list[FederatedRolloutWave] | None,
    ) -> list[str]:
        """Return target_ids if non-empty, else union of wave target_ids."""
        if target_ids:
            return list(target_ids)
        if waves:
            ids: list[str] = []
            for wave in waves:
                ids.extend(wave.target_ids)
            return ids
        return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_target(
        self,
        name: str,
        environment: str,
        tenant_id: str | None = None,
        ring_name: str | None = None,
        region: str | None = None,
        labels: dict[str, str] | None = None,
        actor_id: str | None = None,
        context: RunContext | None = None,
    ) -> FederatedRolloutTarget:
        """Create a new federated rollout target.

        Requires ``FEDERATION_TARGET_CREATE`` permission.
        """
        if context is not None:
            await self._check_permission(
                PolicyReleasePermission.FEDERATION_TARGET_CREATE, context,
            )

        now = datetime.now(timezone.utc)
        target = FederatedRolloutTarget(
            target_id=f"frt_{uuid.uuid4().hex[:12]}",
            name=name,
            tenant_id=tenant_id,
            environment=environment,
            ring_name=ring_name,
            region=region,
            labels=labels or {},
            status=FederatedTargetStatus.ENABLED,
            created_at=now,
        )

        target = await self._target_store.create(target)

        await self._write_audit(
            "policy.federation.target.created",
            user_id=actor_id,
            tenant_id=tenant_id or (context.tenant_id if context else None),
            data={"target_id": target.target_id, "name": name, "environment": environment},
        )
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_TARGET_CREATED,
            actor_id=actor_id,
            data={"target_id": target.target_id, "name": name, "environment": environment},
        )

        return target

    async def create_federated_plan(
        self,
        name: str,
        bundle_id: str,
        target_ids: list[str],
        rollout_template_steps: list[Any],
        created_by: str,
        context: RunContext,
        strategy: FederationExecutionStrategy = FederationExecutionStrategy.SEQUENTIAL,
        waves: list[FederatedRolloutWave] | None = None,
        reason: str | None = None,
    ) -> FederatedRolloutPlan:
        """Create a new federated rollout plan in DRAFT status.

        Requires ``FEDERATION_PLAN_CREATE`` permission.  Runs conflict
        detection and fails on ERROR severity conflicts unless the context
        metadata contains ``allow_federation_conflict_override=True``.
        """
        await self._check_permission(
            PolicyReleasePermission.FEDERATION_PLAN_CREATE, context,
        )

        effective_ids = self._effective_target_ids(target_ids, waves)

        now = datetime.now(timezone.utc)
        executions: list[FederatedRolloutTargetExecution] = []
        for tid in effective_ids:
            executions.append(
                FederatedRolloutTargetExecution(
                    execution_id=f"fre_{uuid.uuid4().hex[:12]}",
                    target_id=tid,
                    status=FederatedRolloutTargetExecutionStatus.PENDING,
                )
            )

        plan = FederatedRolloutPlan(
            federation_id=f"frp_{uuid.uuid4().hex[:12]}",
            name=name,
            bundle_id=bundle_id,
            strategy=strategy,
            status=FederatedRolloutPlanStatus.DRAFT,
            target_ids=target_ids,
            waves=waves or [],
            executions=executions,
            rollout_template_steps=rollout_template_steps,
            created_by=created_by,
            reason=reason,
            created_at=now,
            updated_at=now,
        )

        # Conflict detection
        if self._conflict_detector is not None:
            conflicts = await self._conflict_detector.detect_conflicts(plan)
            if conflicts and self._has_error_conflicts(conflicts):
                allow_override = False
                if context.metadata:
                    allow_override = bool(
                        context.metadata.get("allow_federation_conflict_override", False)
                    )
                if not allow_override:
                    raise ValueError(
                        f"Federated rollout conflicts detected: "
                        f"{self._conflict_summary(conflicts)}"
                    )

        plan = await self._federation_store.create(plan)

        await self._write_audit(
            "policy.federation.plan.created",
            user_id=created_by,
            tenant_id=context.tenant_id,
            data={
                "federation_id": plan.federation_id,
                "name": name,
                "bundle_id": bundle_id,
                "target_count": len(effective_ids),
            },
        )
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_CREATED,
            actor_id=created_by,
            bundle_id=bundle_id,
            reason=reason,
            data={
                "federation_id": plan.federation_id,
                "name": name,
                "target_count": len(effective_ids),
            },
        )

        return plan

    async def start_federated_plan(
        self,
        federation_id: str,
        actor_id: str,
        context: RunContext,
    ) -> FederatedRolloutPlan:
        """Start a federated rollout plan (transition from DRAFT to ACTIVE).

        Requires ``FEDERATION_PLAN_START`` permission.  Rechecks conflicts
        and fails on ERROR severity.
        """
        await self._check_permission(
            PolicyReleasePermission.FEDERATION_PLAN_START, context,
        )

        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated plan '{federation_id}' not found")
        if plan.status != FederatedRolloutPlanStatus.DRAFT:
            raise ValueError(
                f"Cannot start plan with status '{plan.status}'. Must be DRAFT."
            )

        # Recheck conflicts
        if self._conflict_detector is not None:
            conflicts = await self._conflict_detector.detect_conflicts(plan)
            if conflicts and self._has_error_conflicts(conflicts):
                raise ValueError(
                    f"Federated rollout conflicts detected: "
                    f"{self._conflict_summary(conflicts)}"
                )

        now = datetime.now(timezone.utc)
        plan = plan.model_copy(update={
            "status": FederatedRolloutPlanStatus.ACTIVE,
            "updated_at": now,
        })
        plan = await self._federation_store.update(plan)

        await self._write_audit(
            "policy.federation.plan.started",
            user_id=actor_id,
            tenant_id=context.tenant_id,
            data={"federation_id": federation_id, "bundle_id": plan.bundle_id},
        )
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_STARTED,
            actor_id=actor_id,
            bundle_id=plan.bundle_id,
            data={"federation_id": federation_id},
        )

        return plan

    async def detect_conflicts(
        self,
        federation_id: str,
    ) -> list[RolloutConflict]:
        """Detect conflicts for a federated plan by ID.

        Delegates to the configured conflict detector.
        """
        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated plan '{federation_id}' not found")

        if self._conflict_detector is None:
            return []

        return await self._conflict_detector.detect_conflicts(plan)
