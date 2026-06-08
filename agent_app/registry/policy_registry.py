"""PolicyRegistry — stores guardrail / permission / approval policies.

Phase 1: lightweight stub.  Real enforcement logic is added in later phases.
"""

from __future__ import annotations

from agent_app.registry.base import Registry


class PolicyRegistry(Registry[str]):
    """Registry for policy names (guardrails, permissions, etc.).

    In Phase 1 the values are plain strings (policy identifiers).
    Later phases will store richer policy objects.
    """

    pass
