"""Federation service — orchestrates federated rollout targets, plans, and conflict detection.

Phase 46 Task 4: Create target, create plan, start plan, detect conflicts.
Phase 46 Task 5: Execution, waves, cancellation, notifications.
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
from agent_app.governance.policy_rollout import (
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
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
from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalRequest,
    FederationApprovalStatus,
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
        federation_recorder: Any | None = None,
        notification_service: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        fail_on_error_conflicts: bool = True,
        warn_on_bundle_conflict: bool = True,
        approval_service: Any | None = None,
    ) -> None:
        self._target_store = target_store
        self._federation_store = federation_store
        self._rollout_store = rollout_store
        self._rollout_service = rollout_service
        self._conflict_detector = conflict_detector
        self._history_recorder = history_recorder
        self._federation_recorder = federation_recorder
        self._notification_service = notification_service
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._fail_on_error_conflicts = fail_on_error_conflicts
        self._warn_on_bundle_conflict = warn_on_bundle_conflict
        self._approval_service = approval_service

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
    # Approval helpers
    # ------------------------------------------------------------------

    async def _check_approval(
        self,
        federation_id: str,
        action: str,
    ) -> bool:
        """Check whether a federation action is approved.

        Returns True if:
        - No approval_service is configured
        - Approval is not required for the action
        - An approval request exists and is APPROVED

        Returns False if:
        - Approval is required and no request exists (creates one)
        - An approval request exists and is PENDING or ESCALATED
        - An approval request exists and is REJECTED
        """
        if self._approval_service is None:
            return True

        # Check if approval is required for this action
        if not await self._approval_service.requires_approval(action):
            return True

        # Check for existing approval request
        latest = await self._approval_service.check_approval_status(federation_id, action)
        if latest is None:
            # No request exists — create one
            await self._approval_service.create_approval_request(
                federation_id=federation_id,
                action=action,
                requested_by="system",
            )
            return False

        if latest.status == FederationApprovalStatus.APPROVED:
            return True

        # PENDING, ESCALATED, REJECTED, EXPIRED, CANCELLED → not approved
        return False

    def _create_approval_result(
        self,
        approval_request: FederationApprovalRequest,
    ) -> dict[str, Any]:
        """Create a result dict for an approval-required response."""
        return {
            "status": "approval_required",
            "approval_id": approval_request.approval_id,
            "action": approval_request.action,
            "required_approvers": approval_request.required_approvers,
            "message": f"Approval required for {approval_request.action}",
        }

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

        # Best-effort federation recorder
        if self._federation_recorder is not None:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                await self._federation_recorder.record(
                    event_type=FederationHistoryEventType.TARGET_CREATED,
                    target_id=target.target_id,
                    tenant_id=tenant_id,
                    environment=environment,
                    ring_name=ring_name,
                    region=region,
                    actor_id=actor_id,
                    message=f"Target '{name}' created",
                    metadata={"name": name, "environment": environment},
                )
            except Exception:
                pass

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

        # Best-effort federation recorder
        if self._federation_recorder is not None:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                await self._federation_recorder.record(
                    event_type=FederationHistoryEventType.FEDERATION_CREATED,
                    federation_id=plan.federation_id,
                    tenant_id=context.tenant_id,
                    actor_id=created_by,
                    message=f"Federated plan '{name}' created",
                    metadata={
                        "name": name,
                        "bundle_id": bundle_id,
                        "target_count": len(effective_ids),
                        "strategy": strategy.value,
                    },
                )
            except Exception:
                pass

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
        # Approval check
        if self._approval_service is not None:
            allowed = await self._check_approval(federation_id, "federation.plan.start")
            if not allowed:
                latest = await self._approval_service.check_approval_status(federation_id, "federation.plan.start")
                if latest:
                    return self._create_approval_result(latest)  # type: ignore[return-value]
                return {"status": "approval_required", "action": "federation.plan.start"}  # type: ignore[return-value]

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

        # Best-effort federation recorder
        if self._federation_recorder is not None:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                await self._federation_recorder.record(
                    event_type=FederationHistoryEventType.FEDERATION_STARTED,
                    federation_id=federation_id,
                    tenant_id=context.tenant_id,
                    actor_id=actor_id,
                    message=f"Federated plan '{federation_id}' started",
                    metadata={"bundle_id": plan.bundle_id},
                )
            except Exception:
                pass

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

        conflicts = await self._conflict_detector.detect_conflicts(plan)

        # Best-effort federation recorder — record each conflict
        if self._federation_recorder is not None and conflicts:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                for conflict in conflicts:
                    try:
                        await self._federation_recorder.record(
                            event_type=FederationHistoryEventType.CONFLICT_DETECTED,
                            federation_id=federation_id,
                            target_id=conflict.target_id if hasattr(conflict, "target_id") else None,
                            message=conflict.message,
                            metadata={
                                "conflict_type": conflict.conflict_type if hasattr(conflict, "conflict_type") else "",
                                "severity": conflict.severity.value if hasattr(conflict.severity, "value") else str(conflict.severity),
                                "target_id": conflict.target_id if hasattr(conflict, "target_id") else "",
                                "message": conflict.message,
                            },
                        )
                    except Exception:
                        pass
            except Exception:
                pass

        return conflicts

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    async def _notify(self, event_type: str, data: dict[str, Any]) -> None:
        """Best-effort notification (never raises)."""
        if self._notification_service is None:
            return
        try:
            await self._notification_service.notify(event_type=event_type, data=data)
        except TypeError:
            await self._notification_service.notify(event_type, data)
        except Exception:
            return

    @staticmethod
    def _clone_template_steps_for_target(
        plan: FederatedRolloutPlan,
        target: FederatedRolloutTarget,
    ) -> list[RolloutStep]:
        """Clone template steps for a specific target, resetting execution state."""
        suffix = target.target_id[-6:]
        cloned: list[RolloutStep] = []
        for step in plan.rollout_template_steps:
            new_step_id = f"{step.step_id}_{suffix}"
            new_require_prev = (
                f"{step.require_previous_step}_{suffix}"
                if step.require_previous_step is not None
                else None
            )
            cloned.append(
                RolloutStep(
                    step_id=new_step_id,
                    step_type=step.step_type,
                    environment=target.environment,
                    ring_name=target.ring_name if target.ring_name is not None else step.ring_name,
                    from_ring=step.from_ring,
                    to_ring=step.to_ring,
                    required_gate_status=step.required_gate_status,
                    eval_suite=step.eval_suite,
                    requires_approval=step.requires_approval,
                    require_previous_step=new_require_prev,
                    status=RolloutStepStatus.PENDING,
                )
            )
        return cloned

    @staticmethod
    def _next_execution_index(plan: FederatedRolloutPlan) -> int | None:
        """Return the index of the next execution to run, or None."""
        if plan.strategy == FederationExecutionStrategy.WAVE:
            for wave in plan.waves:
                wave_indices: list[int] = []
                for idx, exec_ in enumerate(plan.executions):
                    if exec_.target_id in wave.target_ids:
                        wave_indices.append(idx)
                has_pending = any(
                    plan.executions[i].status == FederatedRolloutTargetExecutionStatus.PENDING
                    for i in wave_indices
                )
                if not has_pending:
                    continue
                # Check if wave is blocked by failed/blocked targets
                if wave.require_all_successful:
                    has_failed = any(
                        plan.executions[i].status
                        in (
                            FederatedRolloutTargetExecutionStatus.FAILED,
                            FederatedRolloutTargetExecutionStatus.BLOCKED,
                        )
                        for i in wave_indices
                    )
                    if has_failed:
                        return None
                # Return first PENDING in this wave
                for i in wave_indices:
                    if plan.executions[i].status == FederatedRolloutTargetExecutionStatus.PENDING:
                        return i
            return None
        # SEQUENTIAL or PARALLEL: first PENDING
        for idx, exec_ in enumerate(plan.executions):
            if exec_.status == FederatedRolloutTargetExecutionStatus.PENDING:
                return idx
        return None

    @staticmethod
    def _execution_status_from_child(child_plan: Any) -> FederatedRolloutTargetExecutionStatus:
        """Map a child rollout plan status to a federation execution status."""
        if child_plan.status == RolloutPlanStatus.COMPLETED:
            return FederatedRolloutTargetExecutionStatus.SUCCEEDED
        if child_plan.status == RolloutPlanStatus.FAILED:
            return FederatedRolloutTargetExecutionStatus.FAILED
        for step in child_plan.steps:
            if step.status == RolloutStepStatus.BLOCKED:
                return FederatedRolloutTargetExecutionStatus.BLOCKED
        return FederatedRolloutTargetExecutionStatus.RUNNING

    @staticmethod
    def _error_from_child(child_plan: Any) -> dict[str, Any] | None:
        """Return the first step error from a child plan, if any."""
        for step in child_plan.steps:
            if step.error is not None:
                return step.error
        return None

    @staticmethod
    def _plan_status_from_executions(
        executions: list[FederatedRolloutTargetExecution],
    ) -> FederatedRolloutPlanStatus:
        """Derive plan status from execution statuses."""
        terminal_statuses = (
            FederatedRolloutTargetExecutionStatus.SUCCEEDED,
            FederatedRolloutTargetExecutionStatus.SKIPPED,
        )
        all_done = all(
            e.status in terminal_statuses
            for e in executions
        )
        if all_done:
            return FederatedRolloutPlanStatus.COMPLETED
        if any(
            e.status == FederatedRolloutTargetExecutionStatus.FAILED
            for e in executions
        ):
            return FederatedRolloutPlanStatus.FAILED
        if any(
            e.status == FederatedRolloutTargetExecutionStatus.BLOCKED
            for e in executions
        ):
            return FederatedRolloutPlanStatus.BLOCKED
        return FederatedRolloutPlanStatus.ACTIVE

    # ------------------------------------------------------------------
    # Execution API
    # ------------------------------------------------------------------

    async def run_next_target(
        self,
        federation_id: str,
        actor_id: str,
        context: RunContext,
    ) -> FederatedRolloutPlan:
        """Execute the next pending target in the federated plan.

        Requires ``FEDERATION_PLAN_EXECUTE`` permission.  Creates a child
        rollout via the rollout service, runs it, and maps the result back
        to the federation execution.
        """
        # Approval check
        if self._approval_service is not None:
            allowed = await self._check_approval(federation_id, "federation.plan.run_next")
            if not allowed:
                latest = await self._approval_service.check_approval_status(federation_id, "federation.plan.run_next")
                if latest:
                    return self._create_approval_result(latest)  # type: ignore[return-value]
                return {"status": "approval_required", "action": "federation.plan.run_next"}  # type: ignore[return-value]

        await self._check_permission(
            PolicyReleasePermission.FEDERATION_PLAN_EXECUTE, context,
        )

        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated plan '{federation_id}' not found")
        if plan.status != FederatedRolloutPlanStatus.ACTIVE:
            raise ValueError(
                f"Cannot execute plan with status '{plan.status}'. Must be ACTIVE."
            )

        idx = self._next_execution_index(plan)
        if idx is None:
            return plan

        execution = plan.executions[idx]
        target = await self._target_store.get(execution.target_id)

        # Handle missing or disabled targets
        if target is None:
            updated_executions = list(plan.executions)
            updated_executions[idx] = execution.model_copy(update={
                "status": FederatedRolloutTargetExecutionStatus.BLOCKED,
                "error": {"message": f"Target '{execution.target_id}' not found"},
            })
            plan = plan.model_copy(update={
                "executions": updated_executions,
                "status": self._plan_status_from_executions(updated_executions),
                "updated_at": datetime.now(timezone.utc),
            })
            plan = await self._federation_store.update(plan)

            # Best-effort federation recorder — target blocked (missing)
            if self._federation_recorder is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    await self._federation_recorder.record(
                        event_type=FederationHistoryEventType.TARGET_EXECUTION_BLOCKED,
                        federation_id=federation_id,
                        target_id=execution.target_id,
                        tenant_id=context.tenant_id,
                        actor_id=actor_id,
                        message=f"Target '{execution.target_id}' not found, blocked",
                        metadata={"error": f"Target '{execution.target_id}' not found"},
                    )
                except Exception:
                    pass

            return plan

        if target.status == FederatedTargetStatus.DISABLED:
            updated_executions = list(plan.executions)
            updated_executions[idx] = execution.model_copy(update={
                "status": FederatedRolloutTargetExecutionStatus.SKIPPED,
            })
            plan = plan.model_copy(update={
                "executions": updated_executions,
                "status": self._plan_status_from_executions(updated_executions),
                "updated_at": datetime.now(timezone.utc),
            })
            plan = await self._federation_store.update(plan)

            # Best-effort federation recorder — target skipped (disabled)
            if self._federation_recorder is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    await self._federation_recorder.record(
                        event_type=FederationHistoryEventType.TARGET_EXECUTION_SKIPPED,
                        federation_id=federation_id,
                        target_id=execution.target_id,
                        tenant_id=context.tenant_id,
                        actor_id=actor_id,
                        message=f"Target '{execution.target_id}' disabled, skipped",
                    )
                except Exception:
                    pass

            return plan

        # Mark execution as RUNNING
        now = datetime.now(timezone.utc)
        updated_executions = list(plan.executions)
        updated_executions[idx] = execution.model_copy(update={
            "status": FederatedRolloutTargetExecutionStatus.RUNNING,
            "started_at": now,
        })
        plan = plan.model_copy(update={
            "executions": updated_executions,
            "updated_at": now,
        })
        plan = await self._federation_store.update(plan)

        # Best-effort federation recorder — target execution started
        if self._federation_recorder is not None:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                await self._federation_recorder.record(
                    event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED,
                    federation_id=federation_id,
                    target_id=execution.target_id,
                    tenant_id=context.tenant_id,
                    environment=target.environment,
                    ring_name=target.ring_name,
                    region=target.region,
                    actor_id=actor_id,
                    message=f"Target execution '{execution.target_id}' started",
                )
            except Exception:
                pass

        # Create child rollout
        child_steps = self._clone_template_steps_for_target(plan, target)
        child_plan = await self._rollout_service.create_plan(
            name=f"{plan.name} / {target.name}",
            bundle_id=plan.bundle_id,
            steps=child_steps,
            created_by=actor_id,
            context=context,
        )

        # Start child rollout
        child_plan = await self._rollout_service.start_plan(
            rollout_id=child_plan.rollout_id,
            started_by=actor_id,
            context=context,
        )

        # Run child rollout
        child_plan = await self._rollout_service.run_all_available(
            rollout_id=child_plan.rollout_id,
            actor_id=actor_id,
            context=context,
        )

        # Map child result to execution
        exec_status = self._execution_status_from_child(child_plan)
        exec_error = self._error_from_child(child_plan)

        updated_executions = list(plan.executions)
        updated_executions[idx] = updated_executions[idx].model_copy(update={
            "rollout_id": child_plan.rollout_id,
            "status": exec_status,
            "completed_at": datetime.now(timezone.utc),
            "error": exec_error,
        })
        new_plan_status = self._plan_status_from_executions(updated_executions)
        plan = plan.model_copy(update={
            "executions": updated_executions,
            "status": new_plan_status,
            "updated_at": datetime.now(timezone.utc),
        })
        plan = await self._federation_store.update(plan)

        # Emit audit/change events
        event_data = {
            "federation_id": federation_id,
            "target_id": execution.target_id,
            "rollout_id": child_plan.rollout_id,
            "status": exec_status.value,
        }
        if exec_status == FederatedRolloutTargetExecutionStatus.SUCCEEDED:
            await self._write_audit(
                "policy.federation.target.succeeded",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data=event_data,
            )
            await self._emit_change_event(
                PolicyChangeEventType.FEDERATION_PLAN_COMPLETED
                if new_plan_status == FederatedRolloutPlanStatus.COMPLETED
                else PolicyChangeEventType.FEDERATION_PLAN_STARTED,
                actor_id=actor_id,
                bundle_id=plan.bundle_id,
                data=event_data,
            )
            # Best-effort federation recorder — target execution succeeded
            if self._federation_recorder is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    await self._federation_recorder.record(
                        event_type=FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
                        federation_id=federation_id,
                        target_id=execution.target_id,
                        rollout_id=child_plan.rollout_id,
                        tenant_id=context.tenant_id,
                        environment=target.environment,
                        ring_name=target.ring_name,
                        region=target.region,
                        actor_id=actor_id,
                        message=f"Target execution '{execution.target_id}' succeeded",
                    )
                except Exception:
                    pass
        elif exec_status == FederatedRolloutTargetExecutionStatus.FAILED:
            await self._write_audit(
                "policy.federation.target.failed",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data=event_data,
            )
            await self._emit_change_event(
                PolicyChangeEventType.FEDERATION_PLAN_FAILED,
                actor_id=actor_id,
                bundle_id=plan.bundle_id,
                data=event_data,
            )
            await self._notify(
                "federation.plan.target_failed",
                event_data,
            )
            # Best-effort federation recorder — target execution failed
            if self._federation_recorder is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    await self._federation_recorder.record(
                        event_type=FederationHistoryEventType.TARGET_EXECUTION_FAILED,
                        federation_id=federation_id,
                        target_id=execution.target_id,
                        rollout_id=child_plan.rollout_id,
                        tenant_id=context.tenant_id,
                        environment=target.environment,
                        ring_name=target.ring_name,
                        region=target.region,
                        actor_id=actor_id,
                        message=f"Target execution '{execution.target_id}' failed",
                        metadata=exec_error or {},
                    )
                except Exception:
                    pass
        elif exec_status == FederatedRolloutTargetExecutionStatus.BLOCKED:
            await self._write_audit(
                "policy.federation.target.blocked",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data=event_data,
            )
            await self._notify(
                "federation.plan.target_blocked",
                event_data,
            )
            # Best-effort federation recorder — target execution blocked
            if self._federation_recorder is not None:
                try:
                    from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                    await self._federation_recorder.record(
                        event_type=FederationHistoryEventType.TARGET_EXECUTION_BLOCKED,
                        federation_id=federation_id,
                        target_id=execution.target_id,
                        rollout_id=child_plan.rollout_id,
                        tenant_id=context.tenant_id,
                        environment=target.environment,
                        ring_name=target.ring_name,
                        region=target.region,
                        actor_id=actor_id,
                        message=f"Target execution '{execution.target_id}' blocked",
                        metadata=exec_error or {},
                    )
                except Exception:
                    pass

        return plan

    async def run_all_available(
        self,
        federation_id: str,
        actor_id: str,
        context: RunContext,
    ) -> FederatedRolloutPlan:
        """Run all available targets until none are pending or plan is terminal.

        Loops calling ``run_next_target`` until no progress or terminal state.
        Max iterations = len(executions) + 1.
        """
        # Approval check
        if self._approval_service is not None:
            allowed = await self._check_approval(federation_id, "federation.plan.run_all")
            if not allowed:
                latest = await self._approval_service.check_approval_status(federation_id, "federation.plan.run_all")
                if latest:
                    return self._create_approval_result(latest)  # type: ignore[return-value]
                return {"status": "approval_required", "action": "federation.plan.run_all"}  # type: ignore[return-value]

        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated plan '{federation_id}' not found")

        max_iterations = len(plan.executions) + 1
        for _ in range(max_iterations):
            prev_status = plan.status
            plan = await self.run_next_target(federation_id, actor_id, context)
            if plan.status != prev_status:
                # Plan reached a terminal state or changed
                if plan.status in (
                    FederatedRolloutPlanStatus.COMPLETED,
                    FederatedRolloutPlanStatus.FAILED,
                    FederatedRolloutPlanStatus.CANCELLED,
                ):
                    break
            # Check if there's any progress to make
            if self._next_execution_index(plan) is None:
                break
        return plan

    async def cancel_federated_plan(
        self,
        federation_id: str,
        actor_id: str,
        context: RunContext,
        reason: str | None = None,
    ) -> FederatedRolloutPlan:
        """Cancel a federated rollout plan.

        Requires ``FEDERATION_PLAN_CANCEL`` permission.  Marks all
        PENDING/RUNNING executions as CANCELLED.
        """
        # Approval check
        if self._approval_service is not None:
            allowed = await self._check_approval(federation_id, "federation.plan.cancel")
            if not allowed:
                latest = await self._approval_service.check_approval_status(federation_id, "federation.plan.cancel")
                if latest:
                    return self._create_approval_result(latest)  # type: ignore[return-value]
                return {"status": "approval_required", "action": "federation.plan.cancel"}  # type: ignore[return-value]

        await self._check_permission(
            PolicyReleasePermission.FEDERATION_PLAN_CANCEL, context,
        )

        plan = await self._federation_store.get(federation_id)
        if plan is None:
            raise KeyError(f"Federated plan '{federation_id}' not found")

        cancellable = (
            FederatedRolloutTargetExecutionStatus.PENDING,
            FederatedRolloutTargetExecutionStatus.RUNNING,
        )
        updated_executions = [
            e.model_copy(update={"status": FederatedRolloutTargetExecutionStatus.CANCELLED})
            if e.status in cancellable
            else e
            for e in plan.executions
        ]

        plan = plan.model_copy(update={
            "status": FederatedRolloutPlanStatus.CANCELLED,
            "executions": updated_executions,
            "updated_at": datetime.now(timezone.utc),
        })
        plan = await self._federation_store.update(plan)

        # Best-effort federation recorder — federation cancelled
        if self._federation_recorder is not None:
            try:
                from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
                await self._federation_recorder.record(
                    event_type=FederationHistoryEventType.FEDERATION_CANCELLED,
                    federation_id=federation_id,
                    tenant_id=context.tenant_id,
                    actor_id=actor_id,
                    message=f"Federation '{federation_id}' cancelled",
                    metadata={"reason": reason} if reason else {},
                )
            except Exception:
                pass

        await self._write_audit(
            "policy.federation.plan.cancelled",
            user_id=actor_id,
            tenant_id=context.tenant_id,
            data={"federation_id": federation_id, "reason": reason},
        )
        await self._emit_change_event(
            PolicyChangeEventType.FEDERATION_PLAN_CANCELLED,
            actor_id=actor_id,
            bundle_id=plan.bundle_id,
            reason=reason,
            data={"federation_id": federation_id},
        )

        return plan
