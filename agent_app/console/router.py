"""Policy Console Lite — read-only HTML UI for policy decision data.

Phase 26: Mounted conditionally when ``policy_console.enabled`` is set in
the governance config.  Reuses Phase 25 store / reporting service — no
duplicate query logic.

Phase 29: Added read-only pages for policy bundles and gate results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

try:
    from fastapi.templating import Jinja2Templates
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False

from agent_app.governance.policy_decision_store import (
    PolicyDecisionStore,
    PolicyReportingService,
)
from agent_app.governance.policy_activation import PolicyActivationStatus
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.runtime.policy_replay_store import PolicyReplayStore

try:
    from fastapi.responses import RedirectResponse
except ImportError:
    RedirectResponse = None  # type: ignore[assignment,misc]


def build_policy_console_router(
    store: PolicyDecisionStore | None,
    config: Any = None,
    replay_store: PolicyReplayStore | None = None,
    replay_job_store: Any = None,
    bundle_store: Any = None,
    gate_store: Any = None,
    promotion_store: Any = None,
    release_service: Any = None,
    activation_store: Any = None,
    environment_store: Any = None,
    ring_store: Any = None,
    ring_assignment_store: Any = None,
    event_store: Any = None,
    reload_manager: Any = None,
    ring_router: Any = None,
    rollout_store: Any = None,
    rollout_service: Any = None,
    approval_store: Any = None,
    runtime_policy_store: Any = None,
    policy_enforcement_service: Any = None,
    observability_service: Any = None,
    simulation_service: Any = None,
    simulation_gate_evaluator: Any = None,
    release_gate_automation_service: Any = None,
    rollout_gate_automation_service: Any = None,
    notification_service: Any = None,
    expiration_service: Any = None,
    rollout_history_service: Any = None,
    rollout_federation_service: Any = None,
    federated_rollout_target_store: Any = None,
    federated_rollout_plan_store: Any = None,
    federation_observability_service: Any = None,
    federation_approval_store: Any = None,
    federation_approval_service: Any = None,
    federation_notification_store: Any = None,
    federation_escalation_worker: Any = None,
) -> APIRouter:
    """Build the policy console FastAPI router.

    Args:
        store: The policy decision store (may be None).
        config: PolicyConsoleConfig with title, base_path, page_size.
        replay_store: Optional policy replay result store.
        replay_job_store: Optional policy replay job store (Phase 28).
        bundle_store: Optional policy bundle store (Phase 29).
        gate_store: Optional policy gate result store (Phase 29).
        promotion_store: Optional promotion request store (Phase 30).
        release_service: Optional policy release service (Phase 30).
        activation_store: Optional policy activation store (Phase 31).
        environment_store: Optional policy environment store (Phase 32).
        ring_store: Optional release ring store (Phase 33).
        ring_assignment_store: Optional ring activation assignment store (Phase 33).
        event_store: Optional policy event store (Phase 34).
        reload_manager: Optional policy reload manager (Phase 34).
        ring_router: Optional ring router for routing simulation (Phase 34).
        rollout_store: Optional rollout plan store (Phase 35).
        rollout_service: Optional rollout service (Phase 35).
        approval_store: Optional rollout step approval store (Phase 36).
        runtime_policy_store: Optional runtime policy rule store (Phase 38).
        policy_enforcement_service: Optional policy enforcement service (Phase 38).
        observability_service: Optional policy observability service (Phase 39).
        simulation_service: Optional policy simulation service (Phase 40).
        simulation_gate_evaluator: Optional simulation gate evaluator (Phase 41).
        release_gate_automation_service: Optional release gate automation service (Phase 42).
        rollout_gate_automation_service: Optional rollout gate automation service (Phase 43).
        notification_service: Optional notification service (Phase 44).
        expiration_service: Optional expiration service (Phase 44).
        rollout_history_service: Optional rollout history service (Phase 45).
        rollout_federation_service: Optional rollout federation service (Phase 46).
        federated_rollout_target_store: Optional federated rollout target store (Phase 46).
        federated_rollout_plan_store: Optional federated rollout plan store (Phase 46).
        federation_observability_service: Optional federation observability service (Phase 47).
        federation_approval_store: Optional federation approval store (Phase 48).
        federation_approval_service: Optional federation approval service (Phase 48).
        federation_notification_store: Optional federation notification store (Phase 49).
        federation_escalation_worker: Optional federation escalation worker (Phase 49).

    Returns:
        An APIRouter ready to be included in the FastAPI app.
    """
    if not _JINJA2_AVAILABLE:
        router = APIRouter()

        @router.get("/")
        async def console_unavailable() -> dict:
            return {"error": "jinja2 not installed. Install with: pip install jinja2"}
        return router

    router = APIRouter()
    title = "Agent App Policy Console"
    base_path = "/policy-console"
    page_size = 50
    if config is not None:
        title = getattr(config, "title", title)
        base_path = getattr(config, "base_path", base_path)
        page_size = getattr(config, "page_size", page_size)

    templates = Jinja2Templates(directory=_get_templates_dir())

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Dashboard — aggregated overview."""
        report = None
        recent: list[dict] = []
        if store is not None:
            service = PolicyReportingService(store)
            report = await service.generate_report(limit=page_size)
            traces = await store.query(limit=min(page_size, 20))
            recent = [_trace_to_card(t) for t in traces]
        return templates.TemplateResponse(
            request,
            "policy_dashboard.html",
            {
                "title": title,
                "report": _report_to_dict(report) if report else None,
                "recent": recent,
                "store_available": store is not None,
            },
        )

    @router.get("/decisions", response_class=HTMLResponse)
    async def decisions_list(request: Request):
        """Decisions list with filters and pagination."""
        action = request.query_params.get("action", "")
        rule_name = request.query_params.get("rule_name", "")
        tool_name = request.query_params.get("tool_name", "")
        agent_name = request.query_params.get("agent_name", "")
        user_id = request.query_params.get("user_id", "")
        tenant_id = request.query_params.get("tenant_id", "")
        run_id = request.query_params.get("run_id", "")
        try:
            limit = int(request.query_params.get("limit", page_size))
        except ValueError:
            limit = page_size
        try:
            offset = int(request.query_params.get("offset", "0"))
        except ValueError:
            offset = 0

        decisions: list[dict] = []
        total = 0
        if store is not None:
            total = await store.count(
                action=action or None,
                rule_name=rule_name or None,
                tool_name=tool_name or None,
                agent_name=agent_name or None,
            )
            traces = await store.query(
                action=action or None,
                rule_name=rule_name or None,
                tool_name=tool_name or None,
                agent_name=agent_name or None,
                limit=limit,
                offset=offset,
            )
            decisions = [_trace_to_row(t) for t in traces]

        pages = _paginate(offset, limit, total)
        return templates.TemplateResponse(
            request,
            "policy_decisions.html",
            {
                "title": title,
                "decisions": decisions,
                "filters": {
                    "action": action,
                    "rule_name": rule_name,
                    "tool_name": tool_name,
                    "agent_name": agent_name,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                },
                "pagination": pages,
                "store_available": store is not None,
            },
        )

    @router.get("/decisions/{decision_id}", response_class=HTMLResponse)
    async def decision_detail(request: Request, decision_id: str):
        """Single decision detail."""
        if store is None:
            return templates.TemplateResponse(
                request,
                "policy_decision_detail.html",
                {
                    "title": title,
                    "store_available": False,
                    "decision": None,
                    "error": "Policy decision store not configured.",
                },
            )
        try:
            trace = await store.get(decision_id)
        except KeyError:
            return templates.TemplateResponse(
                request,
                "policy_decision_detail.html",
                {
                    "title": title,
                    "store_available": True,
                    "decision": None,
                    "error": f"Decision '{decision_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_decision_detail.html",
            {
                "title": title,
                "store_available": True,
                "decision": _trace_to_detail(trace),
                "error": None,
            },
        )

    @router.get("/report", response_class=HTMLResponse)
    async def report_page(request: Request):
        """Full aggregated report page."""
        report = None
        if store is not None:
            service = PolicyReportingService(store)
            report = await service.generate_report(limit=1000)
        return templates.TemplateResponse(
            request,
            "policy_report.html",
            {
                "title": title,
                "report": _report_to_dict(report) if report else None,
                "store_available": store is not None,
            },
        )

    # Phase 27: replay pages
    @router.get("/replays", response_class=HTMLResponse)
    async def replays_index(request: Request):
        """Replay results index."""
        replay_runs: list[dict] = []
        if replay_store is not None:
            runs = await replay_store.list(limit=page_size)
            for run in runs:
                replay_runs.append({
                    "replay_id": run.replay_id,
                    "status": run.status,
                    "created_at": run.created_at.isoformat() if hasattr(run.created_at, "isoformat") else str(run.created_at),
                    "source_decision_count": run.source_decision_count,
                    "changed_count": run.changed_count,
                    "unchanged_count": run.unchanged_count,
                    "failed_count": run.failed_count,
                })
        return templates.TemplateResponse(
            request,
            "replay_index.html",
            {
                "title": title,
                "base_path": base_path,
                "replays": replay_runs,
                "store_available": replay_store is not None,
            },
        )

    @router.get("/replays/{replay_id}", response_class=HTMLResponse)
    async def replay_detail(request: Request, replay_id: str):
        """Single replay detail."""
        if replay_store is None:
            return templates.TemplateResponse(
                request,
                "replay_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "replay": None,
                    "error": "Replay store not configured.",
                },
            )
        result = await replay_store.get(replay_id)
        if result is None:
            return templates.TemplateResponse(
                request,
                "replay_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "replay": None,
                    "error": f"Replay '{replay_id}' not found.",
                },
            )
        run = result.replay
        changes = []
        for c in result.changes:
            changes.append({
                "decision_id": c.decision_id,
                "original_action": c.original_action,
                "replayed_action": c.replayed_action,
                "changed": c.changed,
                "original_rule_id": c.original_rule_id,
                "replayed_rule_id": c.replayed_rule_id,
                "reason": c.reason,
            })
        return templates.TemplateResponse(
            request,
            "replay_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "replay": {
                    "replay_id": run.replay_id,
                    "status": run.status,
                    "created_at": run.created_at.isoformat() if hasattr(run.created_at, "isoformat") else str(run.created_at),
                    "source_decision_count": run.source_decision_count,
                    "changed_count": run.changed_count,
                    "unchanged_count": run.unchanged_count,
                    "failed_count": run.failed_count,
                    "changes": changes,
                },
                "error": None,
            },
        )

    # Phase 28: replay job routes
    @router.get("/replay-jobs", response_class=HTMLResponse)
    async def replay_jobs_index(request: Request):
        """Replay jobs index."""
        jobs_list: list[dict] = []
        if replay_job_store is not None:
            jobs = await replay_job_store.list(limit=page_size)
            for j in jobs:
                jobs_list.append({
                    "job_id": j.job_id,
                    "status": j.status,
                    "replay_id": j.replay_id or "—",
                    "limit": j.limit,
                    "tenant_id": j.tenant_id or "—",
                    "tool_name": j.tool_name or "—",
                    "requested_by": j.requested_by or "—",
                    "created_at": j.created_at.isoformat() if hasattr(j.created_at, "isoformat") else str(j.created_at),
                })
        return templates.TemplateResponse(
            request,
            "replay_jobs.html",
            {
                "title": title,
                "base_path": base_path,
                "jobs": jobs_list,
                "store_available": replay_job_store is not None,
            },
        )

    @router.get("/replay-jobs/{job_id}", response_class=HTMLResponse)
    async def replay_job_detail(request: Request, job_id: str):
        """Single replay job detail."""
        if replay_job_store is None:
            return templates.TemplateResponse(
                request,
                "replay_job_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "job": None,
                    "error": "Replay job store not configured.",
                },
            )
        job = await replay_job_store.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request,
                "replay_job_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "job": None,
                    "error": f"Job '{job_id}' not found.",
                },
            )
        job_dict = {
            "job_id": job.job_id,
            "status": job.status,
            "replay_id": job.replay_id or "—",
            "limit": job.limit,
            "tenant_id": job.tenant_id or "—",
            "tool_name": job.tool_name or "—",
            "rule_id": job.rule_id or "—",
            "requested_by": job.requested_by or "—",
            "error": job.error,
            "created_at": job.created_at.isoformat() if hasattr(job.created_at, "isoformat") else str(job.created_at),
            "started_at": job.started_at.isoformat() if job.started_at and hasattr(job.started_at, "isoformat") else str(job.started_at) if job.started_at else "—",
            "completed_at": job.completed_at.isoformat() if job.completed_at and hasattr(job.completed_at, "isoformat") else str(job.completed_at) if job.completed_at else "—",
        }
        return templates.TemplateResponse(
            request,
            "replay_job_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "job": job_dict,
                "error": None,
            },
        )

    # Phase 29: policy bundle pages
    @router.get("/bundles", response_class=HTMLResponse)
    async def bundles_index(request: Request):
        """Policy bundles list."""
        bundles_list: list[dict] = []
        if bundle_store is not None:
            bundles = await bundle_store.list(limit=page_size)
            for b in bundles:
                bundles_list.append(_bundle_to_row(b))
        return templates.TemplateResponse(
            request,
            "bundles.html",
            {
                "title": title,
                "base_path": base_path,
                "bundles": bundles_list,
                "store_available": bundle_store is not None,
            },
        )

    @router.get("/bundles/{bundle_id}", response_class=HTMLResponse)
    async def bundle_detail(request: Request, bundle_id: str):
        """Single bundle detail."""
        if bundle_store is None:
            return templates.TemplateResponse(
                request,
                "bundle_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "bundle": None,
                    "error": "Policy bundle store not configured.",
                },
            )
        bundle = await bundle_store.get(bundle_id)
        if bundle is None:
            return templates.TemplateResponse(
                request,
                "bundle_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "bundle": None,
                    "error": f"Bundle '{bundle_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "bundle_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "bundle": _bundle_to_detail(bundle),
                "error": None,
            },
        )

    # Phase 29: policy gate pages
    @router.get("/gates", response_class=HTMLResponse)
    async def gates_index(request: Request):
        """Policy gate results list."""
        gates_list: list[dict] = []
        if gate_store is not None:
            results = await gate_store.list(limit=page_size)
            for g in results:
                gates_list.append(_gate_to_row(g))
        return templates.TemplateResponse(
            request,
            "gates.html",
            {
                "title": title,
                "base_path": base_path,
                "gates": gates_list,
                "store_available": gate_store is not None,
            },
        )

    @router.get("/gates/{gate_result_id}", response_class=HTMLResponse)
    async def gate_detail(request: Request, gate_result_id: str):
        """Single gate result detail."""
        if gate_store is None:
            return templates.TemplateResponse(
                request,
                "gate_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "gate": None,
                    "error": "Policy gate store not configured.",
                },
            )
        gate = await gate_store.get(gate_result_id)
        if gate is None:
            return templates.TemplateResponse(
                request,
                "gate_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "gate": None,
                    "error": f"Gate result '{gate_result_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "gate_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "gate": _gate_to_detail(gate),
                "error": None,
            },
        )

    # Phase 30: policy promotion pages
    @router.get("/promotions", response_class=HTMLResponse)
    async def promotions_index(request: Request):
        """Policy promotion requests list."""
        promotions_list: list[dict] = []
        if promotion_store is not None:
            requests = await promotion_store.list()
            for r in requests[:page_size]:
                promotions_list.append(_promotion_to_row(r))
        return templates.TemplateResponse(
            request,
            "policy_promotions.html",
            {
                "title": title,
                "base_path": base_path,
                "promotions": promotions_list,
                "store_available": promotion_store is not None,
            },
        )

    @router.get("/promotions/{promotion_id}", response_class=HTMLResponse)
    async def promotion_detail(request: Request, promotion_id: str):
        """Single promotion request detail."""
        if promotion_store is None:
            return templates.TemplateResponse(
                request,
                "policy_promotion_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "promotion": None,
                    "error": "Promotion store not configured.",
                },
            )
        req = await promotion_store.get(promotion_id)
        if req is None:
            return templates.TemplateResponse(
                request,
                "policy_promotion_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "promotion": None,
                    "error": f"Promotion request '{promotion_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "promotion": _promotion_to_detail(req),
                "error": None,
            },
        )

    # POST routes for promotion write actions
    @router.post("/promotions", response_class=HTMLResponse)
    async def create_promotion(request: Request):
        """Create a new promotion request."""
        error_msg = None
        created_request = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                bundle_id = form.get("bundle_id", "")
                requested_by = form.get("requested_by", "")
                reason = form.get("reason") or None
                if not bundle_id or not requested_by:
                    error_msg = "bundle_id and requested_by are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{requested_by}",
                        user_id=requested_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    created_request = await release_service.request_promotion(
                        bundle_id=bundle_id,
                        requested_by=requested_by,
                        context=context,
                        reason=reason,
                    )
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotions.html",
            {
                "title": title,
                "base_path": base_path,
                "promotions": [],
                "store_available": promotion_store is not None,
                "error": error_msg,
                "created_request": created_request,
            },
        )

    @router.post("/promotions/{promotion_id}/approve", response_class=HTMLResponse)
    async def approve_promotion(request: Request, promotion_id: str):
        """Approve a promotion request."""
        error_msg = None
        updated = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                approved_by = form.get("approved_by", "")
                reason = form.get("reason") or None
                if not approved_by:
                    error_msg = "approved_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{approved_by}",
                        user_id=approved_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await release_service.approve_promotion(
                        promotion_id=promotion_id,
                        approved_by=approved_by,
                        context=context,
                        reason=reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": updated if updated else (await promotion_store.get(promotion_id) if promotion_store else None),
                "error": error_msg,
            },
        )

    @router.post("/promotions/{promotion_id}/reject", response_class=HTMLResponse)
    async def reject_promotion(request: Request, promotion_id: str):
        """Reject a promotion request."""
        error_msg = None
        updated = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                rejected_by = form.get("rejected_by", "")
                reason = form.get("reason") or None
                if not rejected_by:
                    error_msg = "rejected_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{rejected_by}",
                        user_id=rejected_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await release_service.reject_promotion(
                        promotion_id=promotion_id,
                        rejected_by=rejected_by,
                        context=context,
                        reason=reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": updated if updated else (await promotion_store.get(promotion_id) if promotion_store else None),
                "error": error_msg,
            },
        )

    @router.post("/promotions/{promotion_id}/execute", response_class=HTMLResponse)
    async def execute_promotion(request: Request, promotion_id: str):
        """Execute an approved promotion."""
        error_msg = None
        result = None
        if release_service is None:
            error_msg = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                executed_by = form.get("executed_by", "")
                bypass_gate = form.get("bypass_gate") == "on"
                bypass_reason = form.get("bypass_reason") or None
                if not executed_by:
                    error_msg = "executed_by is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{executed_by}",
                        user_id=executed_by,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    result = await release_service.execute_promotion(
                        promotion_id=promotion_id,
                        executed_by=executed_by,
                        context=context,
                        bypass_gate=bypass_gate,
                        bypass_reason=bypass_reason,
                    )
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                error_msg = str(exc)
            except Exception as exc:
                error_msg = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_promotion_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": promotion_store is not None,
                "promotion": await promotion_store.get(promotion_id) if promotion_store else None,
                "error": error_msg,
                "executed_bundle": result,
            },
        )

    # Phase 31: policy activation pages
    @router.get("/activations", response_class=HTMLResponse)
    async def activations_index(request: Request):
        """Policy activations list."""
        activations_list: list[dict] = []
        if activation_store is not None:
            acts = await activation_store.list()
            for a in acts:
                activations_list.append(_activation_to_row(a))
        return templates.TemplateResponse(
            request,
            "policy_activations.html",
            {
                "title": title,
                "base_path": base_path,
                "activations": activations_list,
                "environments": {},
                "store_available": activation_store is not None,
            },
        )

    @router.get("/activations/{activation_id}", response_class=HTMLResponse)
    async def activation_detail(request: Request, activation_id: str):
        """Single activation detail."""
        if activation_store is None:
            return templates.TemplateResponse(
                request,
                "policy_activation_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "activation": None,
                    "error": "Activation store not configured.",
                },
            )
        act = await activation_store.get(activation_id)
        if act is None:
            return templates.TemplateResponse(
                request,
                "policy_activation_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "activation": None,
                    "error": f"Activation '{activation_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_activation_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "activation": _activation_to_detail(act),
                "error": None,
            },
        )

    @router.get("/environments", response_class=HTMLResponse)
    async def environments_page(request: Request):
        """Environment overview showing active policy per environment."""
        env_data: dict[str, dict] = {}
        if activation_store is not None:
            acts = await activation_store.list()
            for a in acts:
                if a.status == PolicyActivationStatus.ACTIVE:
                    env_data[a.environment] = _activation_to_row(a)
        return templates.TemplateResponse(
            request,
            "policy_activations.html",
            {
                "title": title,
                "base_path": base_path,
                "activations": [],
                "environments": env_data,
                "store_available": activation_store is not None,
            },
        )

    # Phase 32: environment detail and rollback actions
    @router.get("/environments/{environment}", response_class=HTMLResponse)
    async def environment_detail(request: Request, environment: str):
        """Environment detail page with status, disable/enable/rollback forms."""
        state = None
        active_activation = None
        activations_list: list[dict] = []
        message = None

        if environment_store is not None:
            state = await environment_store.get(environment)
        if activation_store is not None:
            acts = await activation_store.list()
            env_acts = [a for a in acts if a.environment == environment]
            # Find the active activation for this environment
            for a in env_acts:
                if a.status == PolicyActivationStatus.ACTIVE:
                    active_activation = _activation_to_row(a)
                    break
            # Recent activations (up to 10)
            for a in env_acts[:10]:
                activations_list.append(_activation_to_row(a))

        # Build a simple state dict for the template
        state_dict = None
        if state is not None:
            state_dict = {
                "environment": state.environment,
                "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                "disabled_reason": state.disabled_reason,
                "disabled_by": state.disabled_by,
                "disabled_at": state.disabled_at.isoformat() if state.disabled_at and hasattr(state.disabled_at, "isoformat") else None,
                "enabled_by": state.enabled_by,
                "enabled_at": state.enabled_at.isoformat() if state.enabled_at and hasattr(state.enabled_at, "isoformat") else None,
            }

        return templates.TemplateResponse(
            request,
            "policy_environment_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "state": state_dict,
                "active_activation": active_activation,
                "activations": activations_list,
                "message": message,
                "store_available": environment_store is not None,
            },
        )

    @router.post("/environments/{environment}/disable", response_class=HTMLResponse)
    async def disable_environment(request: Request, environment: str):
        """Disable a policy environment."""
        message = None
        state = None
        active_activation = None
        activations_list: list[dict] = []

        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason", "")
                if not actor_id:
                    message = "actor_id is required."
                elif not reason:
                    message = "reason is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.disable_policy_environment(
                        environment=environment,
                        disabled_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Environment '{environment}' disabled successfully."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render the detail page
        if environment_store is not None:
            state = await environment_store.get(environment)
        if activation_store is not None:
            acts = await activation_store.list()
            env_acts = [a for a in acts if a.environment == environment]
            for a in env_acts:
                if a.status == PolicyActivationStatus.ACTIVE:
                    active_activation = _activation_to_row(a)
                    break
            for a in env_acts[:10]:
                activations_list.append(_activation_to_row(a))

        state_dict = None
        if state is not None:
            state_dict = {
                "environment": state.environment,
                "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                "disabled_reason": state.disabled_reason,
                "disabled_by": state.disabled_by,
                "disabled_at": state.disabled_at.isoformat() if state.disabled_at and hasattr(state.disabled_at, "isoformat") else None,
                "enabled_by": state.enabled_by,
                "enabled_at": state.enabled_at.isoformat() if state.enabled_at and hasattr(state.enabled_at, "isoformat") else None,
            }

        return templates.TemplateResponse(
            request,
            "policy_environment_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "state": state_dict,
                "active_activation": active_activation,
                "activations": activations_list,
                "message": message,
                "store_available": environment_store is not None,
            },
        )

    @router.post("/environments/{environment}/enable", response_class=HTMLResponse)
    async def enable_environment(request: Request, environment: str):
        """Re-enable a disabled policy environment."""
        message = None
        state = None
        active_activation = None
        activations_list: list[dict] = []

        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.enable_policy_environment(
                        environment=environment,
                        enabled_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Environment '{environment}' enabled successfully."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render the detail page
        if environment_store is not None:
            state = await environment_store.get(environment)
        if activation_store is not None:
            acts = await activation_store.list()
            env_acts = [a for a in acts if a.environment == environment]
            for a in env_acts:
                if a.status == PolicyActivationStatus.ACTIVE:
                    active_activation = _activation_to_row(a)
                    break
            for a in env_acts[:10]:
                activations_list.append(_activation_to_row(a))

        state_dict = None
        if state is not None:
            state_dict = {
                "environment": state.environment,
                "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                "disabled_reason": state.disabled_reason,
                "disabled_by": state.disabled_by,
                "disabled_at": state.disabled_at.isoformat() if state.disabled_at and hasattr(state.disabled_at, "isoformat") else None,
                "enabled_by": state.enabled_by,
                "enabled_at": state.enabled_at.isoformat() if state.enabled_at and hasattr(state.enabled_at, "isoformat") else None,
            }

        return templates.TemplateResponse(
            request,
            "policy_environment_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "state": state_dict,
                "active_activation": active_activation,
                "activations": activations_list,
                "message": message,
                "store_available": environment_store is not None,
            },
        )

    @router.post("/activations/{activation_id}/rollback", response_class=HTMLResponse)
    async def rollback_activation(request: Request, activation_id: str):
        """Roll back an environment to a previous activation."""
        message = None
        environment = None

        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                environment = form.get("environment", "")
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not environment or not actor_id:
                    message = "environment and actor_id are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.rollback_environment(
                        environment=environment,
                        rolled_back_by=actor_id,
                        context=context,
                        target_activation_id=activation_id,
                        reason=reason,
                    )
                    message = f"Rollback to activation '{activation_id}' completed successfully."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render the environment detail page
        state = None
        active_activation = None
        activations_list: list[dict] = []
        env_name = environment or ""

        if environment_store is not None and env_name:
            state = await environment_store.get(env_name)
        if activation_store is not None and env_name:
            acts = await activation_store.list()
            env_acts = [a for a in acts if a.environment == env_name]
            for a in env_acts:
                if a.status == PolicyActivationStatus.ACTIVE:
                    active_activation = _activation_to_row(a)
                    break
            for a in env_acts[:10]:
                activations_list.append(_activation_to_row(a))

        state_dict = None
        if state is not None:
            state_dict = {
                "environment": state.environment,
                "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                "disabled_reason": state.disabled_reason,
                "disabled_by": state.disabled_by,
                "disabled_at": state.disabled_at.isoformat() if state.disabled_at and hasattr(state.disabled_at, "isoformat") else None,
                "enabled_by": state.enabled_by,
                "enabled_at": state.enabled_at.isoformat() if state.enabled_at and hasattr(state.enabled_at, "isoformat") else None,
            }

        return templates.TemplateResponse(
            request,
            "policy_environment_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "state": state_dict,
                "active_activation": active_activation,
                "activations": activations_list,
                "message": message,
                "store_available": environment_store is not None,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 33 Task 9: Ring management pages and routes
    # -----------------------------------------------------------------------

    def _ring_to_row(ring: Any) -> dict:
        """Convert a ReleaseRing to a template row dict."""
        return {
            "ring_id": ring.ring_id,
            "environment": ring.environment,
            "name": ring.name,
            "description": ring.description,
            "status": ring.status.value if hasattr(ring.status, "value") else str(ring.status),
            "is_default": ring.is_default,
            "created_at": ring.created_at.isoformat() if hasattr(ring.created_at, "isoformat") else str(ring.created_at),
            "updated_at": ring.updated_at.isoformat() if hasattr(ring.updated_at, "isoformat") else str(ring.updated_at),
        }

    def _assignment_to_row(a: Any) -> dict:
        """Convert a RingActivationAssignment to a template row dict."""
        return {
            "assignment_id": a.assignment_id,
            "environment": a.environment,
            "ring_name": a.ring_name,
            "activation_id": a.activation_id,
            "bundle_id": a.bundle_id,
            "config_hash": a.config_hash,
            "status": a.status.value if hasattr(a.status, "value") else str(a.status),
            "assigned_by": a.assigned_by,
            "reason": a.reason or "—",
            "created_at": a.created_at.isoformat() if hasattr(a.created_at, "isoformat") else str(a.created_at),
        }

    @router.get("/rings", response_class=HTMLResponse)
    async def rings_list(request: Request):
        """Ring list page showing all rings."""
        rings: list[dict] = []
        if ring_store is not None:
            all_rings = await ring_store.list()
            # Attach active assignment bundle_id for display
            for r in all_rings:
                row = _ring_to_row(r)
                if ring_assignment_store is not None:
                    active = await ring_assignment_store.get_active(r.environment, r.name)
                    row["active_bundle_id"] = active.bundle_id if active else None
                else:
                    row["active_bundle_id"] = None
                rings.append(row)
        return templates.TemplateResponse(
            request,
            "policy_rings.html",
            {
                "title": title,
                "base_path": base_path,
                "rings": rings,
                "message": None,
                "store_available": ring_store is not None,
            },
        )

    @router.get("/rings/{environment}/{ring_name}", response_class=HTMLResponse)
    async def ring_detail(request: Request, environment: str, ring_name: str):
        """Ring detail page with status, assignment info, and action forms."""
        ring = None
        active_assignment = None
        assignments: list[dict] = []

        if ring_store is not None:
            ring = await ring_store.get_by_name(environment, ring_name)

        if ring_assignment_store is not None:
            active = await ring_assignment_store.get_active(environment, ring_name)
            if active is not None:
                active_assignment = _assignment_to_row(active)
            all_assigns = await ring_assignment_store.list(environment=environment, ring_name=ring_name)
            for a in all_assigns[:20]:
                assignments.append(_assignment_to_row(a))

        ring_dict = None
        if ring is not None:
            ring_dict = _ring_to_row(ring)

        return templates.TemplateResponse(
            request,
            "policy_ring_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "ring": ring_dict,
                "active_assignment": active_assignment,
                "assignments": assignments,
                "message": None,
                "store_available": ring_store is not None,
            },
        )

    @router.post("/rings", response_class=HTMLResponse)
    async def create_ring(request: Request):
        """Create a new release ring."""
        message = None
        rings: list[dict] = []

        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                environment = form.get("environment", "")
                name = form.get("name", "")
                actor_id = form.get("actor_id", "")
                description = form.get("description") or None
                is_default = form.get("is_default") == "on"
                if not environment or not name or not actor_id:
                    message = "environment, name, and actor_id are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.create_ring(
                        environment=environment,
                        name=name,
                        created_by=actor_id,
                        context=context,
                        description=description,
                        is_default=is_default,
                    )
                    message = f"Ring '{name}' created in environment '{environment}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render the ring list page
        if ring_store is not None:
            all_rings = await ring_store.list()
            for r in all_rings:
                row = _ring_to_row(r)
                if ring_assignment_store is not None:
                    active = await ring_assignment_store.get_active(r.environment, r.name)
                    row["active_bundle_id"] = active.bundle_id if active else None
                else:
                    row["active_bundle_id"] = None
                rings.append(row)

        return templates.TemplateResponse(
            request,
            "policy_rings.html",
            {
                "title": title,
                "base_path": base_path,
                "rings": rings,
                "message": message,
                "store_available": ring_store is not None,
            },
        )

    @router.post("/rings/{environment}/{ring_name}/assign", response_class=HTMLResponse)
    async def assign_activation_to_ring(request: Request, environment: str, ring_name: str):
        """Assign an activation to a ring."""
        message = None
        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                activation_id = form.get("activation_id", "")
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not activation_id or not actor_id:
                    message = "activation_id and actor_id are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.assign_activation_to_ring(
                        environment=environment,
                        ring_name=ring_name,
                        activation_id=activation_id,
                        assigned_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Activation '{activation_id}' assigned to ring '{ring_name}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        ring = None
        active_assignment = None
        assignments: list[dict] = []
        if ring_store is not None:
            ring = await ring_store.get_by_name(environment, ring_name)
        if ring_assignment_store is not None:
            active = await ring_assignment_store.get_active(environment, ring_name)
            if active is not None:
                active_assignment = _assignment_to_row(active)
            all_assigns = await ring_assignment_store.list(environment=environment, ring_name=ring_name)
            for a in all_assigns[:20]:
                assignments.append(_assignment_to_row(a))
        ring_dict = _ring_to_row(ring) if ring is not None else None
        return templates.TemplateResponse(
            request,
            "policy_ring_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "ring": ring_dict,
                "active_assignment": active_assignment,
                "assignments": assignments,
                "message": message,
                "store_available": ring_store is not None,
            },
        )

    @router.post("/rings/{environment}/{ring_name}/promote", response_class=HTMLResponse)
    async def promote_ring(request: Request, environment: str, ring_name: str):
        """Promote a ring's activation to another ring."""
        message = None
        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                to_ring = form.get("to_ring", "")
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not to_ring or not actor_id:
                    message = "to_ring and actor_id are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.promote_canary_to_stable(
                        environment=environment,
                        canary_ring=ring_name,
                        stable_ring=to_ring,
                        promoted_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Promoted from ring '{ring_name}' to ring '{to_ring}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        ring = None
        active_assignment = None
        assignments: list[dict] = []
        if ring_store is not None:
            ring = await ring_store.get_by_name(environment, ring_name)
        if ring_assignment_store is not None:
            active = await ring_assignment_store.get_active(environment, ring_name)
            if active is not None:
                active_assignment = _assignment_to_row(active)
            all_assigns = await ring_assignment_store.list(environment=environment, ring_name=ring_name)
            for a in all_assigns[:20]:
                assignments.append(_assignment_to_row(a))
        ring_dict = _ring_to_row(ring) if ring is not None else None
        return templates.TemplateResponse(
            request,
            "policy_ring_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "ring": ring_dict,
                "active_assignment": active_assignment,
                "assignments": assignments,
                "message": message,
                "store_available": ring_store is not None,
            },
        )

    @router.post("/rings/{environment}/{ring_name}/disable", response_class=HTMLResponse)
    async def disable_ring_route(request: Request, environment: str, ring_name: str):
        """Disable a release ring."""
        message = None
        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.disable_ring(
                        environment=environment,
                        ring_name=ring_name,
                        disabled_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Ring '{ring_name}' disabled."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        ring = None
        active_assignment = None
        assignments: list[dict] = []
        if ring_store is not None:
            ring = await ring_store.get_by_name(environment, ring_name)
        if ring_assignment_store is not None:
            active = await ring_assignment_store.get_active(environment, ring_name)
            if active is not None:
                active_assignment = _assignment_to_row(active)
            all_assigns = await ring_assignment_store.list(environment=environment, ring_name=ring_name)
            for a in all_assigns[:20]:
                assignments.append(_assignment_to_row(a))
        ring_dict = _ring_to_row(ring) if ring is not None else None
        return templates.TemplateResponse(
            request,
            "policy_ring_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "ring": ring_dict,
                "active_assignment": active_assignment,
                "assignments": assignments,
                "message": message,
                "store_available": ring_store is not None,
            },
        )

    @router.post("/rings/{environment}/{ring_name}/enable", response_class=HTMLResponse)
    async def enable_ring_route(request: Request, environment: str, ring_name: str):
        """Re-enable a disabled release ring."""
        message = None
        if release_service is None:
            message = "Policy release service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    await release_service.enable_ring(
                        environment=environment,
                        ring_name=ring_name,
                        enabled_by=actor_id,
                        context=context,
                    )
                    message = f"Ring '{ring_name}' enabled."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        ring = None
        active_assignment = None
        assignments: list[dict] = []
        if ring_store is not None:
            ring = await ring_store.get_by_name(environment, ring_name)
        if ring_assignment_store is not None:
            active = await ring_assignment_store.get_active(environment, ring_name)
            if active is not None:
                active_assignment = _assignment_to_row(active)
            all_assigns = await ring_assignment_store.list(environment=environment, ring_name=ring_name)
            for a in all_assigns[:20]:
                assignments.append(_assignment_to_row(a))
        ring_dict = _ring_to_row(ring) if ring is not None else None
        return templates.TemplateResponse(
            request,
            "policy_ring_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "ring": ring_dict,
                "active_assignment": active_assignment,
                "assignments": assignments,
                "message": message,
                "store_available": ring_store is not None,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 34 Task 10: Events, reload, and routing simulation pages
    # -----------------------------------------------------------------------

    @router.get("/events", response_class=HTMLResponse)
    async def policy_events(request: Request):
        """Events list page."""
        if event_store is None:
            return templates.TemplateResponse(
                request,
                "policy_events.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "events": [],
                    "error": "Event store not configured",
                },
            )
        events_raw = await event_store.list(limit=50)
        rows: list[dict] = []
        for e in events_raw:
            rows.append({
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "environment": e.environment,
                "ring_name": e.ring_name,
                "actor_id": e.actor_id,
                "created_at": e.created_at.isoformat() if e.created_at else "",
            })
        return templates.TemplateResponse(
            request,
            "policy_events.html",
            {
                "title": title,
                "base_path": base_path,
                "events": rows,
                "error": None,
            },
        )

    @router.get("/reload", response_class=HTMLResponse)
    async def policy_reload_page(request: Request):
        """Reload page showing cache info and reload form."""
        cache_info: dict = {}
        if release_service and release_service.policy_resolver:
            cache_info = release_service.policy_resolver.cache_status()
        return templates.TemplateResponse(
            request,
            "policy_reload.html",
            {
                "title": title,
                "base_path": base_path,
                "cache_info": cache_info,
                "results": [],
                "message": None,
            },
        )

    @router.post("/reload", response_class=HTMLResponse)
    async def policy_reload_post(request: Request):
        """POST reload: request a policy reload."""
        form = await request.form()
        environment = form.get("environment", "")
        ring_name = form.get("ring_name", "")
        actor_id = form.get("actor_id", "")
        reason = form.get("reason", "")

        results: list[dict] = []
        message = None
        if reload_manager is not None:
            try:
                reload_results = await reload_manager.request_reload(
                    environment=environment or None,
                    ring_name=ring_name or None,
                    requested_by=actor_id or None,
                    reason=reason or None,
                )
                results = [
                    {"target": r.target.model_dump(), "refreshed": r.refreshed, "error": r.error}
                    for r in reload_results
                ]
                message = f"Reload requested: {len(results)} results"
            except Exception as exc:
                message = f"Reload failed: {exc}"
        else:
            message = "Reload manager not configured"

        cache_info: dict = {}
        if release_service and release_service.policy_resolver:
            cache_info = release_service.policy_resolver.cache_status()

        return templates.TemplateResponse(
            request,
            "policy_reload.html",
            {
                "title": title,
                "base_path": base_path,
                "cache_info": cache_info,
                "results": results,
                "message": message,
            },
        )

    @router.get("/routing/simulate", response_class=HTMLResponse)
    async def policy_routing_simulate_page(request: Request):
        """Routing simulator form page."""
        return templates.TemplateResponse(
            request,
            "policy_routing_simulate.html",
            {
                "title": title,
                "base_path": base_path,
                "result": None,
                "message": None,
            },
        )

    @router.post("/routing/simulate", response_class=HTMLResponse)
    async def policy_routing_simulate_post(request: Request):
        """POST routing simulation: simulate ring routing for a user."""
        form = await request.form()
        environment = form.get("environment", "prod")
        actor_id = form.get("actor_id", "")
        user_id = form.get("user_id", "") or actor_id
        tenant_id = form.get("tenant_id", "")

        result = None
        message = None
        if ring_router is not None:
            from agent_app.core.context import RunContext
            context = RunContext(
                run_id="sim_0",
                user_id=user_id,
                tenant_id=tenant_id,
                permissions=[],
            )
            try:
                result = await ring_router.simulate_routing(environment, context)
            except Exception as exc:
                message = f"Routing simulation failed: {exc}"
        else:
            message = "Ring router not configured"

        return templates.TemplateResponse(
            request,
            "policy_routing_simulate.html",
            {
                "title": title,
                "base_path": base_path,
                "result": result,
                "message": message,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 35 Task 8: Rollout plan pages
    # -----------------------------------------------------------------------

    @router.get("/rollouts", response_class=HTMLResponse)
    async def rollout_list(request: Request):
        """Rollout plans list page."""
        plans: list[dict] = []
        if rollout_store is not None:
            all_plans = await rollout_store.list()
            for p in all_plans:
                plans.append(_rollout_to_row(p))
        return templates.TemplateResponse(
            request,
            "policy_rollouts.html",
            {
                "title": title,
                "base_path": base_path,
                "plans": plans,
                "store_available": rollout_store is not None,
            },
        )

    @router.get("/rollouts/{rollout_id}", response_class=HTMLResponse)
    async def rollout_detail(request: Request, rollout_id: str):
        """Single rollout plan detail page."""
        if rollout_store is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "plan": None,
                    "error": "Rollout store not configured.",
                },
            )
        plan = await rollout_store.get(rollout_id)
        if plan is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "plan": None,
                    "error": f"Rollout plan '{rollout_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "plan": _rollout_to_detail(plan),
                "error": None,
            },
        )

    @router.get("/rollouts/new", response_class=HTMLResponse)
    async def rollout_new(request: Request):
        """Create rollout plan form page."""
        return templates.TemplateResponse(
            request,
            "policy_rollout_create.html",
            {
                "title": title,
                "base_path": base_path,
            },
        )

    @router.post("/rollouts")
    async def rollout_create(request: Request):
        """Create a new rollout plan."""
        message = None
        plans: list[dict] = []

        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                name = form.get("name", "")
                bundle_id = form.get("bundle_id", "")
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                steps_yaml = form.get("steps_yaml", "")

                if not name or not bundle_id or not actor_id:
                    message = "name, bundle_id, and actor_id are required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    # Parse steps from YAML
                    steps = _parse_rollout_steps(steps_yaml)
                    created = await rollout_service.create_plan(
                        name=name,
                        bundle_id=bundle_id,
                        steps=steps,
                        created_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Rollout plan '{created.rollout_id}' created."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render list page
        if rollout_store is not None:
            all_plans = await rollout_store.list()
            for p in all_plans:
                plans.append(_rollout_to_row(p))

        return templates.TemplateResponse(
            request,
            "policy_rollouts.html",
            {
                "title": title,
                "base_path": base_path,
                "plans": plans,
                "store_available": rollout_store is not None,
                "message": message,
            },
        )

    @router.post("/rollouts/{rollout_id}/start")
    async def rollout_start(request: Request, rollout_id: str):
        """Start a rollout plan (transition from DRAFT to ACTIVE)."""
        message = None
        plan_detail = None

        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    started = await rollout_service.start_plan(
                        rollout_id=rollout_id,
                        started_by=actor_id,
                        context=context,
                    )
                    message = f"Rollout plan '{rollout_id}' started."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        if rollout_store is not None:
            plan = await rollout_store.get(rollout_id)
            if plan is not None:
                plan_detail = _rollout_to_detail(plan)

        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": rollout_store is not None,
                "plan": plan_detail,
                "message": message,
                "error": None if plan_detail else message,
            },
        )

    @router.post("/rollouts/{rollout_id}/run-next")
    async def rollout_run_next(request: Request, rollout_id: str):
        """Execute the next runnable step in a rollout plan."""
        message = None
        plan_detail = None

        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    result = await rollout_service.run_next_step(
                        rollout_id=rollout_id,
                        actor_id=actor_id,
                        context=context,
                    )
                    message = f"Next step executed for rollout '{rollout_id}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        if rollout_store is not None:
            plan = await rollout_store.get(rollout_id)
            if plan is not None:
                plan_detail = _rollout_to_detail(plan)

        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": rollout_store is not None,
                "plan": plan_detail,
                "message": message,
                "error": None if plan_detail else message,
            },
        )

    @router.post("/rollouts/{rollout_id}/run-all")
    async def rollout_run_all(request: Request, rollout_id: str):
        """Run all available steps in a rollout plan."""
        message = None
        plan_detail = None

        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    result = await rollout_service.run_all_available(
                        rollout_id=rollout_id,
                        actor_id=actor_id,
                        context=context,
                    )
                    message = f"All available steps executed for rollout '{rollout_id}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        if rollout_store is not None:
            plan = await rollout_store.get(rollout_id)
            if plan is not None:
                plan_detail = _rollout_to_detail(plan)

        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": rollout_store is not None,
                "plan": plan_detail,
                "message": message,
                "error": None if plan_detail else message,
            },
        )

    @router.post("/rollouts/{rollout_id}/cancel")
    async def rollout_cancel(request: Request, rollout_id: str):
        """Cancel a rollout plan."""
        message = None
        plan_detail = None

        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    cancelled = await rollout_service.cancel_plan(
                        rollout_id=rollout_id,
                        cancelled_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Rollout plan '{rollout_id}' cancelled."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render detail page
        if rollout_store is not None:
            plan = await rollout_store.get(rollout_id)
            if plan is not None:
                plan_detail = _rollout_to_detail(plan)

        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": rollout_store is not None,
                "plan": plan_detail,
                "message": message,
                "error": None if plan_detail else message,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 38 Task 8: Runtime policy pages
    # -----------------------------------------------------------------------

    def _runtime_rule_to_row(rule: Any) -> dict:
        """Convert a RuntimePolicyRule to a template row dict."""
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "effect": rule.effect.value if hasattr(rule.effect, "value") else str(rule.effect),
            "status": rule.status.value if hasattr(rule.status, "value") else str(rule.status),
            "action_type": rule.action_type.value if hasattr(rule.action_type, "value") else str(rule.action_type),
            "tool_name": rule.tool_name or "—",
            "risk_level": rule.risk_level or "—",
            "reason": rule.reason or "—",
        }

    def _runtime_rule_to_detail(rule: Any) -> dict:
        """Convert a RuntimePolicyRule to a detail page dict."""
        ap = rule.approval_policy
        ap_dict = None
        if ap is not None:
            ap_dict = {
                "policy_type": ap.policy_type.value if hasattr(ap.policy_type, "value") else str(ap.policy_type),
                "required_approvals": ap.required_approvals,
                "allowed_approver_permissions": list(ap.allowed_approver_permissions) if hasattr(ap, "allowed_approver_permissions") else [],
                "allowed_approver_roles": list(ap.allowed_approver_roles) if hasattr(ap, "allowed_approver_roles") else [],
                "prohibit_requester_approval": ap.prohibit_requester_approval,
                "expires_after_seconds": ap.expires_after_seconds,
            }
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "action_type": rule.action_type.value if hasattr(rule.action_type, "value") else str(rule.action_type),
            "effect": rule.effect.value if hasattr(rule.effect, "value") else str(rule.effect),
            "status": rule.status.value if hasattr(rule.status, "value") else str(rule.status),
            "tool_name": rule.tool_name or "—",
            "risk_level": rule.risk_level or "—",
            "required_permissions": list(rule.required_permissions),
            "required_roles": list(rule.required_roles),
            "approval_policy": ap_dict,
            "reason": rule.reason or "—",
            "metadata": rule.metadata or {},
        }

    @router.get("/runtime-rules", response_class=HTMLResponse)
    async def runtime_rules_list(request: Request):
        """List runtime policy rules."""
        if runtime_policy_store is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        rules = await runtime_policy_store.list()
        rows = [_runtime_rule_to_row(r) for r in rules]
        return templates.TemplateResponse(
            request,
            "policy_runtime_rules.html",
            {
                "title": title,
                "base_path": base_path,
                "rules": rows,
                "error": None,
            },
        )

    @router.get("/runtime-rules/{rule_id}", response_class=HTMLResponse)
    async def runtime_rule_detail(request: Request, rule_id: str):
        """Show runtime policy rule detail."""
        if runtime_policy_store is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        rule = await runtime_policy_store.get(rule_id)
        if rule is None:
            return templates.TemplateResponse(
                request,
                "policy_runtime_rule_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "rule": None,
                    "error": f"Rule '{rule_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_runtime_rule_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "rule": _runtime_rule_to_detail(rule),
                "error": None,
            },
        )

    @router.post("/runtime-rules", response_class=HTMLResponse)
    async def runtime_rule_create(request: Request):
        """Create a runtime policy rule."""
        if runtime_policy_store is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        message = None
        try:
            from agent_app.governance.runtime_policy import (
                RuntimePolicyEffect,
                RuntimePolicyRule,
                RuntimePolicyRuleStatus,
            )
            form = await request.form()
            rule_id = form.get("rule_id", "")
            name = form.get("name", "")
            action_type_str = form.get("action_type", "tool.execute")
            effect_str = form.get("effect", "allow")
            tool_name = form.get("tool_name") or None
            risk_level = form.get("risk_level") or None
            reason = form.get("reason") or None

            if not rule_id or not name:
                message = "rule_id and name are required."
            else:
                action_type = PolicyActionType(action_type_str)
                effect = RuntimePolicyEffect(effect_str)
                rule = RuntimePolicyRule(
                    rule_id=rule_id,
                    name=name,
                    action_type=action_type,
                    effect=effect,
                    tool_name=tool_name,
                    risk_level=risk_level,
                    reason=reason,
                )
                await runtime_policy_store.create(rule)
                message = f"Rule '{rule_id}' created."
        except (ValueError, KeyError) as exc:
            message = str(exc)
        except Exception as exc:
            message = str(exc)

        rules = await runtime_policy_store.list()
        rows = [_runtime_rule_to_row(r) for r in rules]
        return templates.TemplateResponse(
            request,
            "policy_runtime_rules.html",
            {
                "title": title,
                "base_path": base_path,
                "rules": rows,
                "error": message,
            },
        )

    @router.post("/runtime-rules/{rule_id}/enable", response_class=HTMLResponse)
    async def runtime_rule_enable(request: Request, rule_id: str):
        """Enable a runtime policy rule."""
        if runtime_policy_store is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        try:
            await runtime_policy_store.enable(rule_id)
        except KeyError:
            return HTMLResponse("<p>Rule not found.</p>", status_code=404)
        return RedirectResponse(
            f"{base_path}/runtime-rules/{rule_id}", status_code=303
        )

    @router.post("/runtime-rules/{rule_id}/disable", response_class=HTMLResponse)
    async def runtime_rule_disable(request: Request, rule_id: str):
        """Disable a runtime policy rule."""
        if runtime_policy_store is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        try:
            await runtime_policy_store.disable(rule_id)
        except KeyError:
            return HTMLResponse("<p>Rule not found.</p>", status_code=404)
        return RedirectResponse(
            f"{base_path}/runtime-rules/{rule_id}", status_code=303
        )

    @router.get("/runtime-evaluate", response_class=HTMLResponse)
    async def runtime_evaluate_form(request: Request):
        """Show runtime policy evaluation form."""
        return templates.TemplateResponse(
            request,
            "policy_runtime_evaluate.html",
            {
                "title": title,
                "base_path": base_path,
                "result": None,
                "message": None,
            },
        )

    @router.post("/runtime-evaluate", response_class=HTMLResponse)
    async def runtime_evaluate_submit(request: Request):
        """Evaluate a runtime policy decision."""
        if policy_enforcement_service is None:
            return HTMLResponse(
                "<p>Runtime policy not configured.</p>", status_code=404
            )
        result = None
        message = None
        try:
            from agent_app.core.context import RunContext
            from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluationRequest

            form = await request.form()
            action_type_str = form.get("action_type", "tool.execute")
            tool_name = form.get("tool_name") or None
            risk_level = form.get("risk_level") or None
            actor_id = form.get("actor_id", "anonymous")
            permissions_str = form.get("permissions", "")
            roles_str = form.get("roles", "")

            permissions = [p.strip() for p in permissions_str.split(",") if p.strip()] if permissions_str else []
            roles = [r.strip() for r in roles_str.split(",") if r.strip()] if roles_str else []

            action_type = PolicyActionType(action_type_str)
            context = RunContext(
                run_id=f"console_eval_{actor_id}",
                user_id=actor_id,
                tenant_id=form.get("tenant_id") or "default",
                permissions=permissions,
                roles=roles,
            )
            eval_request = RuntimePolicyEvaluationRequest(
                action_type=action_type,
                tool_name=tool_name,
                risk_level=risk_level,
                context=context,
            )
            decision = await policy_enforcement_service.enforce(eval_request)
            result = {
                "decision_id": decision.decision_id,
                "status": decision.status.value,
                "action_type": decision.action_type.value,
                "reason": decision.reason or "—",
                "required_permissions": list(decision.required_permissions),
                "required_roles": list(decision.required_roles),
            }
        except (ValueError, KeyError) as exc:
            message = str(exc)
        except Exception as exc:
            message = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_runtime_evaluate.html",
            {
                "title": title,
                "base_path": base_path,
                "result": result,
                "message": message,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 36 Task 8: Rollout Approval pages
    # -----------------------------------------------------------------------

    @router.get("/rollout-approvals", response_class=HTMLResponse)
    async def rollout_approvals_list(request: Request):
        """Rollout step approvals list page."""
        from agent_app.governance.policy_rollout_approval import RolloutStepApprovalStatus

        status_filter = request.query_params.get("status", "")
        rollout_id_filter = request.query_params.get("rollout_id", "")

        approvals: list[dict] = []
        if approval_store is not None:
            parsed_status = None
            if status_filter:
                try:
                    parsed_status = RolloutStepApprovalStatus(status_filter)
                except ValueError:
                    parsed_status = None
            parsed_rollout_id = rollout_id_filter or None
            results = await approval_store.list(status=parsed_status, rollout_id=parsed_rollout_id)
            for a in results:
                approvals.append(_approval_to_row(a))

        return templates.TemplateResponse(
            request,
            "policy_rollout_approvals.html",
            {
                "title": title,
                "base_path": base_path,
                "approvals": approvals,
                "filters": {
                    "status": status_filter,
                    "rollout_id": rollout_id_filter,
                },
                "store_available": approval_store is not None,
            },
        )

    @router.get("/rollout-approvals/{approval_id}", response_class=HTMLResponse)
    async def rollout_approval_detail(request: Request, approval_id: str):
        """Single rollout step approval detail page."""
        if approval_store is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_approval_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": False,
                    "approval": None,
                    "error": "Approval store not configured.",
                },
            )
        approval = await approval_store.get(approval_id)
        if approval is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_approval_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "store_available": True,
                    "approval": None,
                    "error": f"Approval '{approval_id}' not found.",
                },
            )
        return templates.TemplateResponse(
            request,
            "policy_rollout_approval_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": True,
                "approval": _approval_to_detail(approval),
                "error": None,
            },
        )

    @router.post("/rollouts/{rollout_id}/steps/{step_id}/request-approval")
    async def rollout_request_approval(request: Request, rollout_id: str, step_id: str):
        """Request approval for a rollout step."""
        message = None
        plan_detail = None
        if rollout_service is None:
            message = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                if not actor_id:
                    message = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    result = await rollout_service.request_step_approval(
                        rollout_id=rollout_id,
                        step_id=step_id,
                        requested_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    message = f"Approval requested for step '{step_id}'."
            except PermissionError as exc:
                message = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                message = str(exc)
            except Exception as exc:
                message = str(exc)

        # Re-render rollout detail page
        if rollout_store is not None:
            plan = await rollout_store.get(rollout_id)
            if plan is not None:
                plan_detail = _rollout_to_detail(plan)

        return templates.TemplateResponse(
            request,
            "policy_rollout_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": rollout_store is not None,
                "plan": plan_detail,
                "message": message,
                "error": None if plan_detail else message,
            },
        )

    @router.post("/rollout-approvals/{approval_id}/approve")
    async def rollout_approve_approval(request: Request, approval_id: str):
        """Approve a pending rollout step approval."""
        error_msg = None
        approval_dict = None
        if rollout_service is None:
            error_msg = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                roles_str = form.get("roles", "")
                roles = [r.strip() for r in roles_str.split(",") if r.strip()] if roles_str else []
                if not actor_id:
                    error_msg = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        roles=roles,
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await rollout_service.approve_step(
                        approval_id=approval_id,
                        approved_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    approval_dict = _approval_to_detail(updated)
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                error_msg = str(exc)
            except Exception as exc:
                error_msg = str(exc)

        # Re-render approval detail page
        if approval_dict is None and approval_store is not None:
            approval = await approval_store.get(approval_id)
            if approval is not None:
                approval_dict = _approval_to_detail(approval)

        return templates.TemplateResponse(
            request,
            "policy_rollout_approval_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": approval_store is not None,
                "approval": approval_dict,
                "error": error_msg,
            },
        )

    @router.post("/rollout-approvals/{approval_id}/reject")
    async def rollout_approve_reject(request: Request, approval_id: str):
        """Reject a pending rollout step approval."""
        error_msg = None
        approval_dict = None
        if rollout_service is None:
            error_msg = "Rollout service not configured."
        else:
            try:
                form = await request.form()
                actor_id = form.get("actor_id", "")
                reason = form.get("reason") or None
                roles_str = form.get("roles", "")
                roles = [r.strip() for r in roles_str.split(",") if r.strip()] if roles_str else []
                if not actor_id:
                    error_msg = "actor_id is required."
                else:
                    from agent_app.core.context import RunContext
                    context = RunContext(
                        run_id=f"console_{actor_id}",
                        user_id=actor_id,
                        tenant_id=form.get("tenant_id") or "default",
                        roles=roles,
                        permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                    )
                    updated = await rollout_service.reject_step(
                        approval_id=approval_id,
                        rejected_by=actor_id,
                        context=context,
                        reason=reason,
                    )
                    approval_dict = _approval_to_detail(updated)
            except PermissionError as exc:
                error_msg = f"Permission denied: {exc}"
            except (KeyError, ValueError) as exc:
                error_msg = str(exc)
            except Exception as exc:
                error_msg = str(exc)

        # Re-render approval detail page
        if approval_dict is None and approval_store is not None:
            approval = await approval_store.get(approval_id)
            if approval is not None:
                approval_dict = _approval_to_detail(approval)

        return templates.TemplateResponse(
            request,
            "policy_rollout_approval_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "store_available": approval_store is not None,
                "approval": approval_dict,
                "error": error_msg,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 40 Task 8: Simulation pages
    # -----------------------------------------------------------------------

    def _parse_candidate_rules_yaml(yaml_text: str) -> tuple[list, str | None]:
        """Parse candidate rules from YAML text.

        Returns (rules, error_message).  If parsing fails, rules is empty
        and error_message describes the problem.
        """
        if not yaml_text or not yaml_text.strip():
            return [], "Candidate rules YAML is required."
        try:
            import yaml
        except ImportError:
            return [], "PyYAML is not installed. Install with: pip install pyyaml"
        try:
            parsed = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            return [], f"Invalid YAML: {exc}"
        if not isinstance(parsed, list):
            return [], "YAML must be a list of rule objects."
        from agent_app.governance.runtime_policy import (
            RuntimePolicyEffect,
            RuntimePolicyRule,
        )
        rules: list[RuntimePolicyRule] = []
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                return [], f"Rule at index {i} is not a mapping."
            try:
                rule_id = item.get("rule_id", f"candidate_{i + 1}")
                # Auto-prefix rpr_ if missing — candidate YAML from the
                # console doesn't require knowledge of internal prefixes.
                if not rule_id.startswith("rpr_"):
                    rule_id = f"rpr_{rule_id}"
                name = item.get("name", f"Candidate Rule {i + 1}")
                action_type = PolicyActionType(item.get("action_type", "tool.execute"))
                effect = RuntimePolicyEffect(item.get("effect", "allow"))
                rule = RuntimePolicyRule(
                    rule_id=rule_id,
                    name=name,
                    action_type=action_type,
                    effect=effect,
                    tool_name=item.get("tool_name"),
                    risk_level=item.get("risk_level"),
                    reason=item.get("reason"),
                )
                rules.append(rule)
            except (ValueError, KeyError) as exc:
                return [], f"Invalid rule at index {i}: {exc}"
        return rules, None

    @router.get("/simulation", response_class=HTMLResponse)
    async def simulation_page(request: Request):
        """Main simulation page with textarea for candidate YAML rules."""
        return templates.TemplateResponse(
            request,
            "policy_simulation.html",
            {
                "title": title,
                "base_path": base_path,
                "report": None,
                "error": None,
            },
        )

    @router.post("/simulation/validate", response_class=HTMLResponse)
    async def simulation_validate(request: Request):
        """Validate candidate rules and return validation report."""
        form = await request.form()
        candidate_yaml = form.get("candidate_yaml", "")

        rules, parse_error = _parse_candidate_rules_yaml(candidate_yaml)
        if parse_error:
            return templates.TemplateResponse(
                request,
                "policy_validation_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": parse_error,
                },
            )

        try:
            from agent_app.runtime.policy_validation import RuntimePolicyValidator
            validator = RuntimePolicyValidator()
            report = validator.validate_rules(rules)
            # Convert report to dict for template rendering
            report_dict = {
                "valid": report.valid,
                "issues": [
                    {
                        "severity": issue.severity.value,
                        "code": issue.code,
                        "message": issue.message,
                        "rule_id": issue.rule_id or "",
                        "field": issue.field or "",
                    }
                    for issue in report.issues
                ],
            }
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "policy_validation_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": str(exc),
                },
            )

        return templates.TemplateResponse(
            request,
            "policy_validation_report.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report_dict,
                "error": None,
            },
        )

    @router.post("/simulation/replay", response_class=HTMLResponse)
    async def simulation_replay(request: Request):
        """Run simulation replay and return simulation report."""
        if simulation_service is None:
            return HTMLResponse(
                "<p>Policy simulation service not configured.</p>",
                status_code=404,
            )

        form = await request.form()
        candidate_yaml = form.get("candidate_yaml", "")
        since_str = form.get("since", "")
        until_str = form.get("until", "")
        limit_str = form.get("limit", "")

        rules, parse_error = _parse_candidate_rules_yaml(candidate_yaml)
        if parse_error:
            return templates.TemplateResponse(
                request,
                "policy_simulation_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": parse_error,
                },
            )

        window_start = None
        window_end = None
        limit = None
        error = None

        if since_str:
            try:
                window_start = datetime.fromisoformat(
                    since_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for since: {since_str}"
        if until_str:
            try:
                window_end = datetime.fromisoformat(
                    until_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for until: {until_str}"
        if limit_str:
            try:
                limit = int(limit_str)
            except ValueError:
                error = f"Invalid limit: {limit_str}"

        if error:
            return templates.TemplateResponse(
                request,
                "policy_simulation_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": error,
                },
            )

        try:
            report = await simulation_service.simulate_from_audit(
                candidate_rules=rules,
                window_start=window_start,
                window_end=window_end,
                limit=limit,
            )
            report_dict = {
                "simulation_id": report.simulation_id,
                "name": report.name,
                "generated_at": report.generated_at.isoformat() if hasattr(report.generated_at, "isoformat") else str(report.generated_at),
                "candidate_rule_ids": report.candidate_rule_ids,
                "summary": {
                    "total": report.summary.total,
                    "unchanged": report.summary.unchanged,
                    "would_allow": report.summary.would_allow,
                    "would_deny": report.summary.would_deny,
                    "would_require_approval": report.summary.would_require_approval,
                    "would_change": report.summary.would_change,
                    "errors": report.summary.errors,
                },
                "results": [
                    {
                        "case_id": r.case_id,
                        "baseline_status": r.baseline_status or "",
                        "candidate_status": r.candidate_status or "",
                        "outcome": r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome),
                        "reason": r.reason or "",
                        "decision_id": r.decision_id or "",
                        "errors": r.errors,
                    }
                    for r in report.results
                ],
            }
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "policy_simulation_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": str(exc),
                },
            )

        return templates.TemplateResponse(
            request,
            "policy_simulation_report.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report_dict,
                "error": None,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 41 Task 6: Simulation gate pages
    # -----------------------------------------------------------------------

    def _parse_gate_rules_yaml(yaml_text: str) -> tuple[list, str | None]:
        """Parse gate rules from YAML text.

        Returns (rules, error_message).  If parsing fails, rules is empty
        and error_message describes the problem.
        """
        if not yaml_text or not yaml_text.strip():
            return [], None  # gate rules are optional
        try:
            import yaml
        except ImportError:
            return [], "PyYAML is not installed. Install with: pip install pyyaml"
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            return [], f"Invalid gate rules YAML: {exc}"
        if data is None:
            return [], None
        if isinstance(data, dict):
            data = data.get("gate_rules", data.get("gates", [data]))
        if not isinstance(data, list):
            data = [data]
        from agent_app.governance.policy_gate import PolicyGateRule
        rules: list[PolicyGateRule] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                return [], f"Gate rule at index {i} is not a mapping."
            try:
                rule = PolicyGateRule(
                    name=item.get("name", f"gate_rule_{i + 1}"),
                    description=item.get("description"),
                    max_changed_decisions=item.get("max_changed_decisions"),
                    max_changed_ratio=item.get("max_changed_ratio"),
                    max_failed_replays=item.get("max_failed_replays"),
                    max_new_denies=item.get("max_new_denies"),
                    max_new_approvals=item.get("max_new_approvals"),
                    fail_on_missing_required_context=item.get("fail_on_missing_required_context", False),
                )
                rules.append(rule)
            except (ValueError, KeyError) as exc:
                return [], f"Invalid gate rule at index {i}: {exc}"
        return rules, None

    @router.get("/simulation/gate", response_class=HTMLResponse)
    async def simulation_gate_page(request: Request):
        """Simulation gate form page."""
        return templates.TemplateResponse(
            request,
            "policy_simulation_gate.html",
            {
                "title": title,
                "base_path": base_path,
                "error": None,
            },
        )

    @router.post("/simulation/gate", response_class=HTMLResponse)
    async def simulation_gate_submit(request: Request):
        """Run simulation gate and return result."""
        if simulation_service is None:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": "Policy simulation service not configured.",
                },
            )

        form = await request.form()
        candidate_yaml = form.get("candidate_rules_yaml", "")
        gate_rules_yaml = form.get("gate_rules_yaml", "")
        since_str = form.get("since", "")
        until_str = form.get("until", "")
        limit_str = form.get("limit", "")

        # Parse candidate rules
        candidate_rules, parse_error = _parse_candidate_rules_yaml(candidate_yaml)
        if parse_error:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": parse_error,
                },
            )

        # Parse gate rules (optional; fallback to evaluator's rules)
        gate_rules, gate_parse_error = _parse_gate_rules_yaml(gate_rules_yaml)
        if gate_parse_error:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": gate_parse_error,
                },
            )

        # If no gate rules provided, try using the evaluator's rules
        if not gate_rules and simulation_gate_evaluator is not None:
            gate_rules = simulation_gate_evaluator._rules

        if not gate_rules:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": "No gate rules provided and no default gate evaluator configured.",
                },
            )

        # Parse optional time window / limit
        window_start = None
        window_end = None
        limit = None
        error = None

        if since_str:
            try:
                window_start = datetime.fromisoformat(
                    since_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for since: {since_str}"
        if until_str:
            try:
                window_end = datetime.fromisoformat(
                    until_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for until: {until_str}"
        if limit_str:
            try:
                limit = int(limit_str)
            except ValueError:
                error = f"Invalid limit: {limit_str}"

        if error:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": error,
                },
            )

        try:
            sim_report, validation_report, gate_result = await simulation_service.validate_and_gate(
                candidate_rules=candidate_rules,
                gate_rules=gate_rules,
                window_start=window_start,
                window_end=window_end,
                limit=limit,
            )

            report_dict = {
                "simulation_id": sim_report.simulation_id,
                "name": sim_report.name,
                "generated_at": sim_report.generated_at.isoformat() if hasattr(sim_report.generated_at, "isoformat") else str(sim_report.generated_at),
                "candidate_rule_ids": sim_report.candidate_rule_ids,
                "summary": {
                    "total": sim_report.summary.total,
                    "unchanged": sim_report.summary.unchanged,
                    "would_allow": sim_report.summary.would_allow,
                    "would_deny": sim_report.summary.would_deny,
                    "would_require_approval": sim_report.summary.would_require_approval,
                    "would_change": sim_report.summary.would_change,
                    "errors": sim_report.summary.errors,
                },
                "results": [
                    {
                        "case_id": r.case_id,
                        "baseline_status": r.baseline_status or "",
                        "candidate_status": r.candidate_status or "",
                        "outcome": r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome),
                        "reason": r.reason or "",
                        "decision_id": r.decision_id or "",
                        "errors": r.errors,
                    }
                    for r in sim_report.results
                ],
            }

            validation_dict = {
                "valid": validation_report.valid,
                "issues": [
                    {
                        "severity": issue.severity.value,
                        "code": issue.code,
                        "message": issue.message,
                        "rule_id": issue.rule_id or "",
                        "field": issue.field or "",
                    }
                    for issue in validation_report.issues
                ],
            }

            gate_dict = {
                "gate_result_id": gate_result.gate_result_id,
                "status": gate_result.status,
                "passed": gate_result.passed,
                "total_decisions": gate_result.total_decisions,
                "changed_decisions": gate_result.changed_decisions,
                "failed_replays": gate_result.failed_replays,
                "changed_ratio": gate_result.changed_ratio,
                "new_denies": gate_result.new_denies,
                "new_approvals": gate_result.new_approvals,
                "missing_context_count": gate_result.missing_context_count,
                "rule_results": gate_result.rule_results,
                "summary": gate_result.summary,
            }
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "policy_simulation_gate_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "gate_result": None,
                    "validation_report": None,
                    "error": str(exc),
                },
            )

        return templates.TemplateResponse(
            request,
            "policy_simulation_gate_report.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report_dict,
                "gate_result": gate_dict,
                "validation_report": validation_dict,
                "error": None,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 42 Task 8: Promotion gate lifecycle pages
    # -----------------------------------------------------------------------

    @router.get("/promotions/{promotion_id}/gate", response_class=HTMLResponse)
    async def promotion_gate_page(request: Request, promotion_id: str):
        """Promotion gate form page — shows current requirement and gate forms."""
        requirement = None
        if release_gate_automation_service is not None:
            requirement = await release_gate_automation_service.check_requirement(
                source_type="promotion",
                source_id=promotion_id,
            )

        requirement_dict = None
        if requirement is not None:
            requirement_dict = {
                "requirement_id": requirement.requirement_id,
                "source_type": requirement.source_type,
                "source_id": requirement.source_id,
                "status": requirement.status.value,
                "gate_result_id": requirement.gate_result_id or "",
                "simulation_id": requirement.simulation_id or "",
                "max_age_seconds": requirement.max_age_seconds,
                "created_at": requirement.created_at.isoformat() if hasattr(requirement.created_at, "isoformat") else str(requirement.created_at),
                "satisfied_at": requirement.satisfied_at.isoformat() if requirement.satisfied_at and hasattr(requirement.satisfied_at, "isoformat") else "",
            }

        return templates.TemplateResponse(
            request,
            "policy_promotion_gate.html",
            {
                "title": title,
                "base_path": base_path,
                "promotion_id": promotion_id,
                "requirement": requirement_dict,
                "error": None,
            },
        )

    @router.post("/promotions/{promotion_id}/gate/require", response_class=HTMLResponse)
    async def promotion_gate_require(request: Request, promotion_id: str):
        """Create a gate requirement for a promotion."""
        requirement_dict = None
        error = None

        if release_gate_automation_service is None:
            error = "Release gate automation service not configured."
        else:
            try:
                form = await request.form()
                max_age_str = form.get("max_age_seconds", "")
                max_age_seconds = int(max_age_str) if max_age_str else None
                requirement = await release_gate_automation_service.require_gate_for_promotion(
                    promotion_id=promotion_id,
                    max_age_seconds=max_age_seconds,
                )
                requirement_dict = {
                    "requirement_id": requirement.requirement_id,
                    "source_type": requirement.source_type,
                    "source_id": requirement.source_id,
                    "status": requirement.status.value,
                    "gate_result_id": requirement.gate_result_id or "",
                    "simulation_id": requirement.simulation_id or "",
                    "max_age_seconds": requirement.max_age_seconds,
                    "created_at": requirement.created_at.isoformat() if hasattr(requirement.created_at, "isoformat") else str(requirement.created_at),
                    "satisfied_at": requirement.satisfied_at.isoformat() if requirement.satisfied_at and hasattr(requirement.satisfied_at, "isoformat") else "",
                }
            except Exception as exc:
                error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_promotion_gate_status.html",
            {
                "title": title,
                "base_path": base_path,
                "promotion_id": promotion_id,
                "requirement": requirement_dict,
                "error": error,
            },
        )

    @router.post("/promotions/{promotion_id}/gate/run", response_class=HTMLResponse)
    async def promotion_gate_run(request: Request, promotion_id: str):
        """Run simulation+gate for a promotion and attach the result."""
        requirement_dict = None
        error = None

        if release_gate_automation_service is None:
            error = "Release gate automation service not configured."
        else:
            try:
                form = await request.form()
                candidate_yaml = form.get("candidate_rules", "")
                gate_rules_yaml = form.get("gate_rules", "")

                # Parse candidate rules
                candidate_rules, parse_error = _parse_candidate_rules_yaml(candidate_yaml)
                if parse_error:
                    error = parse_error
                else:
                    # Parse gate rules (optional)
                    gate_rules, gate_parse_error = _parse_gate_rules_yaml(gate_rules_yaml)
                    if gate_parse_error:
                        error = gate_parse_error
                    else:
                        # If no gate rules, use evaluator's rules
                        if not gate_rules and simulation_gate_evaluator is not None:
                            gate_rules = simulation_gate_evaluator._rules
                        if not gate_rules:
                            error = "No gate rules provided and no default gate evaluator configured."
                        else:
                            from agent_app.core.context import RunContext
                            actor_id = form.get("actor_id", "console_user")
                            context = RunContext(
                                run_id=f"console_gate_{actor_id}",
                                user_id=actor_id,
                                tenant_id=form.get("tenant_id") or "default",
                                permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                            )
                            requirement = await release_gate_automation_service.run_and_attach_simulation_gate_for_promotion(
                                promotion_id=promotion_id,
                                candidate_rules=candidate_rules,
                                gate_rules=gate_rules,
                                context=context,
                            )
                            requirement_dict = {
                                "requirement_id": requirement.requirement_id,
                                "source_type": requirement.source_type,
                                "source_id": requirement.source_id,
                                "status": requirement.status.value,
                                "gate_result_id": requirement.gate_result_id or "",
                                "simulation_id": requirement.simulation_id or "",
                                "max_age_seconds": requirement.max_age_seconds,
                                "created_at": requirement.created_at.isoformat() if hasattr(requirement.created_at, "isoformat") else str(requirement.created_at),
                                "satisfied_at": requirement.satisfied_at.isoformat() if requirement.satisfied_at and hasattr(requirement.satisfied_at, "isoformat") else "",
                            }
            except RuntimeError as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_promotion_gate_status.html",
            {
                "title": title,
                "base_path": base_path,
                "promotion_id": promotion_id,
                "requirement": requirement_dict,
                "error": error,
            },
        )

    @router.post("/promotions/{promotion_id}/gate/attach", response_class=HTMLResponse)
    async def promotion_gate_attach(request: Request, promotion_id: str):
        """Attach an existing gate result to a promotion's requirement."""
        requirement_dict = None
        error = None

        if release_gate_automation_service is None:
            error = "Release gate automation service not configured."
        else:
            try:
                form = await request.form()
                gate_result_id = form.get("gate_result_id", "")
                simulation_id = form.get("simulation_id") or None

                if not gate_result_id:
                    error = "gate_result_id is required."
                else:
                    requirement = await release_gate_automation_service.attach_gate_result(
                        source_type="promotion",
                        source_id=promotion_id,
                        gate_result_id=gate_result_id,
                        simulation_id=simulation_id,
                    )
                    requirement_dict = {
                        "requirement_id": requirement.requirement_id,
                        "source_type": requirement.source_type,
                        "source_id": requirement.source_id,
                        "status": requirement.status.value,
                        "gate_result_id": requirement.gate_result_id or "",
                        "simulation_id": requirement.simulation_id or "",
                        "max_age_seconds": requirement.max_age_seconds,
                        "created_at": requirement.created_at.isoformat() if hasattr(requirement.created_at, "isoformat") else str(requirement.created_at),
                        "satisfied_at": requirement.satisfied_at.isoformat() if requirement.satisfied_at and hasattr(requirement.satisfied_at, "isoformat") else "",
                    }
            except KeyError as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_promotion_gate_status.html",
            {
                "title": title,
                "base_path": base_path,
                "promotion_id": promotion_id,
                "requirement": requirement_dict,
                "error": error,
            },
        )

    # Phase 39: Observability routes
    @router.get("/observability", response_class=HTMLResponse)
    async def observability_dashboard(request: Request):
        """Observability dashboard — aggregated enforcement analytics."""
        service = observability_service
        if service is None:
            return HTMLResponse(
                "<p>Policy observability not configured.</p>",
                status_code=404,
            )
        report = await service.generate_report()
        return templates.TemplateResponse(
            request,
            "policy_observability.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report,
            },
        )

    @router.get("/observability/report", response_class=HTMLResponse)
    async def observability_report_form(request: Request):
        """Observability report form — filter by time window."""
        return templates.TemplateResponse(
            request,
            "policy_observability_report.html",
            {
                "title": title,
                "base_path": base_path,
                "report": None,
            },
        )

    @router.post("/observability/report", response_class=HTMLResponse)
    async def observability_report_submit(request: Request):
        """Observability report — generate filtered report."""
        service = observability_service
        if service is None:
            return HTMLResponse(
                "<p>Policy observability not configured.</p>",
                status_code=404,
            )

        form = await request.form()
        since_str = form.get("since", "")
        until_str = form.get("until", "")

        window_start = None
        window_end = None
        error = None

        if since_str:
            try:
                window_start = datetime.fromisoformat(
                    since_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for since: {since_str}"
        if until_str:
            try:
                window_end = datetime.fromisoformat(
                    until_str.replace("Z", "+00:00")
                )
            except ValueError:
                error = f"Invalid datetime format for until: {until_str}"

        if error:
            return templates.TemplateResponse(
                request,
                "policy_observability_report.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "error": error,
                },
            )

        report = await service.generate_report(
            window_start=window_start, window_end=window_end
        )
        return templates.TemplateResponse(
            request,
            "policy_observability_report.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report,
                "since": since_str,
                "until": until_str,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 43 Task 7: Rollout step gate pages
    # -----------------------------------------------------------------------

    @router.get("/rollouts/{rollout_id}/steps/{step_id}/gate", response_class=HTMLResponse)
    async def rollout_step_gate_page(request: Request, rollout_id: str, step_id: str):
        """Rollout step gate form page — shows gate config, current status, and forms."""
        plan = None
        step = None
        gate_result_dict = None
        error = None

        if rollout_store is None:
            error = "Rollout store not configured."
        else:
            plan = await rollout_store.get(rollout_id)
            if plan is None:
                error = f"Rollout plan '{rollout_id}' not found."
            else:
                for s in plan.steps:
                    if s.step_id == step_id:
                        step = s
                        break
                if step is None:
                    error = f"Step '{step_id}' not found in rollout '{rollout_id}'."

        # Check gate status if service and step are available
        if rollout_gate_automation_service is not None and plan is not None and step is not None:
            try:
                gate_result = await rollout_gate_automation_service.check_step_gate(plan, step)
                gate_result_dict = {
                    "execution_id": gate_result.execution_id,
                    "status": gate_result.status.value,
                    "requirement_id": gate_result.requirement_id or "",
                    "gate_result_id": gate_result.gate_result_id or "",
                    "simulation_id": gate_result.simulation_id or "",
                    "action_taken": gate_result.action_taken or "",
                    "reason": gate_result.reason or "",
                    "created_at": gate_result.created_at.isoformat() if hasattr(gate_result.created_at, "isoformat") else str(gate_result.created_at),
                }
            except Exception as exc:
                error = str(exc)

        # Gate config from step
        gate_mode = None
        failure_action = None
        requires_gate = None
        if step is not None:
            gate_mode = step.simulation_gate_mode.value if hasattr(step.simulation_gate_mode, "value") else str(step.simulation_gate_mode)
            failure_action = step.simulation_gate_failure_action.value if hasattr(step.simulation_gate_failure_action, "value") else str(step.simulation_gate_failure_action)
            requires_gate = str(step.requires_simulation_gate)

        return templates.TemplateResponse(
            request,
            "policy_rollout_gate.html",
            {
                "title": title,
                "base_path": base_path,
                "rollout_id": rollout_id,
                "step_id": step_id,
                "gate_mode": gate_mode,
                "failure_action": failure_action,
                "requires_gate": requires_gate,
                "gate_result": gate_result_dict,
                "error": error,
            },
        )

    @router.post("/rollouts/{rollout_id}/steps/{step_id}/gate/run", response_class=HTMLResponse)
    async def rollout_step_gate_run(request: Request, rollout_id: str, step_id: str):
        """Run gate for a rollout step and display the result."""
        gate_result_dict = None
        error = None

        if rollout_gate_automation_service is None:
            error = "Rollout gate automation service not configured."
        elif rollout_store is None:
            error = "Rollout store not configured."
        else:
            try:
                plan = await rollout_store.get(rollout_id)
                if plan is None:
                    error = f"Rollout plan '{rollout_id}' not found."
                else:
                    step = None
                    for s in plan.steps:
                        if s.step_id == step_id:
                            step = s
                            break
                    if step is None:
                        error = f"Step '{step_id}' not found in rollout '{rollout_id}'."
                    else:
                        form = await request.form()
                        from agent_app.core.context import RunContext
                        actor_id = form.get("actor_id", "console_user")
                        context = RunContext(
                            run_id=f"console_gate_{actor_id}",
                            user_id=actor_id,
                            tenant_id=form.get("tenant_id") or "default",
                            permissions=form.get("permissions", "").split(",") if form.get("permissions") else [],
                        )
                        gate_result = await rollout_gate_automation_service.run_step_gate(plan, step, context)
                        gate_result_dict = {
                            "execution_id": gate_result.execution_id,
                            "status": gate_result.status.value,
                            "requirement_id": gate_result.requirement_id or "",
                            "gate_result_id": gate_result.gate_result_id or "",
                            "simulation_id": gate_result.simulation_id or "",
                            "action_taken": gate_result.action_taken or "",
                            "reason": gate_result.reason or "",
                            "created_at": gate_result.created_at.isoformat() if hasattr(gate_result.created_at, "isoformat") else str(gate_result.created_at),
                        }
            except Exception as exc:
                error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_rollout_gate_status.html",
            {
                "title": title,
                "base_path": base_path,
                "rollout_id": rollout_id,
                "step_id": step_id,
                "gate_result": gate_result_dict,
                "error": error,
            },
        )

    @router.post("/rollouts/{rollout_id}/steps/{step_id}/gate/attach", response_class=HTMLResponse)
    async def rollout_step_gate_attach(request: Request, rollout_id: str, step_id: str):
        """Attach an existing gate result to a rollout step's requirement."""
        gate_result_dict = None
        error = None

        if rollout_gate_automation_service is None:
            error = "Rollout gate automation service not configured."
        elif rollout_store is None:
            error = "Rollout store not configured."
        else:
            try:
                form = await request.form()
                gate_result_id = form.get("gate_result_id", "")
                simulation_id = form.get("simulation_id") or None

                if not gate_result_id:
                    error = "gate_result_id is required."
                else:
                    source_id = f"{rollout_id}:{step_id}"
                    requirement = await rollout_gate_automation_service._release_gate.attach_gate_result(
                        source_type="promotion",
                        source_id=source_id,
                        gate_result_id=gate_result_id,
                        simulation_id=simulation_id,
                    )
                    # Build result dict from the requirement
                    gate_result_dict = {
                        "execution_id": "",
                        "status": requirement.status.value if hasattr(requirement.status, "value") else str(requirement.status),
                        "requirement_id": requirement.requirement_id or "",
                        "gate_result_id": requirement.gate_result_id or "",
                        "simulation_id": requirement.simulation_id or "",
                        "action_taken": "attach",
                        "reason": "",
                        "created_at": requirement.created_at.isoformat() if requirement.created_at and hasattr(requirement.created_at, "isoformat") else "",
                    }
            except KeyError as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_rollout_gate_status.html",
            {
                "title": title,
                "base_path": base_path,
                "rollout_id": rollout_id,
                "step_id": step_id,
                "gate_result": gate_result_dict,
                "error": error,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 44 Task 7: Notification and expiration pages
    # -----------------------------------------------------------------------

    @router.get("/notifications", response_class=HTMLResponse)
    async def notifications_page(request: Request):
        """Notifications list page."""
        notifications_list: list[dict] = []
        if notification_service is not None:
            try:
                notifications = await notification_service.list_notifications(limit=page_size)
                for n in notifications:
                    notifications_list.append({
                        "notification_id": n.notification_id,
                        "event_type": n.event_type,
                        "severity": n.severity.value if hasattr(n.severity, "value") else str(n.severity),
                        "title": n.title,
                        "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                        "created_at": n.created_at.isoformat() if n.created_at and hasattr(n.created_at, "isoformat") else "",
                        "sent_at": n.sent_at.isoformat() if n.sent_at and hasattr(n.sent_at, "isoformat") else "",
                    })
            except Exception:
                pass
        return templates.TemplateResponse(
            request,
            "policy_notifications.html",
            {
                "title": title,
                "base_path": base_path,
                "notifications": notifications_list,
                "store_available": notification_service is not None,
                "message": None,
            },
        )

    @router.post("/notifications/send-pending")
    async def notifications_send_pending(request: Request):
        """Send pending notifications."""
        message = None
        notifications_list: list[dict] = []
        if notification_service is None:
            message = "Notification service not configured."
        else:
            try:
                sent = await notification_service.send_pending()
                message = f"Sent {len(sent)} pending notification(s)."
            except Exception as exc:
                message = f"Error sending pending notifications: {exc}"

        # Re-fetch notifications for display
        if notification_service is not None:
            try:
                notifications = await notification_service.list_notifications(limit=page_size)
                for n in notifications:
                    notifications_list.append({
                        "notification_id": n.notification_id,
                        "event_type": n.event_type,
                        "severity": n.severity.value if hasattr(n.severity, "value") else str(n.severity),
                        "title": n.title,
                        "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                        "created_at": n.created_at.isoformat() if n.created_at and hasattr(n.created_at, "isoformat") else "",
                        "sent_at": n.sent_at.isoformat() if n.sent_at and hasattr(n.sent_at, "isoformat") else "",
                    })
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "policy_notifications.html",
            {
                "title": title,
                "base_path": base_path,
                "notifications": notifications_list,
                "store_available": notification_service is not None,
                "message": message,
            },
        )

    @router.get("/notification-rules", response_class=HTMLResponse)
    async def notification_rules_page(request: Request):
        """Notification rules list page."""
        rules_list: list[dict] = []
        if notification_service is not None:
            rule_store = getattr(notification_service, "_rule_store", None)
            if rule_store is not None:
                try:
                    rules = await rule_store.list()
                    for r in rules:
                        rules_list.append({
                            "rule_id": r.rule_id,
                            "name": r.name,
                            "event_types": list(r.event_types) if hasattr(r.event_types, "__iter__") else [],
                            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                            "channels": list(r.channels) if hasattr(r.channels, "__iter__") else [],
                            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                        })
                except Exception:
                    pass
        return templates.TemplateResponse(
            request,
            "policy_notification_rules.html",
            {
                "title": title,
                "base_path": base_path,
                "rules": rules_list,
                "store_available": notification_service is not None,
                "message": None,
            },
        )

    @router.post("/notification-rules/{rule_id}/enable")
    async def notification_rule_enable(request: Request, rule_id: str):
        """Enable a notification rule."""
        message = None
        rules_list: list[dict] = []
        if notification_service is None:
            message = "Notification service not configured."
        else:
            rule_store = getattr(notification_service, "_rule_store", None)
            if rule_store is None:
                message = "Notification rule store not available."
            else:
                try:
                    await rule_store.enable(rule_id)
                    message = f"Rule '{rule_id}' enabled."
                except KeyError:
                    message = f"Rule '{rule_id}' not found."
                except Exception as exc:
                    message = f"Error enabling rule: {exc}"

        # Re-fetch rules for display
        if notification_service is not None:
            rule_store = getattr(notification_service, "_rule_store", None)
            if rule_store is not None:
                try:
                    rules = await rule_store.list()
                    for r in rules:
                        rules_list.append({
                            "rule_id": r.rule_id,
                            "name": r.name,
                            "event_types": list(r.event_types) if hasattr(r.event_types, "__iter__") else [],
                            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                            "channels": list(r.channels) if hasattr(r.channels, "__iter__") else [],
                            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                        })
                except Exception:
                    pass

        return templates.TemplateResponse(
            request,
            "policy_notification_rules.html",
            {
                "title": title,
                "base_path": base_path,
                "rules": rules_list,
                "store_available": notification_service is not None,
                "message": message,
            },
        )

    @router.post("/notification-rules/{rule_id}/disable")
    async def notification_rule_disable(request: Request, rule_id: str):
        """Disable a notification rule."""
        message = None
        rules_list: list[dict] = []
        if notification_service is None:
            message = "Notification service not configured."
        else:
            rule_store = getattr(notification_service, "_rule_store", None)
            if rule_store is None:
                message = "Notification rule store not available."
            else:
                try:
                    await rule_store.disable(rule_id)
                    message = f"Rule '{rule_id}' disabled."
                except KeyError:
                    message = f"Rule '{rule_id}' not found."
                except Exception as exc:
                    message = f"Error disabling rule: {exc}"

        # Re-fetch rules for display
        if notification_service is not None:
            rule_store = getattr(notification_service, "_rule_store", None)
            if rule_store is not None:
                try:
                    rules = await rule_store.list()
                    for r in rules:
                        rules_list.append({
                            "rule_id": r.rule_id,
                            "name": r.name,
                            "event_types": list(r.event_types) if hasattr(r.event_types, "__iter__") else [],
                            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                            "channels": list(r.channels) if hasattr(r.channels, "__iter__") else [],
                            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                        })
                except Exception:
                    pass

        return templates.TemplateResponse(
            request,
            "policy_notification_rules.html",
            {
                "title": title,
                "base_path": base_path,
                "rules": rules_list,
                "store_available": notification_service is not None,
                "message": message,
            },
        )

    @router.get("/expiration", response_class=HTMLResponse)
    async def expiration_page(request: Request):
        """Expiration page showing last sweep info."""
        return templates.TemplateResponse(
            request,
            "policy_expiration.html",
            {
                "title": title,
                "base_path": base_path,
                "last_sweep": None,
                "store_available": expiration_service is not None,
                "message": None,
            },
        )

    @router.post("/expiration/sweep")
    async def expiration_sweep(request: Request):
        """Run expiration sweep."""
        message = None
        last_sweep = None
        if expiration_service is None:
            message = "Expiration service not configured."
        else:
            try:
                report = await expiration_service.sweep()
                expired_count = sum(1 for r in report.results if hasattr(r.action, "value") and r.action.value == "expired")
                error_count = sum(1 for r in report.results if hasattr(r.action, "value") and r.action.value == "error")
                skipped_count = len(report.results) - expired_count - error_count
                last_sweep = {
                    "sweep_id": report.sweep_id,
                    "started_at": report.started_at.isoformat() if report.started_at and hasattr(report.started_at, "isoformat") else "",
                    "completed_at": report.completed_at.isoformat() if report.completed_at and hasattr(report.completed_at, "isoformat") else "",
                    "total_results": len(report.results),
                    "expired_count": expired_count,
                    "skipped_count": skipped_count,
                    "error_count": error_count,
                    "results": [
                        {
                            "target_type": r.target_type.value if hasattr(r.target_type, "value") else str(r.target_type),
                            "target_id": r.target_id,
                            "action": r.action.value if hasattr(r.action, "value") else str(r.action),
                            "reason": r.reason or "",
                        }
                        for r in report.results
                    ],
                }
                message = f"Sweep completed: {expired_count} expired, {skipped_count} skipped, {error_count} errors."
            except Exception as exc:
                message = f"Error running sweep: {exc}"

        return templates.TemplateResponse(
            request,
            "policy_expiration.html",
            {
                "title": title,
                "base_path": base_path,
                "last_sweep": last_sweep,
                "store_available": expiration_service is not None,
                "message": message,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 45 Task 7: Rollout history, timeline, and analytics pages
    # -----------------------------------------------------------------------

    @router.get("/rollouts/{rollout_id}/history", response_class=HTMLResponse)
    async def rollout_history(request: Request, rollout_id: str):
        """Show rollout history events."""
        events_list: list[dict] = []
        event_types: list[str] = []
        selected_type = request.query_params.get("event_type", "")

        if rollout_history_service is not None:
            try:
                from agent_app.governance.policy_rollout_history import RolloutHistoryEventType
                event_types = [e.value for e in RolloutHistoryEventType]

                event_type_filter = None
                if selected_type:
                    try:
                        event_type_filter = RolloutHistoryEventType(selected_type)
                    except ValueError:
                        selected_type = ""

                events = await rollout_history_service.list_history_events(
                    rollout_id=rollout_id,
                    event_type=event_type_filter,
                )
                for e in events:
                    events_list.append({
                        "history_event_id": e.history_event_id,
                        "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                        "step_id": e.step_id,
                        "actor_id": e.actor_id,
                        "environment": e.environment,
                        "ring_name": e.ring_name,
                        "message": e.message,
                        "created_at": e.created_at.isoformat() if e.created_at and hasattr(e.created_at, "isoformat") else "",
                    })
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "policy_rollout_history.html",
            {
                "title": title,
                "base_path": base_path,
                "rollout_id": rollout_id,
                "events": events_list,
                "event_types": event_types,
                "selected_type": selected_type,
                "store_available": rollout_history_service is not None,
                "message": None,
            },
        )

    @router.get("/rollouts/{rollout_id}/timeline", response_class=HTMLResponse)
    async def rollout_timeline(request: Request, rollout_id: str):
        """Show rollout timeline."""
        timeline_dict = None
        export = request.query_params.get("export", "")

        if rollout_history_service is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_timeline.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "rollout_id": rollout_id,
                    "timeline": None,
                    "store_available": False,
                    "message": None,
                },
            )

        try:
            timeline_obj = await rollout_history_service.get_timeline(rollout_id)

            # JSON export
            if export == "json":
                from fastapi.responses import JSONResponse
                return JSONResponse(content=timeline_obj.model_dump(mode="json"))

            # Build dict for template rendering
            steps_dicts: list[dict] = []
            for s in timeline_obj.steps:
                steps_dicts.append({
                    "step_id": s.step_id,
                    "step_type": s.step_type,
                    "status": s.status,
                    "gate_status": s.gate_status,
                    "approval_status": s.approval_status,
                    "duration_seconds": s.duration_seconds,
                    "environment": s.environment,
                    "ring_name": s.ring_name,
                    "started_at": s.started_at.isoformat() if s.started_at and hasattr(s.started_at, "isoformat") else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at and hasattr(s.completed_at, "isoformat") else None,
                    "events": [
                        {
                            "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                            "step_id": e.step_id,
                            "actor_id": e.actor_id,
                            "message": e.message,
                            "created_at": e.created_at.isoformat() if e.created_at and hasattr(e.created_at, "isoformat") else "",
                        }
                        for e in s.events
                    ],
                })

            events_dicts: list[dict] = []
            for e in timeline_obj.events:
                events_dicts.append({
                    "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                    "step_id": e.step_id,
                    "actor_id": e.actor_id,
                    "message": e.message,
                    "created_at": e.created_at.isoformat() if e.created_at and hasattr(e.created_at, "isoformat") else "",
                })

            timeline_dict = {
                "rollout_id": timeline_obj.rollout_id,
                "name": timeline_obj.name,
                "bundle_id": timeline_obj.bundle_id,
                "status": timeline_obj.status,
                "created_at": timeline_obj.created_at.isoformat() if timeline_obj.created_at and hasattr(timeline_obj.created_at, "isoformat") else None,
                "started_at": timeline_obj.started_at.isoformat() if timeline_obj.started_at and hasattr(timeline_obj.started_at, "isoformat") else None,
                "completed_at": timeline_obj.completed_at.isoformat() if timeline_obj.completed_at and hasattr(timeline_obj.completed_at, "isoformat") else None,
                "duration_seconds": timeline_obj.duration_seconds,
                "steps": steps_dicts,
                "events": events_dicts,
            }
        except Exception:
            pass

        return templates.TemplateResponse(
            request,
            "policy_rollout_timeline.html",
            {
                "title": title,
                "base_path": base_path,
                "rollout_id": rollout_id,
                "timeline": timeline_dict,
                "store_available": rollout_history_service is not None,
                "message": None,
            },
        )

    @router.get("/rollout-analytics", response_class=HTMLResponse)
    async def rollout_analytics_get(request: Request):
        """Show rollout analytics dashboard."""
        report_dict = None
        export = request.query_params.get("export", "")

        if rollout_history_service is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": False,
                    "message": None,
                    "since_default": "",
                    "until_default": "",
                },
            )

        # If export requested, generate report and return raw data
        if export in ("json", "csv"):
            try:
                report_obj = await rollout_history_service.generate_report()
                if export == "json":
                    from fastapi.responses import JSONResponse
                    return JSONResponse(content=report_obj.model_dump(mode="json"))
                if export == "csv":
                    # Simple CSV export of summary stats
                    import io
                    buf = io.StringIO()
                    buf.write("metric,value\n")
                    buf.write(f"total_rollouts,{report_obj.total_rollouts}\n")
                    buf.write(f"completed_rollouts,{report_obj.completed_rollouts}\n")
                    buf.write(f"failed_rollouts,{report_obj.failed_rollouts}\n")
                    buf.write(f"blocked_rollouts,{report_obj.blocked_rollouts}\n")
                    buf.write(f"gate_satisfied,{report_obj.gate_outcomes.satisfied}\n")
                    buf.write(f"gate_blocked,{report_obj.gate_outcomes.blocked}\n")
                    buf.write(f"gate_failed,{report_obj.gate_outcomes.failed}\n")
                    buf.write(f"approval_approved,{report_obj.approval_outcomes.approved}\n")
                    buf.write(f"approval_rejected,{report_obj.approval_outcomes.rejected}\n")
                    buf.write(f"avg_approval_latency_seconds,{report_obj.approval_outcomes.average_latency_seconds or ''}\n")
                    from fastapi.responses import StreamingResponse
                    return StreamingResponse(
                        iter([buf.getvalue()]),
                        media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=rollout_analytics.csv"},
                    )
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "policy_rollout_analytics.html",
            {
                "title": title,
                "base_path": base_path,
                "report": None,
                "store_available": rollout_history_service is not None,
                "message": None,
                "since_default": "",
                "until_default": "",
            },
        )

    @router.post("/rollout-analytics", response_class=HTMLResponse)
    async def rollout_analytics_post(request: Request):
        """Generate rollout analytics report with time window."""
        message = None
        report_dict = None

        if rollout_history_service is None:
            return templates.TemplateResponse(
                request,
                "policy_rollout_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": False,
                    "message": "Rollout history service not configured.",
                    "since_default": "",
                    "until_default": "",
                },
            )

        # Parse form data
        form = await request.form()
        since_str = form.get("since", "")
        until_str = form.get("until", "")
        since_dt = None
        until_dt = None
        since_default = str(since_str) if since_str else ""
        until_default = str(until_str) if until_str else ""

        if since_str:
            try:
                from datetime import datetime as _dt
                since_dt = _dt.fromisoformat(str(since_str))
            except (ValueError, TypeError):
                message = "Invalid 'since' datetime format."
        if until_str:
            try:
                from datetime import datetime as _dt
                until_dt = _dt.fromisoformat(str(until_str))
            except (ValueError, TypeError):
                if message:
                    message += " Invalid 'until' datetime format."
                else:
                    message = "Invalid 'until' datetime format."

        if message and (since_dt is None and until_dt is None):
            # Both datetime fields failed, still render with error
            return templates.TemplateResponse(
                request,
                "policy_rollout_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": rollout_history_service is not None,
                    "message": message,
                    "since_default": since_default,
                    "until_default": until_default,
                },
            )

        try:
            report_obj = await rollout_history_service.generate_report(
                window_start=since_dt,
                window_end=until_dt,
            )
            # Build report dict for template rendering
            report_dict = {
                "report_id": report_obj.report_id,
                "generated_at": report_obj.generated_at.isoformat() if report_obj.generated_at and hasattr(report_obj.generated_at, "isoformat") else "",
                "total_rollouts": report_obj.total_rollouts,
                "completed_rollouts": report_obj.completed_rollouts,
                "failed_rollouts": report_obj.failed_rollouts,
                "cancelled_rollouts": report_obj.cancelled_rollouts,
                "blocked_rollouts": report_obj.blocked_rollouts,
                "gate_outcomes": {
                    "total": report_obj.gate_outcomes.total,
                    "satisfied": report_obj.gate_outcomes.satisfied,
                    "blocked": report_obj.gate_outcomes.blocked,
                    "failed": report_obj.gate_outcomes.failed,
                    "skipped": report_obj.gate_outcomes.skipped,
                    "expired": report_obj.gate_outcomes.expired,
                },
                "approval_outcomes": {
                    "total": report_obj.approval_outcomes.total,
                    "pending": report_obj.approval_outcomes.pending,
                    "approved": report_obj.approval_outcomes.approved,
                    "rejected": report_obj.approval_outcomes.rejected,
                    "expired": report_obj.approval_outcomes.expired,
                    "average_latency_seconds": report_obj.approval_outcomes.average_latency_seconds,
                },
                "top_blocked_steps": report_obj.top_blocked_steps,
                "top_failed_gates": report_obj.top_failed_gates,
                "environment_summary": report_obj.environment_summary,
                "ring_summary": report_obj.ring_summary,
            }
        except Exception as exc:
            message = f"Error generating report: {exc}"

        return templates.TemplateResponse(
            request,
            "policy_rollout_analytics.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report_dict,
                "store_available": rollout_history_service is not None,
                "message": message,
                "since_default": since_default,
                "until_default": until_default,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 46 Task 8: Console federation pages
    # -----------------------------------------------------------------------

    async def _fed_form_dict(request: Request) -> dict[str, str]:
        form = await request.form()
        return {str(k): str(v) for k, v in form.items()}

    def _fed_context_from_form(form: dict[str, str]):
        from agent_app.core.context import RunContext
        permissions = [p.strip() for p in form.get("permissions", "").split(",") if p.strip()]
        return RunContext(
            run_id="console-policy-federation",
            user_id=form.get("actor_id") or "console",
            tenant_id=form.get("tenant_id") or "default",
            permissions=permissions,
        )

    @router.get("/federation/targets", response_class=HTMLResponse)
    async def federation_targets_list(request: Request):
        """List federation targets."""
        targets: list = []
        if federated_rollout_target_store is not None:
            targets = await federated_rollout_target_store.list()
        return templates.TemplateResponse(
            request,
            "policy_federation_targets.html",
            {
                "title": title,
                "base_path": base_path,
                "targets": targets,
                "error": None,
            },
        )

    @router.post("/federation/targets")
    async def federation_target_create(request: Request):
        """Create a federation target."""
        targets: list = []
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                await rollout_federation_service.create_target(
                    name=form.get("name", ""),
                    environment=form.get("environment", ""),
                    tenant_id=form.get("tenant_id") or None,
                    ring_name=form.get("ring_name") or None,
                    region=form.get("region") or None,
                    actor_id=form.get("actor_id") or None,
                    context=context,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        if federated_rollout_target_store is not None:
            targets = await federated_rollout_target_store.list()
        return templates.TemplateResponse(
            request,
            "policy_federation_targets.html",
            {
                "title": title,
                "base_path": base_path,
                "targets": targets,
                "error": error,
            },
        )

    @router.post("/federation/targets/{target_id}/disable")
    async def federation_target_disable(request: Request, target_id: str):
        """Disable a federation target."""
        targets: list = []
        error = None
        if federated_rollout_target_store is not None:
            try:
                await federated_rollout_target_store.disable(target_id)
            except Exception as exc:
                error = str(exc)
            targets = await federated_rollout_target_store.list()
        return templates.TemplateResponse(
            request,
            "policy_federation_targets.html",
            {
                "title": title,
                "base_path": base_path,
                "targets": targets,
                "error": error,
            },
        )

    @router.post("/federation/targets/{target_id}/enable")
    async def federation_target_enable(request: Request, target_id: str):
        """Enable a federation target."""
        targets: list = []
        error = None
        if federated_rollout_target_store is not None:
            try:
                await federated_rollout_target_store.enable(target_id)
            except Exception as exc:
                error = str(exc)
            targets = await federated_rollout_target_store.list()
        return templates.TemplateResponse(
            request,
            "policy_federation_targets.html",
            {
                "title": title,
                "base_path": base_path,
                "targets": targets,
                "error": error,
            },
        )

    @router.get("/federation/plans", response_class=HTMLResponse)
    async def federation_plans_list(request: Request):
        """List federated rollout plans."""
        plans: list = []
        if federated_rollout_plan_store is not None:
            plans = await federated_rollout_plan_store.list()
        return templates.TemplateResponse(
            request,
            "policy_federation_plans.html",
            {
                "title": title,
                "base_path": base_path,
                "plans": plans,
            },
        )

    @router.get("/federation/plans/new", response_class=HTMLResponse)
    async def federation_plan_create_page(request: Request):
        """Render federated rollout plan creation form."""
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_create.html",
            {
                "title": title,
                "base_path": base_path,
            },
        )

    @router.get("/federation/plans/{federation_id}", response_class=HTMLResponse)
    async def federation_plan_detail(request: Request, federation_id: str):
        """Federated rollout plan detail page."""
        plan = None
        error = None
        if federated_rollout_plan_store is None:
            error = "Federated rollout plan store not configured."
        else:
            plan = await federated_rollout_plan_store.get(federation_id)
            if plan is None:
                error = f"Federated plan '{federation_id}' not found."
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.post("/federation/plans")
    async def federation_plan_create(request: Request):
        """Create a new federated rollout plan."""
        plan = None
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                target_ids_raw = form.get("target_ids", "")
                target_ids = [t.strip() for t in target_ids_raw.splitlines() if t.strip()]
                strategy_str = form.get("strategy", "sequential")
                try:
                    strategy = FederationExecutionStrategy(strategy_str)
                except ValueError:
                    strategy = FederationExecutionStrategy.SEQUENTIAL
                from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
                step_type_str = form.get("step_type", "activate")
                try:
                    step_type = RolloutStepType(step_type_str)
                except ValueError:
                    step_type = RolloutStepType.ACTIVATE
                step = RolloutStep(
                    step_id=form.get("step_id", "step_activate"),
                    step_type=step_type,
                    environment=form.get("step_environment", "prod"),
                    ring_name=form.get("step_ring_name") or None,
                )
                plan = await rollout_federation_service.create_federated_plan(
                    name=form.get("name", ""),
                    bundle_id=form.get("bundle_id", ""),
                    target_ids=target_ids,
                    rollout_template_steps=[step],
                    created_by=form.get("actor_id", ""),
                    context=context,
                    strategy=strategy,
                    reason=form.get("reason") or None,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.post("/federation/plans/{federation_id}/start")
    async def federation_plan_start(request: Request, federation_id: str):
        """Start a federated rollout plan."""
        plan = None
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                plan = await rollout_federation_service.start_federated_plan(
                    federation_id=federation_id,
                    actor_id=form.get("actor_id", ""),
                    context=context,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        if plan is None and federated_rollout_plan_store is not None:
            plan = await federated_rollout_plan_store.get(federation_id)
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.post("/federation/plans/{federation_id}/run-next")
    async def federation_plan_run_next(request: Request, federation_id: str):
        """Run next target in a federated rollout plan."""
        plan = None
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                plan = await rollout_federation_service.run_next_target(
                    federation_id=federation_id,
                    actor_id=form.get("actor_id", ""),
                    context=context,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        if plan is None and federated_rollout_plan_store is not None:
            plan = await federated_rollout_plan_store.get(federation_id)
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.post("/federation/plans/{federation_id}/run-all")
    async def federation_plan_run_all(request: Request, federation_id: str):
        """Run all available targets in a federated rollout plan."""
        plan = None
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                plan = await rollout_federation_service.run_all_available(
                    federation_id=federation_id,
                    actor_id=form.get("actor_id", ""),
                    context=context,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        if plan is None and federated_rollout_plan_store is not None:
            plan = await federated_rollout_plan_store.get(federation_id)
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.post("/federation/plans/{federation_id}/cancel")
    async def federation_plan_cancel(request: Request, federation_id: str):
        """Cancel a federated rollout plan."""
        plan = None
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                form = await _fed_form_dict(request)
                context = _fed_context_from_form(form)
                plan = await rollout_federation_service.cancel_federated_plan(
                    federation_id=federation_id,
                    actor_id=form.get("actor_id", ""),
                    context=context,
                    reason=form.get("reason") or None,
                )
            except PermissionError as exc:
                error = f"Permission denied: {exc}"
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        if plan is None and federated_rollout_plan_store is not None:
            plan = await federated_rollout_plan_store.get(federation_id)
        return templates.TemplateResponse(
            request,
            "policy_federation_plan_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "plan": plan,
                "error": error,
            },
        )

    @router.get("/federation/plans/{federation_id}/conflicts", response_class=HTMLResponse)
    async def federation_plan_conflicts(request: Request, federation_id: str):
        """Detect and display conflicts for a federated plan."""
        conflicts: list = []
        error = None
        if rollout_federation_service is None:
            error = "Rollout federation service not configured."
        else:
            try:
                conflicts = await rollout_federation_service.detect_conflicts(
                    federation_id=federation_id,
                )
            except (ValueError, KeyError) as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)
        return templates.TemplateResponse(
            request,
            "policy_federation_conflicts.html",
            {
                "title": title,
                "base_path": base_path,
                "federation_id": federation_id,
                "conflicts": conflicts,
                "error": error,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 47 Task 8: Federation observability pages
    # -----------------------------------------------------------------------

    @router.get("/federation/plans/{federation_id}/history", response_class=HTMLResponse)
    async def federation_plan_history(request: Request, federation_id: str):
        """Show federation history events."""
        events_list: list[dict] = []
        error = None

        if federation_observability_service is None:
            return templates.TemplateResponse(
                request,
                "policy_federation_history.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "federation_id": federation_id,
                    "events": events_list,
                    "store_available": False,
                    "error": "Federation observability service not configured.",
                },
            )

        try:
            events = await federation_observability_service.list_history_events(
                federation_id=federation_id,
            )
            for e in events:
                events_list.append({
                    "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                    "target_id": e.target_id or "—",
                    "wave_id": e.wave_id or "—",
                    "rollout_id": e.rollout_id or "—",
                    "environment": e.environment or "—",
                    "message": e.message or "—",
                    "created_at": e.created_at.isoformat() if e.created_at and hasattr(e.created_at, "isoformat") else "",
                })
        except KeyError:
            error = f"Federation '{federation_id}' not found."
        except Exception as exc:
            error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_federation_history.html",
            {
                "title": title,
                "base_path": base_path,
                "federation_id": federation_id,
                "events": events_list,
                "store_available": federation_observability_service is not None,
                "error": error,
            },
        )

    @router.get("/federation/plans/{federation_id}/timeline", response_class=HTMLResponse)
    async def federation_plan_timeline(request: Request, federation_id: str):
        """Show federation timeline."""
        timeline_dict = None
        error = None
        export = request.query_params.get("export", "")

        if federation_observability_service is None:
            return templates.TemplateResponse(
                request,
                "policy_federation_timeline.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "federation_id": federation_id,
                    "timeline": None,
                    "store_available": False,
                    "error": None,
                },
            )

        try:
            timeline_obj = await federation_observability_service.get_timeline(federation_id)

            # JSON export
            if export == "json":
                from fastapi.responses import JSONResponse
                return JSONResponse(content=timeline_obj.model_dump(mode="json"))

            # Build dict for template rendering
            waves_dicts: list[dict] = []
            for w in timeline_obj.waves:
                waves_dicts.append({
                    "wave_id": w.wave_id,
                    "status": w.status or "—",
                    "started_at": w.started_at.isoformat() if w.started_at and hasattr(w.started_at, "isoformat") else "—",
                    "completed_at": w.completed_at.isoformat() if w.completed_at and hasattr(w.completed_at, "isoformat") else "—",
                    "duration_seconds": w.duration_seconds,
                    "target_ids": w.target_ids,
                })

            targets_dicts: list[dict] = []
            for t in timeline_obj.targets:
                targets_dicts.append({
                    "target_id": t.target_id,
                    "status": t.status or "—",
                    "environment": t.environment or "—",
                    "ring_name": t.ring_name or "—",
                    "region": t.region or "—",
                    "started_at": t.started_at.isoformat() if t.started_at and hasattr(t.started_at, "isoformat") else "—",
                    "completed_at": t.completed_at.isoformat() if t.completed_at and hasattr(t.completed_at, "isoformat") else "—",
                    "duration_seconds": t.duration_seconds,
                })

            events_dicts: list[dict] = []
            for e in timeline_obj.events:
                events_dicts.append({
                    "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                    "target_id": e.target_id or "—",
                    "wave_id": e.wave_id or "—",
                    "message": e.message or "—",
                    "created_at": e.created_at.isoformat() if e.created_at and hasattr(e.created_at, "isoformat") else "",
                })

            timeline_dict = {
                "federation_id": timeline_obj.federation_id,
                "name": timeline_obj.name,
                "bundle_id": timeline_obj.bundle_id,
                "strategy": timeline_obj.strategy,
                "status": timeline_obj.status,
                "created_at": timeline_obj.created_at.isoformat() if timeline_obj.created_at and hasattr(timeline_obj.created_at, "isoformat") else None,
                "started_at": timeline_obj.started_at.isoformat() if timeline_obj.started_at and hasattr(timeline_obj.started_at, "isoformat") else None,
                "completed_at": timeline_obj.completed_at.isoformat() if timeline_obj.completed_at and hasattr(timeline_obj.completed_at, "isoformat") else None,
                "duration_seconds": timeline_obj.duration_seconds,
                "waves": waves_dicts,
                "targets": targets_dicts,
                "events": events_dicts,
            }
        except KeyError:
            error = f"Federation '{federation_id}' not found."
        except Exception as exc:
            error = str(exc)

        return templates.TemplateResponse(
            request,
            "policy_federation_timeline.html",
            {
                "title": title,
                "base_path": base_path,
                "federation_id": federation_id,
                "timeline": timeline_dict,
                "store_available": federation_observability_service is not None,
                "error": error,
            },
        )

    @router.get("/federation/analytics", response_class=HTMLResponse)
    async def federation_analytics(request: Request):
        """Show federation analytics dashboard."""
        report_dict = None
        export = request.query_params.get("export", "")

        if federation_observability_service is None:
            return templates.TemplateResponse(
                request,
                "policy_federation_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": False,
                    "message": None,
                    "since_default": "",
                    "until_default": "",
                },
            )

        # If export requested, generate report and return raw data
        if export in ("json", "csv"):
            try:
                report_obj = await federation_observability_service.generate_report()
                if export == "json":
                    from fastapi.responses import JSONResponse
                    return JSONResponse(content=report_obj.model_dump(mode="json"))
                if export == "csv":
                    import io
                    buf = io.StringIO()
                    buf.write("metric,value\n")
                    buf.write(f"total_federations,{report_obj.total_federations}\n")
                    buf.write(f"active_federations,{report_obj.active_federations}\n")
                    buf.write(f"completed_federations,{report_obj.completed_federations}\n")
                    buf.write(f"failed_federations,{report_obj.failed_federations}\n")
                    buf.write(f"blocked_federations,{report_obj.blocked_federations}\n")
                    buf.write(f"total_conflicts,{report_obj.conflicts.total_conflicts}\n")
                    from fastapi.responses import StreamingResponse
                    return StreamingResponse(
                        iter([buf.getvalue()]),
                        media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=federation_analytics.csv"},
                    )
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "policy_federation_analytics.html",
            {
                "title": title,
                "base_path": base_path,
                "report": None,
                "store_available": federation_observability_service is not None,
                "message": None,
                "since_default": "",
                "until_default": "",
            },
        )

    @router.post("/federation/analytics", response_class=HTMLResponse)
    async def federation_analytics_post(request: Request):
        """Generate federation analytics report with time window."""
        message = None
        report_dict = None

        if federation_observability_service is None:
            return templates.TemplateResponse(
                request,
                "policy_federation_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": False,
                    "message": "Federation observability service not configured.",
                    "since_default": "",
                    "until_default": "",
                },
            )

        # Parse form data
        form = await request.form()
        since_str = form.get("since", "")
        until_str = form.get("until", "")
        since_dt = None
        until_dt = None
        since_default = str(since_str) if since_str else ""
        until_default = str(until_str) if until_str else ""

        if since_str:
            try:
                from datetime import datetime as _dt
                since_dt = _dt.fromisoformat(str(since_str))
            except (ValueError, TypeError):
                message = "Invalid 'since' datetime format."
        if until_str:
            try:
                from datetime import datetime as _dt
                until_dt = _dt.fromisoformat(str(until_str))
            except (ValueError, TypeError):
                if message:
                    message += " Invalid 'until' datetime format."
                else:
                    message = "Invalid 'until' datetime format."

        if message and (since_dt is None and until_dt is None):
            return templates.TemplateResponse(
                request,
                "policy_federation_analytics.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "report": None,
                    "store_available": federation_observability_service is not None,
                    "message": message,
                    "since_default": since_default,
                    "until_default": until_default,
                },
            )

        try:
            report_obj = await federation_observability_service.generate_report(
                window_start=since_dt,
                window_end=until_dt,
            )
            report_dict = {
                "report_id": report_obj.report_id,
                "generated_at": report_obj.generated_at.isoformat() if report_obj.generated_at and hasattr(report_obj.generated_at, "isoformat") else "",
                "total_federations": report_obj.total_federations,
                "active_federations": report_obj.active_federations,
                "completed_federations": report_obj.completed_federations,
                "failed_federations": report_obj.failed_federations,
                "cancelled_federations": report_obj.cancelled_federations,
                "blocked_federations": report_obj.blocked_federations,
                "target_health": {
                    "total_targets": report_obj.target_health.total_targets,
                    "enabled_targets": report_obj.target_health.enabled_targets,
                    "disabled_targets": report_obj.target_health.disabled_targets,
                    "succeeded_targets": report_obj.target_health.succeeded_targets,
                    "failed_targets": report_obj.target_health.failed_targets,
                    "blocked_targets": report_obj.target_health.blocked_targets,
                    "skipped_targets": report_obj.target_health.skipped_targets,
                },
                "wave_outcomes": {
                    "total_waves": report_obj.wave_outcomes.total_waves,
                    "succeeded_waves": report_obj.wave_outcomes.succeeded_waves,
                    "failed_waves": report_obj.wave_outcomes.failed_waves,
                    "blocked_waves": report_obj.wave_outcomes.blocked_waves,
                    "pending_waves": report_obj.wave_outcomes.pending_waves,
                },
                "conflicts": {
                    "total_conflicts": report_obj.conflicts.total_conflicts,
                    "error_conflicts": report_obj.conflicts.error_conflicts,
                    "warning_conflicts": report_obj.conflicts.warning_conflicts,
                },
                "top_failed_targets": report_obj.top_failed_targets,
                "top_blocked_targets": report_obj.top_blocked_targets,
                "environment_summary": report_obj.environment_summary,
                "region_summary": report_obj.region_summary,
                "tenant_summary": report_obj.tenant_summary,
            }
        except Exception as exc:
            message = f"Error generating report: {exc}"

        return templates.TemplateResponse(
            request,
            "policy_federation_analytics.html",
            {
                "title": title,
                "base_path": base_path,
                "report": report_dict,
                "store_available": federation_observability_service is not None,
                "message": message,
                "since_default": since_default,
                "until_default": until_default,
            },
        )

    # -----------------------------------------------------------------------
    # Phase 48 Task 7: Federation approval pages
    # -----------------------------------------------------------------------

    if federation_approval_store is not None:

        def _federation_approval_to_row(req: Any) -> dict:
            """Convert FederationApprovalRequest to a table row dict."""
            created = req.created_at
            if hasattr(created, "isoformat"):
                created = created.isoformat()
            return {
                "approval_id": req.approval_id,
                "federation_id": req.federation_id,
                "action": req.action,
                "status": req.status.value if hasattr(req.status, "value") else str(req.status),
                "requested_by": req.requested_by,
                "required_approvers": req.required_approvers,
                "created_at": created,
            }

        def _federation_approval_to_detail(req: Any) -> dict:
            """Convert FederationApprovalRequest to a detail dict."""
            created = req.created_at
            if hasattr(created, "isoformat"):
                created = created.isoformat()
            resolved = req.resolved_at
            if hasattr(resolved, "isoformat"):
                resolved = resolved.isoformat()
            expires = req.expires_at
            if hasattr(expires, "isoformat"):
                expires = expires.isoformat()
            return {
                "approval_id": req.approval_id,
                "federation_id": req.federation_id,
                "rollout_id": req.rollout_id or "",
                "target_id": req.target_id or "",
                "wave_id": req.wave_id or "",
                "tenant_id": req.tenant_id or "",
                "environment": req.environment or "",
                "region": req.region or "",
                "ring": req.ring or "",
                "action": req.action,
                "requested_by": req.requested_by,
                "required_approvers": req.required_approvers,
                "delegated_approvers": req.delegated_approvers,
                "approvers_who_approved": req.approvers_who_approved,
                "approvers_who_rejected": req.approvers_who_rejected,
                "status": req.status.value if hasattr(req.status, "value") else str(req.status),
                "reason": req.reason or "",
                "rejection_reason": req.rejection_reason or "",
                "escalation_level": req.escalation_level,
                "escalation_reason": req.escalation_reason or "",
                "created_at": created,
                "resolved_at": resolved or "",
                "resolved_by": req.resolved_by or "",
                "expires_at": expires or "",
                "metadata": req.metadata,
            }

        @router.get("/federation/approvals", response_class=HTMLResponse)
        async def federation_approvals_page(request: Request):
            """Federation approval requests list."""
            status_filter = request.query_params.get("status", "")
            federation_id_filter = request.query_params.get("federation_id", "")
            tenant_id_filter = request.query_params.get("tenant_id", "")
            action_filter = request.query_params.get("action", "")

            from agent_app.governance.policy_rollout_federation_approval import FederationApprovalStatus
            status_enum = None
            if status_filter:
                try:
                    status_enum = FederationApprovalStatus(status_filter)
                except ValueError:
                    status_enum = None

            approvals_list: list[dict] = []
            if federation_approval_store is not None:
                approvals = await federation_approval_store.list(
                    federation_id=federation_id_filter or None,
                    status=status_enum,
                    tenant_id=tenant_id_filter or None,
                    action=action_filter or None,
                )
                for a in approvals:
                    approvals_list.append(_federation_approval_to_row(a))

            return templates.TemplateResponse(
                request,
                "policy_federation_approval_list.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "approvals": approvals_list,
                    "filters": {
                        "status": status_filter,
                        "federation_id": federation_id_filter,
                        "tenant_id": tenant_id_filter,
                        "action": action_filter,
                    },
                    "store_available": True,
                },
            )

        @router.get("/federation/approvals/{approval_id}", response_class=HTMLResponse)
        async def federation_approval_detail_page(request: Request, approval_id: str):
            """Single federation approval request detail."""
            approval = await federation_approval_store.get(approval_id)
            if approval is None:
                return templates.TemplateResponse(
                    request,
                    "policy_federation_approval_detail.html",
                    {
                        "title": title,
                        "base_path": base_path,
                        "approval": None,
                        "error": f"Approval request '{approval_id}' not found.",
                        "message": None,
                    },
                )
            return templates.TemplateResponse(
                request,
                "policy_federation_approval_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "approval": _federation_approval_to_detail(approval),
                    "error": None,
                    "message": None,
                },
            )

        @router.get("/federation/plans/{federation_id}/approvals", response_class=HTMLResponse)
        async def federation_plan_approvals_page(request: Request, federation_id: str):
            """Approvals for a specific federation plan."""
            approvals_list: list[dict] = []
            if federation_approval_store is not None:
                approvals = await federation_approval_store.list(federation_id=federation_id)
                for a in approvals:
                    approvals_list.append(_federation_approval_to_row(a))
            return templates.TemplateResponse(
                request,
                "policy_federation_plan_approvals.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "federation_id": federation_id,
                    "approvals": approvals_list,
                    "store_available": True,
                },
            )

        @router.post("/federation/approvals/{approval_id}/approve", response_class=HTMLResponse)
        async def federation_approval_approve_action(request: Request, approval_id: str):
            """Approve a federation approval request."""
            message = None
            if federation_approval_service is None:
                return HTMLResponse("Approval service not configured", status_code=400)
            try:
                form = await request.form()
                actor_id = str(form.get("actor_id", ""))
                reason = form.get("reason")
                reason_str = str(reason) if reason is not None else None
                await federation_approval_service.approve(approval_id, actor_id, reason_str)
                message = f"Approval request '{approval_id}' approved."
            except (ValueError, PermissionError) as e:
                return HTMLResponse(f"Error: {e}", status_code=400)
            approval = await federation_approval_store.get(approval_id)
            return templates.TemplateResponse(
                request,
                "policy_federation_approval_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "approval": _federation_approval_to_detail(approval) if approval else None,
                    "error": None,
                    "message": message,
                },
            )

        @router.post("/federation/approvals/{approval_id}/reject", response_class=HTMLResponse)
        async def federation_approval_reject_action(request: Request, approval_id: str):
            """Reject a federation approval request."""
            message = None
            if federation_approval_service is None:
                return HTMLResponse("Approval service not configured", status_code=400)
            try:
                form = await request.form()
                actor_id = str(form.get("actor_id", ""))
                reason = form.get("reason")
                reason_str = str(reason) if reason is not None else None
                await federation_approval_service.reject(approval_id, actor_id, reason_str)
                message = f"Approval request '{approval_id}' rejected."
            except (ValueError, PermissionError) as e:
                return HTMLResponse(f"Error: {e}", status_code=400)
            approval = await federation_approval_store.get(approval_id)
            return templates.TemplateResponse(
                request,
                "policy_federation_approval_detail.html",
                {
                    "title": title,
                    "base_path": base_path,
                    "approval": _federation_approval_to_detail(approval) if approval else None,
                    "error": None,
                    "message": message,
                },
            )

    # -----------------------------------------------------------------------
    # Phase 49 Task 10: Federation notification and escalation pages
    # -----------------------------------------------------------------------

    @router.get("/federation/notifications")
    async def federation_notification_list(request: Request):
        """Federation notification list page."""
        if federation_notification_store is None:
            return HTMLResponse("<h2>Federation notification store not configured</h2>")
        messages = await federation_notification_store.list_pending(limit=200)
        return templates.TemplateResponse(
            request,
            "policy_federation_notification_list.html",
            {
                "title": title,
                "base_path": base_path,
                "notifications": messages,
            },
        )

    @router.get("/federation/notifications/{notification_id}")
    async def federation_notification_detail(notification_id: str, request: Request):
        """Single federation notification detail page."""
        if federation_notification_store is None:
            return HTMLResponse("<h2>Federation notification store not configured</h2>")
        notification = await federation_notification_store.get(notification_id)
        if notification is None:
            return HTMLResponse(f"<h2>Notification '{notification_id}' not found</h2>", status_code=404)
        return templates.TemplateResponse(
            request,
            "policy_federation_notification_detail.html",
            {
                "title": title,
                "base_path": base_path,
                "notification": notification,
            },
        )

    @router.get("/federation/approvals/{approval_id}/notifications")
    async def federation_approval_notifications(approval_id: str, request: Request):
        """Notifications for a specific federation approval."""
        if federation_notification_store is None:
            return HTMLResponse("<h2>Federation notification store not configured</h2>")
        notifications = await federation_notification_store.list_by_approval(approval_id=approval_id)
        return templates.TemplateResponse(
            request,
            "policy_federation_notification_list.html",
            {
                "title": title,
                "base_path": base_path,
                "notifications": notifications,
                "approval_id": approval_id,
            },
        )

    @router.get("/federation/escalations")
    async def federation_escalation_dashboard(request: Request):
        """Federation escalation dashboard page."""
        return templates.TemplateResponse(
            request,
            "policy_federation_escalation.html",
            {
                "title": title,
                "base_path": base_path,
                "worker": federation_escalation_worker,
            },
        )

    return router

