"""Runtime policy rule model — defines configurable enforcement rules.

Phase 38: Lightweight rule engine for tool/approval governance.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.governance.policy_rollout_approval import RolloutApprovalPolicy


class RuntimePolicyRuleStatus(str, Enum):
    """Status of a runtime policy rule."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class RuntimePolicyEffect(str, Enum):
    """Effect of a runtime policy rule when matched."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RuntimePolicyRule(BaseModel):
    """A configurable runtime policy rule."""

    rule_id: str  # rpr_ prefix
    name: str
    action_type: PolicyActionType
    effect: RuntimePolicyEffect
    status: RuntimePolicyRuleStatus = RuntimePolicyRuleStatus.ENABLED

    tool_name: str | None = None
    risk_level: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)

    approval_policy: RolloutApprovalPolicy | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rule_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("rpr_"):
            raise ValueError("rule_id must use rpr_ prefix")
        return v
