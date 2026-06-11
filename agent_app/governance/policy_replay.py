"""Policy replay — re-evaluate historical decisions against current policy.

Phase 27: lightweight policy replay and regression analysis.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_app.governance.policy import PolicyAction, PolicyDecisionTrace, PolicyEvaluationContext
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PolicyReplayStatus(str):
    """Status of a replay run."""
    COMPLETED = "completed"
    FAILED = "failed"


class PolicyReplayDecisionChange(BaseModel):
    """A single decision that changed (or didn't) under replay.

    Attributes:
        decision_id: Original decision trace ID.
        original_action: Action from the original decision.
        replayed_action: Action from the replayed evaluation.
        changed: Whether the action differs.
        original_rule_id: Rule that matched originally (if any).
        replayed_rule_id: Rule that matches under current policy (if any).
        reason: Explanation of why it changed (if applicable).
        context_metadata: Context reconstruction metadata (Phase 28).
    """
    decision_id: str = Field(..., description="Original decision trace ID")
    original_action: str = Field(..., description="Original action")
    replayed_action: str = Field(..., description="Replayed action")
    changed: bool = Field(..., description="Whether action changed")
    original_rule_id: str | None = Field(default=None, description="Original rule name")
    replayed_rule_id: str | None = Field(default=None, description="Replayed rule name")
    reason: str | None = Field(default=None, description="Change reason")
    context_metadata: dict[str, Any] | None = Field(
        default=None, description="Context reconstruction metadata"
    )


class PolicyReplayRun(BaseModel):
    """Summary of a replay run.

    Attributes:
        replay_id: Unique identifier for this replay.
        status: COMPLETED or FAILED.
        source_decision_count: Total decisions evaluated.
        changed_count: Decisions whose action changed.
        unchanged_count: Decisions whose action stayed the same.
        failed_count: Decisions that could not be replayed.
        created_at: When the replay was started.
        metadata: Arbitrary metadata (config snapshot, filters, etc.).
    """
    replay_id: str = Field(..., description="Unique replay identifier")
    status: str = Field(..., description="COMPLETED or FAILED")
    source_decision_count: int = Field(..., description="Total decisions evaluated")
    changed_count: int = Field(..., description="Decisions with changed action")
    unchanged_count: int = Field(..., description="Decisions with unchanged action")
    failed_count: int = Field(default=0, description="Unreplayable decisions")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Replay start time",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary metadata"
    )


class PolicyReplayResult(BaseModel):
    """Full replay result including run summary and per-decision changes.

    Attributes:
        replay: Summary of the replay run.
        changes: List of per-decision change records.
    """
    replay: PolicyReplayRun = Field(..., description="Replay run summary")
    changes: list[PolicyReplayDecisionChange] = Field(
        default_factory=list, description="Per-decision changes"
    )


# ---------------------------------------------------------------------------
# Replay store protocol
# ---------------------------------------------------------------------------

class PolicyReplayStore(Protocol):
    """Protocol for persisting replay results."""

    async def save(self, result: PolicyReplayResult) -> PolicyReplayResult:
        """Persist a replay result. Returns the saved result."""
        ...

    async def get(self, replay_id: str) -> PolicyReplayResult | None:
        """Retrieve a replay result by ID. Returns None if not found."""
        ...

    async def list(self, limit: int = 50) -> list[PolicyReplayRun]:
        """List recent replay runs (most recent first)."""
        ...


class InMemoryPolicyReplayStore:
    """In-memory policy replay store for testing and development.

    Stores replay results in a simple list.
    """

    def __init__(self) -> None:
        self._results: dict[str, PolicyReplayResult] = {}
        self._order: list[str] = []

    async def save(self, result: PolicyReplayResult) -> PolicyReplayResult:
        """Persist a replay result."""
        self._results[result.replay.replay_id] = result
        self._order.append(result.replay.replay_id)
        return result

    async def get(self, replay_id: str) -> PolicyReplayResult | None:
        """Retrieve a replay result by ID."""
        return self._results.get(replay_id)

    async def list(self, limit: int = 50) -> list[PolicyReplayRun]:
        """List recent replay runs, most recent first."""
        ids = list(reversed(self._order[-limit:]))
        runs = []
        for rid in ids:
            r = self._results.get(rid)
            if r:
                runs.append(r.replay)
        return runs


# ---------------------------------------------------------------------------
# Replay runner
# ---------------------------------------------------------------------------

class PolicyReplayRunner:
    """Re-evaluate historical policy decisions against the current policy engine.

    Loads decisions from a PolicyDecisionStore, re-evaluates each one using
    the current policy engine, compares original vs replayed actions, and
    optionally persists the result.

    Args:
        decision_store: Source of historical policy decisions.
        policy_engine: Current policy engine to re-evaluate with.
        replay_store: Optional store for persisting results.
        context_builder: Optional context builder for richer reconstruction.
    """

    def __init__(
        self,
        decision_store: Any,
        policy_engine: Any,
        replay_store: Any = None,
        context_builder: Any = None,
    ) -> None:
        self._decision_store = decision_store
        self._policy_engine = policy_engine
        self._replay_store = replay_store
        self._context_builder = context_builder

    async def run_replay(
        self,
        limit: int | None = None,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        rule_id: str | None = None,
    ) -> PolicyReplayResult:
        """Run a policy replay.

        Args:
            limit: Max decisions to replay.
            tenant_id: Filter by tenant.
            tool_name: Filter by tool name.
            rule_id: Filter by original rule name.

        Returns:
            PolicyReplayResult with summary and per-decision changes.
        """
        from agent_app.governance.policy import PolicyAction

        # Query source decisions
        traces = await self._decision_store.query(
            run_id=None,
            tenant_id=tenant_id,
            agent_name=None,
            tool_name=tool_name,
            rule_name=rule_id,
            action=None,
            limit=limit or 100,
            offset=0,
        )

        replay_id = f"replay_{uuid.uuid4().hex[:12]}"
        changes: list[PolicyReplayDecisionChange] = []
        changed = 0
        unchanged = 0
        failed = 0

        for trace in traces:
            try:
                replay_context_info: dict[str, Any] = {}

                # Use context builder if available
                if self._context_builder is not None:
                    replay_ctx = self._context_builder.build(trace)
                    replay_context_info = {
                        "missing_fields": replay_ctx.missing_fields,
                        "source": replay_ctx.source,
                        "permissions_used": replay_ctx.permissions,
                        "roles": replay_ctx.roles,
                        "user_id": replay_ctx.user_id,
                        "tenant_id": replay_ctx.tenant_id,
                    }

                    # Check if required fields are available
                    eval_ctx = self._context_builder.build_evaluation_context(trace)
                    if eval_ctx is None:
                        raise ValueError(
                            "Cannot replay decision: required context fields "
                            f"missing ({', '.join(replay_ctx.missing_fields)}) "
                            f"for decision {trace.decision_id}"
                        )
                    ctx = eval_ctx
                else:
                    # Fallback to direct trace-to-context conversion
                    ctx = self._trace_to_context(trace)

                # Guard: need at least tool_name to evaluate
                if not ctx.tool_name:
                    raise ValueError(
                        "Cannot replay decision: tool_name is missing from "
                        f"decision {trace.decision_id}"
                    )

                # Re-evaluate with current policy engine
                new_decision = await self._policy_engine.evaluate_tool_call(ctx)
                new_action = new_decision.action.value
                original_action = trace.action.value

                if new_action == original_action:
                    unchanged += 1
                    changes.append(PolicyReplayDecisionChange(
                        decision_id=trace.decision_id,
                        original_action=original_action,
                        replayed_action=new_action,
                        changed=False,
                        original_rule_id=trace.rule_name,
                        replayed_rule_id=new_decision.metadata.get("rule_name"),
                        context_metadata=replay_context_info or None,
                    ))
                else:
                    changed += 1
                    changes.append(PolicyReplayDecisionChange(
                        decision_id=trace.decision_id,
                        original_action=original_action,
                        replayed_action=new_action,
                        changed=True,
                        original_rule_id=trace.rule_name,
                        replayed_rule_id=new_decision.metadata.get("rule_name"),
                        reason=new_decision.reason,
                        context_metadata=replay_context_info or None,
                    ))
            except Exception as exc:
                failed += 1
                changes.append(PolicyReplayDecisionChange(
                    decision_id=trace.decision_id,
                    original_action=trace.action.value,
                    replayed_action="error",
                    changed=False,
                    reason=f"Replay failed: {exc}",
                ))

        run = PolicyReplayRun(
            replay_id=replay_id,
            status=PolicyReplayStatus.COMPLETED if failed == 0 else PolicyReplayStatus.COMPLETED,
            source_decision_count=len(traces),
            changed_count=changed,
            unchanged_count=unchanged,
            failed_count=failed,
            metadata={
                "tenant_id": tenant_id,
                "tool_name": tool_name,
                "rule_id": rule_id,
                "limit": limit,
            },
        )

        result = PolicyReplayResult(replay=run, changes=changes)

        # Persist if store available
        if self._replay_store is not None:
            await self._replay_store.save(result)

        return result

    @staticmethod
    def _trace_to_context(trace: PolicyDecisionTrace) -> PolicyEvaluationContext:
        """Reconstruct a PolicyEvaluationContext from a decision trace.

        Uses context_summary for available fields. Fields not stored in the
        trace (permissions, metadata) are left as defaults.
        """
        cs = trace.context_summary or {}
        return PolicyEvaluationContext(
            run_id=trace.run_id,
            tool_name=trace.tool_name or cs.get("tool_name"),
            workflow_name=cs.get("workflow_name"),
            workflow_type=cs.get("workflow_type"),
            agent_name=cs.get("agent_name"),
            source_agent=cs.get("source_agent"),
            target_agent=cs.get("target_agent"),
            user_id=cs.get("user_id"),
            tenant_id=cs.get("tenant_id"),
            roles=list(cs.get("roles", [])),
            permissions=list(cs.get("permissions", [])),
            metadata=cs.get("metadata", {}),
        )
