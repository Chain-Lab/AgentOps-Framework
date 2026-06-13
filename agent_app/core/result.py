"""AppRunResult — unified run result returned by AgentApp.run()."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkflowStep(BaseModel):
    """A single step in a workflow execution trace.

    Attributes:
        step_id: Unique step identifier.
        step_type: Category of step (e.g. "routing", "agent", "tool").
        agent_name: Agent that performed this step (if applicable).
        tool_name: Tool invoked in this step (if applicable).
        input_summary: Brief description of the input.
        output_summary: Brief description of the output.
        status: Step outcome ("completed", "failed", "skipped").
        metadata: Extra structured data.
    """

    step_id: str = Field(..., description="Unique step identifier")
    step_type: str = Field(..., description="Step category")
    agent_name: str | None = Field(default=None, description="Agent name")
    tool_name: str | None = Field(default=None, description="Tool name")
    input_summary: str | None = Field(default=None, description="Input summary")
    output_summary: str | None = Field(default=None, description="Output summary")
    status: str = Field(..., description="Step outcome")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra structured data"
    )


class WorkflowTrace(BaseModel):
    """Structured execution trace for a workflow run.

    Attributes:
        workflow_name: Name of the executed workflow.
        workflow_type: Topology type (single, handoff, orchestrator).
        entry_agent: Entry agent name.
        steps: Ordered list of execution steps.
    """

    workflow_name: str | None = Field(default=None, description="Workflow name")
    workflow_type: str | None = Field(default=None, description="Workflow type")
    entry_agent: str | None = Field(default=None, description="Entry agent")
    steps: list[WorkflowStep] = Field(
        default_factory=list, description="Execution steps"
    )


class AppRunResult(BaseModel):
    """Standardised result of an Agent application run.

    Attributes:
        run_id: Unique run identifier (matches RunContext.run_id).
        status: "completed" | "interrupted" | "failed".
        final_output: The agent's final response (str or structured dict).
        interruptions: List of pending interruptions (e.g. approval requests).
        tool_calls: Record of tool invocations during this run.
        agent_calls: Record of specialist agent invocations (orchestrator).
        handoffs: Record of agent handoffs during this run.
        usage: Token / model usage summary from the underlying SDK.
        cost: Cost estimate (reserved for future implementation).
        latency_ms: Total wall-clock latency in milliseconds.
        trace_id: Underlying SDK trace identifier.
        error: Error details when status is "failed".
    """

    run_id: str = Field(..., description="Unique run identifier")
    status: str = Field(
        ..., description="completed | interrupted | failed"
    )
    final_output: str | dict | None = Field(
        default=None, description="Agent final response"
    )
    interruptions: list[dict] = Field(
        default_factory=list, description="Pending interruptions"
    )
    tool_calls: list[dict] = Field(
        default_factory=list, description="Tool call records"
    )
    agent_calls: list[dict] = Field(
        default_factory=list, description="Specialist agent call records (orchestrator)"
    )
    handoffs: list[dict] = Field(
        default_factory=list, description="Handoff records"
    )
    usage: dict = Field(default_factory=dict, description="Token usage summary")
    cost: dict = Field(default_factory=dict, description="Cost estimate")
    latency_ms: int = Field(default=0, description="Wall-clock latency (ms)")
    trace_id: str | None = Field(default=None, description="SDK trace ID")
    error: dict | None = Field(default=None, description="Error details")
    workflow_trace: WorkflowTrace | None = Field(
        default=None, description="Structured workflow execution trace"
    )
    # Phase 10: backend-specific state (e.g. OpenAI RunState JSON)
    backend_state: dict[str, Any] = Field(
        default_factory=dict, description="Backend-specific state for resume"
    )
    # Phase 12: structured run events
    trace_events: list[Any] = Field(
        default_factory=list,
        description="Structured events recorded during this run",
    )
    # Phase 13: DAG node execution results
    node_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-node results for DAG workflow execution",
    )
    # Phase 34: additional metadata (policy info, etc.)
    metadata: dict[str, object] = Field(
        default_factory=dict, description="Additional metadata"
    )
