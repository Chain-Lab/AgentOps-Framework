"""Policy Console Lite — read-only HTML UI for policy decision data.

Phase 26: Mounted conditionally when ``policy_console.enabled`` is set in
the governance config.  Reuses Phase 25 store / reporting service — no
duplicate query logic.

Phase 29: Added read-only pages for policy bundles and gate results.
"""

from __future__ import annotations

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
from agent_app.runtime.policy_replay_store import PolicyReplayStore


def build_policy_console_router(
    store: PolicyDecisionStore | None,
    config: Any = None,
    replay_store: PolicyReplayStore | None = None,
    replay_job_store: Any = None,
    bundle_store: Any = None,
    gate_store: Any = None,
    promotion_store: Any = None,
    release_service: Any = None,
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

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
