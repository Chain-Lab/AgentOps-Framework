"""Risk module — risk level and approval status enumerations."""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    """Tool / action risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    """Lifecycle of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def requires_tool_approval(
    risk_level: str | RiskLevel,
    requires_approval: bool = False,
) -> bool:
    """Return True when a tool call must pause for human approval."""
    if requires_approval:
        return True
    normalized = str(risk_level).lower()
    return normalized in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}
