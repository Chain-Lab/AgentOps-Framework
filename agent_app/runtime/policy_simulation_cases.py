"""Audit-to-simulation case extraction — converts enforcement audit events into simulation cases.

Phase 40: Historical audit replay for policy validation.
"""
from __future__ import annotations

import uuid
from typing import Any

from agent_app.governance.policy_simulation import PolicySimulationCase

# Audit event types that map to enforcement decisions
_ENFORCEMENT_EVENT_MAP: dict[str, str] = {
    "policy.runtime.enforcement.allowed": "allowed",
    "policy.runtime.enforcement.denied": "denied",
    "policy.runtime.enforcement.approval_required": "approval_required",
}

_EVALUATED_EVENT_TYPE = "policy.runtime.evaluated"


def audit_event_to_simulation_case(
    event: Any,
) -> PolicySimulationCase | None:
    """Convert a runtime enforcement audit event into a simulation case.

    Supports:
      - policy.runtime.enforcement.{allowed,denied,approval_required}
      - policy.runtime.evaluated (extracts status from data)

    Returns None for unsupported event types.
    Tolerates missing fields — sets them to None/empty.
    """
    data = getattr(event, "data", None) or {}
    event_type = getattr(event, "event_type", None)

    if event_type in _ENFORCEMENT_EVENT_MAP:
        baseline_status = _ENFORCEMENT_EVENT_MAP[event_type]
    elif event_type == _EVALUATED_EVENT_TYPE:
        baseline_status = data.get("status")
        if baseline_status is None:
            return None
    else:
        return None

    case_id = f"psc_{uuid.uuid4().hex[:12]}"

    return PolicySimulationCase(
        case_id=case_id,
        action_type=data.get("action_type", "unknown"),
        subject=data.get("subject"),
        tool_name=data.get("tool_name"),
        risk_level=data.get("risk_level"),
        actor_id=data.get("actor_id"),
        user_id=data.get("user_id"),
        tenant_id=data.get("tenant_id"),
        roles=data.get("roles", []) or [],
        permissions=data.get("permissions", []) or [],
        baseline_status=baseline_status,
        metadata={k: v for k, v in data.items() if k not in {
            "action_type", "subject", "tool_name", "risk_level",
            "actor_id", "user_id", "tenant_id", "roles",
            "permissions", "status",
        }},
    )
