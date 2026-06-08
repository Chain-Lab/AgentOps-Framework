"""ToolSpec — metadata-rich tool definition used by the framework."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """Rich metadata for a registered tool.

    Attributes:
        name: Fully-qualified tool name using dot-notation namespace
              (e.g. "order.query").
        description: What the tool does, shown to the model.
        namespace: Logical grouping (e.g. "order"). Derived from *name* when
                   not provided explicitly.
        risk_level: One of "low", "medium", "high", "critical".
        requires_approval: If True, the framework pauses before executing.
        permissions: List of permission strings required (e.g. ["order:read"]).
        timeout_seconds: Max execution time before the run is cancelled.
        retry: Retry configuration dict (reserved for future use).
        audit_enabled: Whether tool calls are recorded in audit logs.
        tags: Free-form tags for filtering / grouping.
    """

    name: str = Field(..., description="Fully-qualified tool name (e.g. order.query)")
    description: str = Field(..., description="Tool description for the model")
    namespace: str = Field(
        default="", description="Logical namespace, derived from name if empty"
    )
    risk_level: str = Field(
        default="low",
        description="Risk level: low | medium | high | critical",
    )
    requires_approval: bool = Field(
        default=False, description="Whether human approval is required"
    )
    permissions: list[str] = Field(
        default_factory=list, description="Required permissions"
    )
    timeout_seconds: int = Field(default=30, description="Execution timeout")
    retry: dict = Field(default_factory=dict, description="Retry config (reserved)")
    audit_enabled: bool = Field(default=True, description="Enable audit logging")
    tags: list[str] = Field(default_factory=list, description="Free-form tags")

    def model_post_init(self, __context: object) -> None:
        if not self.namespace:
            parts = self.name.rsplit(".", 1)
            self.namespace = parts[0] if len(parts) == 2 else self.name
