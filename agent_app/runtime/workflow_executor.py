"""WorkflowExecutor — dispatches runs by WorkflowType with routing policy support."""

from __future__ import annotations

import time
import uuid
from typing import Any

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult, WorkflowStep, WorkflowTrace
from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.observability.collector import NoOpTraceCollector
from agent_app.observability.events import RunEventType
from agent_app.runtime.backends import DryRunBackend


class WorkflowExecutor:
    """Dispatches execution based on workflow topology.

    Supports optional :class:`RoutingPolicy` for configurable routing
    (Phase 6). Falls back to heuristic keyword matching when no policy
    is configured.

    Args:
        agent_registry: Registry of AgentSpec objects.
        tool_registry: Registry of ToolSpec objects.
        workflow_registry: Registry of Workflow objects.
        backend: Execution backend (defaults to DryRunBackend).
        session_store: Optional session history store.
        approval_store: Optional approval persistence store.
        trace_collector: Optional observability trace collector.
        dag_state_store: Optional DAG execution state store (Phase 14.0).
        app_runner: Optional AppRunner for DAG agent node execution (Phase 14.1).
        dag_snapshot_config: Optional DAG snapshot config (Phase 16.0).
        dag_compensation_config: Optional compensation persistence config (Phase 16.1).
    """

    def __init__(
        self,
        agent_registry: Any,
        tool_registry: Any,
        workflow_registry: Any,
        backend: Any = None,
        session_store: Any = None,
        approval_store: Any = None,
        trace_collector: Any = None,
        function_registry: Any = None,
        dag_state_store: Any = None,
        app_runner: Any = None,
        lease_renewal_config: Any = None,
        dag_snapshot_config: Any = None,
        dag_compensation_config: Any = None,
        dag_lease_config: Any = None,
    ) -> None:
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.tool_executor import ToolExecutor

        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.workflow_registry = workflow_registry
        self.backend: Any = backend or DryRunBackend()
        self.session_store = session_store
        self.approval_store = approval_store
        self.function_registry = function_registry
        self._dag_state_store = dag_state_store
        self._app_runner = app_runner
        # Phase 15.2: Lease renewal config
        self._lease_renewal_config = lease_renewal_config
        # Phase 16.0: Snapshot config
        self._dag_snapshot_config = dag_snapshot_config
        # Phase 16.1: Compensation persistence config
        self._dag_compensation_config = dag_compensation_config
        # Phase 16.2: DAG lease backend config
        self._dag_lease_config = dag_lease_config

        self._tool_executor = ToolExecutor(
            tool_registry=tool_registry,
            approval_store=approval_store or _NoOpApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
            trace_collector=trace_collector,
        )
        self._audit_logger = self._tool_executor.audit_logger
        self._routing_executor = _RoutingPolicyExecutorOrProxy()
        self.trace_collector = trace_collector or NoOpTraceCollector()

    async def run_workflow(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        app_runner: Any = None,
        permissions: list[str] | None = None,
        worker: Any = None,
        idempotency_key: str | None = None,
    ) -> AppRunResult:
        """Execute a workflow according to its type.

        Args:
            workflow: The workflow definition.
            input: User input.
            context: Run context.
            app_runner: The parent AppRunner (needed for single-agent fallback).
            permissions: Granted permissions for this run.
            worker: Optional worker identity for lease management (Phase 15).
            idempotency_key: Optional idempotency key for duplicate prevention (Phase 15.1).

        Returns:
            AppRunResult.
        """
        t0 = time.perf_counter()

        if workflow.type == WorkflowType.SINGLE:
            return await self._run_single(
                workflow, input, context, app_runner, permissions, t0
            )
        if workflow.type == WorkflowType.HANDOFF:
            return await self._run_handoff(
                workflow, input, context, app_runner, permissions, t0
            )
        if workflow.type == WorkflowType.ORCHESTRATOR:
            return await self._run_orchestrator(
                workflow, input, context, app_runner, permissions, t0
            )
        if workflow.type == WorkflowType.DAG:
            return await self._run_dag(
                workflow, input, context, app_runner, permissions, t0, worker, idempotency_key
            )

        return AppRunResult(
            run_id=context.run_id,
            status="failed",
            error={"type": "ValueError", "message": f"Unknown workflow type: {workflow.type}"},
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def _run_single(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        app_runner: Any,
        permissions: list[str] | None,
        t0: float,
    ) -> AppRunResult:
        """Single-agent execution — delegates to AppRunner.run()."""
        if app_runner is None:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "ValueError", "message": "AppRunner required for single workflow"},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        result = await app_runner.run(
            workflow=workflow.name,
            input=input,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            permissions=permissions or context.permissions,
        )
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    async def _run_handoff(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        app_runner: Any,
        permissions: list[str] | None,
        t0: float,
    ) -> AppRunResult:
        """Handoff (triage) workflow — route input to specialist agent."""
        handoffs: list[dict[str, Any]] = []
        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
            entry_agent=workflow.entry,
        )

        # -- Phase 12: workflow.started --
        await _wf_record_event(
            self.trace_collector,
            RunEventType.WORKFLOW_STARTED,
            context=context,
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
        )

        # -- Resolve entry agent --
        entry_name = workflow.entry or ""
        try:
            entry_agent = self.agent_registry.get(entry_name)
        except KeyError:
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                agent_name=entry_name,
                status="failed",
                output_summary=f"Entry agent '{entry_name}' not found",
            ))
            await _wf_record_event(
                self.trace_collector,
                RunEventType.WORKFLOW_FAILED,
                context=context,
                workflow_name=workflow.name,
                workflow_type=workflow.type.value,
                status="failed",
                error={"type": "KeyError", "message": f"Entry agent '{entry_name}' not found"},
            )
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "KeyError", "message": f"Entry agent '{entry_name}' not found"},
                handoffs=handoffs,
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="agent",
            agent_name=entry_name,
            input_summary=input[:100],
            status="completed",
        ))

        # -- Determine target agent --
        allowed_targets = [entry_name, *workflow.agents]
        target_name, reason, rule_name = _resolve_handoff_target(
            workflow, input, allowed_targets, self._routing_executor
        )

        # -- Phase 12: routing.decision --
        await _wf_record_event(
            self.trace_collector,
            RunEventType.ROUTING_DECISION,
            context=context,
            workflow_name=workflow.name,
            agent_name=entry_name,
            data={
                "selected_agent": target_name,
                "rule": rule_name,
                "reason": reason,
                "candidate_agents": allowed_targets,
            },
        )

        # Record routing decision in trace
        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="routing",
            agent_name=entry_name,
            input_summary=input[:100],
            output_summary=f"→ {target_name}",
            status="completed",
            metadata={"rule": rule_name, "reason": reason},
        ))

        if target_name != entry_name:
            # Verify target exists
            try:
                self.agent_registry.get(target_name)
            except KeyError:
                trace.steps.append(WorkflowStep(
                    step_id=_uid(),
                    step_type="error",
                    agent_name=target_name,
                    status="failed",
                    output_summary=f"Handoff target '{target_name}' not found",
                ))
                await _wf_record_event(
                    self.trace_collector,
                    RunEventType.WORKFLOW_FAILED,
                    context=context,
                    workflow_name=workflow.name,
                    workflow_type=workflow.type.value,
                    status="failed",
                    error={"type": "KeyError", "message": f"Handoff target agent '{target_name}' not found"},
                )
                return AppRunResult(
                    run_id=context.run_id,
                    status="failed",
                    error={"type": "KeyError", "message": f"Handoff target agent '{target_name}' not found"},
                    handoffs=[{"from_agent": entry_name, "to_agent": target_name, "reason": reason}],
                    workflow_trace=trace,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            handoffs.append({"from_agent": entry_name, "to_agent": target_name, "reason": reason})

            # -- Phase 12: handoff.occurred --
            await _wf_record_event(
                self.trace_collector,
                RunEventType.HANDOFF_OCCURRED,
                context=context,
                workflow_name=workflow.name,
                agent_name=entry_name,
                data={
                    "from_agent": entry_name,
                    "to_agent": target_name,
                    "reason": reason,
                },
            )

        # -- Execute on target agent --
        if app_runner is None:
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="error",
                status="failed",
                output_summary="No AppRunner available",
            ))
            await _wf_record_event(
                self.trace_collector,
                RunEventType.WORKFLOW_FAILED,
                context=context,
                workflow_name=workflow.name,
                workflow_type=workflow.type.value,
                status="failed",
                error={"type": "ValueError", "message": "AppRunner required"},
            )
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": "ValueError", "message": "AppRunner required"},
                handoffs=handoffs,
                workflow_trace=trace,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        result = await app_runner.run(
            agent=target_name,
            workflow=None,
            input=input,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            permissions=permissions or context.permissions,
        )
        result.handoffs = handoffs
        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="agent",
            agent_name=target_name,
            input_summary=input[:100],
            output_summary=str(result.final_output or "")[:100],
            status=result.status,
        ))
        result.workflow_trace = trace
        result.latency_ms = int((time.perf_counter() - t0) * 1000)

        # -- Phase 12: workflow.completed --
        await _wf_record_event(
            self.trace_collector,
            RunEventType.WORKFLOW_COMPLETED,
            context=context,
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
            status=result.status,
        )

        return result

    async def _run_orchestrator(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        app_runner: Any,
        permissions: list[str] | None,
        t0: float,
    ) -> AppRunResult:
        """Orchestrator workflow — manager delegates to specialists."""
        agent_calls: list[dict[str, Any]] = []
        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
            entry_agent=workflow.entry,
        )
        agents_as_tools = workflow.config.get("agents_as_tools", workflow.agents)

        # -- Phase 12: workflow.started --
        await _wf_record_event(
            self.trace_collector,
            RunEventType.WORKFLOW_STARTED,
            context=context,
            workflow_name=workflow.name,
            workflow_type=workflow.type.value,
        )

        # -- Record manager step --
        trace.steps.append(WorkflowStep(
            step_id=_uid(),
            step_type="agent",
            agent_name=workflow.entry,
            input_summary=input[:100],
            status="completed",
        ))

        # -- Determine which specialists to call --
        allowed_targets = list(agents_as_tools)
        matched_decisions = _resolve_orchestrator_targets(
            workflow, input, allowed_targets, self._routing_executor
        )
        matched_agents = [d.target for d in matched_decisions]

        # -- Record routing decisions --
        for decision in matched_decisions:
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="routing",
                agent_name=workflow.entry,
                output_summary=f"→ {decision.target}",
                status="completed",
                metadata={"rule": decision.rule_name, "reason": decision.reason},
            ))

        # -- Call each specialist --
        for specialist_name in matched_agents:
            if app_runner is None:
                break

            # -- Phase 12: agent.started --
            await _wf_record_event(
                self.trace_collector,
                RunEventType.AGENT_STARTED,
                context=context,
                workflow_name=workflow.name,
                agent_name=specialist_name,
            )

            sub_result = await app_runner.run(
                agent=specialist_name,
                workflow=None,
                input=input,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                permissions=permissions or context.permissions,
            )
            agent_calls.append({
                "agent_name": specialist_name,
                "input": input,
                "status": sub_result.status,
            })
            trace.steps.append(WorkflowStep(
                step_id=_uid(),
                step_type="agent",
                agent_name=specialist_name,
                input_summary=input[:100],
                output_summary=str(sub_result.final_output or "")[:100],
                status=sub_result.status,
            ))

            # -- Phase 12: agent.completed --
            await _wf_record_event(
                self.trace_collector,
                RunEventType.AGENT_COMPLETED,
                context=context,
                workflow_name=workflow.name,
                agent_name=specialist_name,
                status=sub_result.status,
            )

        # -- Build output --
        if agent_calls:
            names = ", ".join(c["agent_name"] for c in agent_calls)
            final_output = f"Manager completed task using: {names}"
            status = "completed"
        else:
            # No specialist matched — run the manager itself
            if app_runner is not None:
                mgr_result = await app_runner.run(
                    agent=workflow.entry,
                    workflow=None,
                    input=input,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    session_id=context.session_id,
                    permissions=permissions or context.permissions,
                )
                final_output = mgr_result.final_output
                status = mgr_result.status
                trace.steps.append(WorkflowStep(
                    step_id=_uid(),
                    step_type="agent",
                    agent_name=workflow.entry,
                    input_summary=input[:100],
                    output_summary=str(final_output or "")[:100],
                    status=status,
                ))
            else:
                final_output = "No manager available"
                status = "failed"

        result = AppRunResult(
            run_id=context.run_id,
            status=status if app_runner else "failed",
            final_output=final_output,
            agent_calls=agent_calls,
            workflow_trace=trace,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

        # -- Phase 12: workflow.completed or workflow.failed --
        wf_status = result.status
        if wf_status == "failed":
            await _wf_record_event(
                self.trace_collector,
                RunEventType.WORKFLOW_FAILED,
                context=context,
                workflow_name=workflow.name,
                workflow_type=workflow.type.value,
                status="failed",
            )
        else:
            await _wf_record_event(
                self.trace_collector,
                RunEventType.WORKFLOW_COMPLETED,
                context=context,
                workflow_name=workflow.name,
                workflow_type=workflow.type.value,
                status=wf_status,
            )

        return result

    async def _run_dag(
        self,
        workflow: Workflow,
        input: str,
        context: RunContext,
        app_runner: Any,
        permissions: list[str] | None,
        t0: float,
        worker: Any = None,
        idempotency_key: str | None = None,
    ) -> AppRunResult:
        """DAG workflow — topological sort + sequential node execution."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow
        from agent_app.core.result import WorkflowTrace, WorkflowStep

        # -- Reconstruct DagWorkflow from config --
        dag_cfg = workflow.config.get("dag")
        if dag_cfg is None:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={
                    "type": "ValueError",
                    "message": "DAG workflow has no 'dag' config",
                },
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        try:
            dag = DagWorkflow.model_validate(dag_cfg)
        except Exception as exc:
            return AppRunResult(
                run_id=context.run_id,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        executor = DagExecutor(
            agent_registry=self.agent_registry,
            tool_registry=self.tool_registry,
            workflow_registry=self.workflow_registry,
            app_runner=app_runner,
            trace_collector=self.trace_collector,
            function_registry=getattr(self, "function_registry", None),
            state_store=getattr(self, "_dag_state_store", None),
            run_id=context.run_id,
            worker=worker,
            idempotency_key=idempotency_key,
            lease_renewal_config=getattr(self, "_lease_renewal_config", None),
            snapshot_config=getattr(self, "_dag_snapshot_config", None),
            compensation_config=getattr(self, "_dag_compensation_config", None),
            lease_backend=self._build_lease_backend(),
            lease_policy=self._build_lease_policy(),
        )
        # Phase 15.1: Set workflow name for fingerprinting
        executor._workflow_name = workflow.name

        node_results, status, final_output, _ = await executor.execute(
            dag=dag,
            input=input,
            context=context,
            permissions=permissions,
        )

        # -- Build workflow trace --
        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type="dag",
            steps=[
                WorkflowStep(
                    step_id=_uid(),
                    step_type=nr.node_id,
                    agent_name=nr.node_id
                    if _is_agent_node(dag, nr.node_id)
                    else None,
                    tool_name=nr.node_id
                    if _is_tool_node(dag, nr.node_id)
                    else None,
                    output_summary=str(nr.output or "")[:100],
                    status=nr.status.value,
                )
                for nr in node_results
            ],
        )

        result = AppRunResult(
            run_id=context.run_id,
            status=status,
            final_output=final_output,
            node_results=[nr.model_dump() for nr in node_results],
            workflow_trace=trace,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        return result

    async def resume_workflow_run(
        self,
        workflow: Workflow,
        run_id: str,
        input: str = "",
        permissions: list[str] | None = None,
        resume_policy: Any = None,
        worker: Any = None,
        idempotency_key: str | None = None,
    ) -> AppRunResult:
        """Resume a previously persisted DAG workflow run.

        Phase 14.1: Reconstructs the DAG workflow definition, creates a
        DagExecutor with the persisted state store and run_id, and delegates
        to ``DagExecutor.resume()``.

        Phase 15: Accepts an optional worker identity for lease management.

        Args:
            workflow: The DAG Workflow definition (from registry).
            run_id: The persisted workflow run ID to resume.
            input: Original user input (passed through to node execution).
            permissions: Granted permissions for this run.
            resume_policy: Optional ResumePolicy controlling retry/skip behavior.
            worker: Optional worker identity for lease management (Phase 15).
            idempotency_key: Optional idempotency key for duplicate prevention (Phase 15.1).

        Returns:
            AppRunResult with the resumed execution outcome.

        Raises:
            DagError: If the run cannot be resumed (no state_store, run not
                found, compensation already started, etc.).
        """
        from agent_app.core.result import AppRunResult
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        if getattr(self, "_dag_state_store", None) is None:
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={
                    "type": "DagError",
                    "message": "Cannot resume workflow run: no state_store configured. "
                               "Configure runtime.workflow_state to enable resume.",
                },
            )

        # -- Reconstruct DagWorkflow from config --
        dag_cfg = workflow.config.get("dag")
        if dag_cfg is None:
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={
                    "type": "ValueError",
                    "message": "DAG workflow has no 'dag' config",
                },
            )

        try:
            dag = DagWorkflow.model_validate(dag_cfg)
        except Exception as exc:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

        from agent_app.core.context import RunContext
        context = RunContext(
            run_id=run_id,
            user_id="anonymous",
            tenant_id="default",
            session_id=None,
            permissions=permissions or [],
        )

        executor = DagExecutor(
            agent_registry=self.agent_registry,
            tool_registry=self.tool_registry,
            workflow_registry=self.workflow_registry,
            app_runner=getattr(self, "_app_runner", None),
            trace_collector=self.trace_collector,
            function_registry=getattr(self, "function_registry", None),
            state_store=getattr(self, "_dag_state_store", None),
            run_id=run_id,
            worker=worker,
            idempotency_key=idempotency_key,
            lease_renewal_config=getattr(self, "_lease_renewal_config", None),
            snapshot_config=getattr(self, "_dag_snapshot_config", None),
            compensation_config=getattr(self, "_dag_compensation_config", None),
            lease_backend=self._build_lease_backend(),
            lease_policy=self._build_lease_policy(),
        )

        try:
            node_results, status, final_output, _ = await executor.resume(
                dag=dag,
                input=input,
                context=context,
                permissions=permissions,
                policy=resume_policy,
            )
        except Exception as exc:
            from agent_app.core.result import AppRunResult
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

        # -- Build workflow trace --
        from agent_app.core.result import WorkflowTrace, WorkflowStep

        trace = WorkflowTrace(
            workflow_name=workflow.name,
            workflow_type="dag",
            steps=[
                WorkflowStep(
                    step_id=_uid(),
                    step_type=nr.node_id,
                    agent_name=nr.node_id
                    if _is_agent_node(dag, nr.node_id)
                    else None,
                    tool_name=nr.node_id
                    if _is_tool_node(dag, nr.node_id)
                    else None,
                    output_summary=str(nr.output or "")[:100],
                    status=nr.status.value,
                )
                for nr in node_results
            ],
        )

        result = AppRunResult(
            run_id=run_id,
            status=status,
            final_output=final_output,
            node_results=[nr.model_dump() for nr in node_results],
            workflow_trace=trace,
        )
        return result

    # -- Phase 16.2: Lease backend helpers --

    def _build_lease_backend(self) -> Any:
        """Build a lease backend from the dag_lease_config.

        Phase 16.3: Wraps the backend with MetricsWorkflowLeaseBackend
        if metrics are enabled.

        Returns:
            A WorkflowLeaseBackend instance, or None if not configured.
        """
        cfg = getattr(self, "_dag_lease_config", None)
        if cfg is None:
            return None
        try:
            from agent_app.runtime.lease_backend import create_lease_backend
            backend_type = getattr(cfg, "backend", "state_store")
            if backend_type == "state_store":
                backend = create_lease_backend(
                    backend_type="state_store",
                    state_store=getattr(self, "_dag_state_store", None),
                )
            elif backend_type in ("memory", "sqlite"):
                db_path = getattr(cfg, "db_path", None) or ".agent_app/workflow_leases.db"
                backend = create_lease_backend(
                    backend_type=backend_type,
                    db_path=db_path,
                )
            else:
                return None

            # Phase 16.3: Wrap with metrics if enabled
            metrics = self._build_lease_metrics()
            if metrics is not None and backend is not None:
                from agent_app.runtime.lease_backend import MetricsWorkflowLeaseBackend
                backend = MetricsWorkflowLeaseBackend(backend, metrics)

            return backend
        except Exception:
            return None

    def _build_lease_policy(self) -> Any:
        """Build a LeasePolicy from the dag_lease_config.

        Returns:
            A LeasePolicy instance, or None if not configured.
        """
        cfg = getattr(self, "_dag_lease_config", None)
        if cfg is None:
            return None
        try:
            from agent_app.runtime.dag_run_state import LeasePolicy
            return LeasePolicy(
                ttl_seconds=getattr(cfg, "ttl_seconds", 300),
                allow_steal_expired=getattr(cfg, "allow_steal_expired", True),
                renew_before_seconds=getattr(cfg, "renew_before_seconds", 60),
            )
        except Exception:
            return None

    def _build_lease_metrics(self) -> Any:
        """Build a LeaseMetrics instance from the dag_lease_config.

        Returns:
            A LeaseMetrics instance if metrics are enabled, None otherwise.
        """
        cfg = getattr(self, "_dag_lease_config", None)
        if cfg is None:
            return None
        metrics_cfg = getattr(cfg, "metrics", None)
        if metrics_cfg is None:
            return None
        if not getattr(metrics_cfg, "enabled", False):
            return None
        try:
            from agent_app.runtime.lease_metrics import LeaseMetrics
            return LeaseMetrics()
        except Exception:
            return None

    def _build_lease_health_checker(self) -> Any:
        """Build a LeaseBackendHealthChecker from the dag_lease_config.

        Returns:
            A LeaseBackendHealthChecker instance if health checks are
            enabled, None otherwise.
        """
        cfg = getattr(self, "_dag_lease_config", None)
        if cfg is None:
            return None
        health_cfg = getattr(cfg, "health", None)
        if health_cfg is None:
            return None
        if not getattr(health_cfg, "enabled", True):
            return None
        try:
            from agent_app.runtime.lease_health import LeaseBackendHealthChecker
            # We create the checker lazily when needed (after backend is built)
            return None  # checker is created in get_lease_health_checker()
        except Exception:
            return None

    def get_lease_health_checker(self) -> Any:
        """Get a LeaseBackendHealthChecker for the current lease backend.

        Returns:
            LeaseBackendHealthChecker or None if not available.
        """
        backend = self._build_lease_backend()
        if backend is None:
            return None
        try:
            from agent_app.runtime.lease_health import LeaseBackendHealthChecker
            return LeaseBackendHealthChecker(backend)
        except Exception:
            return None

    def get_lease_diagnostics(
        self,
        include_expired_sample: bool = False,
        expired_sample_limit: int = 10,
    ) -> Any:
        """Collect lease backend diagnostics.

        Args:
            include_expired_sample: Include sample expired leases.
            expired_sample_limit: Max sample size.

        Returns:
            LeaseDiagnostics dict or None if not available.
        """
        backend = self._build_lease_backend()
        if backend is None:
            return None
        metrics = self._build_lease_metrics()
        try:
            from agent_app.runtime.lease_coordinator import get_lease_diagnostics
            import asyncio
            return asyncio.run(get_lease_diagnostics(
                backend,
                metrics=metrics,
                include_expired_sample=include_expired_sample,
                expired_sample_limit=expired_sample_limit,
            ))
        except Exception:
            return None


def _is_agent_node(dag: Any, node_id: str) -> bool:
    """Check if a node in the DAG is an agent node."""
    for node in dag.nodes:
        if node.id == node_id:
            return node.type.value == "agent"
    return False


def _is_tool_node(dag: Any, node_id: str) -> bool:
    """Check if a node in the DAG is a tool node."""
    for node in dag.nodes:
        if node.id == node_id:
            return node.type.value == "tool"
    return False


# ---------------------------------------------------------------------------
# Routing resolution helpers
# ---------------------------------------------------------------------------

class _RoutingPolicyExecutorOrProxy:
    """Lazy proxy for RoutingPolicyExecutor to avoid circular imports."""

    def route_one(
        self,
        policy: Any,
        input: str,
        allowed_targets: list[str],
    ) -> Any | None:
        from agent_app.runtime.routing import RoutingPolicyExecutor
        executor = RoutingPolicyExecutor()
        return executor.route_one(policy, input, allowed_targets)

    def route_many(
        self,
        policy: Any,
        input: str,
        allowed_targets: list[str],
    ) -> list[Any]:
        from agent_app.runtime.routing import RoutingPolicyExecutor
        executor = RoutingPolicyExecutor()
        return executor.route_many(policy, input, allowed_targets)


def _resolve_handoff_target(
    workflow: Workflow,
    input: str,
    allowed_targets: list[str],
    routing_executor: _RoutingPolicyExecutorOrProxy,
) -> tuple[str, str, str]:
    """Resolve the target agent for a handoff workflow.

    Returns (target_name, reason, rule_name).
    Falls back to heuristic keyword matching when no routing policy is set.
    """
    policy = workflow.routing_policy
    if policy is not None:
        decision = routing_executor.route_one(policy, input, allowed_targets)
        if decision is not None:
            return decision.target, decision.reason, decision.rule_name

    # Fallback: heuristic keyword matching
    target, reason = _route_handoff_heuristic(input, workflow.agents, workflow.entry or "")
    return target, reason, "heuristic_fallback"


def _resolve_orchestrator_targets(
    workflow: Workflow,
    input: str,
    allowed_targets: list[str],
    routing_executor: _RoutingPolicyExecutorOrProxy,
) -> list[Any]:
    """Resolve target specialists for an orchestrator workflow.

    Returns list of RoutingDecision.
    Falls back to heuristic keyword matching when no routing policy is set.
    """
    policy = workflow.routing_policy
    if policy is not None:
        decisions = routing_executor.route_many(policy, input, allowed_targets)
        if decisions:
            return decisions

    # Fallback: heuristic keyword matching → build synthetic decisions
    matched = _route_orchestrator_heuristic(input, allowed_targets)
    return [
        _SyntheticDecision(target=name, rule_name="heuristic_fallback", reason=f"matched heuristic")
        for name in matched
    ]


class _SyntheticDecision:
    """Minimal stand-in for RoutingDecision when using heuristic fallback."""

    def __init__(self, target: str, rule_name: str, reason: str) -> None:
        self.target = target
        self.rule_name = rule_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Heuristic fallback (preserved from Phase 5)
# ---------------------------------------------------------------------------

_HANDOFF_KEYWORDS: dict[str, list[str]] = {
    "refund": ["refund", "退款", "退钱"],
    "billing": ["billing", "invoice", "发票", "账单", "付款"],
    "technical_support": ["tech", "error", "报错", "技术", "故障", "问题"],
}


def _route_handoff_heuristic(
    input: str, candidates: list[str], entry: str
) -> tuple[str, str]:
    """Route input to a handoff target agent using keyword heuristics.

    Returns (target_agent, reason).
    """
    input_lower = input.lower()
    for candidate in candidates:
        keywords = _HANDOFF_KEYWORDS.get(candidate, [candidate])
        for kw in keywords:
            if kw.lower() in input_lower:
                return candidate, f"matched {kw} intent"
        for part in candidate.replace("_", " ").replace("-", " ").split():
            if part.lower() in input_lower:
                return candidate, f"matched agent name '{part}'"
    return entry, "no match, staying at entry"


_ORCHESTRATOR_KEYWORDS: dict[str, list[str]] = {
    "researcher": ["research", "调研", "研究", "search"],
    "analyst": ["data", "数据", "分析", "analyze"],
    "writer": ["write", "写", "总结", "报告", "report", "summary"],
}


def _route_orchestrator_heuristic(
    input: str, agents_as_tools: list[str]
) -> list[str]:
    """Determine which specialist agents to call for orchestrator."""
    input_lower = input.lower()
    matched: list[str] = []
    for agent in agents_as_tools:
        keywords = _ORCHESTRATOR_KEYWORDS.get(agent, [agent])
        for kw in keywords:
            if kw.lower() in input_lower:
                matched.append(agent)
                break
    return matched


# ---------------------------------------------------------------------------
# No-op fallback
# ---------------------------------------------------------------------------

class _NoOpApprovalStore:
    """Fallback when no approval store is configured."""

    async def create(self, request: Any) -> Any:
        return request

    async def get(self, approval_id: str) -> Any:
        raise KeyError(approval_id)

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> Any:
        raise RuntimeError("No approval store configured.")

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> Any:
        raise RuntimeError("No approval store configured.")

    async def list_pending(self, tenant_id: str | None = None) -> list:
        return []


# -- Helpers --

def _uid() -> str:
    """Generate a short unique ID for trace steps."""
    return uuid.uuid4().hex[:12]


async def _wf_record_event(
    collector: Any,
    event_type: RunEventType | str,
    context: RunContext,
    workflow_name: str | None = None,
    workflow_type: str | None = None,
    agent_name: str | None = None,
    approval_id: str | None = None,
    status: str | None = None,
    error: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Record a workflow-level RunEvent."""
    if collector is None:
        return
    from agent_app.observability.events import RunEvent
    event = RunEvent(
        event_type=event_type,
        trace_id=context.trace_id or "",
        run_id=context.run_id,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        workflow_name=workflow_name,
        workflow_type=workflow_type,
        agent_name=agent_name,
        approval_id=approval_id,
        status=status,
        error=error,
        data=data or {},
    )
    await collector.record(event)