def _get_templates_dir() -> str:
    """Return the templates directory path."""
    import os
    return os.path.join(os.path.dirname(__file__), "templates")


def _promotion_to_row(req: Any) -> dict:
    """Convert PromotionRequest to a table row dict."""
    created = req.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    resolved = req.resolved_at
    if hasattr(resolved, "isoformat"):
        resolved = resolved.isoformat()
    return {
        "promotion_id": req.promotion_id,
        "bundle_id": req.bundle_id,
        "status": req.status,
        "requested_by": req.requested_by,
        "resolved_by": req.resolved_by or "—",
        "created_at": created,
        "resolved_at": resolved or "—",
        "reason": req.reason or "—",
    }


def _promotion_to_detail(req: Any) -> dict:
    """Convert PromotionRequest to a detail page dict."""
    created = req.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    resolved = req.resolved_at
    if hasattr(resolved, "isoformat"):
        resolved = resolved.isoformat()
    executed = req.executed_at
    if hasattr(executed, "isoformat"):
        executed = executed.isoformat()
    return {
        "promotion_id": req.promotion_id,
        "bundle_id": req.bundle_id,
        "gate_result_id": req.gate_result_id or "—",
        "status": req.status,
        "requested_by": req.requested_by,
        "tenant_id": req.tenant_id or "—",
        "reason": req.reason or "—",
        "approval_reason": req.approval_reason or "—",
        "rejection_reason": req.rejection_reason or "—",
        "resolved_by": req.resolved_by or "—",
        "resolved_at": resolved,
        "executed_by": req.executed_by or "—",
        "executed_at": executed,
        "created_at": created,
    }


