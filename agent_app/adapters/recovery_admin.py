"""Optional FastAPI admin router for recovery observability.

This module is an optional dependency.  Install with:

    pip install 'agent-app-framework[api]'

The router is created lazily — importing this module does NOT require FastAPI.
Call ``create_recovery_admin_router(app)`` only when FastAPI is installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_app.core.app import AgentApp


def create_recovery_admin_router(app: Any) -> Any:
    """Create an optional FastAPI router for recovery admin endpoints.

    Phase 18: Provides read-only and admin endpoints for the recovery
    subsystem.  This function lazy-imports FastAPI, so it only fails
    at call time (not import time) when FastAPI is not installed.

    Endpoints:
        GET  /admin/recovery/status
        GET  /admin/recovery/runs/{run_id}/inspect
        GET  /admin/recovery/runs/{run_id}/history
        POST /admin/recovery/scan
        POST /admin/recovery/runs/{run_id}/recover

    Args:
        app: A configured :class:`AgentApp` instance.

    Returns:
        A FastAPI ``APIRouter`` with recovery admin endpoints.

    Raises:
        ImportError: If FastAPI is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException
    except ImportError as e:
        raise ImportError(
            "FastAPI dependencies are not installed. "
            "Install with: pip install 'agent-app-framework[api]'"
        ) from e

    router = APIRouter(prefix="/admin/recovery", tags=["recovery"])

    @router.get("/status")
    async def get_status() -> dict:
        """Get recovery subsystem status."""
        try:
            status = app.get_recovery_system_status()
            return {
                "enabled": status.enabled,
                "dry_run": status.dry_run,
                "daemon_configured": status.daemon_configured,
                "scanner_available": status.scanner_available,
                "recovery_service_available": status.recovery_service_available,
                "last_tick_at": status.last_tick_at.isoformat() if status.last_tick_at else None,
                "policy": status.policy.model_dump(mode="json") if status.policy else None,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/inspect")
    async def inspect_run(run_id: str) -> dict:
        """Inspect a single run as a recovery candidate."""
        try:
            candidate = await app.inspect_recovery_candidate(run_id)
            return candidate.model_dump(mode="json")
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/history")
    async def get_history(run_id: str, limit: int = 50) -> dict:
        """Get recovery audit history for a run."""
        try:
            events = await app.get_recovery_history(run_id, limit=limit)
            return {
                "run_id": run_id,
                "total": len(events),
                "events": [
                    {
                        "event_id": e.event_id,
                        "event_type": e.event_type,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                        "user_id": e.user_id,
                        "tenant_id": e.tenant_id,
                        "data": e.data,
                    }
                    for e in events
                ],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/scan")
    async def run_scan(dry_run: bool = True) -> dict:
        """Run a single recovery scan cycle (dry-run by default)."""
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy

        policy = AutoRecoveryPolicy(dry_run=dry_run)
        try:
            result = await app.run_recovery_scan_once(policy=policy)
            return {
                "scanned_count": result.scanned_count,
                "selected_count": result.selected_count,
                "recovered_count": result.recovered_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "dry_run": result.dry_run,
                "selected_run_ids": result.selected_run_ids,
                "recovered_run_ids": result.recovered_run_ids,
                "skipped": result.skipped,
                "failures": result.failures,
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/recover")
    async def recover_run(run_id: str, dry_run: bool = True) -> dict:
        """Trigger recovery for a specific run (dry-run by default)."""
        try:
            result = await app.recover_run(run_id=run_id, dry_run=dry_run)
            return {
                "run_id": result.run_id,
                "attempted": result.attempted,
                "recovered": result.recovered,
                "status": result.status,
                "dry_run": dry_run,
                "error": result.error,
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
