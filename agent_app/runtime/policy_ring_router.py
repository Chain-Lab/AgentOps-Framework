"""Policy ring router — resolves which ring to use for a given request."""
from __future__ import annotations

from typing import Any

from agent_app.core.context import RunContext


class PolicyRingRouter:
    """Resolves the release ring for a request based on context and store state.

    Resolution order:
    1. If context.policy_ring is set, use it (explicit override)
    2. If ring_store is configured, look up default ring for environment
    3. Fall back to configured default_ring
    4. Raise if selected ring is disabled
    5. Raise if selected ring does not exist (unless no ring_store)
    """

    def __init__(
        self,
        ring_store: Any = None,
        default_ring: str = "stable",
    ) -> None:
        self._ring_store = ring_store
        self._default_ring = default_ring

    async def resolve_ring(
        self,
        environment: str,
        context: RunContext,
    ) -> str:
        """Resolve which ring to use for the given request.

        Args:
            environment: The target environment.
            context: Current run context.

        Returns:
            The ring name to use.

        Raises:
            RuntimeError: If the selected ring is disabled.
            KeyError: If the selected ring does not exist.
        """
        from agent_app.governance.policy_ring import ReleaseRingStatus

        # 1. Explicit context override
        ring_name = context.policy_ring

        # 2. Default ring from store
        if ring_name is None and self._ring_store is not None:
            rings = await self._ring_store.list(environment=environment)
            for ring in rings:
                if ring.is_default and ring.status == ReleaseRingStatus.ENABLED:
                    ring_name = ring.name
                    break

        # 3. Configured fallback
        if ring_name is None:
            ring_name = self._default_ring

        # 4. Validate ring state if store available
        if self._ring_store is not None:
            ring = await self._ring_store.get_by_name(environment, ring_name)
            if ring is None:
                raise KeyError(
                    f"Ring '{ring_name}' does not exist in environment '{environment}'. "
                    f"Create the ring before using it."
                )
            if ring.status == ReleaseRingStatus.DISABLED:
                raise RuntimeError(
                    f"Ring '{ring_name}' is disabled in environment '{environment}'. "
                    f"Enable the ring before using it."
                )

        return ring_name
