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

    def __init__(self, bundle_store: Any, activation_store: PolicyActivationStore, cache_ttl_seconds: float = 0, environment_store: Any = None) -> None:
        self._bundle_store = bundle_store
        self._activation_store = activation_store
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}
        self._environment_store = environment_store

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

    def refresh(self, environment: str) -> None:
        self._cache.pop(environment, None)

    def clear_cache(self) -> None:
        self._cache.clear()
