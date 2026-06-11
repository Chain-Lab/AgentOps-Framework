"""Policy Simulator — offline policy evaluation without side effects.

Phase 24: Allows operators to test policy outcomes without executing
tools, creating approvals, or writing sessions.

Architecture:
  - PolicySimulationInput: structured input for simulation
  - PolicySimulationResult: decision + optional explain trace
  - PolicySimulator: wraps a PolicyEngine for offline evaluation
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_app.governance.policy import (
    PolicyAction,
    PolicyDecision,
    PolicyDecisionTrace,
    PolicyEngine,
    PolicyEvaluationContext,
)


class PolicySimulationInput(BaseModel):
    """Input for a policy simulation run.

    All fields mirror PolicyEvaluationContext but are self-contained
    for CLI / API use without a live run context.
    """

    tool_name: str = Field(..., description="Tool name to simulate")
    risk_level: str = Field(default="low", description="Tool risk level")
    workflow_type: str | None = Field(default=None)
    agent_name: str | None = Field(default=None)
    target_agent: str | None = Field(default=None)
    user_id: str | None = Field(default=None)
    tenant_id: str | None = Field(default=None)
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicySimulationResult(BaseModel):
    """Result of a policy simulation."""

    decision: PolicyDecision = Field(..., description="Policy decision")
    trace: PolicyDecisionTrace | None = Field(
        default=None, description="Explain trace if requested"
    )


class PolicySimulator:
    """Offline policy simulator — evaluates policy without side effects.

    Does NOT:
    - Execute tool functions
    - Create approval requests
    - Write to session or state stores
    - Emit audit events (unless explicitly enabled)

    Args:
        policy_engine: The policy engine to simulate against.
        write_audit: If True, write simulation events to audit logger.
        audit_logger: Required when write_audit is True.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        write_audit: bool = False,
        audit_logger: Any = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.write_audit = write_audit
        self.audit_logger = audit_logger

    async def simulate(self, input_data: PolicySimulationInput) -> PolicySimulationResult:
        """Run a policy simulation.

        Args:
            input_data: The simulation input with tool/context info.

        Returns:
            PolicySimulationResult with decision and optional trace.
        """
        context = PolicyEvaluationContext(
            tool_name=input_data.tool_name,
            risk_level=input_data.risk_level,
            workflow_type=input_data.workflow_type,
            agent_name=input_data.agent_name,
            target_agent=input_data.target_agent,
            user_id=input_data.user_id,
            tenant_id=input_data.tenant_id,
            roles=list(input_data.roles),
            permissions=list(input_data.permissions),
            metadata=dict(input_data.metadata),
        )
        decision = await self.policy_engine.evaluate_tool_call(context)

        # Optional audit
        if self.write_audit and self.audit_logger is not None:
            import uuid
            from agent_app.governance.audit import AuditEvent
            await self.audit_logger.log(AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=None,
                event_type="policy.simulated",
                user_id=input_data.user_id,
                tenant_id=input_data.tenant_id,
                tool_name=input_data.tool_name,
                data={
                    "action": decision.action.value,
                    "reason": decision.reason,
                    "rule_name": decision.metadata.get("rule_name"),
                },
            ))

        return PolicySimulationResult(decision=decision)

    async def explain(self, input_data: PolicySimulationInput) -> PolicySimulationResult:
        """Run simulation and include explain trace.

        Args:
            input_data: The simulation input.

        Returns:
            PolicySimulationResult with decision and trace.
        """
        context = PolicyEvaluationContext(
            tool_name=input_data.tool_name,
            risk_level=input_data.risk_level,
            workflow_type=input_data.workflow_type,
            agent_name=input_data.agent_name,
            target_agent=input_data.target_agent,
            user_id=input_data.user_id,
            tenant_id=input_data.tenant_id,
            roles=list(input_data.roles),
            permissions=list(input_data.permissions),
            metadata=dict(input_data.metadata),
        )
        trace = await self.policy_engine.explain(context)
        decision = PolicyDecision(
            action=trace.action,
            allowed=(trace.action != PolicyAction.DENY),
            requires_approval=(trace.action == PolicyAction.REQUIRE_APPROVAL),
            reason=trace.reason,
            metadata={"rule_name": trace.rule_name},
        )
        return PolicySimulationResult(decision=decision, trace=trace)
