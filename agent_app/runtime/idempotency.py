"""Idempotency enforcement — request fingerprinting, scope, and atomic key reservation.

Phase 15.1: Upgrades IdempotencyRecord from passive storage to API-level
duplicate prevention.  When a caller supplies an ``idempotency_key``, the
framework atomically reserves it before creating or resuming a workflow run,
rejecting duplicate and mismatched requests.

Design goals:
  * Best-effort API-level duplicate prevention (NOT exactly-once execution).
  * Scope isolation per tenant + operation to avoid cross-tenant collisions.
  * Stable error types for HTTP 409 mapping in FastAPI.
  * Atomic SQLite enforcement via UNIQUE constraint + transaction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

# Fields that do NOT affect execution semantics and must be excluded.
_TRANSIENT_FIELDS: set[str] = {
    "idempotency_key",
    "worker",
    "worker_id",
    "trace_id",
    "request_id",
    "correlation_id",
}


def compute_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """Compute a stable SHA-256 fingerprint for a request payload.

    Uses deterministic JSON serialisation (sorted keys, no whitespace) so
    semantically identical payloads always produce the same fingerprint
    regardless of dict insertion order.

    Transient fields (idempotency_key, worker, trace_id, …) are excluded
    so that the fingerprint reflects only the operation's semantic content.

    Args:
        payload: Arbitrary mapping of request parameters.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    # Build a filtered copy with only semantic fields
    clean = _filter_transient(payload)
    canonical = json.dumps(
        clean,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _filter_transient(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with transient keys removed.

    Recursively filters nested dicts but does not deduplicate lists or
    alter non-dict values — list ordering is part of the payload's
    semantic content and must be preserved.
    """
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _TRANSIENT_FIELDS:
            continue
        if isinstance(value, Mapping):
            result[key] = _filter_transient(value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

#: Canonical operation identifiers for idempotency scope.
class IdempotencyOperation:
    WORKFLOW_EXECUTE = "workflow.execute"
    WORKFLOW_RESUME = "workflow.resume"
    DAG_EXECUTE = "dag.execute"
    DAG_RESUME = "dag.resume"


def compute_scope(tenant_id: str, operation: str) -> str:
    """Build a scoped namespace for idempotency key lookup.

    Scope prevents different tenants or operation types from sharing
    the same idempotency key namespace.

    Args:
        tenant_id: Tenant identifier.
        operation: One of the ``IdempotencyOperation`` constants.

    Returns:
        Scope string in the form ``"{tenant_id}:{operation}"``.
    """
    return f"{tenant_id}:{operation}"


# ---------------------------------------------------------------------------
# Minimal payload builder
# ---------------------------------------------------------------------------

def build_execute_payload(
    *,
    workflow_name: str | None = None,
    agent_name: str | None = None,
    input: str = "",
    session_id: str | None = None,
    tenant_id: str = "default",
    user_id: str = "anonymous",
    run_id: str | None = None,
    permissions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal, stable payload for execute idempotency fingerprinting.

    Only fields that affect execution semantics are included.  Extra fields
    are intentionally omitted so that minor API version changes do not break
    existing idempotency keys.

    Args:
        workflow_name: Workflow identifier (if applicable).
        agent_name: Agent identifier (if applicable).
        input: User input text.
        session_id: Conversation/session identifier.
        tenant_id: Tenant identifier.
        user_id: End-user identifier.
        run_id: Run identifier (None for new runs).
        permissions: Granted permissions list.

    Returns:
        Minimal payload dict suitable for fingerprinting.
    """
    payload: dict[str, Any] = {
        "workflow_name": workflow_name,
        "agent_name": agent_name,
        "input": input,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
    if run_id is not None:
        payload["run_id"] = run_id
    if permissions:
        payload["permissions"] = sorted(permissions)
    return payload


def build_resume_payload(
    *,
    run_id: str,
    input: str = "",
    tenant_id: str = "default",
    user_id: str = "anonymous",
    approval_id: str | None = None,
    permissions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal payload for resume idempotency fingerprinting.

    Args:
        run_id: The workflow run being resumed.
        input: Original user input.
        tenant_id: Tenant identifier.
        user_id: End-user identifier.
        approval_id: Approval ID if resume depends on an approval.
        permissions: Granted permissions list.

    Returns:
        Minimal payload dict suitable for fingerprinting.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "input": input,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
    if approval_id is not None:
        payload["approval_id"] = approval_id
    if permissions:
        payload["permissions"] = sorted(permissions)
    return payload


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class IdempotencyError(Exception):
    """Base error for idempotency enforcement failures.

    Attributes:
        idempotency_key: The conflicting idempotency key.
        scope: The scope in which the conflict occurred.
        operation: The operation type.
        existing_run_id: The run_id already associated with this key.
        message: Human-readable explanation.
    """

    def __init__(
        self,
        *,
        idempotency_key: str,
        scope: str,
        operation: str,
        existing_run_id: str | None = None,
        message: str,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.scope = scope
        self.operation = operation
        self.existing_run_id = existing_run_id
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a stable error dict for API responses."""
        return {
            "type": type(self).__name__,
            "message": self.message,
            "idempotency_key": self.idempotency_key,
            "scope": self.scope,
            "operation": self.operation,
            "existing_run_id": self.existing_run_id,
        }


class DuplicateIdempotencyKeyError(IdempotencyError):
    """Raised when an idempotency key is reused with the same fingerprint.

    Indicates a true duplicate request — the same operation has already
    been executed with the same parameters.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("message", "Idempotency key has already been used for this operation.")
        super().__init__(**kwargs)


class IdempotencyKeyMismatchError(IdempotencyError):
    """Raised when an idempotency key is reused with different parameters.

    Indicates a potential replay attack or client error — the same key
    was used with a different request payload.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault(
            "message",
            "Idempotency key was reused with different request parameters.",
        )
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Atomic reservation
# ---------------------------------------------------------------------------

async def reserve_idempotency_key(
    store: Any,
    *,
    record: IdempotencyRecord | None = None,
    scope: str | None = None,
    key: str | None = None,
    operation: str | None = None,
    request_fingerprint: str | None = None,
    run_id: str | None = None,
    created_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Atomically reserve an idempotency key on the state store.

    This is the single enforcement point for API-level idempotency.
    It must be called before any side-effect-producing operation
    (creating or resuming a workflow run).

    Can be called either with a pre-built ``IdempotencyRecord`` (preferred)
    or with individual fields.

    Behavior:
      * Key does not exist → create record, return it.
      * Key exists + same fingerprint → raise ``DuplicateIdempotencyKeyError``.
      * Key exists + different fingerprint → raise ``IdempotencyKeyMismatchError``.

    SQLite enforcement:
      The store's ``reserve_idempotency_key`` implementation must use a
      UNIQUE constraint on (scope, key) within a transaction so that two
      concurrent callers cannot both observe "key absent" and then both
      insert.

    Args:
        store: A WorkflowStateStore implementation.
        record: Pre-built IdempotencyRecord (preferred).
        scope: Scoped namespace (tenant + operation). Required if record not provided.
        key: Caller-supplied idempotency key. Required if record not provided.
        operation: Operation identifier. Required if record not provided.
        request_fingerprint: SHA-256 fingerprint. Required if record not provided.
        run_id: The workflow run_id. Required if record not provided.
        created_by: Optional identity of the caller.
        metadata: Optional additional metadata.

    Returns:
        The created IdempotencyRecord.

    Raises:
        DuplicateIdempotencyKeyError: Key already used with same fingerprint.
        IdempotencyKeyMismatchError: Key already used with different fingerprint.
    """
    if record is not None:
        # Use the provided record as-is (scope/fingerprint already set)
        pass
    elif scope is not None and key is not None and operation is not None and request_fingerprint is not None and run_id is not None:
        from agent_app.runtime.dag_run_state import IdempotencyRecord as _IR
        record = _IR(
            key=key,
            run_id=run_id,
            operation=operation,
            result_ref=run_id,
            scope=scope,
            request_fingerprint=request_fingerprint,
            metadata={
                "created_by": created_by,
                **(metadata or {}),
            },
        )
    else:
        raise TypeError(
            "reserve_idempotency_key requires either 'record' or all of "
            "'scope', 'key', 'operation', 'request_fingerprint', 'run_id'."
        )
    return await store.reserve_idempotency_key(record)
