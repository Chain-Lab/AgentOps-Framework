"""Routing policy executor — applies RoutingPolicy to workflow inputs."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from agent_app.core.routing import (
    RoutingMatchType,
    RoutingPolicy,
    RoutingRule,
)


class RoutingDecision(BaseModel):
    """Result of applying a routing rule to an input.

    Attributes:
        target: The chosen agent name.
        rule_name: Name of the rule that produced this decision.
        reason: Human-readable explanation.
        confidence: Optional confidence score (0.0–1.0).
        metadata: Extra data from the rule.
    """

    target: str = Field(..., description="Chosen agent name")
    rule_name: str = Field(..., description="Matching rule name")
    reason: str = Field(..., description="Why this rule matched")
    confidence: float | None = Field(default=None, description="Confidence score")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Rule metadata"
    )


class RoutingPolicyExecutor:
    """Applies a :class:`RoutingPolicy` to user input.

    Provides two modes:

    * ``route_one()`` — returns the highest-priority single match (handoff).
    * ``route_many()`` — returns all non-default matches (orchestrator).
    """

    def route_one(
        self,
        policy: RoutingPolicy,
        input: str,
        allowed_targets: list[str],
    ) -> RoutingDecision | None:
        """Route input to exactly one target agent.

        Iterates rules in priority order. Returns the first match whose
        target is in *allowed_targets*. If no specific rule matches,
        falls back to the first ``DEFAULT`` rule whose target is allowed.

        Args:
            policy: The routing policy to apply.
            input: Raw user input string.
            allowed_targets: Agent names that are valid targets.

        Returns:
            A :class:`RoutingDecision` or ``None`` if no rule matches.
        """
        input_lower = input.lower()
        default_decision: RoutingDecision | None = None

        for rule in policy.sorted_rules():
            # Skip rules targeting agents not in the workflow
            if rule.target not in allowed_targets:
                continue

            if rule.match_type == RoutingMatchType.DEFAULT:
                # Remember the default but keep looking for specific matches
                default_decision = RoutingDecision(
                    target=rule.target,
                    rule_name=rule.name,
                    reason=rule.reason or "default fallback",
                    metadata=rule.metadata or {},
                )
                continue

            if rule.match_type == RoutingMatchType.KEYWORD:
                if self._match_keyword(input_lower, rule.keywords):
                    return RoutingDecision(
                        target=rule.target,
                        rule_name=rule.name,
                        reason=rule.reason or f"matched keywords: {rule.keywords}",
                        metadata=rule.metadata or {},
                    )

            if rule.match_type == RoutingMatchType.REGEX:
                if self._match_regex(input, rule.pattern):
                    return RoutingDecision(
                        target=rule.target,
                        rule_name=rule.name,
                        reason=rule.reason or f"matched regex: {rule.pattern}",
                        metadata=rule.metadata or {},
                    )

        # No specific match — return default if available
        return default_decision

    def route_many(
        self,
        policy: RoutingPolicy,
        input: str,
        allowed_targets: list[str],
    ) -> list[RoutingDecision]:
        """Route input to zero or more target agents.

        Collects all non-default rules that match. Default rules are
        intentionally excluded — orchestrator only delegates to
        specialists when there is an explicit signal.

        Args:
            policy: The routing policy to apply.
            input: Raw user input string.
            allowed_targets: Agent names that are valid targets.

        Returns:
            List of :class:`RoutingDecision` for each matched rule.
        """
        input_lower = input.lower()
        decisions: list[RoutingDecision] = []

        for rule in policy.sorted_rules():
            if rule.target not in allowed_targets:
                continue

            if rule.match_type == RoutingMatchType.DEFAULT:
                continue  # Orchestrator never uses default rules

            matched = False
            if rule.match_type == RoutingMatchType.KEYWORD:
                matched = self._match_keyword(input_lower, rule.keywords)
            elif rule.match_type == RoutingMatchType.REGEX:
                matched = self._match_regex(input, rule.pattern)

            if matched:
                decisions.append(
                    RoutingDecision(
                        target=rule.target,
                        rule_name=rule.name,
                        reason=rule.reason or f"matched {rule.match_type}",
                        metadata=rule.metadata or {},
                    )
                )

        return decisions

    # -- Private helpers --

    @staticmethod
    def _match_keyword(input_lower: str, keywords: list[str]) -> bool:
        """Check if any keyword appears in the lowercased input."""
        for kw in keywords:
            if kw.lower() in input_lower:
                return True
        return False

    @staticmethod
    def _match_regex(input_text: str, pattern: str | None) -> bool:
        """Check if the regex pattern matches the input."""
        if not pattern:
            return False
        try:
            return bool(re.search(pattern, input_text, re.IGNORECASE))
        except re.error:
            return False
