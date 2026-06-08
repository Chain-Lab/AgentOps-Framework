"""Lease backend health checks — lightweight, non-destructive diagnostics.

Phase 16.3: Provides health checking for ``WorkflowLeaseBackend``
implementations without creating or modifying real workflow leases.
Health checks are diagnostic only — they do NOT imply distributed
recovery or self-healing.

This is NOT a distributed health protocol, NOT a liveness probe, and
does NOT guarantee backend availability.  It is a best-effort
diagnostic helper for operator visibility.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------


class LeaseHealthStatus(StrEnum):
    """Overall lease backend health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


# ---------------------------------------------------------------------------
# Health result
# ---------------------------------------------------------------------------


class LeaseHealthCheckResult(BaseModel):
    """Result of a lease backend health check.

    Attributes:
        status: Overall health status.
        backend_type: Human-readable backend type identifier.
        details: Additional diagnostic information.
        checked_at: UTC timestamp when the check was performed.
        error: Error message if the check failed.
    """

    status: LeaseHealthStatus
    backend_type: str
    details: dict[str, Any] = {}
    checked_at: datetime
    error: str | None = None


# ---------------------------------------------------------------------------
# Health checker
# ---------------------------------------------------------------------------


class LeaseBackendHealthChecker:
    """Non-destructive health checker for ``WorkflowLeaseBackend`` instances.

    Performs lightweight checks without creating or modifying real
    workflow leases.  Each check is isolated and should not affect
    backend state.

    Usage::

        checker = LeaseBackendHealthChecker(backend)
        result = await checker.check()
        if result.status == LeaseHealthStatus.UNHEALTHY:
            logger.error("Lease backend unhealthy: %s", result.error)
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    async def check(self) -> LeaseHealthCheckResult:
        """Perform a health check on the lease backend.

        Returns a ``LeaseHealthCheckResult`` with status and details.
        Never raises — exceptions are captured in the result.
        """
        checked_at = datetime.now(timezone.utc)
        backend_type = self._detect_backend_type()

        try:
            details = await self._run_checks()
            status = self._evaluate_status(details, backend_type)
            error = None
            if status == LeaseHealthStatus.UNHEALTHY:
                error = details.get("error") or details.get("reason")
            return LeaseHealthCheckResult(
                status=status,
                backend_type=backend_type,
                details=details,
                checked_at=checked_at,
                error=error,
            )
        except Exception as exc:
            return LeaseHealthCheckResult(
                status=LeaseHealthStatus.UNHEALTHY,
                backend_type=backend_type,
                checked_at=checked_at,
                error=str(exc),
            )

    # -- Private helpers --

    def _detect_backend_type(self) -> str:
        """Detect the backend type from its class name."""
        cls_name = type(self._backend).__name__
        if "InMemory" in cls_name:
            return "memory"
        if "SQLite" in cls_name:
            return "sqlite"
        if "StateStore" in cls_name:
            return "state_store"
        if "Metrics" in cls_name:
            return "metrics"
        return cls_name.lower().replace("workflowleasebackend", "").strip("_") or "unknown"

    async def _run_checks(self) -> dict[str, Any]:
        """Run backend-specific health checks and return details."""
        backend_type = self._detect_backend_type()

        if backend_type == "memory":
            return await self._check_inmemory()
        if backend_type == "sqlite":
            return await self._check_sqlite()
        if backend_type == "state_store":
            return await self._check_state_store()
        if backend_type == "metrics":
            return await self._check_metrics()
        # Generic fallback
        return await self._check_generic()

    async def _check_inmemory(self) -> dict[str, Any]:
        """InMemory backend is always healthy (no external dependency)."""
        return {"check": "inmemory", "result": "ok"}

    async def _check_sqlite(self) -> dict[str, Any]:
        """Check SQLite backend by attempting a lightweight query."""
        db_path = getattr(self._backend, "_db_path", None)
        if db_path is None:
            return {"check": "sqlite", "result": "unknown", "reason": "no db_path"}

        try:
            conn = sqlite3.connect(db_path)
            try:
                version = conn.execute("SELECT 1").fetchone()[0]
                # Count leases (lightweight)
                count_row = conn.execute(
                    "SELECT COUNT(*) FROM workflow_run_leases WHERE released_at IS NULL"
                ).fetchone()
                active_leases = count_row[0] if count_row else 0
                return {
                    "check": "sqlite",
                    "result": "ok",
                    "db_path": db_path,
                    "active_leases": active_leases,
                }
            finally:
                conn.close()
        except Exception as exc:
            return {"check": "sqlite", "result": "error", "error": str(exc)}

    async def _check_state_store(self) -> dict[str, Any]:
        """Check state store backend by testing delegation."""
        store = getattr(self._backend, "_state_store", None)
        if store is None:
            return {"check": "state_store", "result": "degraded", "reason": "no state_store"}
        return {"check": "state_store", "result": "ok", "has_state_store": True}

    async def _check_metrics(self) -> dict[str, Any]:
        """Check metrics wrapper backend."""
        inner = getattr(self._backend, "_backend", None)
        if inner is None:
            return {"check": "metrics", "result": "degraded", "reason": "no inner backend"}
        return {"check": "metrics", "result": "ok", "has_inner_backend": True}

    async def _check_generic(self) -> dict[str, Any]:
        """Generic fallback: try a non-destructive operation."""
        try:
            # Try get_run_lease on a nonexistent run_id — should return None
            result = await self._backend.get_run_lease("__health_check__")
            if result is None:
                return {"check": "generic", "result": "ok"}
            return {"check": "generic", "result": "unexpected_lease"}
        except Exception as exc:
            return {"check": "generic", "result": "error", "error": str(exc)}

    def _evaluate_status(self, details: dict[str, Any], backend_type: str) -> LeaseHealthStatus:
        """Determine overall health from check details."""
        result = details.get("result", "unknown")
        if result == "ok":
            return LeaseHealthStatus.HEALTHY
        if result == "degraded":
            return LeaseHealthStatus.DEGRADED
        if result == "error":
            return LeaseHealthStatus.UNHEALTHY
        # Unknown — treat as degraded
        return LeaseHealthStatus.DEGRADED