# Phase 31: policy activation helpers
def _activation_to_row(act: Any) -> dict:
    """Convert PolicyActivation to a table row dict."""
    created = act.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    superseded = act.superseded_at
    if hasattr(superseded, "isoformat"):
        superseded = superseded.isoformat() if superseded else "—"
    return {
        "activation_id": act.activation_id,
        "environment": act.environment,
        "bundle_id": act.bundle_id,
        "status": act.status,
        "activated_by": act.activated_by,
        "promotion_id": act.promotion_id or "—",
        "created_at": created,
        "superseded_at": superseded,
    }


def _activation_to_detail(act: Any) -> dict:
    """Convert PolicyActivation to a detail page dict."""
    created = act.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    superseded = act.superseded_at
    if hasattr(superseded, "isoformat"):
        superseded = superseded.isoformat() if superseded else "—"
    return {
        "activation_id": act.activation_id,
        "environment": act.environment,
        "bundle_id": act.bundle_id,
        "config_hash": act.config_hash,
        "promotion_id": act.promotion_id or "—",
        "status": act.status,
        "activated_by": act.activated_by,
        "reason": act.reason or "—",
        "created_at": created,
        "superseded_at": superseded,
        "superseded_by_activation_id": act.superseded_by_activation_id or "—",
    }


