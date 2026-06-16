"""ToolExecutor — wraps tool execution with governance checks.

Execution pipeline:
  1. Resolve tool from registry
  2. Check permissions (permission_denied → FAILED)
  3. If requires_approval → create approval → INTERRUPTED
  4. Execute tool function
  5. Audit log

Phase 3: in-memory stores, no OpenAI SDK binding.
"""

from __future__ import annotations

import secrets
import uuid
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext
from agent_app.core.tool_spec import ToolSpec
from agent_app.governance.audit import AuditEvent, AuditLogger
from agent_app.governance.policy import PolicyAction, PolicyDecisionTrace, PolicyEngine, PolicyEvaluationContext
from agent_app.governance.policy_decision_store import PolicyDecisionStore
from agent_app.governance.permission import PermissionChecker
from agent_app.governance.risk import RiskLevel, requires_tool_approval
from agent_app.governance.sanitization import sanitize_payload
from agent_app.observability.collector import NoOpTraceCollector, TraceCollector
from agent_app.observability.events import RunEventType
from agent_app.registry.tool_registry import ToolRegistry


_NATIVE_HITL_APPROVAL_TOKEN = object()


def _make_native_hitl_approval_marker(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str | None = None,
) -> dict[str, Any]:
    """Create an internal marker for an SDK-approved native HITL tool call."""
    marker: dict[str, Any] = {
        "tool_name": tool_name,
        "arguments": dict(arguments),
        "source": "openai_native_hitl",
        "token": _NATIVE_HITL_APPROVAL_TOKEN,
    }
    if call_id is not None:
        marker["call_id"] = call_id
    return marker


