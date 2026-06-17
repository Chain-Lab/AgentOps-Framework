"""RolloutGateAutomationService — orchestrates simulation gate evaluation per rollout step.

Phase 43: Automates gate evaluation for rollout steps with DISABLED/MANUAL/AUTO modes
and BLOCK/FAIL/SKIP failure actions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.policy_rollout import (
    RolloutGateFailureAction,
    RolloutGateMode,
    RolloutPlan,
    RolloutStep,
)
from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
from agent_app.governance.policy_rollout_gate import (
    RolloutGateExecutionResult,
    RolloutGateExecutionStatus,
)
from agent_app.governance.policy_rollout_history import RolloutHistoryEventType


class RolloutGateAutomationService:
    """Orchestrates simulation gate evaluation for rollout steps.

    Delegates to ReleaseGateAutomationService for requirement management
    and simulation execution. Provides ensure/run/check step gate methods
    that the RolloutService calls during step execution.
    """

    def __init__(
        self,
        release_gate_automation_service: Any,
        simulation_service: Any | None = None,
        simulation_gate_evaluator: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
        default_gate_rules: list[Any] | None = None,
        default_max_age_seconds: int | None = None,
        history_recorder: Any | None = None,
    ) -> None:
        self._release_gate = release_gate_automation_service
        self._simulation_service = simulation_service
        self._simulation_gate_evaluator = simulation_gate_evaluator
        self._audit_logger = audit_logger
        self._event_store = event_store
        self._default_gate_rules = default_gate_rules or []
        self._default_max_age_seconds = default_max_age_seconds
        self._history_recorder = history_recorder

    async def ensure_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        context: RunContext,
    ) -> RolloutGateExecutionResult:
        """Ensure the step's gate requirement is satisfied.

        * If gate is not required (DISABLED), return NOT_REQUIRED.
        * If existing attached requirement is SATISFIED and fresh, return SATISFIED.
        * If MANUAL mode, return BLOCKED when missing/failed/expired.
        * If AUTO mode, run simulation gate and attach result.
        * If gate passes, return SATISFIED.
        * If gate fails, apply simulation_gate_failure_action.
        * If error occurs, return ERROR.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Gate not required
        if not step.requires_simulation_gate and step.simulation_gate_mode == RolloutGateMode.DISABLED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.NOT_REQUIRED,
                action_taken="gate_disabled",
            )

        # Check existing requirement
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "check_error", "message": str(exc)},
            )

        # Already satisfied
        if existing.status == ReleaseGateRequirementStatus.SATISFIED:
            await self._record_history(
                rollout.rollout_id,
                RolloutHistoryEventType.GATE_SATISFIED,
                step_id=step.step_id,
            )
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                gate_result_id=existing.gate_result_id,
                simulation_id=existing.simulation_id,
                action_taken="existing_satisfied",
            )

        # MANUAL mode — cannot auto-run, block
        if step.simulation_gate_mode == RolloutGateMode.MANUAL:
            await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
            await self._record_history(
                rollout.rollout_id,
                RolloutHistoryEventType.GATE_BLOCKED,
                step_id=step.step_id,
            )
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="manual_blocked",
                reason=f"Gate is {existing.status.value}, manual mode requires explicit gate result",
            )

        # AUTO mode — run simulation
        if step.simulation_gate_mode == RolloutGateMode.AUTO:
            try:
                run_result = await self.run_step_gate(rollout, step, context)
            except Exception as exc:
                await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
                return self._make_result(
                    rollout, step, RolloutGateExecutionStatus.ERROR,
                    error={"type": "run_error", "message": str(exc)},
                    action_taken="auto_error",
                )

            if run_result.status == RolloutGateExecutionStatus.SATISFIED:
                await self._emit_events(rollout, step, "policy.rollout.gate.satisfied", context)
                await self._record_history(
                    rollout.rollout_id,
                    RolloutHistoryEventType.GATE_SATISFIED,
                    step_id=step.step_id,
                )
                return run_result

            # Gate failed — emit appropriate event
            event_map = {
                RolloutGateExecutionStatus.FAILED: "policy.rollout.gate.failed",
                RolloutGateExecutionStatus.BLOCKED: "policy.rollout.gate.blocked",
                RolloutGateExecutionStatus.SKIPPED: "policy.rollout.gate.skipped",
            }
            event_type = event_map.get(run_result.status, "policy.rollout.gate.blocked")
            await self._emit_events(rollout, step, event_type, context)
            # Record history for gate failure outcomes
            history_event_map = {
                RolloutGateExecutionStatus.FAILED: RolloutHistoryEventType.GATE_FAILED,
                RolloutGateExecutionStatus.BLOCKED: RolloutHistoryEventType.GATE_BLOCKED,
                RolloutGateExecutionStatus.SKIPPED: RolloutHistoryEventType.GATE_SKIPPED,
            }
            history_event = history_event_map.get(run_result.status, RolloutHistoryEventType.GATE_BLOCKED)
            await self._record_history(
                rollout.rollout_id,
                history_event,
                step_id=step.step_id,
            )
            return run_result

        # Fallback: block if gate is in a bad state
        await self._emit_events(rollout, step, "policy.rollout.gate.blocked", context)
        await self._record_history(
            rollout.rollout_id,
            RolloutHistoryEventType.GATE_BLOCKED,
            step_id=step.step_id,
        )
        return self._make_result(
            rollout, step, RolloutGateExecutionStatus.BLOCKED,
            requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
            action_taken="fallback_blocked",
            reason=f"Gate is {existing.status.value}",
        )

    async def run_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        context: RunContext,
    ) -> RolloutGateExecutionResult:
        """Run simulation gate for a step and attach the result.

        Uses candidate rules and gate rules from the step (or defaults).
        Creates or reuses a release gate requirement for the step.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Resolve rules: step-level or defaults
        candidate_rules = step.simulation_candidate_rules or []
        gate_rules = step.simulation_gate_rules or self._default_gate_rules

        if not candidate_rules or not gate_rules:
            raise ValueError(
                "candidate_rules and gate_rules must be provided either on the step "
                "or as defaults to run simulation gate"
            )

        # Create requirement if not exists
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id,
            )
            if existing.requirement_id == "rgr_none" or existing.status == ReleaseGateRequirementStatus.NOT_REQUIRED:
                await self._release_gate.require_gate_for_promotion(
                    promotion_id=source_id,
                    max_age_seconds=step.simulation_gate_max_age_seconds or self._default_max_age_seconds,
                    metadata={"rollout_id": rollout.rollout_id, "step_id": step.step_id},
                )
        except Exception:
            pass  # Requirement may already exist

        await self._emit_events(rollout, step, "policy.rollout.gate.run", context)

        # Run simulation + gate
        try:
            cast_candidates = self._cast_candidate_rules(candidate_rules)
            cast_gates = self._cast_gate_rules(gate_rules)

            req = await self._release_gate.run_and_attach_simulation_gate_for_promotion(
                promotion_id=source_id,
                candidate_rules=cast_candidates,
                gate_rules=cast_gates,
                context=context,
                include_base=step.simulation_include_base,
                window_start=step.simulation_window_start,
                window_end=step.simulation_window_end,
                limit=step.simulation_limit,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "simulation_error", "message": str(exc)},
                action_taken="simulation_failed",
            )

        # Determine result based on requirement status and failure action
        if req.status == ReleaseGateRequirementStatus.SATISFIED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=req.requirement_id,
                gate_result_id=req.gate_result_id,
                simulation_id=req.simulation_id,
                action_taken="auto_passed",
            )

        # Gate failed — apply failure action
        failure_action = step.simulation_gate_failure_action
        if failure_action == RolloutGateFailureAction.FAIL:
            status = RolloutGateExecutionStatus.FAILED
            action = "auto_failed"
        elif failure_action == RolloutGateFailureAction.SKIP:
            status = RolloutGateExecutionStatus.SKIPPED
            action = "auto_skipped"
        else:
            status = RolloutGateExecutionStatus.BLOCKED
            action = "auto_blocked"

        return self._make_result(
            rollout, step, status,
            requirement_id=req.requirement_id,
            gate_result_id=req.gate_result_id,
            simulation_id=req.simulation_id,
            action_taken=action,
            reason=f"Gate failed with status {req.status.value}",
        )

    async def check_step_gate(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        now: datetime | None = None,
    ) -> RolloutGateExecutionResult:
        """Check step gate status without running simulation.

        Uses existing requirement if present. Checks max age / failed /
        expired / satisfied status. Does not run simulation.
        """
        source_id = f"{rollout.rollout_id}:{step.step_id}"

        # Gate not required
        if not step.requires_simulation_gate and step.simulation_gate_mode == RolloutGateMode.DISABLED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.NOT_REQUIRED,
                action_taken="gate_disabled",
            )

        # Check existing requirement
        try:
            existing = await self._release_gate.check_requirement(
                "rollout_step", source_id, now=now,
            )
        except Exception as exc:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.ERROR,
                error={"type": "check_error", "message": str(exc)},
            )

        if existing.status == ReleaseGateRequirementStatus.NOT_REQUIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                action_taken="no_requirement",
                reason="No gate requirement found for step",
            )

        if existing.status == ReleaseGateRequirementStatus.SATISFIED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.SATISFIED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                gate_result_id=existing.gate_result_id,
                simulation_id=existing.simulation_id,
                action_taken="existing_satisfied",
            )

        if existing.status == ReleaseGateRequirementStatus.REQUIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="no_result_attached",
                reason="Gate is required but no result attached",
            )

        if existing.status == ReleaseGateRequirementStatus.FAILED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="gate_failed",
                reason="Gate result indicates failure",
            )

        if existing.status == ReleaseGateRequirementStatus.EXPIRED:
            return self._make_result(
                rollout, step, RolloutGateExecutionStatus.BLOCKED,
                requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
                action_taken="gate_expired",
                reason="Gate result has expired",
            )

        return self._make_result(
            rollout, step, RolloutGateExecutionStatus.BLOCKED,
            requirement_id=existing.requirement_id if existing.requirement_id != "rgr_none" else None,
            reason=f"Gate status: {existing.status.value}",
        )

    # --- Helpers ---

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
            pass  # History recording failure must not break gate execution

    def _make_result(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        status: RolloutGateExecutionStatus,
        *,
        requirement_id: str | None = None,
        gate_result_id: str | None = None,
        simulation_id: str | None = None,
        action_taken: str | None = None,
        reason: str | None = None,
        error: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RolloutGateExecutionResult:
        return RolloutGateExecutionResult(
            execution_id=f"rge_{uuid.uuid4().hex[:12]}",
            rollout_id=rollout.rollout_id,
            step_id=step.step_id,
            status=status,
            requirement_id=requirement_id,
            gate_result_id=gate_result_id,
            simulation_id=simulation_id,
            action_taken=action_taken,
            reason=reason,
            error=error,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

    async def _emit_events(
        self,
        rollout: RolloutPlan,
        step: RolloutStep,
        event_type: str,
        context: RunContext | None = None,
    ) -> None:
        """Emit audit and change events (best-effort)."""
        data = {
            "rollout_id": rollout.rollout_id,
            "step_id": step.step_id,
        }
        # Audit event
        if self._audit_logger is not None:
            try:
                from agent_app.governance.audit import AuditEvent
                event = AuditEvent(
                    event_id=f"ae_{uuid.uuid4().hex[:12]}",
                    event_type=event_type,
                    user_id=getattr(context, "user_id", None) if context else None,
                    tenant_id=getattr(context, "tenant_id", None) if context else None,
                    data=data,
                )
                await self._audit_logger.log(event)
            except Exception:
                pass

        # Change event
        if self._event_store is not None:
            try:
                from agent_app.governance.policy_change_event import PolicyChangeEvent
                event = PolicyChangeEvent(
                    event_id=f"pce_{uuid.uuid4().hex[:12]}",
                    event_type=event_type,
                    bundle_id=rollout.bundle_id,
                    environment=step.environment,
                    ring_name=step.ring_name,
                    actor_id=getattr(context, "user_id", None) if context else None,
                    data=data,
                    created_at=datetime.now(timezone.utc),
                )
                await self._event_store.append(event)
            except Exception:
                pass

    @staticmethod
    def _cast_candidate_rules(rules: list[Any]) -> list[Any]:
        """Cast candidate rule dicts to RuntimePolicyRule if needed."""
        from agent_app.governance.runtime_policy import RuntimePolicyRule
        result: list[Any] = []
        for r in rules:
            if isinstance(r, RuntimePolicyRule):
                result.append(r)
            elif isinstance(r, dict):
                result.append(RuntimePolicyRule(**r))
            else:
                result.append(r)
        return result

    @staticmethod
    def _cast_gate_rules(rules: list[Any]) -> list[Any]:
        """Cast gate rule dicts to PolicyGateRule if needed."""
        from agent_app.governance.policy_gate import PolicyGateRule
        result: list[Any] = []
        for r in rules:
            if isinstance(r, PolicyGateRule):
                result.append(r)
            elif isinstance(r, dict):
                result.append(PolicyGateRule(**r))
            else:
                result.append(r)
        return result