def _bundle_to_row(bundle: Any) -> dict:
    """Convert a PolicyBundle to a table row dict."""
    created = bundle.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    activated = bundle.activated_at
    if hasattr(activated, "isoformat"):
        activated = activated.isoformat()
    return {
        "bundle_id": bundle.bundle_id,
        "name": bundle.name,
        "version": bundle.version,
        "status": bundle.status,
        "config_hash": bundle.config_hash[:12] + "..." if bundle.config_hash else "—",
        "created_at": created,
        "activated_at": activated or "—",
        "created_by": bundle.created_by or "—",
    }


def _bundle_to_detail(bundle: Any) -> dict:
    """Convert a PolicyBundle to a detail page dict."""
    created = bundle.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    activated = bundle.activated_at
    if hasattr(activated, "isoformat"):
        activated = activated.isoformat()
    archived = bundle.archived_at
    if hasattr(archived, "isoformat"):
        archived = archived.isoformat()
    return {
        "bundle_id": bundle.bundle_id,
        "name": bundle.name,
        "version": bundle.version,
        "status": bundle.status,
        "config_path": bundle.config_path or "—",
        "config_hash": bundle.config_hash,
        "description": bundle.description or "—",
        "created_by": bundle.created_by or "—",
        "created_at": created,
        "activated_at": activated,
        "archived_at": archived,
        "metadata": bundle.metadata or {},
    }


