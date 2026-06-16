"""PolicySimulationService — orchestrates policy simulation against historical audit events.

Phase 40: Offline policy validation and historical replay framework.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.audit import AuditLogger, InMemoryAuditLogger
from agent_app.governance.policy_enforcement import PolicyActionType, PolicyDecisionStatus
from agent_app.governance.policy_simulation import (
    PolicySimulationCase,
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.policy_simulation_cases import audit_event_to_simulation_case
from agent_app.runtime.runtime_policy_evaluator import (
    RuntimePolicyEvaluationRequest,
    RuntimePolicyEvaluator,
)
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore, RuntimePolicyStore


class PolicySimulationService:
    """Orchestrates simulation of candidate policy rules against historical audit data.

    Collects cases from audit events, evaluates them against candidate rules,
    and produces a comparison report showing how the candidate rules would change
    outcomes relative to the baseline decisions.
    """

    def __init__(
        self,
        audit_logger: AuditLogger | None = None,
        runtime_policy_store: RuntimePolicyStore | None = None,
    ) -> None:
        self._audit_logger = audit_logger or InMemoryAuditLogger()
        self._runtime_policy_store = runtime_policy_store

    async def collect_cases_from_audit(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[PolicySimulationCase]:
        """Read audit events and convert them to simulation cases.

        Args:
            window_start: Only include events at or after this time.
            window_end: Only include audit events before this time.
            limit: Maximum number of cases to return.

        Returns:
            List of PolicySimulationCase instances extracted from audit events.
        """
        events = self._audit_logger.list_events()  # type: ignore[union-attr]

        # Filter by time window
        if window_start is not None:
            events = [e for e in events if e.created_at >= window_start]
        if window_end is not None:
            events = [e for e in events if e.created_at < window_end]

        cases: list[PolicySimulationCase] = []
        for event in events:
            case = audit_event_to_simulation_case(event)
            if case is not None:
                cases.append(case)

        if limit is not None:
            cases = cases[:limit]

        return cases

    async def simulate_cases(
        self,
        cases: list[PolicySimulationCase],
        candidate_rules: list[RuntimePolicyRule],
        include_base: bool = True,
        name: str | None = None,
    ) -> PolicySimulationReport:
        """Evaluate each case against candidate rules and produce a comparison report.

        Args:
            cases: Simulation cases (typically from collect_cases_from_audit).
            candidate_rules: Candidate runtime policy rules to test.
            include_base: If True, include existing base rules alongside candidates.
            name: Optional name for the simulation report.

        Returns:
            A PolicySimulationReport with per-case results and summary.
        """
        # Build base rules list from active store and construct candidate store.
        # If setup fails, every case gets an ERROR outcome.
        try:
            base_rules: list[RuntimePolicyRule] = []
            if self._runtime_policy_store is not None:
                base_rules = await self._runtime_policy_store.list()

            # Build the candidate store inline (avoids nested event-loop issues
            # from build_candidate_policy_store which creates its own loop).
            candidate_store = InMemoryRuntimePolicyStore()
            all_rules: list[RuntimePolicyRule] = []
            if include_base:
                all_rules.extend(base_rules)
            all_rules.extend(candidate_rules)
            for rule in all_rules:
                await candidate_store.create(rule)

            evaluator = RuntimePolicyEvaluator(policy_store=candidate_store)
            setup_error: Exception | None = None
        except Exception as exc:
            setup_error = exc
            evaluator = None

        results: list[PolicySimulationResult] = []
        summary = PolicySimulationSummary()

        for case in cases:
            if setup_error is not None or evaluator is None:
                result = PolicySimulationResult(
                    case_id=case.case_id,
                    baseline_status=case.baseline_status,
                    outcome=PolicySimulationOutcome.ERROR,
                    reason=str(setup_error),
                    errors=[str(setup_error)],
                )
            else:
                try:
                    # Build evaluation request from case fields
                    request = self._build_evaluation_request(case)
                    decision = await evaluator.evaluate(request)

                    candidate_status = decision.status.value
                    baseline_status = case.baseline_status
                    outcome = self._determine_outcome(baseline_status, candidate_status)

                    result = PolicySimulationResult(
                        case_id=case.case_id,
                        baseline_status=baseline_status,
                        candidate_status=candidate_status,
                        outcome=outcome,
                        reason=decision.reason,
                        decision_id=decision.decision_id,
                    )
                except Exception as exc:
                    result = PolicySimulationResult(
                        case_id=case.case_id,
                        baseline_status=case.baseline_status,
                        outcome=PolicySimulationOutcome.ERROR,
                        reason=str(exc),
                        errors=[str(exc)],
                    )

            results.append(result)
            self._increment_summary(summary, result.outcome)

        return PolicySimulationReport(
            simulation_id=f"psim_{uuid.uuid4().hex[:12]}",
            name=name,
            generated_at=datetime.now(timezone.utc),
            candidate_rule_ids=[r.rule_id for r in candidate_rules],
            summary=summary,
            results=results,
        )

    async def simulate_from_audit(
        self,
        candidate_rules: list[RuntimePolicyRule],
        include_base: bool = True,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
        name: str | None = None,
    ) -> PolicySimulationReport:
        """Convenience method: collect cases from audit and simulate them.

        Args:
            candidate_rules: Candidate runtime policy rules to test.
            include_base: If True, include existing base rules alongside candidates.
            window_start: Only include audit events at or after this time.
            window_end: Only include audit events before this time.
            limit: Maximum number of audit cases to include.
            name: Optional name for the simulation report.

        Returns:
            A PolicySimulationReport with per-case results and summary.
        """
        cases = await self.collect_cases_from_audit(
            window_start=window_start,
            window_end=window_end,
            limit=limit,
        )
        return await self.simulate_cases(
            cases=cases,
            candidate_rules=candidate_rules,
            include_base=include_base,
            name=name,
        )

    def _build_evaluation_request(
        self, case: PolicySimulationCase
    ) -> RuntimePolicyEvaluationRequest:
        """Build a RuntimePolicyEvaluationRequest from a simulation case."""
        try:
            action_type = PolicyActionType(case.action_type)
        except ValueError:
            action_type = PolicyActionType.TOOL_EXECUTE

        context = RunContext(
            run_id=f"sim_{case.case_id}",
            user_id=case.user_id or "unknown",
            tenant_id=case.tenant_id or "unknown",
            roles=case.roles,
            permissions=case.permissions,
        )

        return RuntimePolicyEvaluationRequest(
            action_type=action_type,
            subject=case.subject,
            tool_name=case.tool_name,
            risk_level=case.risk_level,
            context=context,
            metadata=case.metadata,
        )

    @staticmethod
    def _determine_outcome(
        baseline_status: str | None,
        candidate_status: str,
    ) -> PolicySimulationOutcome:
        """Determine the simulation outcome by comparing baseline and candidate statuses."""
        if baseline_status == candidate_status:
            return PolicySimulationOutcome.UNCHANGED

        if candidate_status == PolicyDecisionStatus.ALLOWED.value:
            return PolicySimulationOutcome.WOULD_ALLOW

        if candidate_status == PolicyDecisionStatus.DENIED.value:
            return PolicySimulationOutcome.WOULD_DENY

        if candidate_status == PolicyDecisionStatus.APPROVAL_REQUIRED.value:
            return PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL

        return PolicySimulationOutcome.WOULD_CHANGE

    @staticmethod
    def _increment_summary(
        summary: PolicySimulationSummary,
        outcome: PolicySimulationOutcome,
    ) -> None:
        """Increment the appropriate summary counter for an outcome."""
        summary.total += 1
        if outcome == PolicySimulationOutcome.UNCHANGED:
            summary.unchanged += 1
        elif outcome == PolicySimulationOutcome.WOULD_ALLOW:
            summary.would_allow += 1
        elif outcome == PolicySimulationOutcome.WOULD_DENY:
            summary.would_deny += 1
        elif outcome == PolicySimulationOutcome.WOULD_REQUIRE_APPROVAL:
            summary.would_require_approval += 1
        elif outcome == PolicySimulationOutcome.WOULD_CHANGE:
            summary.would_change += 1
        elif outcome == PolicySimulationOutcome.ERROR:
            summary.errors += 1
