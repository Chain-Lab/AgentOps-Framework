"""Candidate policy store — builds isolated runtime policy stores for simulation.

Phase 40: Simulate candidate rules without mutating active policy store.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore, RuntimePolicyStore


class CandidateRuntimePolicySet(BaseModel):
    """A named set of candidate runtime policy rules for simulation."""

    name: str | None = None
    rules: list[RuntimePolicyRule] = []


def build_candidate_policy_store(
    base_rules: list[RuntimePolicyRule],
    candidate_rules: list[RuntimePolicyRule],
    include_base: bool = True,
) -> RuntimePolicyStore:
    """Build an isolated InMemoryRuntimePolicyStore for simulation.

    Args:
        base_rules: Existing runtime policy rules (from active store).
        candidate_rules: New candidate rules to test.
        include_base: If True, include base rules alongside candidates.
                      If False, only candidate rules are included.

    Returns:
        An InMemoryRuntimePolicyStore populated with the appropriate rules.
        This store is independent of the active runtime policy store.
    """
    store = InMemoryRuntimePolicyStore()
    all_rules: list[RuntimePolicyRule] = []

    if include_base:
        all_rules.extend(base_rules)

    all_rules.extend(candidate_rules)

    async def _populate() -> None:
        for rule in all_rules:
            await store.create(rule)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_populate())
    finally:
        loop.close()

    return store
