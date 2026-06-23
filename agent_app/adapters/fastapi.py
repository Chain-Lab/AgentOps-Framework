"""FastAPI adapter — HTTP API for AgentApp.

This module is an optional dependency.  Install with:

    pip install 'agent-app-framework[api]'

If FastAPI is not installed, importing this module raises ImportError
with a clear message.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agent_app.runtime.streaming import StreamEvent

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import PlainTextResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
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


# -- Phase 24: Policy diagnostics models --
class PolicySimulateRequest(BaseModel):
    tool_name: str
    risk_level: str = "low"
    workflow_type: str | None = None
    agent_name: str | None = None
    target_agent: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class PolicyExplainRequest(BaseModel):
    tool_name: str
    risk_level: str = "low"
    workflow_type: str | None = None
    agent_name: str | None = None
    target_agent: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class PolicyDecisionSummary(BaseModel):
    decision_id: str
    action: str
    rule_name: str | None = None
    reason: str | None = None
    tool_name: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Phase 56: Request models for operations endpoints
# (Module-level so Pydantic v2 can resolve forward references)
# ---------------------------------------------------------------------------

class _RunOnceRequest(BaseModel):
    """Request body for retry daemon run-once."""
    dry_run: bool = False
    confirmation: str | None = None


class _StartDaemonRequest(BaseModel):
    """Request body for retry daemon start."""
    confirmation: str | None = None


class _StopDaemonRequest(BaseModel):
    """Request body for retry daemon stop."""
    confirmation: str | None = None


class BatchReplayRequest(BaseModel):
    """Request body for batch DLQ replay."""
    target_id: str | None = None
    alert_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    dry_run: bool = False
    confirmation: str | None = None


class PriorityUpdateRequest(BaseModel):
    """Request body for retry queue priority update."""
    priority: int
    confirmation: str | None = None


class CleanupRequest(BaseModel):
    """Request body for archive cleanup."""
    data_type: str = Field(default="rollup")
    dry_run: bool = False
    confirmation: str | None = None


class ClearCheckpointRequest(BaseModel):
    """Request body for clearing archive checkpoints."""
    checkpoint_id: str
    confirmation: str | None = None


class IncrementalRollupRequest(BaseModel):
    """Request body for incremental rollup build."""
    granularity: str = Field(default="hourly")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_fastapi_app(agent_app: AgentApp, console_config: Any = None) -> FastAPI:
    """Create a FastAPI application wrapping *agent_app*.

    Args:
        agent_app: A configured :class:`AgentApp` instance.
        console_config: Optional PolicyConsoleConfig.  When not provided,
            reads from ``agent_app._console_config`` if available.

    Returns:
        A ``FastAPI`` application ready to serve.
    """
    api = FastAPI(
        title="Agent App Framework API",
        description="HTTP API for AgentApp runs and management.",
        version="0.1.0",
    )

    # Phase 26: Resolve console config — explicit arg > agent_app attr > None
    if console_config is None:
        console_config = getattr(agent_app, "_console_config", None)

    # -- Phase 26: Mount policy console if enabled --
    _mount_policy_console(api, agent_app, console_config)

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

    # -- Phase 24: Policy diagnostics endpoints --
    # Policy engine is looked up dynamically per-request

    @api.get("/policies")
    async def get_policies() -> dict:
        """Return policy configuration summary (no sensitive data)."""
        from agent_app.governance.policy import ConfigurablePolicyEngine, DefaultPolicyEngine
        policy_engine = getattr(agent_app, "policy_engine", None)

        gov = getattr(getattr(agent_app, "_config", None), "governance", None)
        policy_cfg = getattr(gov, "policies", None) if gov else None

        if policy_cfg is None or not getattr(policy_cfg, "enabled", False):
            return {"enabled": False, "rules": []}

        rules_summary = []
        for rule in policy_cfg.rules:
            rules_summary.append({
                "name": rule.name,
                "when": dict(rule.when) if hasattr(rule, "when") else {},
                "then_action": rule.then.get("action") if hasattr(rule, "then") else None,
            })

        return {
            "enabled": True,
            "default_action": getattr(policy_cfg, "default_action", "allow"),
            "rule_count": len(rules_summary),
            "rules": rules_summary,
        }

    @api.post("/policies/validate")
    async def validate_policy() -> dict:
        """Validate the current policy configuration."""
        from agent_app.governance.policy_validation import validate_policy_config

        gov = getattr(getattr(agent_app, "_config", None), "governance", None)
        policy_cfg = getattr(gov, "policies", None) if gov else None

        if policy_cfg is None:
            return {"valid": True, "issues": [], "message": "No policy config."}

        result = validate_policy_config(policy_cfg)
        return {
            "valid": result.valid,
            "issues": [
                {
                    "level": i.level,
                    "rule_name": i.rule_name,
                    "message": i.message,
                    "path": i.path,
                }
                for i in result.issues
            ],
        }

    def _get_policy_engine(agent_app: Any):
        """Get the policy engine, building from config if needed."""
        from agent_app.governance.policy import ConfigurablePolicyEngine, DefaultPolicyEngine
        engine = getattr(agent_app, "policy_engine", None)
        if engine is not None:
            return engine
        # Build from config if available
        gov = getattr(getattr(agent_app, "_config", None), "governance", None)
        policy_cfg = getattr(gov, "policies", None) if gov else None
        if policy_cfg is not None and getattr(policy_cfg, "enabled", False):
            rules = [r.model_dump() if hasattr(r, "model_dump") else r for r in policy_cfg.rules]
            return ConfigurablePolicyEngine(
                rules=rules,
                default_action=getattr(policy_cfg, "default_action", "allow"),
            )
        return DefaultPolicyEngine()

    @api.post("/policies/simulate")
    async def simulate_policy(req: PolicySimulateRequest) -> dict:
        """Simulate a policy decision without executing the tool."""
        from agent_app.governance.policy_simulator import PolicySimulationInput, PolicySimulator

        engine = _get_policy_engine(agent_app)
        sim = PolicySimulator(policy_engine=engine)
        inp = PolicySimulationInput(
            tool_name=req.tool_name,
            risk_level=req.risk_level,
            workflow_type=req.workflow_type,
            agent_name=req.agent_name,
            target_agent=req.target_agent,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            roles=list(req.roles),
            permissions=list(req.permissions),
        )
        result = await sim.simulate(inp)
        return {
            "tool": req.tool_name,
            "action": result.decision.action.value,
            "allowed": result.decision.allowed,
            "requires_approval": result.decision.requires_approval,
            "reason": result.decision.reason,
            "rule_name": result.decision.metadata.get("rule_name"),
            "ttl_seconds": result.decision.ttl_seconds,
        }

    @api.post("/policies/explain")
    async def explain_policy(req: PolicyExplainRequest) -> dict:
        """Explain a policy decision with matched rule and conditions."""
        from agent_app.governance.policy_simulator import PolicySimulationInput, PolicySimulator

        engine = _get_policy_engine(agent_app)
        sim = PolicySimulator(policy_engine=engine)
        inp = PolicySimulationInput(
            tool_name=req.tool_name,
            risk_level=req.risk_level,
            workflow_type=req.workflow_type,
            agent_name=req.agent_name,
            target_agent=req.target_agent,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            roles=list(req.roles),
            permissions=list(req.permissions),
        )
        result = await sim.explain(inp)
        trace = result.trace
        if trace is None:
            return {"error": "No trace available"}
        return {
            "decision_id": trace.decision_id,
            "action": trace.action.value,
            "rule_name": trace.rule_name,
            "reason": trace.reason,
            "matched_conditions": trace.matched_conditions,
            "context_summary": trace.context_summary,
            "created_at": trace.created_at.isoformat() if trace.created_at else None,
        }

    @api.get("/policy-decisions")
    async def list_policy_decisions(
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List policy decisions with filtering and pagination.

        Uses PolicyDecisionStore when available, falls back to audit log.
        """
        store = getattr(agent_app, "policy_decision_store", None)
        if store is not None:
            traces = await store.query(
                run_id=run_id,
                tenant_id=tenant_id,
                agent_name=agent_name,
                tool_name=tool_name,
                rule_name=rule_name,
                action=action,
                limit=limit,
                offset=offset,
            )
            return [
                {
                    "decision_id": t.decision_id,
                    "run_id": t.run_id,
                    "rule_name": t.rule_name,
                    "action": t.action.value,
                    "reason": t.reason,
                    "tool_name": t.tool_name,
                    "matched_conditions": t.matched_conditions,
                    "context_summary": t.context_summary,
                    "created_at": t.created_at.isoformat(),
                }
                for t in traces
            ]

        # Fallback: read from audit log
        audit_logger = getattr(agent_app, "audit_logger", None)
        if audit_logger is None:
            return []
        policy_event_types = {
            "policy.evaluated", "policy.allowed", "policy.denied",
            "policy.approval_required", "policy.audit_only", "policy.simulated",
        }
        events = audit_logger.list_events(
            run_id=run_id,
            tenant_id=tenant_id,
        )
        results = []
        for ev in events:
            if ev.event_type in policy_event_types:
                results.append({
                    "event_id": ev.event_id,
                    "event_type": ev.event_type,
                    "run_id": ev.run_id,
                    "tenant_id": ev.tenant_id,
                    "tool_name": ev.tool_name,
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                    "data": ev.data,
                })
            if len(results) >= limit:
                break
        return results[offset:offset + limit]

    @api.get("/policy-decisions/{decision_id}")
    async def get_policy_decision(decision_id: str) -> dict:
        """Get a single policy decision by ID."""
        store = getattr(agent_app, "policy_decision_store", None)
        if store is None:
            return {"error": "Policy decision store not configured."}
        try:
            trace = await store.get(decision_id)
        except KeyError:
            return {"error": f"Decision '{decision_id}' not found."}
        return {
            "decision_id": trace.decision_id,
            "run_id": trace.run_id,
            "rule_name": trace.rule_name,
            "action": trace.action.value,
            "reason": trace.reason,
            "tool_name": trace.tool_name,
            "matched_conditions": trace.matched_conditions,
            "context_summary": trace.context_summary,
            "created_at": trace.created_at.isoformat(),
        }

    @api.get("/policy-report")
    async def get_policy_report(
        run_id: str | None = None,
        tenant_id: str | None = None,
        agent_name: str | None = None,
        tool_name: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        limit: int = 1000,
    ) -> dict:
        """Generate an aggregated policy decision report."""
        from agent_app.governance.policy_decision_store import PolicyReportingService
        store = getattr(agent_app, "policy_decision_store", None)
        if store is None:
            return {"error": "Policy decision store not configured."}
        service = PolicyReportingService(store)
        report = await service.generate_report(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_name=agent_name,
            tool_name=tool_name,
            rule_name=rule_name,
            action=action,
            limit=limit,
        )
        return report.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Phase 56: FastAPI operations endpoints for Phase 55 services
    # ------------------------------------------------------------------

    # -- Request models are defined at module level for Pydantic v2 compatibility --

    def _redact_error(exc: Exception) -> str:
        """Redact an exception message — no stack traces, no secrets."""
        msg = str(exc)
        _SENSITIVE_PATTERNS = (
            "token", "secret", "password", "api_key", "authorization",
            "x-signature", "x-api-key", "x-secret", "x-auth-token",
        )
        lowered = msg.lower()
        for pattern in _SENSITIVE_PATTERNS:
            if pattern in lowered:
                return "Internal server error."
        return msg[:500]

    async def _safe_service_call(result):
        """Normalize a service result to a dict.

        Accepts both coroutines and resolved objects.  Never calls asyncio.run().
        """
        if hasattr(result, "__await__"):
            result = await result
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if hasattr(result, "__dict__"):
            return result.__dict__
        return result

    async def _safe_service_call_async(result):
        """Await a service result, catching and redacting errors."""
        try:
            return await _safe_service_call(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    def _dt_iso(dt) -> str | None:
        """Convert a datetime to ISO string, or None."""
        if dt is None:
            return None
        if hasattr(dt, "isoformat"):
            return dt.isoformat()
        return str(dt)

    def _attempt_to_dict(attempt: Any) -> dict:
        """Convert an AlertDeliveryAttempt to a serialisable dict."""
        return {
            "attempt_id": getattr(attempt, "attempt_id", ""),
            "alert_id": getattr(attempt, "alert_id", ""),
            "target_id": getattr(attempt, "target_id", ""),
            "status": str(getattr(attempt, "status", "")),
            "attempt": getattr(attempt, "attempt", 0),
            "priority": getattr(attempt, "priority", 0),
            "error_code": getattr(attempt, "error_code", None),
            "error_message": getattr(attempt, "error_message", None),
            "created_at": _dt_iso(getattr(attempt, "created_at", None)),
            "delivered_at": _dt_iso(getattr(attempt, "delivered_at", None)),
            "next_retry_at": _dt_iso(getattr(attempt, "next_retry_at", None)),
        }

    def _dedup_to_dict(record: Any) -> dict:
        """Convert a NotificationAlertDedupRecord to a serialisable dict."""
        return {
            "dedup_key": record.dedup_key,
            "alert_id": record.alert_id,
            "federation_id": record.federation_id,
            "channel": record.channel,
            "metric": record.metric,
            "severity": record.severity,
            "occurrence_count": record.occurrence_count,
            "first_seen_at": _dt_iso(record.first_seen_at),
            "last_seen_at": _dt_iso(record.last_seen_at),
            "expires_at": _dt_iso(record.expires_at),
            "status": record.status,
        }

    def _checkpoint_to_dict(cp: Any) -> dict:
        """Convert an ArchiveCheckpoint to a serialisable dict."""
        return {
            "checkpoint_id": cp.checkpoint_id,
            "data_type": cp.data_type,
            "last_processed_id": cp.last_processed_id,
            "last_processed_at": _dt_iso(cp.last_processed_at),
            "records_processed": cp.records_processed,
            "is_complete": cp.is_complete,
            "created_at": _dt_iso(cp.created_at),
            "updated_at": _dt_iso(cp.updated_at),
        }

    def _rollup_to_dict(rollup: Any) -> dict:
        """Convert a NotificationMetricsRollup to a serialisable dict."""
        return {
            "rollup_id": rollup.rollup_id,
            "granularity": rollup.granularity.value if hasattr(rollup.granularity, "value") else str(rollup.granularity),
            "window_start": _dt_iso(rollup.window_start),
            "window_end": _dt_iso(rollup.window_end),
            "federation_id": rollup.federation_id,
            "channel": rollup.channel,
            "total": rollup.total,
            "sent": rollup.sent,
            "failed": rollup.failed,
            "suppressed": rollup.suppressed,
            "dlq": rollup.dlq,
            "retry_scheduled": rollup.retry_scheduled,
            "success_rate": rollup.success_rate,
            "failure_rate": rollup.failure_rate,
            "dlq_rate": rollup.dlq_rate,
            "avg_latency_ms": rollup.avg_latency_ms,
            "p95_latency_ms": rollup.p95_latency_ms,
            "created_at": _dt_iso(rollup.created_at),
        }

    # -- Retry daemon endpoints --
    @api.get("/federation/notifications/retry-daemon/status")
    async def get_retry_daemon_status() -> dict:
        """Return the current status of the alert delivery retry daemon."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            return {"is_running": False, "config": None}
        # Use _running flag directly for cross-loop compatibility
        running = getattr(daemon, "_running", False)
        return {
            "is_running": running,
            "config": {
                "enabled": daemon._config.enabled,
                "interval_seconds": daemon._config.interval_seconds,
                "jitter_seconds": daemon._config.jitter_seconds,
                "batch_limit": daemon._config.batch_limit,
            } if hasattr(daemon, "_config") else None,
        }

    @api.post("/federation/notifications/retry-daemon/run-once")
    async def retry_daemon_run_once(body: _RunOnceRequest) -> dict:
        """Execute a single retry daemon run."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            raise HTTPException(
                status_code=404,
                detail="Retry daemon is not configured.",
            )
        if body.dry_run is False and body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        result = await daemon.run_once(dry_run=body.dry_run)
        return {"result": await _safe_service_call_async(result)}

    @api.post("/federation/notifications/retry-daemon/start")
    async def retry_daemon_start(body: _StartDaemonRequest) -> dict:
        """Start the retry daemon loop."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            raise HTTPException(
                status_code=404,
                detail="Retry daemon is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        await daemon.start()
        return {"is_running": daemon._running}

    @api.post("/federation/notifications/retry-daemon/stop")
    async def retry_daemon_stop(body: _StopDaemonRequest) -> dict:
        """Stop the retry daemon loop."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            raise HTTPException(
                status_code=404,
                detail="Retry daemon is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        await daemon.stop()
        return {"is_running": daemon._running}

    @api.get("/federation/notifications/retry-daemon/health")
    async def get_retry_daemon_health() -> dict:
        """Return daemon health status (healthy/degraded/unhealthy/stopped)."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            return {"state": "stopped", "detail": "Retry daemon is not configured."}
        return daemon.get_health_status()

    @api.get("/federation/notifications/retry-daemon/ready")
    async def get_retry_daemon_ready() -> dict:
        """Readiness probe — 200 when daemon is healthy or degraded, 503 when unhealthy."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            return {"ready": True, "state": "stopped"}
        health = daemon.get_health_status()
        is_ready = health["state"] in ("healthy", "degraded", "stopped")
        return {"ready": is_ready, "state": health["state"]}

    @api.get("/federation/notifications/retry-daemon/live")
    async def get_retry_daemon_live() -> dict:
        """Liveness probe — always alive if the daemon is configured."""
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        if daemon is None:
            return {"alive": True, "state": "stopped"}
        return {"alive": True, "state": daemon.get_health_status()["state"]}

    # -- Retry queue endpoints --
    @api.get("/federation/notifications/retry-queue")
    async def list_retry_queue(
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List alert delivery attempts in the retry queue, sorted by priority."""
        priority_queue = getattr(agent_app, "_federation_notification_priority_queue", None)
        if priority_queue is None:
            return {"attempts": [], "total": 0, "limit": limit, "offset": offset}
        try:
            attempts = await priority_queue.dequeue(limit=min(limit, 1000))
            return {
                "attempts": [_attempt_to_dict(a) for a in attempts],
                "total": await priority_queue.count(),
                "limit": limit,
                "offset": offset,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/retry-queue/{attempt_id}/priority")
    async def update_retry_queue_priority(
        attempt_id: str,
        body: PriorityUpdateRequest,
    ) -> dict:
        """Update the priority of a retry queue attempt."""
        delivery_store = getattr(
            agent_app, "_federation_notification_alert_delivery_store", None
        )
        if delivery_store is None:
            raise HTTPException(
                status_code=404,
                detail="Alert delivery store is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            existing = await delivery_store.get_attempt(attempt_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"Attempt '{attempt_id}' not found.",
            )
        try:
            from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                AlertDeliveryAttempt,
            )
            updated = AlertDeliveryAttempt(
                attempt_id=existing.attempt_id,
                alert_id=existing.alert_id,
                target_id=existing.target_id,
                channel_type=existing.channel_type,
                status=existing.status,
                attempt=existing.attempt,
                next_retry_at=existing.next_retry_at,
                error_code=existing.error_code,
                error_message=existing.error_message,
                payload_preview=getattr(existing, "payload_preview", {}),
                priority=body.priority,
                delivered_at=getattr(existing, "delivered_at", None),
                created_at=getattr(existing, "created_at", None),
            )
            result = await delivery_store.update_attempt(updated)
            return _attempt_to_dict(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    # -- DLQ endpoints --
    @api.get("/federation/notifications/dlq")
    async def list_dlq(
        federation_id: str | None = None,
        approval_id: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List dead-letter queue entries."""
        delivery_service = getattr(
            agent_app, "_federation_notification_alert_delivery_service", None
        )
        delivery_store = getattr(
            agent_app, "_federation_notification_alert_delivery_store", None
        )
        if delivery_store is None:
            return {"attempts": [], "total": 0, "limit": limit, "offset": offset}
        try:
            # Use the store directly for listing DLQ items
            from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                AlertDeliveryStatus,
            )
            attempts = await delivery_store.list_attempts(
                status=AlertDeliveryStatus.DLQ,
                limit=limit,
                offset=offset,
            )
            return {
                "attempts": [_attempt_to_dict(a) for a in attempts],
                "total": len(attempts),
                "limit": limit,
                "offset": offset,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/dlq/{attempt_id}/replay")
    async def replay_dlq_attempt(
        attempt_id: str,
        body: BatchReplayRequest,
    ) -> dict:
        """Replay a single DLQ attempt."""
        delivery_service = getattr(
            agent_app, "_federation_notification_alert_delivery_service", None
        )
        if delivery_service is None:
            raise HTTPException(
                status_code=404,
                detail="Alert delivery service is not configured.",
            )
        if body.dry_run is False and body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            result = await delivery_service.replay_dlq_attempt(
                attempt_id=attempt_id,
                dry_run=body.dry_run,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"DLQ attempt '{attempt_id}' not found.",
            )
        return {"attempt": _attempt_to_dict(result)}

    @api.post("/federation/notifications/dlq/batch-replay")
    async def batch_replay_dlq(body: BatchReplayRequest) -> dict:
        """Replay multiple DLQ entries matching the given filters."""
        delivery_service = getattr(
            agent_app, "_federation_notification_alert_delivery_service", None
        )
        if delivery_service is None:
            raise HTTPException(
                status_code=404,
                detail="Alert delivery service is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
                AlertDeliveryStatus,
            )
            delivery_store = getattr(
                agent_app, "_federation_notification_alert_delivery_store", None
            )
            if delivery_store is None:
                return {"result": {"attempts": [], "total": 0, "dry_run": body.dry_run}}

            # Parse time filters
            since_dt = None
            until_dt = None
            if body.since:
                since_dt = datetime.fromisoformat(body.since.replace("Z", "+00:00"))
            if body.until:
                until_dt = datetime.fromisoformat(body.until.replace("Z", "+00:00"))

            # Fetch DLQ attempts
            dlq_attempts = await delivery_store.list_attempts(
                status=AlertDeliveryStatus.DLQ,
                limit=min(body.limit, 1000),
            )
            replayed = []
            for attempt in dlq_attempts:
                # Apply filters
                if body.target_id and attempt.target_id != body.target_id:
                    continue
                if body.alert_id and attempt.alert_id != body.alert_id:
                    continue
                if since_dt and attempt.created_at < since_dt:
                    continue
                if until_dt and attempt.created_at > until_dt:
                    continue
                result = await delivery_service.replay_dlq_attempt(
                    attempt_id=attempt.attempt_id,
                    dry_run=body.dry_run,
                )
                if result is not None:
                    replayed.append(_attempt_to_dict(result))
            return {"result": {"attempts": replayed, "total": len(replayed), "dry_run": body.dry_run}}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    # -- Dedup endpoints --
    @api.get("/federation/notifications/dedup/active")
    async def list_active_dedup(
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List active (open) dedup records."""
        dedup_store = getattr(agent_app, "_federation_notification_dedup_store", None)
        if dedup_store is None:
            return {"records": [], "total": 0, "limit": limit, "offset": offset}
        try:
            records = dedup_store.list_active(limit=min(limit, 1000), offset=offset)
            return {
                "records": [_dedup_to_dict(r) for r in records],
                "total": len(records),
                "limit": limit,
                "offset": offset,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/dedup/prune")
    async def prune_dedup(body: BatchReplayRequest) -> dict:
        """Prune expired dedup records."""
        dedup_store = getattr(agent_app, "_federation_notification_dedup_store", None)
        if dedup_store is None:
            raise HTTPException(
                status_code=404,
                detail="Dedup store is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            count = dedup_store.prune_expired()
            return {"pruned_count": count}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.get("/federation/notifications/dedup/{dedup_key}")
    async def get_dedup_record(dedup_key: str) -> dict:
        """Get a specific dedup record by key."""
        dedup_store = getattr(agent_app, "_federation_notification_dedup_store", None)
        if dedup_store is None:
            raise HTTPException(
                status_code=404,
                detail="Dedup store is not configured.",
            )
        try:
            record = dedup_store.get(dedup_key)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Dedup key '{dedup_key}' not found.",
            )
        return _dedup_to_dict(record)

    # -- Archive cleanup endpoints --
    @api.get("/federation/notifications/archives/checkpoint")
    async def list_archive_checkpoints() -> dict:
        """List archive cleanup checkpoints."""
        cleanup_service = getattr(
            agent_app, "_federation_notification_archive_cleanup_service", None
        )
        if cleanup_service is None:
            return {"checkpoints": []}
        try:
            checkpoint_store = cleanup_service._checkpoint_store
            if hasattr(checkpoint_store, "list_checkpoints"):
                checkpoints = await checkpoint_store.list_checkpoints()
            else:
                checkpoints = []
            return {
                "checkpoints": [_checkpoint_to_dict(cp) for cp in checkpoints],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/archives/cleanup")
    async def run_archive_cleanup(body: CleanupRequest) -> dict:
        """Run archive cleanup for a data type."""
        cleanup_service = getattr(
            agent_app, "_federation_notification_archive_cleanup_service", None
        )
        if cleanup_service is None:
            raise HTTPException(
                status_code=404,
                detail="Archive cleanup service is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            result = await cleanup_service.run_cleanup(
                data_type=body.data_type,
                dry_run=body.dry_run,
            )
            return {"result": await _safe_service_call_async(result)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/archives/checkpoint/clear")
    async def clear_archive_checkpoint(body: ClearCheckpointRequest) -> dict:
        """Clear (delete) an archive checkpoint."""
        cleanup_service = getattr(
            agent_app, "_federation_notification_archive_cleanup_service", None
        )
        if cleanup_service is None:
            raise HTTPException(
                status_code=404,
                detail="Archive cleanup service is not configured.",
            )
        if body.confirmation != "yes":
            raise HTTPException(
                status_code=400,
                detail="Destructive operation requires confirmation='yes'.",
            )
        try:
            checkpoint_store = cleanup_service._checkpoint_store
            if hasattr(checkpoint_store, "delete_checkpoint"):
                await checkpoint_store.delete_checkpoint(body.checkpoint_id)
            return {"checkpoint_id": body.checkpoint_id, "deleted": True}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    # -- Rollup endpoints --
    @api.get("/federation/notifications/rollup")
    async def list_rollups(
        granularity: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List metric rollup records."""
        rollup_service = getattr(agent_app, "_federation_notification_rollup_service", None)
        if rollup_service is None:
            return {"rollups": [], "total": 0, "limit": limit, "offset": offset}
        try:
            gran_enum = None
            if granularity is not None:
                gran_enum = NotificationRollupGranularity(granularity)
            rollups = await rollup_service.list_rollups_async(
                granularity=gran_enum,
                channel=channel,
                limit=min(limit, 1000),
                offset=offset,
            )
            return {
                "rollups": [_rollup_to_dict(r) for r in rollups],
                "total": len(rollups),
                "limit": limit,
                "offset": offset,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.get("/federation/notifications/rollup/checkpoints")
    async def list_rollup_checkpoints() -> dict:
        """List rollup build checkpoints."""
        rollup_service = getattr(agent_app, "_federation_notification_rollup_service", None)
        if rollup_service is None:
            return {"checkpoints": []}
        try:
            checkpoints = await rollup_service.list_checkpoints_async()
            return {"checkpoints": checkpoints}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.post("/federation/notifications/rollup/incremental")
    async def build_incremental_rollup(body: IncrementalRollupRequest) -> dict:
        """Build an incremental rollup for recent delivery events."""
        rollup_service = getattr(agent_app, "_federation_notification_rollup_service", None)
        if rollup_service is None:
            raise HTTPException(
                status_code=404,
                detail="Rollup service is not configured.",
            )
        try:
            gran_enum = NotificationRollupGranularity(body.granularity)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid granularity '{body.granularity}'. Use 'hourly' or 'daily'.",
            )
        try:
            rollups = await rollup_service.build_incremental_rollup(
                granularity=gran_enum,
            )
            return {"rollups": [_rollup_to_dict(r) for r in rollups]}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_redact_error(exc))

    @api.get("/federation/notifications/prometheus", response_class=PlainTextResponse)
    async def get_prometheus_metrics() -> str:
        """Export notification metrics in Prometheus text exposition format.

        Phase 53: Core notification metrics.
        Phase 56: Expanded with daemon health, queue depths, dedup counts.
        """
        from agent_app.runtime.policy_rollout_federation_notification_prometheus import (
            export_notification_prometheus_metrics,
        )

        notification_service = getattr(agent_app, "_federation_notification_service", None)
        if notification_service is None:
            return export_notification_prometheus_metrics([], [], [])

        obs_store = getattr(notification_service, "_observability_store", None)

        metrics: list[Any] = []
        alerts: list[Any] = []
        if obs_store is not None:
            try:
                metrics = await obs_store.aggregate_metrics(window_minutes=60)
            except Exception:
                pass
            try:
                alerts = await obs_store.list_alerts(limit=1000)
            except Exception:
                pass

        # Daemon health
        daemon = getattr(agent_app, "_federation_notification_retry_daemon", None)
        daemon_health = daemon.get_health_status() if daemon is not None else None

        # Queue depths
        retry_queue_depth = 0
        priority_queue = getattr(agent_app, "_federation_notification_priority_queue", None)
        if priority_queue is not None:
            try:
                retry_queue_depth = await priority_queue.count()
            except Exception:
                pass

        dlq_depth = 0
        dlq_store = getattr(agent_app, "federation_dlq_store", None)
        if dlq_store is not None:
            try:
                dlq_items = await dlq_store.list(limit=10000)
                dlq_depth = len(dlq_items)
            except Exception:
                pass

        dedup_active = 0
        dedup_store = getattr(agent_app, "_federation_notification_dedup_store", None)
        if dedup_store is not None:
            try:
                dedup_records = dedup_store.list_active(limit=10000)
                dedup_active = len(dedup_records)
            except Exception:
                pass

        return export_notification_prometheus_metrics(
            metrics=metrics,
            health=[],
            alerts=alerts,
            daemon_health=daemon_health,
            retry_queue_depth=retry_queue_depth,
            dlq_depth=dlq_depth,
            dedup_active=dedup_active,
        )

    return api


def _mount_policy_console(api: FastAPI, agent_app: AgentApp, console_config: Any) -> None:
    """Mount the policy console router if enabled (Phase 26)."""
    if console_config is None or not getattr(console_config, "enabled", False):
        return
    from agent_app.console.router import build_policy_console_router
    from agent_app.runtime.policy_replay_store import InMemoryPolicyReplayStore
    from agent_app.runtime.policy_replay_jobs import InMemoryPolicyReplayJobStore
    store = getattr(agent_app, "policy_decision_store", None)
    # Phase 27: replay store (in-memory, created on mount)
    replay_store = getattr(agent_app, "_replay_store", None)
    if replay_store is None:
        replay_store = InMemoryPolicyReplayStore()
    # Phase 28: replay job store (in-memory, created on mount)
    replay_job_store = getattr(agent_app, "_replay_job_store", None)
    if replay_job_store is None:
        replay_job_store = InMemoryPolicyReplayJobStore()
    router = build_policy_console_router(
        store=store, config=console_config, replay_store=replay_store,
        replay_job_store=replay_job_store,
        # Phase 29: policy release stores
        bundle_store=_get_bundle_store(agent_app),
        gate_store=_get_gate_store(agent_app),
        # Phase 30: promotion store and release service
        promotion_store=_get_promotion_store(agent_app),
        release_service=getattr(agent_app, "_release_service", None),
        # Phase 31: activation store
        activation_store=getattr(agent_app, "_activation_store", None),
        # Phase 32: environment store
        environment_store=getattr(agent_app, "_environment_store", None),
        # Phase 33: ring stores
        ring_store=getattr(agent_app, "_ring_store", None),
        ring_assignment_store=getattr(agent_app, "_ring_assignment_store", None),
        # Phase 34: event store, reload manager, ring router
        event_store=getattr(agent_app, "_event_store", None),
        reload_manager=getattr(agent_app, "_reload_manager", None),
        ring_router=getattr(agent_app, "_ring_router", None),
        # Phase 35: rollout store and service
        rollout_store=getattr(agent_app, "_rollout_store", None),
        rollout_service=getattr(agent_app, "_rollout_service", None),
        # Phase 36: rollout approval store
        approval_store=getattr(agent_app, "_rollout_approval_store", None),
        # Phase 38: runtime policy store and enforcement service
        runtime_policy_store=getattr(agent_app, "_runtime_policy_store", None),
        policy_enforcement_service=getattr(agent_app, "_policy_enforcement_service", None),
        # Phase 39: policy observability service
        observability_service=getattr(agent_app, "policy_observability_service", None),
        # Phase 40: policy simulation service
        simulation_service=getattr(agent_app, "policy_simulation_service", None),
        # Phase 41: simulation gate evaluator
        simulation_gate_evaluator=getattr(agent_app, "simulation_gate_evaluator", None),
        # Phase 42: release gate automation service
        release_gate_automation_service=getattr(agent_app, "release_gate_automation_service", None),
        # Phase 43: rollout gate automation service
        rollout_gate_automation_service=getattr(agent_app, "rollout_gate_automation_service", None),
        # Phase 44: notification and expiration services
        notification_service=getattr(agent_app, "notification_service", None),
        expiration_service=getattr(agent_app, "expiration_service", None),
        # Phase 45: rollout history service
        rollout_history_service=getattr(agent_app, "rollout_history_service", None),
        # Phase 46: federation service and stores
        rollout_federation_service=getattr(agent_app, "rollout_federation_service", None),
        federated_rollout_target_store=getattr(agent_app, "federated_rollout_target_store", None),
        federated_rollout_plan_store=getattr(agent_app, "federated_rollout_plan_store", None),
        # Phase 47: federation observability service
        federation_observability_service=getattr(agent_app, "federation_observability_service", None),
        # Phase 48: federation approval store and service
        federation_approval_store=getattr(agent_app, "federation_approval_store", None),
        federation_approval_service=getattr(agent_app, "federation_approval_service", None),
        # Phase 49: federation notification store and escalation worker
        federation_notification_store=getattr(agent_app, "federation_notification_store", None),
        federation_escalation_worker=getattr(agent_app, "federation_escalation_worker", None),
        # Phase 50: federation DLQ store and scheduled worker
        federation_dlq_store=getattr(agent_app, "federation_dlq_store", None),
        federation_scheduled_worker=getattr(agent_app, "federation_scheduled_worker", None),
        # Phase 51: federation notification template and preference stores/services
        federation_notification_template_store=getattr(agent_app, "federation_notification_template_store", None),
        federation_notification_template_service=getattr(agent_app, "federation_notification_template_service", None),
        federation_notification_preference_store=getattr(agent_app, "federation_notification_preference_store", None),
        federation_notification_preference_service=getattr(agent_app, "federation_notification_preference_service", None),
        # Phase 52: notification observability, alert, SLA
        federation_notification_observability_store=getattr(
            getattr(agent_app, "_federation_notification_service", None), "_observability_store", None
        ),
        federation_notification_alert_store=getattr(agent_app, "_federation_notification_alert_store", None),
        federation_notification_sla_service=getattr(agent_app, "_federation_notification_sla_service", None),
        # Phase 53: alert delivery, retention, rollup
        federation_notification_alert_delivery_service=getattr(agent_app, "_federation_notification_alert_delivery_service", None),
        federation_notification_alert_delivery_store=getattr(agent_app, "_federation_notification_alert_delivery_store", None),
        federation_notification_retention_service=getattr(agent_app, "_federation_notification_retention_service", None),
        federation_notification_rollup_service=getattr(agent_app, "_federation_notification_rollup_service", None),
    )
    base_path = getattr(console_config, "base_path", "/policy-console")
    api.include_router(router, prefix=base_path, tags=["Policy Console"])

    # Serve static files
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "..", "console", "static")
    if os.path.isdir(static_dir):
        api.mount(
            f"{base_path}/static",
            StaticFiles(directory=static_dir),
            name="policy-console-static",
        )


def _get_bundle_store(agent_app: Any) -> Any:
    """Extract the policy bundle store from the agent app (Phase 29)."""
    release_service = getattr(agent_app, "_release_service", None)
    if release_service is not None:
        return getattr(release_service, "bundle_store", None)
    return None


def _get_gate_store(agent_app: Any) -> Any:
    """Extract the policy gate store from the agent app (Phase 29)."""
    release_service = getattr(agent_app, "_release_service", None)
    if release_service is not None:
        return getattr(release_service, "gate_store", None)
    return None


def _get_promotion_store(agent_app: Any) -> Any:
    """Extract the promotion request store from the agent app (Phase 30)."""
    release_service = getattr(agent_app, "_release_service", None)
    if release_service is not None:
        return getattr(release_service, "promotion_store", None)
    return None


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