class ToolExecutionStatus(Enum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ToolExecutionResult(BaseModel):
    """Result of a governed tool execution.

    Attributes:
        status: Execution outcome.
        tool_name: The tool that was called.
        output: Tool return value (when completed).
        approval_request: Pending approval (when interrupted).
        error: Error details (when failed).
    """

    status: str = Field(..., description="completed | interrupted | failed")
    tool_name: str = Field(..., description="Tool name")
    output: Any | None = Field(default=None, description="Tool result")
    approval_request: Any | None = Field(
        default=None, description="Pending approval"
    )
    error: dict | None = Field(default=None, description="Error details")


class ToolExecutor:
    """Executes tools with governance checks.

    Args:
        tool_registry: Registry of ToolSpec + callables.
        approval_store: Persistence for approval requests.
        permission_checker: Authorization checker.
        audit_logger: Audit event recorder.
        trace_collector: Optional observability trace collector.
        policy_engine: Optional policy engine for governance decisions.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        approval_store: Any,
        permission_checker: PermissionChecker,
        audit_logger: AuditLogger,
        trace_collector: TraceCollector | None = None,
        rate_limiter: Any = None,
        default_ttl_seconds: int | None = None,
        policy_engine: PolicyEngine | None = None,
        trace_events_callback: Callable[[Any], None] | None = None,
        policy_decision_store: PolicyDecisionStore | None = None,
        policy_enforcement_service: Any | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.approval_store = approval_store
        self.permission_checker = permission_checker
        self.audit_logger = audit_logger
        self.trace_collector = trace_collector or NoOpTraceCollector()
        self.rate_limiter = rate_limiter
        self.default_ttl_seconds = default_ttl_seconds
        self.policy_engine = policy_engine
        self._trace_events_callback = trace_events_callback
        self._policy_decision_store = policy_decision_store
        self._policy_enforcement_service = policy_enforcement_service

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: RunContext,
        *,
        approved_tool_call: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool with full governance pipeline.

        Args:
            tool_name: Fully-qualified tool name.
            arguments: Call arguments.
            context: Run context (for auth and audit).

        Returns:
            ToolExecutionResult.
        """
        # -- 1. Resolve tool --
        try:
            entry = self.tool_registry.get_entry(tool_name)
        except KeyError:
            error_detail = {
                "type": "tool_not_found",
                "message": f"Tool '{tool_name}' not registered.",
            }
            # -- Phase 12: tool.failed --
            await self._record_event(
                event_type=RunEventType.TOOL_FAILED,
                context=context,
                tool_name=tool_name,
                status="failed",
                error=error_detail,
            )
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED.value,
                tool_name=tool_name,
                error=error_detail,
            )

        spec = entry.spec
        fn = entry.fn

        # -- Phase 23: policy evaluation (before existing governance checks) --
        if self.policy_engine is not None:
            policy_ctx = PolicyEvaluationContext(
                run_id=context.run_id,
                agent_name=context.agent_name,
                tool_name=tool_name,
                risk_level=str(spec.risk_level),
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                roles=list(getattr(context, "roles", [])),
                permissions=list(getattr(context, "permissions", [])),
                metadata={
                    "requires_approval": spec.requires_approval,
                    "required_permissions": spec.permissions,
                },
            )
            policy_decision = await self.policy_engine.evaluate_tool_call(policy_ctx)

            # Log that policy was evaluated (all decisions)
            await self.audit_logger.log(AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=context.run_id,
                event_type="policy.evaluated",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                tool_name=tool_name,
                approval_id=None,
                data={
                    "action": policy_decision.action.value,
                    "rule_name": policy_decision.metadata.get("rule_name"),
                    "reason": policy_decision.reason,
                },
            ))
            # Record policy decision in trace_events for eval assertions
            await self._record_policy_event(
                context=context,
                tool_name=tool_name,
                action=policy_decision.action.value,
                rule_name=policy_decision.metadata.get("rule_name"),
                reason=policy_decision.reason,
            )
            # Phase 25: persist policy decision trace to store
            if self._policy_decision_store is not None:
                try:
                    trace = await self.policy_engine.explain(policy_ctx)
                    trace.decision_id = f"dec_{uuid.uuid4()}"
                    await self._policy_decision_store.record(trace)
                except Exception:
                    pass  # Never let policy store failures block execution

            if policy_decision.action == PolicyAction.DENY:
                error_detail = {
                    "type": "policy_denied",
                    "message": policy_decision.reason or "Denied by policy",
                    "tool_name": tool_name,
                    "rule_name": policy_decision.metadata.get("rule_name"),
                }
                await self.audit_logger.log(AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    event_type="policy.denied",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tool_name=tool_name,
                    data={"error": error_detail, "reason": policy_decision.reason},
                ))
                await self._record_event(
                    event_type=RunEventType.TOOL_FAILED,
                    context=context,
                    tool_name=tool_name,
                    status="failed",
                    error=error_detail,
                )
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILED.value,
                    tool_name=tool_name,
                    error=error_detail,
                )

            if policy_decision.action == PolicyAction.REQUIRE_APPROVAL:
                from agent_app.governance.approval import ApprovalRequest
                from datetime import datetime, timedelta, timezone
                sanitized_arguments = sanitize_payload(arguments)
                metadata = {
                    "argument_keys": sorted(arguments.keys()),
                    "requester_context": {
                        "user_id": context.user_id,
                        "tenant_id": context.tenant_id,
                        "trace_id": context.trace_id,
                    },
                    "rule_name": policy_decision.metadata.get("rule_name"),
                    "policy_reason": policy_decision.reason,
                }
                # Policy TTL overrides default_ttl_seconds
                ttl = policy_decision.ttl_seconds or self.default_ttl_seconds
                expires_at = None
                if ttl is not None and ttl > 0:
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
                approval = ApprovalRequest(
                    approval_id=f"apv_{secrets.token_hex(16)}",
                    run_id=context.run_id,
                    agent_name=None,
                    tool_name=tool_name,
                    arguments=sanitized_arguments,
                    risk_level=spec.risk_level,
                    tenant_id=context.tenant_id,
                    metadata=metadata,
                    expires_at=expires_at,
                )
                await self.approval_store.create(approval)
                await self.audit_logger.log(AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    event_type="policy.approval_required",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tool_name=tool_name,
                    approval_id=approval.approval_id,
                    data={
                        "arguments": sanitized_arguments,
                        "risk_level": spec.risk_level,
                        "reason": policy_decision.reason,
                        "rule_name": policy_decision.metadata.get("rule_name"),
                        "ttl_seconds": ttl,
                    },
                ))
                await self._record_event(
                    event_type=RunEventType.TOOL_APPROVAL_REQUIRED,
                    context=context,
                    tool_name=tool_name,
                    status="interrupted",
                    approval_id=approval.approval_id,
                    data={
                        "argument_keys": sorted(arguments.keys()),
                        "risk_level": str(spec.risk_level),
                        "rule_name": policy_decision.metadata.get("rule_name"),
                    },
                )
                await self._record_event(
                    event_type=RunEventType.APPROVAL_CREATED,
                    context=context,
                    tool_name=tool_name,
                    approval_id=approval.approval_id,
                    data={"risk_level": str(spec.risk_level)},
                )
                return ToolExecutionResult(
                    status=ToolExecutionStatus.INTERRUPTED.value,
                    tool_name=tool_name,
                    approval_request=approval,
                )

            if policy_decision.action == PolicyAction.AUDIT_ONLY:
                await self.audit_logger.log(AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    event_type="policy.audit_only",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tool_name=tool_name,
                    data={
                        "reason": policy_decision.reason,
                        "rule_name": policy_decision.metadata.get("rule_name"),
                    },
                ))
                # Continue to existing governance checks — do not block

            # ALLOW → continue to existing governance checks

        # -- Phase 12: tool.started --
        await self._record_event(
            event_type=RunEventType.TOOL_STARTED,
            context=context,
            tool_name=tool_name,
            data={
                "argument_keys": sorted(arguments.keys()),
                "risk_level": str(spec.risk_level),
                "requires_approval": spec.requires_approval,
                "required_permissions": spec.permissions,
            },
        )

        # -- 2. Permission check --
        if not await self.permission_checker.check(spec.permissions, context):
            error_detail = {
                "type": "permission_denied",
                "message": f"Missing permissions: {', '.join(spec.permissions)}",
                "tool_name": tool_name,
            }
            await self.audit_logger.log(AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=context.run_id,
                event_type="tool.permission_denied",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                tool_name=tool_name,
                data={"error": error_detail},
            ))
            # -- Phase 12: tool.permission_denied --
            await self._record_event(
                event_type=RunEventType.TOOL_PERMISSION_DENIED,
                context=context,
                tool_name=tool_name,
                status="failed",
                error=error_detail,
                data={"required_permissions": spec.permissions},
            )
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED.value,
                tool_name=tool_name,
                error=error_detail,
            )

        # -- Phase 38: Runtime policy enforcement --
        if self._policy_enforcement_service is not None:
            from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluationRequest
            from agent_app.governance.policy_enforcement import PolicyActionType, PolicyDecisionStatus

            enforce_request = RuntimePolicyEvaluationRequest(
                action_type=PolicyActionType.TOOL_EXECUTE,
                subject=f"tool:{tool_name}",
                tool_name=tool_name,
                risk_level=str(spec.risk_level),
                context=context,
            )
            enforce_decision = await self._policy_enforcement_service.enforce(enforce_request)

            if enforce_decision.status == PolicyDecisionStatus.DENIED:
                error_detail = {
                    "type": "policy_enforcement_denied",
                    "message": enforce_decision.reason or "Denied by runtime policy",
                    "tool_name": tool_name,
                    "decision_id": enforce_decision.decision_id,
                }
                await self._record_event(
                    event_type=RunEventType.TOOL_FAILED,
                    context=context,
                    tool_name=tool_name,
                    status="failed",
                    error=error_detail,
                )
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILED.value,
                    tool_name=tool_name,
                    error=error_detail,
                )

            if enforce_decision.status == PolicyDecisionStatus.APPROVAL_REQUIRED:
                # Check if ToolSpec already requires approval — avoid duplicate
                tool_spec_already_requires = requires_tool_approval(spec.risk_level, spec.requires_approval) and not _is_tool_call_approval_marker_valid(approved_tool_call, tool_name, arguments)

                if tool_spec_already_requires:
                    # ToolSpec already triggers approval — skip runtime policy approval
                    # but include policy_decision_id in metadata when the approval is created below
                    pass  # Fall through to existing approval gate
                else:
                    # Runtime policy requires approval — create approval
                    from agent_app.governance.approval import ApprovalRequest
                    from datetime import datetime, timedelta, timezone
                    sanitized_arguments = sanitize_payload(arguments)

                    # Use approval_policy from enforcement decision if available
                    approval_policy = enforce_decision.approval_policy
                    ttl_seconds = approval_policy.expires_after_seconds if approval_policy else None

                    expires_at = None
                    if ttl_seconds is not None and ttl_seconds > 0:
                        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

                    metadata = {
                        "argument_keys": sorted(arguments.keys()),
                        "requester_context": {
                            "user_id": context.user_id,
                            "tenant_id": context.tenant_id,
                            "trace_id": context.trace_id,
                        },
                        "policy_decision_id": enforce_decision.decision_id,
                        "enforcement_reason": enforce_decision.reason,
                    }

                    approval = ApprovalRequest(
                        approval_id=f"apv_{secrets.token_hex(16)}",
                        run_id=context.run_id,
                        agent_name=None,
                        tool_name=tool_name,
                        arguments=sanitized_arguments,
                        risk_level=spec.risk_level,
                        tenant_id=context.tenant_id,
                        metadata=metadata,
                        expires_at=expires_at,
                    )
                    await self.approval_store.create(approval)
                    await self.audit_logger.log(AuditEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=context.run_id,
                        event_type="policy.runtime.approval_required",
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        tool_name=tool_name,
                        approval_id=approval.approval_id,
                        data={
                            "decision_id": enforce_decision.decision_id,
                            "reason": enforce_decision.reason,
                            "risk_level": spec.risk_level,
                        },
                    ))
                    return ToolExecutionResult(
                        status=ToolExecutionStatus.INTERRUPTED.value,
                        tool_name=tool_name,
                        approval_request=approval,
                    )

            # ALLOWED → continue to existing approval gate

        # -- 3. Approval gate --
        if requires_tool_approval(
            spec.risk_level, spec.requires_approval
        ) and not _is_tool_call_approval_marker_valid(
            approved_tool_call,
            tool_name,
            arguments,
        ):
            # -- Phase 21: rate limit check --
            if self.rate_limiter is not None:
                allowed = await self.rate_limiter.check_allowed(
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    tool_name=tool_name,
                )
                if not allowed:
                    await self.audit_logger.log(AuditEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=context.run_id,
                        event_type="approval.rate_limited",
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        tool_name=tool_name,
                        data={"risk_level": spec.risk_level},
                    ))
                    return ToolExecutionResult(
                        status=ToolExecutionStatus.FAILED.value,
                        tool_name=tool_name,
                        error={
                            "type": "approval_rate_limited",
                            "message": "Approval request rate limit exceeded. Please try again later.",
                        },
                    )

            from agent_app.governance.approval import ApprovalRequest
            from datetime import datetime, timedelta, timezone
            sanitized_arguments = sanitize_payload(arguments)
            metadata = {
                "argument_keys": sorted(arguments.keys()),
                "requester_context": {
                    "user_id": context.user_id,
                    "tenant_id": context.tenant_id,
                    "trace_id": context.trace_id,
                },
            }
            expires_at = None
            if self.default_ttl_seconds is not None and self.default_ttl_seconds > 0:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.default_ttl_seconds)
            approval = ApprovalRequest(
                approval_id=f"apv_{secrets.token_hex(16)}",
                run_id=context.run_id,
                agent_name=None,
                tool_name=tool_name,
                arguments=sanitized_arguments,
                risk_level=spec.risk_level,
                tenant_id=context.tenant_id,
                metadata=metadata,
                expires_at=expires_at,
            )
            await self.approval_store.create(approval)
            risk_level = str(spec.risk_level).lower()
            if risk_level in {"high", "critical"}:
                await self.audit_logger.log(AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    event_type="tool.high_risk_intercepted",
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tool_name=tool_name,
                    approval_id=approval.approval_id,
                    data={
                        "risk_level": spec.risk_level,
                        "argument_keys": sorted(arguments.keys()),
                    },
                ))
            await self.audit_logger.log(AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=context.run_id,
                event_type="tool.approval_required",
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                tool_name=tool_name,
                approval_id=approval.approval_id,
                data={"arguments": sanitized_arguments, "risk_level": spec.risk_level},
            ))
            # -- Phase 12: tool.approval_required + approval.created --
            await self._record_event(
                event_type=RunEventType.TOOL_APPROVAL_REQUIRED,
                context=context,
                tool_name=tool_name,
                status="interrupted",
                approval_id=approval.approval_id,
                data={
                    "argument_keys": sorted(arguments.keys()),
                    "risk_level": str(spec.risk_level),
                },
            )
            await self._record_event(
                event_type=RunEventType.APPROVAL_CREATED,
                context=context,
                tool_name=tool_name,
                approval_id=approval.approval_id,
                data={"risk_level": str(spec.risk_level)},
            )
            return ToolExecutionResult(
                status=ToolExecutionStatus.INTERRUPTED.value,
                tool_name=tool_name,
                approval_request=approval,
            )

        # -- 4. Execute --
        output = None
        error = None
        try:
            import asyncio
            if asyncio.iscoroutinefunction(fn):
                output = await fn(**arguments)
            else:
                output = fn(**arguments)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}

        # -- 5. Audit --
        await self.audit_logger.log(AuditEvent(
            event_id=str(uuid.uuid4()),
            run_id=context.run_id,
            event_type="tool.executed",
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            tool_name=tool_name,
            data={
                "arguments": _safe_serialize(sanitize_payload(arguments)),
                "output": _safe_serialize(sanitize_payload(output)),
                "error": _safe_serialize(sanitize_payload(error)),
                "status": (
                    ToolExecutionStatus.FAILED.value
                    if error
                    else ToolExecutionStatus.COMPLETED.value
                ),
            },
        ))

        status = (
            ToolExecutionStatus.FAILED.value
            if error
            else ToolExecutionStatus.COMPLETED.value
        )

        # -- Phase 12: tool.completed or tool.failed --
        await self._record_event(
            event_type=RunEventType.TOOL_COMPLETED if not error else RunEventType.TOOL_FAILED,
            context=context,
            tool_name=tool_name,
            status=status,
            error={"type": error["type"], "message": error["message"]} if error else None,
            data={"argument_keys": sorted(arguments.keys())},
        )

        return ToolExecutionResult(
            status=status,
            tool_name=tool_name,
            output=output,
            error=error,
        )

    async def _record_event(
        self,
        event_type: RunEventType | str,
        context: RunContext,
        tool_name: str | None = None,
        approval_id: str | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a RunEvent via the trace collector."""
        from agent_app.observability.events import RunEvent
        event = RunEvent(
            event_type=event_type,
            trace_id=context.trace_id or "",
            run_id=context.run_id,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            tool_name=tool_name,
            approval_id=approval_id,
            status=status,
            error=error,
            data=data or {},
        )
        await self.trace_collector.record(event)

    async def _record_policy_event(
        self,
        context: RunContext,
        tool_name: str,
        action: str,
        rule_name: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a policy decision RunEvent into AppRunner trace_events."""
        from agent_app.observability.events import RunEvent
        event = RunEvent(
            event_type="policy.evaluated",
            trace_id=context.trace_id or "",
            run_id=context.run_id,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            tool_name=tool_name,
            data={
                "action": action,
                "rule_name": rule_name,
                "reason": reason,
            },
        )
        await self.trace_collector.record(event)
        if self._trace_events_callback is not None:
            self._trace_events_callback(event)


def _is_tool_call_approval_marker_valid(
    marker: dict[str, Any] | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> bool:
    """Return True when an internal marker approves this exact tool call."""
    return (
        isinstance(marker, dict)
        and marker.get("tool_name") == tool_name
        and marker.get("arguments") == arguments
        and marker.get("source") == "openai_native_hitl"
        and marker.get("token") is _NATIVE_HITL_APPROVAL_TOKEN
    )


def _safe_serialize(value: Any, max_len: int = 500) -> Any:
    """Serialize value for audit logging, truncating long strings."""
    try:
        import json
        text = json.dumps(value, default=str)
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return value
    except Exception:
        return str(value)[:max_len]
