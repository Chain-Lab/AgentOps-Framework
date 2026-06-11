"""Policy replay context reconstruction.

Phase 28: rebuilds PolicyEvaluationContext from stored decision records
and trace metadata, tracking which fields were reconstructed vs. missing.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from agent_app.governance.policy import PolicyEvaluationContext, PolicyDecisionTrace


# ---------------------------------------------------------------------------
# Context Model
# ---------------------------------------------------------------------------

class PolicyReplayContext(BaseModel):
    """Reconstructed context for a policy replay.

    Attributes:
        decision_id: Source decision ID.
        user_id: Reconstructed user ID (if available).
        tenant_id: Reconstructed tenant ID (if available).
        roles: Reconstructed roles (if available).
        permissions: Reconstructed permissions (if available).
        tool_name: Tool name (required for replay).
        tool_arguments: Tool arguments from trace (if available).
        context_summary: Raw context_summary from the decision record.
        source: Where the context came from.
        missing_fields: Fields that could not be reconstructed.
        metadata: Additional metadata.
    """
    decision_id: str = Field(..., description="Source decision ID")
    user_id: str | None = Field(default=None, description="Reconstructed user ID")
    tenant_id: str | None = Field(default=None, description="Reconstructed tenant ID")
    roles: list[str] = Field(default_factory=list, description="Reconstructed roles")
    permissions: list[str] = Field(
        default_factory=list, description="Reconstructed permissions"
    )
    tool_name: str | None = Field(default=None, description="Tool name")
    tool_arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments"
    )
    context_summary: str | None = Field(
        default=None, description="Raw context summary from record"
    )
    source: str = Field(
        default="decision_record",
        description="Source of reconstruction",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Fields that could not be reconstructed",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


# ---------------------------------------------------------------------------
# Context Builder
# ---------------------------------------------------------------------------

class PolicyReplayContextBuilder:
    """Rebuilds PolicyEvaluationContext from decision records.

    Reconstruction priority:
    1. Structured fields on the decision record (user_id, tenant_id, etc.)
    2. Trace metadata (context_summary dict)
    3. Mark missing fields explicitly — do not guess
    """

    # Fields required for a successful replay
    REQUIRED_FIELDS: list[str] = ["tool_name"]

    def build(self, decision: PolicyDecisionTrace) -> PolicyReplayContext:
        """Build a PolicyReplayContext from a decision trace.

        Args:
            decision: The source policy decision trace.

        Returns:
            PolicyReplayContext with reconstructed fields and missing field list.
        """
        missing: list[str] = []
        ctx = decision.context_summary or {}
        metadata: dict[str, Any] = {}

        # 1. tool_name: check trace field first, then context_summary
        tool_name = decision.tool_name or ctx.get("tool_name")
        if not tool_name:
            missing.append("tool_name")

        # 2. tenant_id: from context_summary only (trace doesn't have it directly)
        tenant_id = ctx.get("tenant_id")
        if not tenant_id:
            missing.append("tenant_id")

        # 3. user_id: from context_summary only
        user_id = ctx.get("user_id")
        if not user_id:
            missing.append("user_id")

        # 4. roles: from context_summary only
        roles = list(ctx.get("roles", []))

        # 5. permissions: from context_summary only
        permissions = list(ctx.get("permissions", []))
        if not permissions:
            missing.append("permissions")

        # 6. tool_arguments: from context_summary
        tool_arguments = dict(ctx.get("tool_arguments", {}))

        # 7. Other context fields
        agent_name = ctx.get("agent_name")
        if agent_name:
            metadata["agent_name"] = agent_name
        workflow_name = ctx.get("workflow_name")
        if workflow_name:
            metadata["workflow_name"] = workflow_name
        workflow_type = ctx.get("workflow_type")
        if workflow_type:
            metadata["workflow_type"] = workflow_type
        source_agent = ctx.get("source_agent")
        if source_agent:
            metadata["source_agent"] = source_agent
        target_agent = ctx.get("target_agent")
        if target_agent:
            metadata["target_agent"] = target_agent

        return PolicyReplayContext(
            decision_id=decision.decision_id,
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            permissions=permissions,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            context_summary=json.dumps(decision.context_summary) if decision.context_summary else None,
            source="decision_record",
            missing_fields=missing,
            metadata=metadata,
        )

    def build_evaluation_context(
        self, decision: PolicyDecisionTrace
    ) -> PolicyEvaluationContext | None:
        """Build a PolicyEvaluationContext for use with the policy engine.

        Returns None if required fields are missing.

        Args:
            decision: The source policy decision trace.

        Returns:
            PolicyEvaluationContext ready for engine evaluation, or None.
        """
        replay_ctx = self.build(decision)

        if "tool_name" in replay_ctx.missing_fields:
            return None

        return PolicyEvaluationContext(
            run_id=decision.run_id,
            tool_name=replay_ctx.tool_name,
            workflow_name=replay_ctx.metadata.get("workflow_name"),
            workflow_type=replay_ctx.metadata.get("workflow_type"),
            agent_name=replay_ctx.metadata.get("agent_name"),
            source_agent=replay_ctx.metadata.get("source_agent"),
            target_agent=replay_ctx.metadata.get("target_agent"),
            user_id=replay_ctx.user_id,
            tenant_id=replay_ctx.tenant_id,
            roles=replay_ctx.roles,
            permissions=replay_ctx.permissions,
            metadata=replay_ctx.metadata,
        )
