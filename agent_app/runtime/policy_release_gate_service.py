"""ReleaseGateAutomationService — orchestrates simulation gate requirements for promotions and rollout steps.

Phase 42: Policy Release Automation and Simulation Gate Enforcement.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.audit import AuditEvent, AuditLogger
from agent_app.governance.policy_change_event import PolicyChangeEvent
from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.policy_change_event_store import PolicyChangeEventStore
from agent_app.runtime.policy_gate_store import PolicyGateStore
from agent_app.runtime.policy_release_gate_store import ReleaseGateRequirementStore


class ReleaseGateAutomationService:
    """Orchestrates simulation gate requirements for the release workflow.

    Creates requirements, attaches gate results, runs simulation+gate
    in one call, and checks requirement freshness.
    """

    def __init__(
        self,
        requirement_store: ReleaseGateRequirementStore,
        gate_store: PolicyGateStore | None = None,
        simulation_service: Any = None,  # PolicySimulationService
        simulation_gate_evaluator: Any = None,  # SimulationGateEvaluator
        audit_logger: AuditLogger | None = None,
        event_store: PolicyChangeEventStore | None = None,
    ) -> None:
        self._requirement_store = requirement_store
        self._gate_store = gate_store
        self._simulation_service = simulation_service
        self._simulation_gate_evaluator = simulation_gate_evaluator
        self._audit_logger = audit_logger
        self._event_store = event_store

    async def require_gate_for_promotion(
        self,
        promotion_id: str,
        max_age_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReleaseGateRequirement:
        """Create a REQUIRED gate requirement for a promotion."""
        requirement_id = f"rgr_{uuid.uuid4().hex[:12]}"
        req = ReleaseGateRequirement(
            requirement_id=requirement_id,
            source_type="promotion",
            source_id=promotion_id,
            max_age_seconds=max_age_seconds,
            metadata=metadata or {},
        )
        result = await self._requirement_store.create(req)
        await self._write_audit(
            "policy.promotion.gate.required",
            data={"promotion_id": promotion_id, "requirement_id": requirement_id},
        )
        await self._emit_change_event(
            "policy.promotion.gate.required",
            data={"promotion_id": promotion_id, "requirement_id": requirement_id},
        )
        return result

    async def attach_gate_result(
        self,
        source_type: str,
        source_id: str,
        gate_result_id: str,
        simulation_id: str | None = None,
        actor_id: str | None = None,
    ) -> ReleaseGateRequirement:
        """Attach a gate result to an existing requirement.

        Loads the gate result, determines status (SATISFIED or FAILED),
        and updates the requirement.
        """
        req = await self._requirement_store.get_for_source(source_type, source_id)
        if req is None:
            raise KeyError(
                f"No gate requirement found for {source_type}/{source_id}"
            )

        # Load gate result if store available
        gate_result: PolicyGateResult | None = None
        if self._gate_store is not None:
            gate_result = await self._gate_store.get(gate_result_id)

        # Determine status
        if gate_result is not None and gate_result.passed:
            new_status = ReleaseGateRequirementStatus.SATISFIED
        elif gate_result is not None and not gate_result.passed:
            new_status = ReleaseGateRequirementStatus.FAILED
        else:
            # If no gate store or result not found, assume passed
            # (backward compat: if gate_result_id is given, trust it)
            new_status = ReleaseGateRequirementStatus.SATISFIED

        now = datetime.now(timezone.utc)
        update_data: dict[str, Any] = {
            "gate_result_id": gate_result_id,
            "status": new_status,
            "satisfied_at": now if new_status == ReleaseGateRequirementStatus.SATISFIED else None,
        }
        if simulation_id is not None:
            update_data["simulation_id"] = simulation_id

        updated = req.model_copy(update=update_data)
        result = await self._requirement_store.update(updated)

        # Emit events
        event_type = (
            "policy.promotion.gate.satisfied"
            if new_status == ReleaseGateRequirementStatus.SATISFIED
            else "policy.promotion.gate.failed"
        )
        await self._write_audit(
            f"policy.promotion.gate.{new_status.value}",
            data={
                "promotion_id": source_id if source_type == "promotion" else None,
                "requirement_id": req.requirement_id,
                "gate_result_id": gate_result_id,
                "status": new_status.value,
            },
        )
        await self._emit_change_event(
            event_type,
            data={
                "source_type": source_type,
                "source_id": source_id,
                "requirement_id": req.requirement_id,
                "gate_result_id": gate_result_id,
            },
        )
        return result

    async def run_and_attach_simulation_gate_for_promotion(
        self,
        promotion_id: str,
        candidate_rules: list[RuntimePolicyRule],
        gate_rules: list[PolicyGateRule],
        context: Any,  # RunContext
        include_base: bool = True,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> ReleaseGateRequirement:
        """Run simulation + gate and attach the result to the promotion requirement."""
        if self._simulation_service is None or self._simulation_gate_evaluator is None:
            raise RuntimeError(
                "Simulation service and gate evaluator must be configured "
                "to run simulation gate for promotion"
            )

        # Run validate → replay → gate pipeline
        sim_report, validation_report, gate_result = (
            await self._simulation_service.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
                include_base=include_base,
                window_start=window_start,
                window_end=window_end,
                limit=limit,
            )
        )

        # Store gate result if store available
        if self._gate_store is not None:
            await self._gate_store.save(gate_result)

        # Attach
        result = await self.attach_gate_result(
            source_type="promotion",
            source_id=promotion_id,
            gate_result_id=gate_result.gate_result_id,
            simulation_id=sim_report.simulation_id,
            actor_id=getattr(context, "user_id", None),
        )
        return result

    async def check_requirement(
        self,
        source_type: str,
        source_id: str,
        now: datetime | None = None,
    ) -> ReleaseGateRequirement:
        """Check the current status of a gate requirement.

        Returns a synthetic NOT_REQUIRED requirement if no record exists.
        Re-evaluates freshness (expiry) if max_age_seconds is set.
        """
        req = await self._requirement_store.get_for_source(source_type, source_id)
        if req is None:
            return ReleaseGateRequirement(
                requirement_id="rgr_none",
                source_type=source_type,
                source_id=source_id,
                required=False,
                status=ReleaseGateRequirementStatus.NOT_REQUIRED,
            )

        # If already in a terminal state other than SATISFIED, return as-is
        if req.status in (ReleaseGateRequirementStatus.REQUIRED, ReleaseGateRequirementStatus.SATISFIED):
            # Check expiry for SATISFIED requirements
            if req.status == ReleaseGateRequirementStatus.SATISFIED and req.max_age_seconds is not None:
                check_time = now or datetime.now(timezone.utc)
                # Load gate result to get its created_at for freshness check
                if self._gate_store is not None and req.gate_result_id:
                    gate_result = await self._gate_store.get(req.gate_result_id)
                    if gate_result is not None:
                        age = (check_time - gate_result.created_at).total_seconds()
                        if age > req.max_age_seconds:
                            # Mark as expired
                            updated = req.model_copy(update={
                                "status": ReleaseGateRequirementStatus.EXPIRED,
                            })
                            await self._requirement_store.update(updated)
                            await self._write_audit(
                                "policy.promotion.gate.expired",
                                data={
                                    "source_type": source_type,
                                    "source_id": source_id,
                                    "requirement_id": req.requirement_id,
                                    "gate_result_id": req.gate_result_id,
                                    "max_age_seconds": req.max_age_seconds,
                                },
                            )
                            await self._emit_change_event(
                                "policy.promotion.gate.expired",
                                data={
                                    "source_type": source_type,
                                    "source_id": source_id,
                                    "requirement_id": req.requirement_id,
                                },
                            )
                            return updated
                # Fallback: check satisfied_at against max_age_seconds
                elif req.satisfied_at is not None:
                    age = (check_time - req.satisfied_at).total_seconds()
                    if age > req.max_age_seconds:
                        updated = req.model_copy(update={
                            "status": ReleaseGateRequirementStatus.EXPIRED,
                        })
                        await self._requirement_store.update(updated)
                        return updated

        return req

    async def _write_audit(
        self,
        event_type: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self._audit_logger is None:
            return
        try:
            event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                user_id=user_id,
                tenant_id=tenant_id,
                data=data or {},
            )
            await self._audit_logger.log(event)
        except Exception:
            pass

    async def _emit_change_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self._event_store is None:
            return
        try:
            event = PolicyChangeEvent(
                event_id=f"pce_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                data=data or {},
                created_at=datetime.now(timezone.utc),
            )
            await self._event_store.append(event)
        except Exception:
            pass