def _gate_to_row(gate: Any) -> dict:
    """Convert a PolicyGateResult to a table row dict."""
    created = gate.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    unchanged = gate.total_decisions - gate.changed_decisions - gate.failed_replays
    return {
        "gate_result_id": gate.gate_result_id,
        "bundle_id": gate.bundle_id,
        "status": gate.status,
        "passed": gate.passed,
        "changed_count": gate.changed_decisions,
        "unchanged_count": max(0, unchanged),
        "failed_count": gate.failed_replays,
        "rule_results": gate.rule_results or [],
        "created_at": created,
        "created_by": gate.created_by or "—",
    }


def _gate_to_detail(gate: Any) -> dict:
    """Convert a PolicyGateResult to a detail page dict."""
    created = gate.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    rule_results = []
    for r in (gate.rule_results or []):
        rule_results.append({
            "rule_name": r.get("rule_name", "—"),
            "passed": r.get("passed", False),
            "status": r.get("status", "unknown"),
            "actual": r.get("actual"),
            "threshold": r.get("threshold"),
            "message": r.get("message", ""),
        })
    unchanged = gate.total_decisions - gate.changed_decisions - gate.failed_replays
    return {
        "gate_result_id": gate.gate_result_id,
        "bundle_id": gate.bundle_id,
        "status": gate.status,
        "passed": gate.passed,
        "changed_count": gate.changed_decisions,
        "unchanged_count": max(0, unchanged),
        "failed_count": gate.failed_replays,
        "total_decisions": gate.total_decisions,
        "changed_ratio": gate.changed_ratio,
        "rule_results": rule_results,
        "error": gate.summary.get("error") if gate.summary else None,
        "created_at": created,
        "created_by": gate.created_by or "—",
    }


