"""Workflow — orchestrates how an agent run is executed."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agent_app.core.agent_spec import AgentSpec


class WorkflowType(StrEnum):
    """Supported workflow topologies."""

    SINGLE = "single"
    HANDOFF = "handoff"
    ORCHESTRATOR = "orchestrator"
    DAG = "dag"


class Workflow(BaseModel):
    """Describes an execution flow.

    In Phase 1 only ``WorkflowType.SINGLE`` is fully implemented.
    Other types are accepted but raise ``NotImplementedError`` at runtime.

    Attributes:
        name: Workflow identifier.
        type: Topology type.
        entry: Entry point agent name (for non-single types).
        agents: Agent names involved (for handoff / orchestrator).
        config: Type-specific free-form configuration.
    """

    name: str = Field(..., description="Workflow identifier")
    type: WorkflowType = Field(
        default=WorkflowType.SINGLE, description="Workflow topology"
    )
    entry: str | None = Field(default=None, description="Entry agent name")
    agents: list[str] = Field(
        default_factory=list, description="Involved agent names"
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="Type-specific config"
    )
    routing_policy: Any = Field(
        default=None,
        description="Optional RoutingPolicy for configurable routing",
    )
    max_handoffs: int = Field(
        default=3, description="Max handoff hops allowed (handoff workflows)"
    )
    max_agent_calls: int = Field(
        default=5, description="Max specialist agent calls (orchestrator workflows)"
    )

    @classmethod
    def single(cls, agent: str, name: str = "default") -> Workflow:
        """Create a single-agent workflow.

        Args:
            agent: Name of the agent to run.
            name: Workflow identifier (defaults to "default").
        """
        return cls(name=name, type=WorkflowType.SINGLE, entry=agent)

    @classmethod
    def handoff(
        cls,
        entry: str,
        agents: list[str],
        name: str = "handoff",
        max_handoffs: int = 3,
    ) -> Workflow:
        """Create a handoff (triage) workflow.

        Args:
            entry: Triage agent name.
            agents: Candidate handoff target agent names.
            name: Workflow identifier.
            max_handoffs: Maximum handoff hops allowed (default 3).
        """
        return cls(
            name=name,
            type=WorkflowType.HANDOFF,
            entry=entry,
            agents=agents,
            max_handoffs=max_handoffs,
        )

    @classmethod
    def orchestrator(
        cls,
        manager: str,
        agents_as_tools: list[str],
        name: str = "orchestrator",
        max_agent_calls: int = 5,
    ) -> Workflow:
        """Create an orchestrator (agents-as-tools) workflow.

        Args:
            manager: Manager agent name.
            agents_as_tools: Specialist agent names exposed as tools.
            name: Workflow identifier.
            max_agent_calls: Maximum specialist agent calls allowed (default 5).
        """
        return cls(
            name=name,
            type=WorkflowType.ORCHESTRATOR,
            entry=manager,
            agents=[manager, *agents_as_tools],
            config={"agents_as_tools": agents_as_tools},
            max_agent_calls=max_agent_calls,
        )

    @classmethod
    def dag(cls, name: str = "dag", nodes: list[dict[str, Any]] | None = None,
            execution_mode: str = "sequential",
            max_concurrency: int | None = None,
            retry: dict[str, Any] | None = None,
            timeout_seconds: float | None = None,
            deadline_seconds: float | None = None,
            compensation: dict[str, Any] | None = None) -> Workflow:
        """Create a DAG workflow.

        Args:
            name: Workflow identifier.
            nodes: List of node dicts, each with ``id``, ``type``, ``ref``,
                   optional ``input``, optional ``depends_on``, optional ``retry``
                   dict, optional ``condition`` dict (with ``expr`` key), and
                   optional ``timeout_seconds`` (float).
            execution_mode: "sequential" (default) or "parallel".
            max_concurrency: Max concurrent nodes (None = unlimited, parallel only).
            retry: Workflow-level default retry policy dict with keys:
                ``max_attempts`` (int, >= 1), ``backoff_seconds`` (float, >= 0),
                ``backoff_multiplier`` (float, >= 1.0),
                ``retry_on_statuses`` (list of status strings).
            timeout_seconds: Workflow-level default node timeout (seconds).
                Individual nodes can override with their own ``timeout_seconds``.
            deadline_seconds: Workflow-level execution deadline (seconds).
                Limits total DAG execution time. Must be > 0.
            compensation: Workflow-level compensation/rollback policy dict with keys:
                ``enabled`` (bool, default False), ``trigger_on`` (list of failure types),
                ``continue_on_failure`` (bool, default True), ``timeout_seconds`` (float).

        Returns:
            A Workflow with type DAG and a DagWorkflow stored in ``config``.
        """
        from agent_app.workflows.dag import DagCondition, DagExecutionMode, DagNode, DagWorkflow, NodeExecutionStatus, RetryPolicy

        dag_nodes: list[DagNode] = []
        if nodes:
            for n in nodes:
                node_retry = n.get("retry")
                retry_policy: RetryPolicy | None = None
                if node_retry and isinstance(node_retry, dict):
                    statuses = node_retry.get("retry_on_statuses", ["failed"])
                    retry_policy = RetryPolicy(
                        max_attempts=node_retry.get("max_attempts", 1),
                        backoff_seconds=node_retry.get("backoff_seconds", 0.0),
                        backoff_multiplier=node_retry.get("backoff_multiplier", 1.0),
                        retry_on_statuses=[
                            NodeExecutionStatus(s) if isinstance(s, str) else s
                            for s in statuses
                        ],
                    )

                node_condition: DagCondition | None = None
                cond_cfg = n.get("condition")
                if cond_cfg and isinstance(cond_cfg, dict):
                    node_condition = DagCondition(expr=cond_cfg.get("expr", ""))

                node_timeout = n.get("timeout_seconds")
                if node_timeout is not None:
                    node_timeout = float(node_timeout)

                # Phase 13.4: FUNCTION nodes use 'function' field instead of 'ref'
                # Phase 13.7: IF_ELSE/SWITCH nodes don't need a ref
                node_type = n.get("type", "tool")
                if node_type == "function":
                    node_ref = n.get("function", n.get("ref", ""))
                elif node_type == "subworkflow":
                    # subworkflow: YAML 'workflow' key or DagNode 'subworkflow_name' key
                    node_ref = n.get("workflow", n.get("subworkflow_name", n.get("ref", "")))
                elif node_type in ("if_else", "switch"):
                    node_ref = ""
                else:
                    node_ref = n.get("ref", "")

                # Build node input — include 'default' for SWITCH nodes at top level
                node_input = n.get("inputs", n.get("input", {}))
                if node_type == "switch" and "default" in n and "default" not in node_input:
                    node_input = {**node_input, "default": n["default"]}

                dag_nodes.append(
                    DagNode(
                        id=n["id"],
                        type=node_type,
                        ref=node_ref,
                        input=node_input,
                        depends_on=n.get("depends_on", []),
                        retry=retry_policy,
                        condition=node_condition,
                        timeout_seconds=node_timeout,
                        permissions=n.get("permissions", []),
                        subworkflow_name=n.get("workflow", n.get("subworkflow_name")) if node_type == "subworkflow" else None,
                        then=n.get("then", []) if node_type == "if_else" else [],
                        else_branch=n.get("else_branch", n.get("else", [])) if node_type == "if_else" else [],
                        switch_expr=n.get("switch_expr") if node_type == "switch" else None,
                        cases=n.get("cases", []) if node_type == "switch" else [],
                        compensate=n.get("compensate"),
                    )
                )

        try:
            mode = DagExecutionMode(execution_mode)
        except ValueError:
            raise ValueError(
                f"Invalid execution_mode '{execution_mode}'. "
                f"Must be one of: {[m.value for m in DagExecutionMode]}"
            )

        wf_retry: RetryPolicy | None = None
        if retry and isinstance(retry, dict):
            statuses = retry.get("retry_on_statuses", ["failed"])
            wf_retry = RetryPolicy(
                max_attempts=retry.get("max_attempts", 1),
                backoff_seconds=retry.get("backoff_seconds", 0.0),
                backoff_multiplier=retry.get("backoff_multiplier", 1.0),
                retry_on_statuses=[
                    NodeExecutionStatus(s) if isinstance(s, str) else s
                    for s in statuses
                ],
            )

        # Phase 13.8: Validate deadline_seconds
        if deadline_seconds is not None:
            if not isinstance(deadline_seconds, (int, float)):
                raise ValueError(
                    f"deadline_seconds must be a number, got {type(deadline_seconds).__name__}"
                )
            if deadline_seconds <= 0:
                raise ValueError(
                    f"deadline_seconds must be > 0, got {deadline_seconds}"
                )

        # Phase 13.9: Validate compensation
        if compensation is not None:
            if not isinstance(compensation, dict):
                raise ValueError(
                    f"compensation must be a dict, got {type(compensation).__name__}"
                )
            if compensation.get("enabled", False):
                trigger_on = compensation.get("trigger_on", ["workflow_failed"])
                valid_triggers = {"workflow_failed", "workflow_interrupted", "node_timeout", "deadline_exceeded"}
                invalid = set(trigger_on) - valid_triggers
                if invalid:
                    raise ValueError(f"Invalid compensation trigger_on values: {invalid}")

        dag_wf = DagWorkflow(
            name=name,
            nodes=dag_nodes,
            execution_mode=mode,
            max_concurrency=max_concurrency,
            retry=wf_retry,
            timeout_seconds=timeout_seconds,
            deadline_seconds=deadline_seconds,
            compensation=compensation,
        )
        return cls(
            name=name,
            type=WorkflowType.DAG,
            config={"dag": dag_wf.model_dump()},
        )

    def entry_agent_name(self) -> str:
        """Return the entry agent name for this workflow."""
        if self.type == WorkflowType.SINGLE:
            return self.entry or ""
        return self.entry or ""
