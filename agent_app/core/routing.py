"""Routing policy — declarative, configurable routing rules for workflows."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RoutingMatchType(str, Enum):
    """How a routing rule matches input."""

    KEYWORD = "keyword"
    REGEX = "regex"
    DEFAULT = "default"


class RoutingRule(BaseModel):
    """A single routing rule.

    Attributes:
        name: Unique rule identifier (used in traces and eval assertions).
        target: Agent name to route to when this rule matches.
        match_type: How to match — keyword, regex, or default.
        keywords: Keyword list for KEYWORD match type.
        pattern: Regex pattern string for REGEX match type.
        priority: Lower number = higher priority (default 100).
        reason: Human-readable description of this rule.
        metadata: Extra data for observability.
    """

    name: str = Field(..., description="Rule identifier")
    target: str = Field(..., description="Target agent name")
    match_type: RoutingMatchType = Field(
        default=RoutingMatchType.KEYWORD, description="Match strategy"
    )
    keywords: list[str] = Field(
        default_factory=list, description="Keywords for keyword matching"
    )
    pattern: str | None = Field(
        default=None, description="Regex pattern for regex matching"
    )
    priority: int = Field(default=100, description="Priority (lower = higher)")
    reason: str | None = Field(default=None, description="Rule description")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra metadata"
    )


class RoutingPolicy(BaseModel):
    """A collection of routing rules for a workflow.

    Attributes:
        name: Policy identifier.
        rules: Ordered list of routing rules (sorted by priority).
    """

    name: str = Field(..., description="Policy identifier")
    rules: list[RoutingRule] = Field(
        default_factory=list, description="Routing rules (sorted by priority)"
    )

    def sorted_rules(self) -> list[RoutingRule]:
        """Return rules sorted by priority (lower number first)."""
        return sorted(self.rules, key=lambda r: r.priority)