def _trace_to_card(trace: Any) -> dict:
    """Convert a PolicyDecisionTrace to a dashboard card dict."""
    created = trace.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    return {
        "decision_id": trace.decision_id,
        "action": trace.action.value if hasattr(trace.action, "value") else str(trace.action),
        "rule_name": trace.rule_name,
        "tool_name": trace.tool_name,
        "reason": trace.reason,
        "created_at": created,
    }


def _trace_to_row(trace: Any) -> dict:
    """Convert a PolicyDecisionTrace to a table row dict."""
    ctx = trace.context_summary or {}
    created = trace.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    return {
        "decision_id": trace.decision_id,
        "created_at": created,
        "action": trace.action.value if hasattr(trace.action, "value") else str(trace.action),
        "rule_name": trace.rule_name or "—",
        "tool_name": trace.tool_name or "—",
        "agent_name": ctx.get("agent_name", "—"),
        "workflow_type": trace.workflow_type or "—",
        "user_id": trace.user_id or "—",
        "tenant_id": trace.tenant_id or "—",
        "run_id": trace.run_id or "—",
    }


def _trace_to_detail(trace: Any) -> dict:
    """Convert a PolicyDecisionTrace to a detail page dict."""
    created = trace.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    ctx = trace.context_summary or {}
    return {
        "decision_id": trace.decision_id,
        "run_id": trace.run_id,
        "created_at": created,
        "action": trace.action.value if hasattr(trace.action, "value") else str(trace.action),
        "reason": trace.reason,
        "rule_name": trace.rule_name,
        "tool_name": trace.tool_name,
        "workflow_type": ctx.get("workflow_type"),
        "target_agent": ctx.get("target_agent"),
        "user_id": ctx.get("user_id"),
        "tenant_id": ctx.get("tenant_id"),
        "matched_conditions": trace.matched_conditions,
        "context_summary": trace.context_summary,
    }


