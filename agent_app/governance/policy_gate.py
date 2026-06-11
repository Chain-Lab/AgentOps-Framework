"""Policy gate — release safety evaluation for policy bundles.

Phase 29: evaluates policy bundles against historical replay data using
configurable gate rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PolicyGateStatus(str, Enum):
    """Overall gate evaluation result."""
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class PolicyGateRule(BaseModel):
    """A single release gate rule with thresholds.

    Attributes:
        name: Unique rule name.
        description: Human-readable description.
        max_changed_decisions: Max total changed decisions allowed.
        max_changed_ratio: Max ratio of changed decisions (0.0–1.0).
        max_failed_replays: Max failed replays allowed.
        max_new_denies: Max new deny actions introduced.
        max_new_approvals: Max new require_approval actions introduced.
        fail_on_missing_required_context: Whether missing context causes failure.
    """

    name: str = Field(..., description="Unique rule name")
    description: str | None = Field(default=None, description="Rule description")
    max_changed_decisions: int | None = Field(
        default=None, description="Max changed decisions allowed"
    )
    max_changed_ratio: float | None = Field(
        default=None, description="Max changed ratio (0.0–1.0)"
    )
    max_failed_replays: int | None = Field(
        default=None, description="Max failed replays allowed"
    )
    max_new_denies: int | None = Field(
        default=None, description="Max new denies introduced"
    )
    max_new_approvals: int | None = Field(
        default=None, description="Max new approvals introduced"
    )
    fail_on_missing_required_context: bool = Field(
        default=False,
        description="Fail if required context is missing in replay",
    )


class PolicyGateResult(BaseModel):
    """Result of evaluating a policy bundle against gate rules.

    Attributes:
        gate_result_id: Unique identifier for this gate result.
        bundle_id: The bundle that was evaluated.
        replay_id: The replay that was used for evaluation.
        status: Overall gate status (passed/warning/failed).
        passed: Whether the gate passed (True for passed/warning, False for failed).
        total_decisions: Total decisions evaluated.
        changed_decisions: Decisions whose action changed.
        failed_replays: Decisions that could not be replayed.
        changed_ratio: Ratio of changed decisions.
        new_denies: New deny actions introduced.
        new_approvals: New require_approval actions introduced.
        missing_context_count: Decisions with missing required context.
        rule_results: Per-rule evaluation results.
        summary: Arbitrary summary data.
        created_at: When the gate was evaluated.
        created_by: Identity of who triggered the evaluation.
    """

    gate_result_id: str = Field(..., description="Unique gate result ID")
    bundle_id: str = Field(..., description="Bundle that was evaluated")
    replay_id: str = Field(..., description="Replay used for evaluation")
    status: str = Field(..., description="Overall gate status")
    passed: bool = Field(..., description="Whether the gate passed")

    total_decisions: int = Field(..., description="Total decisions evaluated")
    changed_decisions: int = Field(..., description="Changed decisions")
    failed_replays: int = Field(..., description="Failed replays")
    changed_ratio: float = Field(..., description="Changed ratio")

    new_denies: int = Field(default=0, description="New denies introduced")
    new_approvals: int = Field(default=0, description="New approvals introduced")
    missing_context_count: int = Field(default=0, description="Missing context count")

    rule_results: list[dict[str, Any]] = Field(
        default_factory=list, description="Per-rule results"
    )
    summary: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary summary"
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Evaluation timestamp",
    )
    created_by: str | None = Field(default=None, description="Evaluator identity")


# ---------------------------------------------------------------------------
# Gate Evaluator
# ---------------------------------------------------------------------------

class PolicyGateEvaluator:
    """Evaluates policy bundles against configurable release gate rules.

    Takes a list of PolicyGateRule thresholds and evaluates a replay result
    against them, producing a PolicyGateResult with per-rule outcomes.

    Args:
        rules: List of gate rules to evaluate against.
    """

    def __init__(self, rules: list[PolicyGateRule]) -> None:
        self._rules = rules

    async def evaluate(
        self,
        bundle: PolicyBundle,
        replay_result: Any,  # PolicyReplayResult — avoid circular import
        created_by: str | None = None,
    ) -> PolicyGateResult:
        """Evaluate a bundle against gate rules.

        Args:
            bundle: The policy bundle to evaluate.
            replay_result: The replay result to evaluate against.
            created_by: Identity of who triggered the evaluation.

        Returns:
            PolicyGateResult with overall status and per-rule results.
        """
        from agent_app.governance.policy_bundle import PolicyBundle

        run = replay_result.replay
        changes = replay_result.changes

        # Compute metrics from replay result
        total = run.source_decision_count
        changed = run.changed_count
        failed = run.failed_count
        changed_ratio = changed / total if total > 0 else 0.0

        # Count new denies/approvals from changes
        new_denies = sum(
            1 for c in changes
            if c.changed and c.replayed_action == "deny"
            and c.original_action != "deny"
        )
        new_approvals = sum(
            1 for c in changes
            if c.changed and c.replayed_action == "require_approval"
            and c.original_action != "require_approval"
        )

        # Count missing context from context_metadata in changes
        missing_context_count = sum(
            1 for c in changes
            if c.context_metadata and c.context_metadata.get("missing_fields")
        )

        # Evaluate each rule
        rule_results: list[dict[str, Any]] = []
        overall_failed = False

        for rule in self._rules:
            rule_result = self._evaluate_rule(
                rule=rule,
                total=total,
                changed=changed,
                failed=failed,
                changed_ratio=changed_ratio,
                new_denies=new_denies,
                new_approvals=new_approvals,
                missing_context_count=missing_context_count,
            )
            rule_results.append(rule_result)
            if rule_result["status"] == "failed":
                overall_failed = True

        # Determine overall status
        if overall_failed:
            status = PolicyGateStatus.FAILED
            passed = False
        elif self._has_warnings(rule_results):
            status = PolicyGateStatus.WARNING
            passed = True
        else:
            status = PolicyGateStatus.PASSED
            passed = True

        return PolicyGateResult(
            gate_result_id=f"gr_{uuid.uuid4().hex[:12]}",
            bundle_id=bundle.bundle_id,
            replay_id=run.replay_id,
            status=status.value,
            passed=passed,
            total_decisions=total,
            changed_decisions=changed,
            failed_replays=failed,
            changed_ratio=round(changed_ratio, 4),
            new_denies=new_denies,
            new_approvals=new_approvals,
            missing_context_count=missing_context_count,
            rule_results=rule_results,
            summary={
                "bundle_name": bundle.name,
                "bundle_version": bundle.version,
                "rule_count": len(self._rules),
            },
            created_by=created_by,
        )

    def _evaluate_rule(
        self,
        rule: PolicyGateRule,
        total: int,
        changed: int,
        failed: int,
        changed_ratio: float,
        new_denies: int,
        new_approvals: int,
        missing_context_count: int,
    ) -> dict[str, Any]:
        """Evaluate a single gate rule."""
        failures: list[str] = []

        if rule.max_changed_decisions is not None and changed > rule.max_changed_decisions:
            failures.append(
                f"changed_decisions {changed} > max {rule.max_changed_decisions}"
            )

        if rule.max_changed_ratio is not None and changed_ratio > rule.max_changed_ratio:
            failures.append(
                f"changed_ratio {changed_ratio:.2%} > max {rule.max_changed_ratio:.2%}"
            )

        if rule.max_failed_replays is not None and failed > rule.max_failed_replays:
            failures.append(
                f"failed_replays {failed} > max {rule.max_failed_replays}"
            )

        if rule.max_new_denies is not None and new_denies > rule.max_new_denies:
            failures.append(
                f"new_denies {new_denies} > max {rule.max_new_denies}"
            )

        if rule.max_new_approvals is not None and new_approvals > rule.max_new_approvals:
            failures.append(
                f"new_approvals {new_approvals} > max {rule.max_new_approvals}"
            )

        if rule.fail_on_missing_required_context and missing_context_count > 0:
            failures.append(
                f"missing_context_count {missing_context_count} > 0 (fail_on_missing_required_context)"
            )

        if failures:
            return {
                "rule_name": rule.name,
                "status": "failed",
                "failures": failures,
            }
        return {
            "rule_name": rule.name,
            "status": "passed",
            "failures": [],
        }

    def _has_warnings(self, rule_results: list[dict[str, Any]]) -> bool:
        """Check if any rule results indicate warnings (future extension)."""
        # Currently no explicit warning level per rule, but structure supports it
        return False
