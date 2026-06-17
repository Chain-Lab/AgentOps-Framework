"""Rollout service — orchestrates multi-environment rollout plans."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.policy_change_event import (
    PolicyChangeEvent,
    PolicyChangeEventType,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)
from agent_app.runtime.policy_rollout_approval_policy import (
    ApprovalPolicyError,
    RolloutApprovalPolicyEvaluator,
)
from agent_app.governance.audit import AuditEvent
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus


class RolloutService:
    """Orchestrates multi-environment rollout plans step by step.

    Delegates to PolicyReleaseService for activations, ring assignments,
    canary evals, and ring promotions.
    """

    def __init__(
        self,
        rollout_store: Any,
        release_service: Any,
        eval_runner: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        permission_checker: Any | None = None,
        approval_store: Any | None = None,
        approval_require_reason: bool = False,
        approval_policy: RolloutApprovalPolicy | None = None,
        release_gate_automation_service: Any = None,
    ) -> None:
        self._rollout_store = rollout_store
        self._release_service = release_service
        self._eval_runner = eval_runner
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._permission_checker = permission_checker
        self._approval_store = approval_store
        self._approval_require_reason = approval_require_reason
        self._approval_policy = approval_policy
        self._release_gate_automation_service = release_gate_automation_service

    # --- Permission check ---
    async def _check_permission(
        self,
        permission: PolicyReleasePermission,
        context: RunContext,
    ) -> None:
        if self._permission_checker is None:
            return
        allowed = await self._permission_checker.check(permission, context)
        if not allowed:
            raise PermissionError(
                f"Permission denied: {permission.value} required"
            )

    # --- Audit logging ---
    async def _write_audit(
        self,
        event_type: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        if self._audit_logger is None:
            return
        event = AuditEvent(
            event_id=f"ae_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            user_id=user_id,
            tenant_id=tenant_id,
            data=data or {},
            created_at=datetime.now(timezone.utc),
        )
        await self._audit_logger.log(event)

    # --- Change event emission ---
    async def _emit_change_event(
        self,
        event_type: PolicyChangeEventType,
        environment: str | None = None,
        ring_name: str | None = None,
        bundle_id: str | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
        data: dict | None = None,
    ) -> None:
        if self._event_store is None:
            return
        try:
            event = PolicyChangeEvent(
                event_id=f"pce_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                environment=environment,
                ring_name=ring_name,
                bundle_id=bundle_id,
                actor_id=actor_id,
                reason=reason,
                data=data or {},
                created_at=datetime.now(timezone.utc),
            )
            await self._event_store.append(event)
        except Exception:
            pass  # Event emission failure shouldn't crash rollout operations

    # --- Public methods ---

    async def create_plan(
        self,
        name: str,
        bundle_id: str,
        steps: list[RolloutStep],
        created_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> RolloutPlan:
        """Create a new rollout plan in DRAFT status."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_CREATE, context)

        now = datetime.now(timezone.utc)
        plan = RolloutPlan(
            rollout_id=f"ro_{uuid.uuid4().hex[:12]}",
            name=name,
            bundle_id=bundle_id,
            status=RolloutPlanStatus.DRAFT,
            steps=steps,
            created_by=created_by,
            reason=reason,
            created_at=now,
            updated_at=now,
        )

        plan = await self._rollout_store.create(plan)

        await self._emit_change_event(
            PolicyChangeEventType.ROLLOUT_CREATED,
            bundle_id=bundle_id,
            actor_id=created_by,
            reason=reason,
            data={"rollout_id": plan.rollout_id, "name": name, "step_count": len(steps)},
        )
        await self._write_audit(
            "policy.rollout.created",
            user_id=created_by,
            tenant_id=context.tenant_id,
            data={"rollout_id": plan.rollout_id, "bundle_id": bundle_id, "name": name},
        )

        return plan

    async def start_plan(
        self,
        rollout_id: str,
        started_by: str,
        context: RunContext,
    ) -> RolloutPlan:
        """Start a rollout plan (transition from DRAFT to ACTIVE)."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_START, context)

        plan = await self._rollout_store.get(rollout_id)
        if plan is None:
            raise KeyError(f"Rollout plan '{rollout_id}' not found")
        if plan.status != RolloutPlanStatus.DRAFT:
            raise ValueError(f"Cannot start plan with status '{plan.status}'. Must be DRAFT.")

        now = datetime.now(timezone.utc)
        plan = plan.model_copy(update={
            "status": RolloutPlanStatus.ACTIVE,
            "updated_at": now,
        })
        plan = await self._rollout_store.update(plan)

        await self._emit_change_event(
            PolicyChangeEventType.ROLLOUT_STARTED,
            bundle_id=plan.bundle_id,
            actor_id=started_by,
            data={"rollout_id": rollout_id},
        )
        await self._write_audit(
            "policy.rollout.started",
            user_id=started_by,
            tenant_id=context.tenant_id,
            data={"rollout_id": rollout_id, "bundle_id": plan.bundle_id},
        )

        return plan

    async def run_next_step(
        self,
        rollout_id: str,
        actor_id: str,
        context: RunContext,
    ) -> RolloutPlan:
        """Execute the next runnable step in the rollout plan."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_EXECUTE, context)

        plan = await self._rollout_store.get(rollout_id)
        if plan is None:
            raise KeyError(f"Rollout plan '{rollout_id}' not found")
        if plan.status != RolloutPlanStatus.ACTIVE:
            raise ValueError(f"Cannot run steps on plan with status '{plan.status}'. Must be ACTIVE.")

        # Find next runnable step
        next_step = self._find_next_runnable_step(plan)
        if next_step is None:
            return plan  # No runnable step available

        # Phase 42: Check simulation gate requirement for step
        if next_step.requires_simulation_gate and self._release_gate_automation_service is not None:
            gate_req = await self._release_gate_automation_service.check_requirement(
                "rollout_step", next_step.step_id
            )
            if gate_req.status != ReleaseGateRequirementStatus.SATISFIED:
                # Mark step as BLOCKED
                blocked_step = next_step.model_copy(update={
                    "status": RolloutStepStatus.BLOCKED,
                    "error": {
                        "type": "simulation_gate_required",
                        "message": f"Simulation gate is {gate_req.status.value}, step requires SATISFIED",
                        "requirement_status": gate_req.status.value,
                    },
                    "simulation_gate_requirement_id": gate_req.requirement_id if gate_req.requirement_id != "rgr_none" else None,
                })
                # Update plan and return
                updated_steps = [
                    blocked_step if s.step_id == blocked_step.step_id else s
                    for s in plan.steps
                ]
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "updated_at": datetime.now(timezone.utc),
                })
                await self._rollout_store.update(plan)
                await self._write_audit(
                    "policy.rollout.step_blocked",
                    user_id=actor_id,
                    data={
                        "rollout_id": rollout_id,
                        "step_id": next_step.step_id,
                        "reason": f"simulation_gate_{gate_req.status.value}",
                    },
                )
                return plan

        # Execute the step
        executed_step = await self._execute_step(plan, next_step, actor_id, context)

        # Update the plan with the executed step
        updated_steps = [
            executed_step if s.step_id == executed_step.step_id else s
            for s in plan.steps
        ]
        now = datetime.now(timezone.utc)

        # Determine plan status
        new_status = plan.status
        if executed_step.status == RolloutStepStatus.FAILED:
            new_status = RolloutPlanStatus.FAILED
        elif all(s.status == RolloutStepStatus.SUCCEEDED for s in updated_steps):
            new_status = RolloutPlanStatus.COMPLETED
        elif executed_step.status == RolloutStepStatus.BLOCKED:
            pass  # Plan stays ACTIVE, step is blocked

        plan = plan.model_copy(update={
            "steps": updated_steps,
            "status": new_status,
            "updated_at": now,
        })
        plan = await self._rollout_store.update(plan)

        # Emit events based on step result
        if executed_step.status == RolloutStepStatus.SUCCEEDED:
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_STEP_SUCCEEDED,
                environment=executed_step.environment,
                ring_name=executed_step.ring_name,
                bundle_id=plan.bundle_id,
                actor_id=actor_id,
                data={
                    "rollout_id": rollout_id,
                    "step_id": executed_step.step_id,
                    "step_type": executed_step.step_type.value,
                    "activation_id": executed_step.activation_id,
                    "assignment_id": executed_step.assignment_id,
                },
            )
            await self._write_audit(
                "policy.rollout.step_succeeded",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data={"rollout_id": rollout_id, "step_id": executed_step.step_id, "step_type": executed_step.step_type.value},
            )

        if new_status == RolloutPlanStatus.COMPLETED:
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_COMPLETED,
                bundle_id=plan.bundle_id,
                actor_id=actor_id,
                data={"rollout_id": rollout_id},
            )
            await self._write_audit(
                "policy.rollout.completed",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data={"rollout_id": rollout_id, "bundle_id": plan.bundle_id},
            )

        if new_status == RolloutPlanStatus.FAILED:
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_FAILED,
                bundle_id=plan.bundle_id,
                actor_id=actor_id,
                data={"rollout_id": rollout_id, "step_id": executed_step.step_id, "error": executed_step.error},
            )
            await self._write_audit(
                "policy.rollout.failed",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data={"rollout_id": rollout_id, "step_id": executed_step.step_id, "error": executed_step.error},
            )

        if executed_step.status == RolloutStepStatus.FAILED:
            await self._write_audit(
                "policy.rollout.step_failed",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data={"rollout_id": rollout_id, "step_id": executed_step.step_id, "error": executed_step.error},
            )

        if executed_step.status == RolloutStepStatus.BLOCKED:
            await self._write_audit(
                "policy.rollout.step_blocked",
                user_id=actor_id,
                tenant_id=context.tenant_id,
                data={"rollout_id": rollout_id, "step_id": executed_step.step_id, "error": executed_step.error},
            )

        return plan

    async def run_all_available(
        self,
        rollout_id: str,
        actor_id: str,
        context: RunContext,
    ) -> RolloutPlan:
        """Run all available steps until none are runnable or a step fails/blocks."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_EXECUTE, context)

        plan = await self._rollout_store.get(rollout_id)
        if plan is None:
            raise KeyError(f"Rollout plan '{rollout_id}' not found")
        if plan.status != RolloutPlanStatus.ACTIVE:
            raise ValueError(f"Cannot run steps on plan with status '{plan.status}'. Must be ACTIVE.")

        while True:
            next_step = self._find_next_runnable_step(plan)
            if next_step is None:
                break
            plan = await self.run_next_step(rollout_id, actor_id, context)
            if plan.status in (RolloutPlanStatus.FAILED, RolloutPlanStatus.CANCELLED):
                break
            # If the step was blocked, stop
            step = next((s for s in plan.steps if s.step_id == next_step.step_id), None)
            if step and step.status == RolloutStepStatus.BLOCKED:
                break

        return plan

    async def cancel_plan(
        self,
        rollout_id: str,
        cancelled_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> RolloutPlan:
        """Cancel a rollout plan."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_CANCEL, context)

        plan = await self._rollout_store.get(rollout_id)
        if plan is None:
            raise KeyError(f"Rollout plan '{rollout_id}' not found")
        if plan.status not in (RolloutPlanStatus.DRAFT, RolloutPlanStatus.ACTIVE):
            raise ValueError(f"Cannot cancel plan with status '{plan.status}'. Must be DRAFT or ACTIVE.")

        now = datetime.now(timezone.utc)
        plan = plan.model_copy(update={
            "status": RolloutPlanStatus.CANCELLED,
            "updated_at": now,
        })
        plan = await self._rollout_store.update(plan)

        await self._emit_change_event(
            PolicyChangeEventType.ROLLOUT_CANCELLED,
            bundle_id=plan.bundle_id,
            actor_id=cancelled_by,
            reason=reason,
            data={"rollout_id": rollout_id},
        )
        await self._write_audit(
            "policy.rollout.cancelled",
            user_id=cancelled_by,
            tenant_id=context.tenant_id,
            data={"rollout_id": rollout_id, "bundle_id": plan.bundle_id, "reason": reason},
        )

        return plan

    # --- Approval workflow ---

    async def _check_permission_any(
        self,
        permissions: list[PolicyReleasePermission],
        context: RunContext,
    ) -> None:
        """Check that at least one of the given permissions is held."""
        if self._permission_checker is None:
            return
        for permission in permissions:
            allowed = await self._permission_checker.check(permission, context)
            if allowed:
                return
        raise PermissionError(
            f"Permission denied: one of {', '.join(p.value for p in permissions)} required"
        )

    async def request_step_approval(
        self,
        rollout_id: str,
        step_id: str,
        requested_by: str,
        context: RunContext,
        reason: str | None = None,
        policy: RolloutApprovalPolicy | None = None,
    ) -> RolloutStepApproval:
        """Request approval for a rollout step that requires human sign-off."""
        await self._check_permission_any(
            [PolicyReleasePermission.ROLLOUT_APPROVAL_REQUEST, PolicyReleasePermission.ROLLOUT_EXECUTE],
            context,
        )

        plan = await self._rollout_store.get(rollout_id)
        if plan is None:
            raise KeyError(f"Rollout plan '{rollout_id}' not found")

        step = next((s for s in plan.steps if s.step_id == step_id), None)
        if step is None:
            raise KeyError(f"Step '{step_id}' not found in rollout plan '{rollout_id}'")

        if not step.requires_approval:
            raise ValueError(f"Step '{step_id}' does not require approval")

        if step.status not in (RolloutStepStatus.BLOCKED, RolloutStepStatus.PENDING):
            raise ValueError(
                f"Step '{step_id}' is in status '{step.status.value}', "
                f"must be BLOCKED or PENDING to request approval"
            )

        if self._approval_store is None:
            raise RuntimeError("No approval store configured")

        # Check for existing pending approval
        existing = await self._approval_store.get_pending_for_step(rollout_id, step_id)
        if existing is not None:
            return existing

        # Resolve policy: explicit > step-level > service default > SINGLE default
        resolved_policy = policy
        if resolved_policy is None:
            resolved_policy = getattr(step, "approval_policy", None)
        if resolved_policy is None:
            resolved_policy = self._approval_policy
        if resolved_policy is None:
            resolved_policy = RolloutApprovalPolicy()

        # Compute expires_at if policy specifies it
        expires_at = None
        if resolved_policy.expires_after_seconds is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=resolved_policy.expires_after_seconds
            )

        # Create new approval
        approval = RolloutStepApproval(
            approval_id=f"rsa_{uuid.uuid4().hex[:12]}",
            rollout_id=rollout_id,
            step_id=step_id,
            bundle_id=plan.bundle_id,
            environment=step.environment,
            ring_name=step.ring_name,
            requested_by=requested_by,
            requested_reason=reason,
            status=RolloutStepApprovalStatus.PENDING,
            policy=resolved_policy,
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )
        approval = await self._approval_store.create(approval)

        # Update step status to BLOCKED
        updated_steps = [
            step.model_copy(update={
                "status": RolloutStepStatus.BLOCKED,
                "approval_id": approval.approval_id,
                "error": {
                    "type": "approval_required",
                    "message": "Step requires approval",
                    "approval_id": approval.approval_id,
                },
            })
            if s.step_id == step_id else s
            for s in plan.steps
        ]
        plan = plan.model_copy(update={
            "steps": updated_steps,
            "updated_at": datetime.now(timezone.utc),
        })
        await self._rollout_store.update(plan)

        # Emit events
        await self._write_audit(
            "policy.rollout.approval.requested",
            user_id=requested_by,
            tenant_id=context.tenant_id,
            data={
                "rollout_id": rollout_id,
                "step_id": step_id,
                "approval_id": approval.approval_id,
                "bundle_id": plan.bundle_id,
                "environment": step.environment,
            },
        )
        await self._emit_change_event(
            PolicyChangeEventType.ROLLOUT_APPROVAL_REQUESTED,
            environment=step.environment,
            ring_name=step.ring_name,
            bundle_id=plan.bundle_id,
            actor_id=requested_by,
            reason=reason,
        )

        return approval

    async def approve_step(
        self,
        approval_id: str,
        approved_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> RolloutStepApproval:
        """Approve a pending rollout step approval via decision-based flow."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_APPROVAL_APPROVE, context)

        approval = await self._approval_store.get(approval_id)
        if approval is None:
            raise KeyError(f"Approval '{approval_id}' not found")

        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot approve: approval '{approval_id}' status is "
                f"{approval.status.value}, expected PENDING"
            )

        if self._approval_require_reason and reason is None:
            raise ValueError("Reason is required for approval")

        # Build decision
        decision = RolloutApprovalDecision(
            decision_id=f"rsd_{uuid.uuid4().hex[:12]}",
            approval_id=approval_id,
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by=approved_by,
            reason=reason,
            roles=list(context.roles) if hasattr(context, "roles") else [],
            permissions=list(context.permissions) if hasattr(context, "permissions") else [],
            created_at=datetime.now(timezone.utc),
        )

        # Validate via evaluator
        plan = await self._rollout_store.get(approval.rollout_id)
        evaluator = RolloutApprovalPolicyEvaluator()
        try:
            evaluator.validate_decision(approval, decision, rollout=plan)
        except ApprovalPolicyError as exc:
            try:
                await self._write_audit(
                    "policy.rollout.approval.policy_denied",
                    user_id=approved_by,
                    tenant_id=context.tenant_id,
                    data={
                        "approval_id": approval_id,
                        "rollout_id": approval.rollout_id,
                        "step_id": approval.step_id,
                        "actor_id": approved_by,
                        "denial_reason": str(exc),
                    },
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations
            raise

        # Add decision to store
        approval = await self._approval_store.add_decision(approval_id, decision)

        # Compute current approval count for event data
        current_approvals = sum(
            1 for d in approval.decisions if d.decision_type == RolloutApprovalDecisionType.APPROVE
        )

        if approval.status == RolloutStepApprovalStatus.APPROVED:
            # Quorum reached (or SINGLE) — unblock step
            if plan is not None:
                step = next(
                    (s for s in plan.steps if s.step_id == approval.step_id), None
                )
                if step is not None and step.status == RolloutStepStatus.BLOCKED:
                    updated_steps = [
                        s.model_copy(update={
                            "status": RolloutStepStatus.PENDING,
                            "error": None,
                        })
                        if s.step_id == approval.step_id else s
                        for s in plan.steps
                    ]
                    plan = plan.model_copy(update={
                        "steps": updated_steps,
                        "updated_at": datetime.now(timezone.utc),
                    })
                    await self._rollout_store.update(plan)

            # Emit events
            try:
                await self._write_audit(
                    "policy.rollout.approval.approved",
                    user_id=approved_by,
                    tenant_id=context.tenant_id,
                    data={
                        "rollout_id": approval.rollout_id,
                        "step_id": approval.step_id,
                        "approval_id": approval_id,
                        "bundle_id": approval.bundle_id,
                        "environment": approval.environment,
                    },
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations
            try:
                await self._emit_change_event(
                    PolicyChangeEventType.ROLLOUT_APPROVAL_APPROVED,
                    environment=approval.environment,
                    ring_name=approval.ring_name,
                    bundle_id=approval.bundle_id,
                    actor_id=approved_by,
                    reason=reason,
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations
            # If quorum policy, emit quorum_reached audit event
            if approval.policy.policy_type == RolloutApprovalPolicyType.QUORUM:
                try:
                    await self._write_audit(
                        "policy.rollout.approval.quorum_reached",
                        user_id=approved_by,
                        tenant_id=context.tenant_id,
                        data={
                            "approval_id": approval_id,
                            "rollout_id": approval.rollout_id,
                            "step_id": approval.step_id,
                            "actor_id": approved_by,
                            "decision_type": "approve",
                            "required_approvals": approval.policy.required_approvals,
                            "current_approvals": current_approvals,
                            "policy_type": approval.policy.policy_type.value,
                        },
                    )
                except Exception:
                    pass  # Event emission failure shouldn't crash rollout operations
                try:
                    await self._emit_change_event(
                        PolicyChangeEventType.ROLLOUT_APPROVAL_QUORUM_REACHED,
                        environment=approval.environment,
                        ring_name=approval.ring_name,
                        bundle_id=approval.bundle_id,
                        actor_id=approved_by,
                        reason="Quorum reached",
                        data={
                            "approval_id": approval_id,
                            "required_approvals": approval.policy.required_approvals,
                            "current_approvals": current_approvals,
                        },
                    )
                except Exception:
                    pass  # Event emission failure shouldn't crash rollout operations
        elif approval.status == RolloutStepApprovalStatus.PENDING:
            # Quorum not yet reached — keep step BLOCKED
            try:
                await self._write_audit(
                    "policy.rollout.approval.decision_recorded",
                    user_id=approved_by,
                    tenant_id=context.tenant_id,
                    data={
                        "approval_id": approval_id,
                        "rollout_id": approval.rollout_id,
                        "step_id": approval.step_id,
                        "actor_id": approved_by,
                        "decision_type": "approve",
                        "required_approvals": approval.policy.required_approvals,
                        "current_approvals": current_approvals,
                        "policy_type": approval.policy.policy_type.value,
                    },
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations
            try:
                await self._emit_change_event(
                    PolicyChangeEventType.ROLLOUT_APPROVAL_DECISION_RECORDED,
                    environment=approval.environment,
                    ring_name=approval.ring_name,
                    bundle_id=approval.bundle_id,
                    actor_id=approved_by,
                    reason=reason,
                    data={
                        "approval_id": approval_id,
                        "decision_type": "approve",
                        "current_approvals": current_approvals,
                    },
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations

        return approval

    async def reject_step(
        self,
        approval_id: str,
        rejected_by: str,
        context: RunContext,
        reason: str | None = None,
    ) -> RolloutStepApproval:
        """Reject a pending rollout step approval via decision-based flow."""
        await self._check_permission(PolicyReleasePermission.ROLLOUT_APPROVAL_REJECT, context)

        approval = await self._approval_store.get(approval_id)
        if approval is None:
            raise KeyError(f"Approval '{approval_id}' not found")

        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot reject: approval '{approval_id}' status is "
                f"{approval.status.value}, expected PENDING"
            )

        if self._approval_require_reason and reason is None:
            raise ValueError("Reason is required for rejection")

        # Build decision
        decision = RolloutApprovalDecision(
            decision_id=f"rsd_{uuid.uuid4().hex[:12]}",
            approval_id=approval_id,
            decision_type=RolloutApprovalDecisionType.REJECT,
            decided_by=rejected_by,
            reason=reason,
            roles=list(context.roles) if hasattr(context, "roles") else [],
            permissions=list(context.permissions) if hasattr(context, "permissions") else [],
            created_at=datetime.now(timezone.utc),
        )

        # Validate via evaluator
        plan = await self._rollout_store.get(approval.rollout_id)
        evaluator = RolloutApprovalPolicyEvaluator()
        try:
            evaluator.validate_decision(approval, decision, rollout=plan)
        except ApprovalPolicyError as exc:
            try:
                await self._write_audit(
                    "policy.rollout.approval.policy_denied",
                    user_id=rejected_by,
                    tenant_id=context.tenant_id,
                    data={
                        "approval_id": approval_id,
                        "rollout_id": approval.rollout_id,
                        "step_id": approval.step_id,
                        "actor_id": rejected_by,
                        "denial_reason": str(exc),
                    },
                )
            except Exception:
                pass  # Event emission failure shouldn't crash rollout operations
            raise

        # Add decision to store — any reject immediately resolves to REJECTED
        approval = await self._approval_store.add_decision(approval_id, decision)

        # Compute current approval count for event data
        current_approvals = sum(
            1 for d in approval.decisions if d.decision_type == RolloutApprovalDecisionType.APPROVE
        )

        # Find the rollout plan and step — mark step FAILED, plan FAILED
        if plan is not None:
            step = next(
                (s for s in plan.steps if s.step_id == approval.step_id), None
            )
            if step is not None:
                updated_steps = [
                    s.model_copy(update={
                        "status": RolloutStepStatus.FAILED,
                        "error": {
                            "type": "approval_rejected",
                            "message": f"Approval rejected by {rejected_by}",
                        },
                    })
                    if s.step_id == approval.step_id else s
                    for s in plan.steps
                ]
                new_plan_status = plan.status
                if plan.status == RolloutPlanStatus.ACTIVE:
                    new_plan_status = RolloutPlanStatus.FAILED
                plan = plan.model_copy(update={
                    "steps": updated_steps,
                    "status": new_plan_status,
                    "updated_at": datetime.now(timezone.utc),
                })
                await self._rollout_store.update(plan)

        # Emit decision_recorded audit event
        try:
            await self._write_audit(
                "policy.rollout.approval.decision_recorded",
                user_id=rejected_by,
                tenant_id=context.tenant_id,
                data={
                    "approval_id": approval_id,
                    "rollout_id": approval.rollout_id,
                    "step_id": approval.step_id,
                    "actor_id": rejected_by,
                    "decision_type": "reject",
                    "required_approvals": approval.policy.required_approvals,
                    "current_approvals": current_approvals,
                    "policy_type": approval.policy.policy_type.value,
                },
            )
        except Exception:
            pass  # Event emission failure shouldn't crash rollout operations
        try:
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_APPROVAL_DECISION_RECORDED,
                environment=approval.environment,
                ring_name=approval.ring_name,
                bundle_id=approval.bundle_id,
                actor_id=rejected_by,
                reason=reason,
                data={
                    "approval_id": approval_id,
                    "decision_type": "reject",
                    "current_approvals": current_approvals,
                },
            )
        except Exception:
            pass  # Event emission failure shouldn't crash rollout operations

        # Emit rejected events
        try:
            await self._write_audit(
                "policy.rollout.approval.rejected",
                user_id=rejected_by,
                tenant_id=context.tenant_id,
                data={
                    "rollout_id": approval.rollout_id,
                    "step_id": approval.step_id,
                    "approval_id": approval_id,
                    "bundle_id": approval.bundle_id,
                    "environment": approval.environment,
                },
            )
        except Exception:
            pass  # Event emission failure shouldn't crash rollout operations
        try:
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_APPROVAL_REJECTED,
                environment=approval.environment,
                ring_name=approval.ring_name,
                bundle_id=approval.bundle_id,
                actor_id=rejected_by,
                reason=reason,
            )
        except Exception:
            pass  # Event emission failure shouldn't crash rollout operations

        return approval

    async def list_step_approvals(
        self,
        status: RolloutStepApprovalStatus | None = None,
        rollout_id: str | None = None,
        context: RunContext | None = None,
    ) -> list[RolloutStepApproval]:
        """List rollout step approvals, optionally filtered."""
        if context is not None:
            await self._check_permission(PolicyReleasePermission.ROLLOUT_APPROVAL_VIEW, context)

        if self._approval_store is None:
            return []

        return await self._approval_store.list(status, rollout_id)

    async def expire_approvals(self, context: RunContext) -> list[RolloutStepApproval]:
        """Mark pending approvals past their expires_at as EXPIRED."""
        if self._approval_store is None:
            return []
        expired = await self._approval_store.expire_pending()
        for approval in expired:
            await self._write_audit(
                "policy.rollout.approval.expired",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                data={
                    "rollout_id": approval.rollout_id,
                    "step_id": approval.step_id,
                    "approval_id": approval.approval_id,
                    "bundle_id": approval.bundle_id,
                    "environment": approval.environment,
                },
            )
            await self._emit_change_event(
                PolicyChangeEventType.ROLLOUT_APPROVAL_EXPIRED,
                environment=approval.environment,
                ring_name=approval.ring_name,
                bundle_id=approval.bundle_id,
                actor_id=context.user_id,
                data={"approval_id": approval.approval_id},
            )
        return expired

    # --- Step execution ---

    def _find_next_runnable_step(self, plan: RolloutPlan) -> RolloutStep | None:
        """Find the next PENDING step whose dependencies are met."""
        for step in plan.steps:
            if step.status != RolloutStepStatus.PENDING:
                continue
            if step.require_previous_step is not None:
                prev = next(
                    (s for s in plan.steps if s.step_id == step.require_previous_step),
                    None,
                )
                if prev is None or prev.status != RolloutStepStatus.SUCCEEDED:
                    continue
            return step
        return None

    async def _execute_step(
        self,
        plan: RolloutPlan,
        step: RolloutStep,
        actor_id: str,
        context: RunContext,
    ) -> RolloutStep:
        """Execute a single rollout step."""
        now = datetime.now(timezone.utc)
        step = step.model_copy(update={
            "status": RolloutStepStatus.RUNNING,
            "started_at": now,
        })

        try:
            # Check approval requirement first
            if step.requires_approval:
                if self._approval_store is not None:
                    # Full approval workflow
                    # Check for existing approval by step's approval_id first
                    existing = None
                    if step.approval_id:
                        existing = await self._approval_store.get(step.approval_id)
                    # Fall back to checking for pending approval
                    if existing is None:
                        existing = await self._approval_store.get_pending_for_step(plan.rollout_id, step.step_id)
                    if existing is not None and existing.status == RolloutStepApprovalStatus.APPROVED:
                        # Already approved — proceed with execution
                        pass  # fall through to step type execution
                    elif existing is not None and existing.status == RolloutStepApprovalStatus.REJECTED:
                        return step.model_copy(update={
                            "status": RolloutStepStatus.FAILED,
                            "error": {"type": "approval_rejected", "message": "Approval was rejected"},
                            "completed_at": datetime.now(timezone.utc),
                        })
                    else:
                        # Create or reuse pending approval
                        if existing is None:
                            approval = RolloutStepApproval(
                                approval_id=f"rsa_{uuid.uuid4().hex[:12]}",
                                rollout_id=plan.rollout_id,
                                step_id=step.step_id,
                                bundle_id=plan.bundle_id,
                                environment=step.environment,
                                ring_name=step.ring_name,
                                requested_by=actor_id,
                                status=RolloutStepApprovalStatus.PENDING,
                                created_at=datetime.now(timezone.utc),
                            )
                            approval = await self._approval_store.create(approval)
                        else:
                            approval = existing
                        return step.model_copy(update={
                            "status": RolloutStepStatus.BLOCKED,
                            "approval_id": approval.approval_id,
                            "error": {"type": "approval_required", "message": "Step requires approval before execution", "approval_id": approval.approval_id},
                            "completed_at": datetime.now(timezone.utc),
                        })
                else:
                    # Legacy MVP: block without approval store
                    return step.model_copy(update={
                        "status": RolloutStepStatus.BLOCKED,
                        "error": {"type": "approval_required", "message": "Step requires approval before execution"},
                        "completed_at": datetime.now(timezone.utc),
                    })

            result_step: RolloutStep | None = None

            if step.step_type == RolloutStepType.ACTIVATE:
                result_step = await self._execute_activate(plan, step, actor_id, context)
            elif step.step_type == RolloutStepType.ASSIGN_RING:
                result_step = await self._execute_assign_ring(plan, step, actor_id, context)
            elif step.step_type == RolloutStepType.CANARY_EVAL:
                result_step = await self._execute_canary_eval(plan, step, actor_id, context)
            elif step.step_type == RolloutStepType.PROMOTE_RING:
                result_step = await self._execute_promote_ring(plan, step, actor_id, context)
            else:
                raise ValueError(f"Unknown step type: {step.step_type}")

            return result_step

        except Exception as e:
            return step.model_copy(update={
                "status": RolloutStepStatus.FAILED,
                "error": {"type": "execution_error", "message": str(e)},
                "completed_at": datetime.now(timezone.utc),
            })

    async def _execute_activate(
        self,
        plan: RolloutPlan,
        step: RolloutStep,
        actor_id: str,
        context: RunContext,
    ) -> RolloutStep:
        """Execute an ACTIVATE step: promote bundle and optionally assign to ring."""
        svc = self._release_service

        # Check required gate status if configured
        if step.required_gate_status:
            # For MVP, we just verify the bundle exists and assume gate check
            # Full gate verification can be added later
            pass

        # Execute promotion to create activation
        # First request promotion, then approve and execute
        promotion = await svc.request_promotion(
            bundle_id=plan.bundle_id,
            requested_by=actor_id,
            context=context,
            reason=f"Rollout plan {plan.rollout_id} step {step.step_id}",
        )
        promotion = await svc.approve_promotion(
            promotion_id=promotion.promotion_id,
            approved_by=actor_id,
            context=context,
            reason=f"Auto-approved for rollout step {step.step_id}",
        )
        activation = await svc.execute_promotion(
            promotion_id=promotion.promotion_id,
            executed_by=actor_id,
            context=context,
            environment=step.environment,
            reason=f"Rollout plan {plan.rollout_id} step {step.step_id}",
        )

        activation_id = getattr(activation, "activation_id", None)
        assignment_id = None

        # If ring_name specified, assign activation to ring
        if step.ring_name and activation_id:
            assignment = await svc.assign_activation_to_ring(
                environment=step.environment,
                ring_name=step.ring_name,
                activation_id=activation_id,
                assigned_by=actor_id,
                context=context,
                reason=f"Rollout plan {plan.rollout_id} step {step.step_id}",
            )
            assignment_id = getattr(assignment, "assignment_id", None)

        return step.model_copy(update={
            "status": RolloutStepStatus.SUCCEEDED,
            "activation_id": activation_id,
            "assignment_id": assignment_id,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _execute_assign_ring(
        self,
        plan: RolloutPlan,
        step: RolloutStep,
        actor_id: str,
        context: RunContext,
    ) -> RolloutStep:
        """Execute an ASSIGN_RING step: find activation and assign to ring."""
        svc = self._release_service

        # Find the most recent activation for this environment
        activation_id = step.activation_id
        if not activation_id:
            activations = await svc.activation_store.list(environment=step.environment)
            if activations:
                # Get the most recent active activation
                active = [a for a in activations if getattr(a, "status", None) == "active"]
                if active:
                    activation_id = getattr(active[-1], "activation_id", None)
                else:
                    activation_id = getattr(activations[-1], "activation_id", None)

        if not activation_id:
            raise ValueError(
                f"No activation found for environment '{step.environment}' "
                f"in step '{step.step_id}'"
            )

        if not step.ring_name:
            raise ValueError(f"ASSIGN_RING step '{step.step_id}' requires ring_name")

        assignment = await svc.assign_activation_to_ring(
            environment=step.environment,
            ring_name=step.ring_name,
            activation_id=activation_id,
            assigned_by=actor_id,
            context=context,
            reason=f"Rollout plan {plan.rollout_id} step {step.step_id}",
        )
        assignment_id = getattr(assignment, "assignment_id", None)

        return step.model_copy(update={
            "status": RolloutStepStatus.SUCCEEDED,
            "activation_id": activation_id,
            "assignment_id": assignment_id,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _execute_canary_eval(
        self,
        plan: RolloutPlan,
        step: RolloutStep,
        actor_id: str,
        context: RunContext,
    ) -> RolloutStep:
        """Execute a CANARY_EVAL step: run eval suite against environment/ring."""
        if self._eval_runner is None:
            return step.model_copy(update={
                "status": RolloutStepStatus.FAILED,
                "error": {"type": "no_eval_runner", "message": "No eval runner configured"},
                "completed_at": datetime.now(timezone.utc),
            })

        if not step.eval_suite:
            return step.model_copy(update={
                "status": RolloutStepStatus.FAILED,
                "error": {"type": "missing_eval_suite", "message": "CANARY_EVAL step requires eval_suite"},
                "completed_at": datetime.now(timezone.utc),
            })

        # Run the eval suite
        from agent_app.evals.loader import load_eval_suite
        suite = load_eval_suite(step.eval_suite)
        result = await self._eval_runner.run_suite(suite)

        if result.passed:
            return step.model_copy(update={
                "status": RolloutStepStatus.SUCCEEDED,
                "completed_at": datetime.now(timezone.utc),
            })
        else:
            return step.model_copy(update={
                "status": RolloutStepStatus.FAILED,
                "error": {
                    "type": "eval_failed",
                    "message": f"Eval suite '{step.eval_suite}' failed",
                    "details": {
                        "passed": result.passed,
                        "total": result.total,
                        "failures": result.failures if hasattr(result, "failures") else 0,
                    },
                },
                "completed_at": datetime.now(timezone.utc),
            })

    async def _execute_promote_ring(
        self,
        plan: RolloutPlan,
        step: RolloutStep,
        actor_id: str,
        context: RunContext,
    ) -> RolloutStep:
        """Execute a PROMOTE_RING step: promote from one ring to another."""
        svc = self._release_service

        from_ring = step.from_ring
        to_ring = step.to_ring
        if not from_ring or not to_ring:
            raise ValueError(
                f"PROMOTE_RING step '{step.step_id}' requires both from_ring and to_ring"
            )

        assignment = await svc.promote_canary_to_stable(
            environment=step.environment,
            canary_ring=from_ring,
            stable_ring=to_ring,
            promoted_by=actor_id,
            context=context,
            reason=f"Rollout plan {plan.rollout_id} step {step.step_id}",
        )
        assignment_id = getattr(assignment, "assignment_id", None)

        return step.model_copy(update={
            "status": RolloutStepStatus.SUCCEEDED,
            "assignment_id": assignment_id,
            "completed_at": datetime.now(timezone.utc),
        })
