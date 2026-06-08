"""FastAPI adapter — HTTP API for AgentApp.

This module is an optional dependency.  Install with:

    pip install 'agent-app-framework[api]'

If FastAPI is not installed, importing this module raises ImportError
with a clear message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_app.runtime.streaming import StreamEvent

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except ImportError as e:
    raise ImportError(
        "FastAPI dependencies are not installed. "
        "Install with: pip install 'agent-app-framework[api]'"
    ) from e


if TYPE_CHECKING:
    from agent_app.core.app import AgentApp


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    """Incoming run request body."""

    agent: str | None = Field(default=None, description="Agent name")
    workflow: str | None = Field(default=None, description="Workflow name")
    input: str = Field(default="", description="User input text")
    user_id: str = Field(default="anonymous")
    tenant_id: str = Field(default="default")
    session_id: str | None = Field(default=None)
    stream: bool = Field(default=False)
    permissions: list[str] = Field(default_factory=list)
    # Phase 15.1: idempotency key (body-level, header takes priority)
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key for duplicate prevention (Phase 15.1)",
    )


class ApprovalActionRequest(BaseModel):
    """Request body for approve/reject actions."""

    approved_by: str = Field(..., description="Approver identity")
    reason: str | None = Field(default=None, description="Optional reason")


class AgentInfo(BaseModel):
    name: str
    description: str | None
    model: str | None
    tools: list[str]


class ToolInfo(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_approval: bool


class WorkflowInfo(BaseModel):
    name: str
    type: str
    entry: str | None


class TraceSummary(BaseModel):
    """Summary of a trace for list responses."""

    trace_id: str
    run_id: str | None = None
    event_count: int = 0
    first_event_at: str | None = None
    last_event_at: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_fastapi_app(agent_app: AgentApp) -> FastAPI:
    """Create a FastAPI application wrapping *agent_app*.

    Args:
        agent_app: A configured :class:`AgentApp` instance.

    Returns:
        A ``FastAPI`` application ready to serve.
    """
    api = FastAPI(
        title="Agent App Framework API",
        description="HTTP API for AgentApp runs and management.",
        version="0.1.0",
    )

    @api.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @api.get("/agents", response_model=list[AgentInfo])
    async def list_agents() -> list[AgentInfo]:
        infos: list[AgentInfo] = []
        for name in agent_app.agent_registry.list():
            spec = agent_app.agent_registry.get(name)
            infos.append(
                AgentInfo(
                    name=spec.name,
                    description=spec.description,
                    model=spec.model,
                    tools=spec.tools,
                )
            )
        return infos

    @api.get("/tools", response_model=list[ToolInfo])
    async def list_tools() -> list[ToolInfo]:
        infos: list[ToolInfo] = []
        for name in agent_app.tool_registry.list():
            spec = agent_app.tool_registry.get_spec(name)
            infos.append(
                ToolInfo(
                    name=spec.name,
                    description=spec.description,
                    risk_level=spec.risk_level,
                    requires_approval=spec.requires_approval,
                )
            )
        return infos

    @api.get("/workflows", response_model=list[WorkflowInfo])
    async def list_workflows() -> list[WorkflowInfo]:
        infos: list[WorkflowInfo] = []
        for name in agent_app.workflow_registry.list():
            wf = agent_app.workflow_registry.get(name)
            infos.append(
                WorkflowInfo(
                    name=wf.name,
                    type=wf.type.value,
                    entry=wf.entry,
                )
            )
        return infos

    @api.post("/runs")
    async def run(request: Request, req: RunRequest) -> dict:
        """Execute a run and return the result.

        Phase 15.1: Supports idempotency key via HTTP header (priority)
        or JSON body.  Duplicate keys return HTTP 409.
        """
        if not req.agent and not req.workflow:
            raise HTTPException(
                status_code=400,
                detail="Must provide exactly one of 'agent' or 'workflow'.",
            )
        if req.agent and req.workflow:
            raise HTTPException(
                status_code=400,
                detail="Provide 'agent' OR 'workflow', not both.",
            )

        # Phase 15.1: Header Idempotency-Key takes priority over body
        header_key = request.headers.get("Idempotency-Key")
        idempotency_key = header_key if header_key else req.idempotency_key

        try:
            result = await agent_app.run(
                workflow=req.workflow,
                agent=req.agent,
                input=req.input,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                session_id=req.session_id,
                permissions=req.permissions,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            # Phase 15.1: Convert idempotency errors to HTTP 409
            idemp_error = _extract_idempotency_error(exc)
            if idemp_error:
                raise HTTPException(
                    status_code=409,
                    detail=idemp_error,
                )
            raise

        # Check for idempotency conflict in result
        if result.status == "failed" and result.error and _is_idempotency_error(result.error):
            raise HTTPException(status_code=409, detail=result.error)

        return _result_to_dict(result)

    @api.post("/runs/stream")
    async def run_stream(req: RunRequest) -> StreamingResponse:
        """Execute a run and stream events via Server-Sent Events."""
        if not req.agent and not req.workflow:
            raise HTTPException(
                status_code=400,
                detail="Must provide exactly one of 'agent' or 'workflow'.",
            )
        if req.agent and req.workflow:
            raise HTTPException(
                status_code=400,
                detail="Provide 'agent' OR 'workflow', not both.",
            )

        async def _gen() -> Any:
            async for event in agent_app.stream(
                workflow=req.workflow,
                agent=req.agent,
                input=req.input,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                session_id=req.session_id,
            ):
                import json
                yield f"data: {json.dumps(event.to_dict())}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # -- Approval endpoints --
    @api.get("/approvals")
    async def list_approvals(tenant_id: str | None = None) -> list[dict]:
        """List pending approval requests."""
        pending = await agent_app.list_pending_approvals(tenant_id=tenant_id)
        return [
            {
                "approval_id": a.approval_id,
                "run_id": a.run_id,
                "tool_name": a.tool_name,
                "risk_level": a.risk_level,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
            }
            for a in pending
        ]

    @api.post("/approvals/{approval_id}/approve")
    async def approve(approval_id: str, body: ApprovalActionRequest) -> dict:
        """Approve a pending approval request."""
        try:
            req = await agent_app.approve(
                approval_id=approval_id,
                approved_by=body.approved_by,
                reason=body.reason,
            )
            return {
                "approval_id": req.approval_id,
                "status": req.status,
                "resolved_by": req.resolved_by,
                "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found.")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @api.post("/approvals/{approval_id}/reject")
    async def reject(approval_id: str, body: ApprovalActionRequest) -> dict:
        """Reject a pending approval request."""
        try:
            req = await agent_app.reject(
                approval_id=approval_id,
                rejected_by=body.approved_by,
                reason=body.reason,
            )
            return {
                "approval_id": req.approval_id,
                "status": req.status,
                "resolved_by": req.resolved_by,
                "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found.")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @api.post("/runs/{run_id}/resume")
    async def resume(
        request: Request,
        run_id: str,
        approval_id: str | None = None,
    ) -> dict:
        """Resume a run that was interrupted for approval.

        Phase 15.1: Supports idempotency key via HTTP header or body.
        """
        # Phase 15.1: Extract idempotency key from header or body
        header_key = request.headers.get("Idempotency-Key")
        body_key = None
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
                body_key = body.get("idempotency_key")
            except Exception:
                pass
        idempotency_key = header_key if header_key else body_key

        try:
            result = await agent_app.resume_workflow_run(
                workflow="",  # Not used in framework-level resume
                run_id=run_id,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            idemp_error = _extract_idempotency_error(exc)
            if idemp_error:
                raise HTTPException(
                    status_code=409,
                    detail=idemp_error,
                )
            raise

        if result.status == "failed" and result.error and _is_idempotency_error(result.error):
            raise HTTPException(status_code=409, detail=result.error)

        return _result_to_dict(result)

    # -- Run state endpoints (Phase 9) --
    @api.get("/runs/interrupted")
    async def list_interrupted_runs(tenant_id: str | None = None) -> list[dict]:
        """List all interrupted runs, optionally filtered by tenant."""
        if agent_app._run_state_store is None:
            return []
        runs = await agent_app._run_state_store.list_interrupted(tenant_id=tenant_id)
        return [
            {
                "run_id": run.run_id,
                "status": run.status,
                "agent_name": run.agent_name,
                "workflow_name": run.workflow_name,
                "input": run.input[:200] if len(run.input) > 200 else run.input,
                "interruptions": run.interruptions,
                "approval_ids": run.approval_ids,
                "backend_name": run.backend_name,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
            }
            for run in runs
        ]

    @api.get("/runs/{run_id}/state")
    async def get_run_state(run_id: str) -> dict:
        """Get the full state of a run by ID."""
        if agent_app._run_state_store is None:
            raise HTTPException(
                status_code=404,
                detail="Run state store not configured.",
            )
        try:
            run = await agent_app._run_state_store.get(run_id)
            return {
                "run_id": run.run_id,
                "status": run.status,
                "agent_name": run.agent_name,
                "workflow_name": run.workflow_name,
                "workflow_type": run.workflow_type,
                "input": run.input,
                "context": run.context.model_dump(mode="json"),
                "interruptions": run.interruptions,
                "approval_ids": run.approval_ids,
                "backend_name": run.backend_name,
                "backend_state": run.backend_state,
                "result_snapshot": run.result_snapshot,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
                "resumed_at": run.resumed_at.isoformat() if run.resumed_at else None,
                "error": run.error,
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    # -- Trace endpoints (Phase 12) --
    @api.get("/traces", response_model=list[TraceSummary])
    async def list_traces(
        run_id: str | None = None,
        tenant_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[TraceSummary]:
        """List trace summaries, optionally filtered."""
        collector = _get_trace_collector(agent_app)
        if collector is None:
            return []
        trace_ids = await collector.list_traces(tenant_id=tenant_id, run_id=run_id, limit=limit)
        summaries: list[TraceSummary] = []
        for tid in trace_ids:
            events = await collector.get_events(tid)
            if event_type:
                events = [e for e in events if event_type in _event_type_str(e)]
            if not events:
                continue
            status = _infer_status(events)
            first_ts = events[0].timestamp.isoformat() if events[0].timestamp else None
            last_ts = events[-1].timestamp.isoformat() if events[-1].timestamp else None
            summaries.append(TraceSummary(
                trace_id=tid,
                run_id=events[0].run_id,
                event_count=len(events),
                first_event_at=first_ts,
                last_event_at=last_ts,
                status=status,
            ))
        return summaries

    @api.get("/traces/{trace_id}")
    async def get_trace(trace_id: str) -> dict:
        """Get all events for a specific trace. Returns 404 if not found."""
        collector = _get_trace_collector(agent_app)
        if collector is None:
            raise HTTPException(status_code=404, detail="Trace collector not configured.")
        events = await collector.get_events(trace_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
        return {
            "trace_id": trace_id,
            "run_id": events[0].run_id,
            "events": [
                {
                    "event_id": getattr(e, "event_id", ""),
                    "event_type": str(getattr(e, "event_type", "")),
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "run_id": e.run_id,
                    "user_id": e.user_id,
                    "tenant_id": e.tenant_id,
                    "workflow_name": e.workflow_name,
                    "agent_name": e.agent_name,
                    "tool_name": e.tool_name,
                    "approval_id": e.approval_id,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                    "error": e.error,
                    "data": e.data,
                }
                for e in events
            ],
        }

    return api


def _result_to_dict(result: Any) -> dict:
    """Convert an AppRunResult to a plain dict for JSON serialisation."""
    return {
        "run_id": result.run_id,
        "status": result.status,
        "final_output": result.final_output,
        "interruptions": result.interruptions,
        "tool_calls": result.tool_calls,
        "handoffs": result.handoffs,
        "usage": result.usage,
        "latency_ms": result.latency_ms,
        "trace_id": result.trace_id,
        "error": result.error,
    }


def _is_idempotency_error(error: dict[str, Any] | None) -> bool:
    """Check if an error dict represents an idempotency conflict."""
    if not error or not isinstance(error, dict):
        return False
    error_type = error.get("type", "")
    return error_type in (
        "idempotency_duplicate",
        "idempotency_key_reuse_mismatch",
    )


def _extract_idempotency_error(exc: Exception) -> dict[str, Any] | None:
    """Extract a structured idempotency error dict from an exception.

    Handles DagError wrapping IdempotencyError and direct IdempotencyError.
    Returns None if the exception is not an idempotency conflict.
    """
    # Check for DagError wrapping IdempotencyError
    inner = getattr(exc, "__cause__", None) or exc
    error_dict = getattr(inner, "args", [{}])[0] if getattr(inner, "args", None) else None
    if isinstance(error_dict, dict) and _is_idempotency_error(error_dict):
        return error_dict
    # Check if the exception itself has to_dict
    if hasattr(inner, "to_dict"):
        d = inner.to_dict()
        if _is_idempotency_error(d):
            return d
    return None


def _get_trace_collector(app: Any) -> Any:
    """Extract trace_collector from an AgentApp, returning None if absent."""
    return getattr(app, "trace_collector", None)


def _event_type_str(event: Any) -> str:
    """Extract the string value of an event's event_type field."""
    val = getattr(event, "event_type", None)
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


def _infer_status(events: list[Any]) -> str | None:
    """Infer overall run status from a list of trace events."""
    failed_types = {"run.failed", "workflow.failed", "agent.failed", "tool.failed"}
    for e in events:
        et = _event_type_str(e)
        if et in failed_types:
            return "failed"
    for e in events:
        if _event_type_str(e) == "run.interrupted":
            return "interrupted"
    for e in events:
        if _event_type_str(e) == "run.completed":
            return "completed"
    return None
