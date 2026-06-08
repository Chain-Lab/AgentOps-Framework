"""AgentSpec — declarative agent definition, compiled into an OpenAI Agents SDK Agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentSpec(BaseModel):
    """Describes an Agent's configuration without depending on the OpenAI SDK.

    Attributes:
        name: Unique identifier for this agent.
        description: Human-readable description of the agent's purpose.
        model: Model name override (e.g. "gpt-4o"). Falls back to the app default.
        instructions: Prompt text or path to a prompt file.
        tools: Names of registered tools this agent may call.
        handoffs: Names of other agents this agent may hand off to.
        guardrails: Names of guardrail policies to apply.
        output_schema: Optional Pydantic model class for structured output.
        model_settings: Extra kwargs forwarded to the underlying SDK Agent.
        metadata: Free-form key/value pairs for tooling, auditing, etc.
        raw_agent_kwargs: Escape hatch — forwarded verbatim to agents.Agent().
    """

    name: str = Field(..., description="Unique agent identifier")
    description: str | None = Field(default=None, description="Agent purpose")
    model: str | None = Field(default=None, description="Model override")
    instructions: str = Field(
        ..., description="System prompt text or file path"
    )
    tools: list[str] = Field(default_factory=list, description="Registered tool names")
    handoffs: list[str] = Field(default_factory=list, description="Handoff target names")
    guardrails: list[str] = Field(default_factory=list, description="Guardrail policy names")
    output_schema: type | None = Field(
        default=None, description="Pydantic model for structured output"
    )
    model_settings: dict[str, object] = Field(
        default_factory=dict, description="Extra SDK model settings"
    )
    metadata: dict[str, object] = Field(
        default_factory=dict, description="Free-form metadata"
    )
    raw_agent_kwargs: dict[str, object] = Field(
        default_factory=dict, description="Passthrough kwargs to agents.Agent"
    )

    model_config = {"arbitrary_types_allowed": True}
