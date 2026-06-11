"""RunContext — per-run execution context (user identity, tenant, session, etc.)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunContext(BaseModel):
    """Immutable context attached to a single run.

    Attributes:
        run_id: Unique identifier for this run.
        user_id: End-user identifier.
        tenant_id: Multi-tenant identifier.
        roles: List of role names for the user/agent.
        permissions: Granted permission strings.
        session_id: Optional conversation/session identifier.
        request_id: Optional upstream request identifier (for tracing).
        channel: Optional channel hint (e.g. "web", "api", "slack").
        metadata: Free-form key/value pairs for downstream middleware.
    """

    run_id: str = Field(..., description="Unique run identifier")
    user_id: str = Field(..., description="End-user identifier")
    tenant_id: str = Field(..., description="Tenant identifier")
    roles: list[str] = Field(default_factory=list, description="User/agent roles")
    permissions: list[str] = Field(
        default_factory=list, description="Granted permissions"
    )
    session_id: str | None = Field(
        default=None, description="Conversation / session identifier"
    )
    request_id: str | None = Field(
        default=None, description="Upstream request identifier"
    )
    channel: str | None = Field(default=None, description="Channel hint")
    metadata: dict[str, object] = Field(
        default_factory=dict, description="Free-form metadata"
    )
    # Phase 12: observability trace identifier
    trace_id: str | None = Field(default=None, description="Observability trace ID")
    # Phase 24: agent name for policy evaluation
    agent_name: str | None = Field(default=None, description="Executing agent name")
    # Phase 31: policy environment for runtime activation
    policy_environment: str | None = Field(
        default=None,
        description="Policy environment for runtime resolution (dev, staging, prod)",
    )
