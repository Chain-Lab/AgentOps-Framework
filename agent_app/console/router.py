"""Policy Console Lite — read-only HTML UI for policy decision data.

Phase 26: Mounted conditionally when ``policy_console.enabled`` is set in
the governance config.  Reuses Phase 25 store / reporting service — no
duplicate query logic.
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


def build_policy_console_router(
    store: PolicyDecisionStore | None,
    config: Any = None,
) -> APIRouter:
    """Build the policy console FastAPI router.

    Args:
        store: The policy decision store (may be None).
        config: PolicyConsoleConfig with title, base_path, page_size.

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
    page_size = 50
    if config is not None:
        title = getattr(config, "title", title)
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

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_templates_dir() -> str:
    """Return the templates directory path."""
    import os
    return os.path.join(os.path.dirname(__file__), "templates")


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
