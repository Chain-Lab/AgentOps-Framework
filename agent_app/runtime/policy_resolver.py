"""Active policy resolver -- resolves the active bundle for an environment."""

from __future__ import annotations

import time
from typing import Any

from agent_app.governance.policy_activation import PolicyActivationStatus
from agent_app.runtime.policy_activation_store import PolicyActivationStore


class _CacheEntry:
    __slots__ = ("bundle", "expires_at")

    def __init__(self, bundle: Any, ttl_seconds: float) -> None:
        self.bundle = bundle
        self.expires_at = time.monotonic() + ttl_seconds

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class ActivePolicyResolver:
    """Resolves the active policy bundle for a given environment."""

    def __init__(
        self,
        bundle_store: Any,
        activation_store: PolicyActivationStore,
        cache_ttl_seconds: float = 0,
        environment_store: Any = None,
        ring_assignment_store: Any = None,
        ring_store: Any = None,
    ) -> None:
        self._bundle_store = bundle_store
        self._activation_store = activation_store
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str | tuple[str, str], _CacheEntry] = {}
        self._environment_store = environment_store
        self._ring_assignment_store = ring_assignment_store
        self._ring_store = ring_store

    async def resolve_active_bundle(self, environment: str) -> Any | None:
        # Phase 32: Check environment state
        if self._environment_store is not None:
            from agent_app.governance.policy_environment import PolicyEnvironmentStatus
            env_state = await self._environment_store.get(environment)
            if env_state.status == PolicyEnvironmentStatus.DISABLED:
                if self._cache_ttl > 0:
                    self._cache[environment] = _CacheEntry(None, self._cache_ttl)
                return None

        if self._cache_ttl > 0 and environment in self._cache:
            entry = self._cache[environment]
            if not entry.is_expired():
                return entry.bundle
            del self._cache[environment]

        activation = await self._activation_store.get_active(environment)
        if activation is None:
            if self._cache_ttl > 0:
                self._cache[environment] = _CacheEntry(None, self._cache_ttl)
            return None

        bundle = await self._bundle_store.get(activation.bundle_id)
        if bundle is None:
            raise KeyError(f"Bundle '{activation.bundle_id}' referenced by activation '{activation.activation_id}' not found in bundle store.")

        if activation.config_hash != bundle.config_hash:
            raise ValueError(f"config_hash mismatch for activation '{activation.activation_id}': activation has '{activation.config_hash}', bundle has '{bundle.config_hash}'.")

        if self._cache_ttl > 0:
            self._cache[environment] = _CacheEntry(bundle, self._cache_ttl)
        return bundle

    async def resolve_active_bundle_for_ring(self, environment: str, ring_name: str) -> Any | None:
        """Resolve the active policy bundle for a specific environment + ring.

        Phase 33: Ring-aware resolution. Checks environment state, ring state,
        ring assignment, activation, bundle, and config_hash integrity.
        """
        cache_key = (environment, ring_name)

        # Check cache first
        if self._cache_ttl > 0 and cache_key in self._cache:
            entry = self._cache[cache_key]
            if not entry.is_expired():
                return entry.bundle
            del self._cache[cache_key]

        # Check environment state
        if self._environment_store is not None:
            from agent_app.governance.policy_environment import PolicyEnvironmentStatus
            env_state = await self._environment_store.get(environment)
            if env_state.status == PolicyEnvironmentStatus.DISABLED:
                if self._cache_ttl > 0:
                    self._cache[cache_key] = _CacheEntry(None, self._cache_ttl)
                return None

        # Check ring state
        if self._ring_store is not None:
            from agent_app.governance.policy_ring import ReleaseRingStatus
            ring = await self._ring_store.get_by_name(environment, ring_name)
            if ring is not None and ring.status == ReleaseRingStatus.DISABLED:
                if self._cache_ttl > 0:
                    self._cache[cache_key] = _CacheEntry(None, self._cache_ttl)
                return None

        # Check ring assignment
        if self._ring_assignment_store is None:
            return None

        assignment = await self._ring_assignment_store.get_active(environment, ring_name)
        if assignment is None:
            if self._cache_ttl > 0:
                self._cache[cache_key] = _CacheEntry(None, self._cache_ttl)
            return None

        # Load activation
        activation = await self._activation_store.get(assignment.activation_id)
        if activation is None:
            raise KeyError(f"Activation '{assignment.activation_id}' referenced by ring assignment not found.")

        # Load bundle
        bundle = await self._bundle_store.get(activation.bundle_id)
        if bundle is None:
            raise KeyError(f"Bundle '{activation.bundle_id}' referenced by activation '{activation.activation_id}' not found.")

        # Verify config hash across assignment, activation, and bundle
        if assignment.config_hash != activation.config_hash:
            raise ValueError(f"config_hash mismatch between ring assignment and activation for '{assignment.assignment_id}'.")
        if activation.config_hash != bundle.config_hash:
            raise ValueError(f"config_hash mismatch between activation and bundle for '{activation.activation_id}'.")

        if self._cache_ttl > 0:
            self._cache[cache_key] = _CacheEntry(bundle, self._cache_ttl)
        return bundle

    async def require_active_bundle(self, environment: str) -> Any:
        # Phase 32: Check if environment is disabled first for better error message
        if self._environment_store is not None:
            from agent_app.governance.policy_environment import PolicyEnvironmentStatus
            env_state = await self._environment_store.get(environment)
            if env_state.status == PolicyEnvironmentStatus.DISABLED:
                raise RuntimeError(
                    f"Policy environment '{environment}' is disabled"
                    f"{f': {env_state.disabled_reason}' if env_state.disabled_reason else ''}. "
                    f"Enable the environment before requiring active policy."
                )
        bundle = await self.resolve_active_bundle(environment)
        if bundle is None:
            raise KeyError(f"No active policy bundle for environment '{environment}'. Activate a bundle before requiring it.")
        return bundle

    async def require_active_bundle_for_ring(self, environment: str, ring_name: str) -> Any:
        """Require an active policy bundle for environment + ring.

        Raises RuntimeError if environment or ring is disabled.
        Raises KeyError if no assignment or bundle found.
        """
        # Check environment
        if self._environment_store is not None:
            from agent_app.governance.policy_environment import PolicyEnvironmentStatus
            env_state = await self._environment_store.get(environment)
            if env_state.status == PolicyEnvironmentStatus.DISABLED:
                raise RuntimeError(
                    f"Policy environment '{environment}' is disabled"
                    f"{f': {env_state.disabled_reason}' if env_state.disabled_reason else ''}."
                )

        # Check ring
        if self._ring_store is not None:
            from agent_app.governance.policy_ring import ReleaseRingStatus
            ring = await self._ring_store.get_by_name(environment, ring_name)
            if ring is not None and ring.status == ReleaseRingStatus.DISABLED:
                raise RuntimeError(f"Ring '{ring_name}' is disabled in environment '{environment}'.")

        bundle = await self.resolve_active_bundle_for_ring(environment, ring_name)
        if bundle is None:
            raise KeyError(f"No active policy bundle for environment '{environment}' ring '{ring_name}'.")
        return bundle

    def cache_status(self) -> dict[str, Any]:
        """Return cache status information."""
        keys: list[str] = []
        for k in self._cache:
            if isinstance(k, tuple):
                keys.append(f"{k[0]}:{k[1]}")
            else:
                keys.append(k)
        return {
            "entries": len(self._cache),
            "keys": keys,
            "ttl": self._cache_ttl,
        }

    def refresh(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> None:
        """Clear cache for target. If both None, clear all."""
        if environment is None and ring_name is None:
            self._cache.clear()
        elif environment is not None and ring_name is not None:
            self._cache.pop((environment, ring_name), None)
        elif environment is not None:
            # Clear env key + all ring tuple keys for that env
            self._cache.pop(environment, None)
            keys_to_remove = [k for k in self._cache if isinstance(k, tuple) and k[0] == environment]
            for k in keys_to_remove:
                del self._cache[k]
        # ring_name alone without environment: no-op (need env for cache key)

    def clear_cache(
        self,
        environment: str | None = None,
        ring_name: str | None = None,
    ) -> None:
        """Clear cache. If both None, clear all. If environment, clear env + ring keys. If env+ring, clear specific."""
        if environment is None and ring_name is None:
            self._cache.clear()
        elif environment is not None and ring_name is not None:
            self._cache.pop((environment, ring_name), None)
        elif environment is not None:
            self._cache.pop(environment, None)
            keys_to_remove = [k for k in self._cache if isinstance(k, tuple) and k[0] == environment]
            for k in keys_to_remove:
                del self._cache[k]
