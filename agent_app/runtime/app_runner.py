"""AppRunner — orchestrates a single run end-to-end."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.core.workflow import Workflow
from agent_app.governance.audit import AuditEvent
from agent_app.runtime.backends import AgentBackend, DryRunBackend
from agent_app.runtime.run_state import RunStateStatus
from agent_app.runtime.session import SessionStore
from agent_app.runtime.streaming import StreamEvent, StreamEventType
from agent_app.runtime.tool_executor import ToolExecutor, ToolExecutionStatus
from agent_app.runtime.run_state import InterruptedRun, RunStateStore

if TYPE_CHECKING:
    from agent_app.core.app import AgentApp
    from agent_app.observability.events import RunEvent, RunEventType
    from agent_app.observability.collector import TraceCollector
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry


class AppRunner:
    """Executes a single agent or workflow run with governance.

    Args:
        agent_registry: Registry of AgentSpec objects.
        tool_registry: Registry of ToolSpec objects.
        workflow_registry: Registry of Workflow objects.
        backend: Execution backend (defaults to DryRunBackend).
        session_store: Optional session history store.
        approval_store: Optional approval persistence store.
        dag_state_store: Optional DAG execution state store (Phase 14.0).
        lease_renewal_config: Optional lease renewal configuration (Phase 15.2).
        dag_snapshot_config: Optional DAG snapshot config (Phase 16.0).
        dag_compensation_config: Optional compensation persistence config (Phase 16.1).
        dag_lease_config: Optional DAG lease backend config (Phase 16.2).
        policy_engine: Optional policy engine for governance decisions (Phase 23).
    """

    def __init__(
        self,
        agent_registry: Any,
        tool_registry: Any,
        workflow_registry: Any,
        backend: AgentBackend | None = None,
        session_store: SessionStore | None = None,
        approval_store: Any = None,
        run_state_store: RunStateStore | None = None,
        trace_collector: Any = None,
        dag_state_store: Any = None,
        lease_renewal_config: Any = None,
        dag_snapshot_config: Any = None,
        dag_compensation_config: Any = None,
        dag_lease_config: Any = None,
        policy_engine: Any = None,
        policy_decision_store: Any = None,
    ) -> None:
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker

        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.workflow_registry = workflow_registry
        self.backend: AgentBackend = backend or DryRunBackend()
        self.session_store = session_store
        self.approval_store = approval_store
        self.run_state_store: RunStateStore | None = run_state_store
        self._dag_state_store = dag_state_store
        # Phase 15.2: Lease renewal config (best-effort background renewal)
        self._lease_renewal_config = lease_renewal_config
        # Phase 16.0: Snapshot config (DAG execution recovery points)
        self._dag_snapshot_config = dag_snapshot_config
        # Phase 16.1: Compensation persistence config
        self._dag_compensation_config = dag_compensation_config
        # Phase 16.2: DAG lease backend config
        self._dag_lease_config = dag_lease_config

        # Phase 12: observability
        self.trace_collector = trace_collector
        self._trace_events: list[Any] = []

        # Governance layer
        self._tool_executor = ToolExecutor(
            tool_registry=tool_registry,
            approval_store=approval_store or _NoOpApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
            trace_collector=trace_collector,
            trace_events_callback=self._trace_events.append,
            policy_engine=policy_engine,
            policy_decision_store=policy_decision_store,
        )
        self._audit_logger = self._tool_executor.audit_logger

        # Phase 14.1: WorkflowExecutor for DAG resume support
        from agent_app.runtime.workflow_executor import WorkflowExecutor

        self._workflow_executor = WorkflowExecutor(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            session_store=session_store,
            approval_store=approval_store,
            trace_collector=trace_collector,
            dag_state_store=dag_state_store,
            app_runner=self,
            lease_renewal_config=lease_renewal_config,
            dag_snapshot_config=dag_snapshot_config,
            dag_compensation_config=dag_compensation_config,
            dag_lease_config=dag_lease_config,
        )

        if approval_store is not None and hasattr(self.backend, "_approval_store"):
            self.backend._approval_store = approval_store

    def _record_event(
        self,
        event_type: str,
        trace_id: str,
        run_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Create and record a RunEvent, also tracking it locally."""
        event = _make_event(
            event_type=event_type,
            trace_id=trace_id,
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            **kwargs,
        )
        self._trace_events.append(event)
        if self.trace_collector is not None:
            import asyncio
            asyncio.create_task(self.trace_collector.record(event))

    def _attach_trace(self, result: AppRunResult, trace_id: str) -> AppRunResult:
        """Attach trace_id and trace_events to result, then clear local buffer."""
        result.trace_id = trace_id
        result.trace_events = list(self._trace_events)
        self._trace_events.clear()
        return result

    async def run(
        self,
        workflow: str | None = None,
        agent: str | None = None,
        input: str = "",
        user_id: str = "anonymous",
        tenant_id: str = "default",
        session_id: str | None = None,
        app: Any = None,
        permissions: list[str] | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> AppRunResult:
        """Run a workflow or single agent.

        Args:
            workflow: Workflow name to execute.
            agent: Agent name (shortcut for a single-agent workflow).
            input: User input.
            user_id: End-user ID.
            tenant_id: Tenant ID.
            session_id: Session / conversation ID.
            app: The parent AgentApp.
            permissions: Granted permissions for this run.
            idempotency_key: Optional idempotency key for duplicate prevention (Phase 15.1).
            metadata: Optional metadata dict propagated into RunContext (Phase 22).
            **kwargs: Extra forwarded to the backend.

        Returns:
            AppRunResult.
        """
        run_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        trace_id = str(uuid.uuid4())

        # -- Phase 12: Emit run.started --
        self._record_event(
            "run.started",
            trace_id=trace_id,
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            workflow_name=workflow,
            agent_name=agent,
        )

        # -- Resolve entry agent --
        entry_agent_name = self._resolve_entry(workflow=workflow, agent=agent)
        agent_spec = self.agent_registry.get(entry_agent_name)

        # -- Phase 22: Build context with merged metadata --
        merged_meta = dict(metadata) if metadata else {}
        context = RunContext(
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            permissions=permissions or [],
            trace_id=trace_id,
            metadata=merged_meta,
            agent_name=entry_agent_name,
        )

        # -- Simulate tool call (governance pipeline) --
        tool_result = await self._simulate_tool_call(
            agent_spec=agent_spec,
            input=input,
            context=context,
        )

        # -- Handle approval interruption --
        if tool_result and tool_result.status == ToolExecutionStatus.INTERRUPTED.value:
            approval = tool_result.approval_request
            result = AppRunResult(
                run_id=run_id,
                status="interrupted",
                interruptions=[{
                    "type": "approval_required",
                    "approval_id": approval.approval_id,
                    "tool_name": tool_result.tool_name,
                    "arguments": approval.arguments,
                    "risk_level": approval.risk_level,
                }] if approval else [],
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            await self._append_to_session(session_id, input, result)

            # -- Phase 9: Save interrupted run state --
            if self.run_state_store is not None:
                await self._save_interrupted_run(
                    result=result,
                    agent_name=entry_agent_name,
                    workflow_name=workflow,
                    workflow_type=None,
                    context=context,
                    backend_name=_get_backend_name(self.backend),
                )

            # -- Phase 12: Emit run.interrupted + run_state.saved --
            self._record_event(
                "run.interrupted",
                trace_id=trace_id,
                run_id=run_id,
                user_id=user_id,
                tenant_id=tenant_id,
                status="interrupted",
                data={"agent_name": entry_agent_name},
            )
            if self.run_state_store is not None:
                self._record_event(
                    "run_state.saved",
                    trace_id=trace_id,
                    run_id=run_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    data={"agent_name": entry_agent_name},
                )

            return self._attach_trace(result, trace_id)

        # -- Handle permission denial --
        if tool_result and tool_result.status == ToolExecutionStatus.FAILED.value:
            result = AppRunResult(
                run_id=run_id,
                status="failed",
                error=tool_result.error,
                tool_calls=[{
                    "tool": tool_result.tool_name,
                    "status": "failed",
                    "error": tool_result.error,
                }],
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            await self._append_to_session(session_id, input, result)
            self._record_event(
                "run.failed",
                trace_id=trace_id,
                run_id=run_id,
                user_id=user_id,
                tenant_id=tenant_id,
                status="failed",
                error=tool_result.error,
            )
            return self._attach_trace(result, trace_id)

        # -- Execute via backend --
        try:
            result = await self.backend.run(
                agent_spec=agent_spec,
                input=input,
                context=context,
                tools=[],
                **kwargs,
            )
        except Exception as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            error_detail = {"type": "backend_execution_failed", "message": "Backend execution failed; check server logs for details."}
            result = AppRunResult(
                run_id=run_id,
                status="failed",
                error=error_detail,
                latency_ms=latency,
            )
            self._record_event(
                "run.failed",
                trace_id=trace_id,
                run_id=run_id,
                user_id=user_id,
                tenant_id=tenant_id,
                status="failed",
                error=error_detail,
            )
            return self._attach_trace(result, trace_id)

        # -- Phase 9: Save interrupted run state --
        if result.status == "interrupted" and self.run_state_store is not None:
            await self._save_interrupted_run(
                result=result,
                agent_name=entry_agent_name,
                workflow_name=workflow,
                workflow_type=None,
                context=context,
                backend_name=_get_backend_name(self.backend),
            )

        # -- Store compiled native agent --
        if app is not None and hasattr(self.backend, "_last_native_agent"):
            app._native_agents[entry_agent_name] = getattr(
                self.backend, "_last_native_agent"
            )

        # -- Append to session --
        await self._append_to_session(session_id, input, result)

        result.latency_ms = int((time.perf_counter() - t0) * 1000)

        # -- Phase 12: Emit run.completed --
        self._record_event(
            "run.completed",
            trace_id=trace_id,
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            status=result.status,
            duration_ms=result.latency_ms,
        )

        return self._attach_trace(result, trace_id)

    async def resume_workflow_run(
        self,
        workflow: str,
        run_id: str,
        input: str = "",
        permissions: list[str] | None = None,
        resume_policy: Any = None,
        worker: Any = None,
        idempotency_key: str | None = None,
    ) -> AppRunResult:
        """Resume a persisted DAG workflow run.

        Phase 14.1: Looks up the DAG workflow by name, then delegates to
        ``WorkflowExecutor.resume_workflow_run()``.

        Phase 15: Accepts optional worker identity for lease management.

        Phase 15.1: Accepts optional idempotency_key for duplicate prevention.

        Args:
            workflow: Name of the DAG workflow to resume.
            run_id: The persisted workflow run ID.
            input: Original user input.
            permissions: Granted permissions.
            resume_policy: Optional ResumePolicy.
            worker: Optional worker identity for lease management (Phase 15).
            idempotency_key: Optional idempotency key for duplicate prevention (Phase 15.1).

        Returns:
            AppRunResult.
        """
        try:
            wf = self.workflow_registry.get(workflow)
        except KeyError:
            return AppRunResult(
                run_id=run_id,
                status="failed",
                error={"type": "KeyError", "message": f"Workflow '{workflow}' not found."},
            )

        return await self._workflow_executor.resume_workflow_run(
            workflow=wf,
            run_id=run_id,
            input=input,
            permissions=permissions,
            resume_policy=resume_policy,
            worker=worker,
            idempotency_key=idempotency_key,
        )

    async def stream(
        self,
        workflow: str | None = None,
        agent: str | None = None,
        input: str = "",
        user_id: str = "anonymous",
        tenant_id: str = "default",
        session_id: str | None = None,
        app: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream events for a workflow or single agent run."""
        run_id = str(uuid.uuid4())

        entry_agent_name = self._resolve_entry(workflow=workflow, agent=agent)
        agent_spec = self.agent_registry.get(entry_agent_name)

        context = RunContext(
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
        )

        full_output_parts: list[str] = []

        async for event in self.backend.stream(
            agent_spec=agent_spec,
            input=input,
            context=context,
            tools=[],
            **kwargs,
        ):
            if event.type == StreamEventType.TEXT_DELTA and event.delta:
                full_output_parts.append(event.delta)
            yield event

        full_output = "".join(full_output_parts)
        if session_id and self.session_store is not None:
            await self.session_store.add_items(session_id, [
                {"role": "user", "content": input},
                {"role": "assistant", "content": full_output},
            ])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_entry(self, workflow: str | None, agent: str | None) -> str:
        if workflow:
            wf = self.workflow_registry.get(workflow)
            return wf.entry_agent_name()
        if agent:
            return agent
        raise ValueError("Must provide exactly one of 'workflow' or 'agent'.")

    async def _simulate_tool_call(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
    ) -> Any:
        """Simulate a tool call from input for DryRunBackend testing.

        Phase 3: matches keywords to tool names and runs governance.
        Returns None if no tool is matched (normal path).
        """
        if not agent_spec.tools:
            return None

        input_lower = input.lower()
        matched_tool = None

        # Score each tool by how many of its parts appear in the input.
        # Pick the tool with the highest score (most specific match).
        best_score = 0
        for tool_name in agent_spec.tools:
            parts = tool_name.split(".")
            score = 0
            for part in parts:
                if part in input_lower:
                    score += len(part)  # longer matches score higher
            if score > best_score:
                best_score = score
                matched_tool = tool_name

        if matched_tool is None:
            return None

        # Build reasonable arguments for the tool based on its name.
        tool_args = _build_tool_arguments(matched_tool, input)

        return await self._tool_executor.execute(
            tool_name=matched_tool,
            arguments=tool_args,
            context=context,
        )

    async def _append_to_session(
        self, session_id: str | None, input: str, result: AppRunResult
    ) -> None:
        if session_id and self.session_store is not None:
            output = result.final_output or ""
            if result.status == "interrupted":
                output = "[Run interrupted — approval required]"
            elif result.status == "failed":
                output = f"[Run failed: {result.error}]"
            await self.session_store.add_items(session_id, [
                {"role": "user", "content": input},
                {"role": "assistant", "content": str(output)},
            ])

    async def _save_interrupted_run(
        self,
        result: AppRunResult,
        agent_name: str,
        workflow_name: str | None,
        workflow_type: str | None,
        context: RunContext,
        backend_name: str,
    ) -> None:
        """Persist an interrupted run to the RunStateStore.

        Phase 9: Saves the full run state so it can be resumed later.
        Writes an audit event for the interruption.
        """
        if self.run_state_store is None:
            return

        approval_ids = _extract_approval_ids(result)

        interrupted = InterruptedRun(
            run_id=result.run_id,
            status=RunStateStatus.INTERRUPTED.value,
            agent_name=agent_name,
            workflow_name=workflow_name,
            workflow_type=workflow_type,
            input="",  # input not available here; set by caller if needed
            context=context,
            interruptions=result.interruptions or [],
            approval_ids=approval_ids,
            backend_name=backend_name,
            backend_state=getattr(result, "backend_state", {}) or {},
            result_snapshot=result.model_dump(mode="json"),
        )

        await self.run_state_store.save_interrupted(interrupted)

        # Audit: run interrupted
        await self._audit_logger.log(AuditEvent(
            event_id=str(uuid.uuid4()),
            run_id=result.run_id,
            event_type="run.interrupted",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            data={
                "agent_name": agent_name,
                "workflow_name": workflow_name,
                "approval_ids": approval_ids,
                "interruptions_count": len(result.interruptions or []),
            },
        ))


def _build_tool_arguments(tool_name: str, input_text: str) -> dict[str, Any]:
    """Build simulated arguments for a tool call based on input text.

    Phase 3 heuristic: extract likely parameter values from the input.
    """
    # Common patterns: "order 123" → {"order_id": "123"}
    import re
    numbers = re.findall(r'\b\d+\b', input_text)

    if tool_name.startswith("order."):
        if numbers:
            return {"order_id": numbers[0]}
        return {"order_id": input_text.strip()}

    if tool_name.startswith("refund."):
        return {
            "order_id": numbers[0] if numbers else "unknown",
            "amount": 199.0 if not numbers else float(numbers[0]),
            "reason": "customer request",
        }

    # Generic fallback
    if numbers:
        return {"id": numbers[0], "input": input_text}
    return {"input": input_text}


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


def _extract_approval_ids(result: AppRunResult) -> list[str]:
    """Extract approval IDs from an AppRunResult's interruptions."""
    ids: list[str] = []
    for interruption in result.interruptions or []:
        if interruption.get("type") == "approval_required":
            apv_id = interruption.get("approval_id")
            if apv_id:
                ids.append(apv_id)
    return ids


def _get_backend_name(backend: Any) -> str:
    """Get a human-readable backend name."""
    cls_name = type(backend).__name__
    if "OpenAI" in cls_name:
        return "openai"
    if "DryRun" in cls_name:
        return "dry_run"
    return cls_name.lower().replace("backend", "")


def _make_event(
    event_type: str,
    trace_id: str,
    run_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    **kwargs: Any,
) -> Any:
    """Create a RunEvent with minimal boilerplate."""
    from agent_app.observability.events import RunEvent
    return RunEvent(
        event_type=event_type,
        trace_id=trace_id,
        run_id=run_id,
        user_id=user_id,
        tenant_id=tenant_id,
        **kwargs,
    )