def _report_to_dict(report: Any) -> dict:
    """Convert PolicyReport to a plain dict for template rendering."""
    if report is None:
        return {}
    time_range = report.time_range or {}
    start = time_range.get("start")
    end = time_range.get("end")
    if hasattr(start, "isoformat"):
        start = start.isoformat()
    if hasattr(end, "isoformat"):
        end = end.isoformat()
    return {
        "total_decisions": report.total_decisions,
        "action_breakdown": report.action_breakdown,
        "rule_breakdown": report.rule_breakdown,
        "tool_breakdown": report.tool_breakdown,
        "time_range": {
            "start": start or "",
            "end": end or "",
        },
    }


def _paginate(offset: int, limit: int, total: int) -> dict:
    """Build pagination metadata."""
    pages = max(1, (total + limit - 1) // limit) if total > 0 else 1
    current = min(offset // limit + 1, pages) if total > 0 else 1
    return {
        "offset": offset,
        "limit": limit,
        "total": total,
        "pages": pages,
        "current": current,
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
        "prev_offset": max(0, offset - limit),
        "next_offset": offset + limit,
    }


# Phase 35: rollout helpers
def _rollout_to_row(plan: Any) -> dict:
    """Convert RolloutPlan to a table row dict."""
    return {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "bundle_id": plan.bundle_id,
        "status": plan.status,
        "step_count": len(plan.steps),
        "created_by": plan.created_by,
        "created_at": plan.created_at.isoformat()[:19] if plan.created_at else "",
    }


def _rollout_to_detail(plan: Any) -> dict:
    """Convert RolloutPlan to a detail page dict."""
    row = _rollout_to_row(plan)
    row["steps"] = [_step_to_row(s) for s in plan.steps]
    row["reason"] = plan.reason
    return row


def _step_to_row(step: Any) -> dict:
    """Convert RolloutStep to a table row dict."""
    return {
        "step_id": step.step_id,
        "step_type": step.step_type,
        "environment": step.environment,
        "ring_name": step.ring_name or "",
        "status": step.status,
        "error": step.error,
        "activation_id": step.activation_id or "",
        "requires_approval": getattr(step, "requires_approval", False),
        "approval_id": getattr(step, "approval_id", None) or "",
    }


def _parse_rollout_steps(steps_yaml: str) -> list:
    """Parse rollout steps from YAML text.

    Accepts a simple YAML format like:
        - step_id: s1
          step_type: activate
          environment: prod
        - step_id: s2
          step_type: assign_ring
          environment: prod
          ring_name: canary

    Falls back to creating a single activate step if parsing fails.
    """
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType

    if not steps_yaml or not steps_yaml.strip():
        # Default: single activate step
        return [RolloutStep(
            step_id="s1",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
        )]

    try:
        import yaml
        parsed = yaml.safe_load(steps_yaml)
        if not isinstance(parsed, list):
            raise ValueError("Steps YAML must be a list")
        steps = []
        for item in parsed:
            step_type_str = item.get("step_type", "activate")
            try:
                step_type = RolloutStepType(step_type_str)
            except ValueError:
                step_type = RolloutStepType.ACTIVATE
            steps.append(RolloutStep(
                step_id=item.get("step_id", f"s{len(steps) + 1}"),
                step_type=step_type,
                environment=item.get("environment", "prod"),
                ring_name=item.get("ring_name"),
                from_ring=item.get("from_ring"),
                to_ring=item.get("to_ring"),
                required_gate_status=item.get("required_gate_status"),
                eval_suite=item.get("eval_suite"),
                requires_approval=item.get("requires_approval", False),
                require_previous_step=item.get("require_previous_step"),
            ))
        return steps
    except ImportError:
        # If PyYAML not available, create default step
        return [RolloutStep(
            step_id="s1",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
        )]
    except Exception:
        # If YAML parsing fails, re-raise with clear message
        raise


# Phase 36: rollout approval helpers
def _approval_to_row(approval: Any) -> dict:
    """Convert RolloutStepApproval to a dict for template rendering."""
    # Compute approval progress
    decisions = getattr(approval, "decisions", [])
    current_approvals = sum(
        1 for d in decisions
        if hasattr(d, "decision_type") and d.decision_type.value == "approve"
    )
    policy = getattr(approval, "policy", None)
    required_approvals = policy.required_approvals if policy else 1
    return {
        "approval_id": approval.approval_id,
        "rollout_id": approval.rollout_id,
        "step_id": approval.step_id,
        "bundle_id": approval.bundle_id,
        "environment": approval.environment,
        "ring_name": approval.ring_name or "",
        "status": approval.status.value,
        "requested_by": approval.requested_by,
        "resolved_by": approval.resolved_by or "",
        "created_at": approval.created_at.isoformat() if approval.created_at else "",
        "current_approvals": current_approvals,
        "required_approvals": required_approvals,
    }


def _approval_to_detail(approval: Any) -> dict:
    """Convert RolloutStepApproval to a detail page dict."""
    created = approval.created_at
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    resolved = approval.resolved_at
    if resolved and hasattr(resolved, "isoformat"):
        resolved = resolved.isoformat()
    expires_at = getattr(approval, "expires_at", None)
    if expires_at and hasattr(expires_at, "isoformat"):
        expires_at = expires_at.isoformat()

    # Policy information
    policy = getattr(approval, "policy", None)
    policy_dict = None
    if policy is not None:
        policy_dict = {
            "policy_type": policy.policy_type.value if hasattr(policy.policy_type, "value") else str(policy.policy_type),
            "required_approvals": policy.required_approvals,
            "allowed_approver_permissions": list(policy.allowed_approver_permissions) if hasattr(policy, "allowed_approver_permissions") else [],
            "allowed_approver_roles": list(policy.allowed_approver_roles) if hasattr(policy, "allowed_approver_roles") else [],
            "prohibit_requester_approval": policy.prohibit_requester_approval,
            "expires_after_seconds": policy.expires_after_seconds,
        }

    # Decisions
    decisions_list = []
    raw_decisions = getattr(approval, "decisions", [])
    for d in raw_decisions:
        d_created = d.created_at
        if hasattr(d_created, "isoformat"):
            d_created = d_created.isoformat()
        decisions_list.append({
            "decision_id": d.decision_id,
            "decision_type": d.decision_type.value if hasattr(d.decision_type, "value") else str(d.decision_type),
            "decided_by": d.decided_by,
            "reason": d.reason or "",
            "roles": list(d.roles) if hasattr(d, "roles") else [],
            "permissions": list(d.permissions) if hasattr(d, "permissions") else [],
            "created_at": d_created,
        })

    # Approval progress
    current_approvals = sum(1 for d in decisions_list if d["decision_type"] == "approve")
    required_approvals = policy.required_approvals if policy else 1
    remaining_approvals = max(0, required_approvals - current_approvals)

    return {
        "approval_id": approval.approval_id,
        "rollout_id": approval.rollout_id,
        "step_id": approval.step_id,
        "bundle_id": approval.bundle_id,
        "environment": approval.environment,
        "ring_name": approval.ring_name or "",
        "requested_by": approval.requested_by,
        "requested_reason": approval.requested_reason or "",
        "status": approval.status.value,
        "resolved_by": approval.resolved_by or "",
        "resolved_reason": approval.resolved_reason or "",
        "created_at": created,
        "resolved_at": resolved or "",
        "policy": policy_dict,
        "decisions": decisions_list,
        "required_approvals": required_approvals,
        "current_approvals": current_approvals,
        "remaining_approvals": remaining_approvals,
        "expires_at": expires_at or "",
    }
