"""Policy ring router — resolves which ring to use for a given request."""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext


class RingRoutingConfig(BaseModel):
    """Configuration for deterministic canary percentage routing."""

    enabled: bool = False
    canary_percentage: int = Field(default=0, ge=0, le=100)
    canary_ring: str = "canary"
    stable_ring: str = "stable"
    hash_key: Literal["actor_id", "user_id", "tenant_id"] = "actor_id"


class PolicyRingRouter:
    """Resolves the release ring for a request based on context and store state.

    Resolution order:
    1. If context.policy_ring is set, use it (explicit override)
    2. If routing_config is enabled, deterministically route by percentage
    3. If ring_store is configured, look up default ring for environment
    4. Fall back to configured default_ring
    5. Validate ring exists and is not disabled
    """

    def __init__(
        self,
        ring_store: Any = None,
        default_ring: str = "stable",
        routing_config: RingRoutingConfig | None = None,
    ) -> None:
        self._ring_store = ring_store
        self._default_ring = default_ring
        self._routing_config = routing_config

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

        # 2. Deterministic percentage routing (if configured and no explicit override)
        if ring_name is None and self._routing_config is not None and self._routing_config.enabled:
            ring_name = self._deterministic_route(environment, context)

        # 3. Default ring from store
        if ring_name is None and self._ring_store is not None:
            rings = await self._ring_store.list(environment=environment)
            for ring in rings:
                if ring.is_default and ring.status == ReleaseRingStatus.ENABLED:
                    ring_name = ring.name
                    break

        # 4. Configured fallback
        if ring_name is None:
            ring_name = self._default_ring

        # 5. Validate ring state if store available
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

    def _deterministic_route(self, environment: str, context: RunContext) -> str | None:
        """Deterministically route to canary or stable based on percentage.

        Uses SHA-256 hash of "{environment}:{key_value}" to determine routing.
        Same key always routes to the same ring.

        Returns:
            Ring name (canary or stable), or None if no hash key value available.
        """
        config = self._routing_config

        # Get hash key value from context
        key_value = None
        if config.hash_key == "actor_id":
            # Map actor_id from user_id (most common mapping)
            key_value = context.user_id
        elif config.hash_key == "user_id":
            key_value = context.user_id
        elif config.hash_key == "tenant_id":
            key_value = context.tenant_id

        # If no key value, route to stable
        if not key_value:
            return config.stable_ring

        # Compute deterministic hash
        hash_input = f"{environment}:{key_value}"
        hash_bytes = hashlib.sha256(hash_input.encode("utf-8")).digest()

        # Take first 8 bytes, convert to int, modulo 100
        bucket = int.from_bytes(hash_bytes[:8], byteorder="big") % 100

        # Route based on canary percentage
        if bucket < config.canary_percentage:
            return config.canary_ring
        return config.stable_ring

    async def simulate_routing(
        self,
        environment: str,
        context: RunContext,
    ) -> dict[str, Any]:
        """Simulate routing for the given context without actually routing.

        Returns dict with: environment, selected_ring, routing_mode,
        hash_key, bucket, canary_percentage, reason.
        """
        result: dict[str, Any] = {
            "environment": environment,
            "selected_ring": None,
            "routing_mode": "none",
            "hash_key": None,
            "bucket": None,
            "canary_percentage": None,
            "reason": None,
        }

        # Check explicit override
        if context.policy_ring:
            result["selected_ring"] = context.policy_ring
            result["routing_mode"] = "explicit"
            result["reason"] = "Explicit policy_ring override"
            return result

        # Check deterministic routing
        if self._routing_config is not None and self._routing_config.enabled:
            config = self._routing_config
            result["routing_mode"] = "deterministic"
            result["canary_percentage"] = config.canary_percentage
            result["hash_key"] = config.hash_key

            key_value = None
            if config.hash_key == "actor_id":
                key_value = context.user_id
            elif config.hash_key == "user_id":
                key_value = context.user_id
            elif config.hash_key == "tenant_id":
                key_value = context.tenant_id

            if not key_value:
                result["selected_ring"] = config.stable_ring
                result["reason"] = f"No {config.hash_key} value, routing to stable"
                return result

            hash_input = f"{environment}:{key_value}"
            hash_bytes = hashlib.sha256(hash_input.encode("utf-8")).digest()
            bucket = int.from_bytes(hash_bytes[:8], byteorder="big") % 100
            result["bucket"] = bucket

            if bucket < config.canary_percentage:
                result["selected_ring"] = config.canary_ring
                result["reason"] = f"Bucket {bucket} < canary_percentage {config.canary_percentage}"
            else:
                result["selected_ring"] = config.stable_ring
                result["reason"] = f"Bucket {bucket} >= canary_percentage {config.canary_percentage}"
            return result

        # Default routing
        result["routing_mode"] = "default"
        result["selected_ring"] = self._default_ring
        result["reason"] = "Default ring (no deterministic routing)"
        return result
