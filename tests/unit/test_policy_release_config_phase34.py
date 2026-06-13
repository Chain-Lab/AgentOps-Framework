"""Tests for config schema Phase 34: change events, reload, and routing config."""
import pytest
from agent_app.config.schema import (
    PolicyChangeEventsConfig,
    PolicyReloadConfig,
    PolicyReleaseConfig,
    PolicyReleaseRuntimeConfig,
    PolicyReleaseStoreConfig,
)


class TestPhase34Config:
    def test_change_events_config(self):
        """PolicyChangeEventsConfig with type/path/strict."""
        cfg = PolicyChangeEventsConfig(
            type="sqlite",
            path=".agent_app/change_events.db",
            strict=True,
        )
        assert cfg.type == "sqlite"
        assert cfg.path == ".agent_app/change_events.db"
        assert cfg.strict is True

    def test_change_events_config_defaults(self):
        """PolicyChangeEventsConfig defaults: type=memory, path=None, strict=False."""
        cfg = PolicyChangeEventsConfig()
        assert cfg.type == "memory"
        assert cfg.path is None
        assert cfg.strict is False

    def test_reload_config(self):
        """PolicyReloadConfig with auto_refresh."""
        cfg = PolicyReloadConfig(auto_refresh=False)
        assert cfg.auto_refresh is False

    def test_reload_config_defaults(self):
        """PolicyReloadConfig defaults: auto_refresh=True."""
        cfg = PolicyReloadConfig()
        assert cfg.auto_refresh is True

    def test_routing_config_in_runtime(self):
        """PolicyReleaseRuntimeConfig with routing dict."""
        routing_dict = {
            "enabled": True,
            "canary_percentage": 20,
            "canary_ring": "canary",
            "stable_ring": "stable",
            "hash_key": "actor_id",
        }
        cfg = PolicyReleaseRuntimeConfig(routing=routing_dict)
        assert cfg.routing is not None
        assert cfg.routing["enabled"] is True
        assert cfg.routing["canary_percentage"] == 20

    def test_routing_config_default_none(self):
        """PolicyReleaseRuntimeConfig routing defaults to None."""
        cfg = PolicyReleaseRuntimeConfig()
        assert cfg.routing is None

    def test_backward_compat_all_none(self):
        """Existing Phase 31/32/33 configs still load (no change_events, reload, routing)."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            activations=PolicyReleaseStoreConfig(type="memory"),
            environments=PolicyReleaseStoreConfig(type="memory"),
            rings=PolicyReleaseStoreConfig(type="memory"),
            ring_assignments=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.change_events is None
        assert cfg.reload is None
        assert cfg.runtime.routing is None
        # Phase 31/32/33 fields still accessible
        assert cfg.activations is not None
        assert cfg.environments is not None
        assert cfg.rings is not None
        assert cfg.ring_assignments is not None

    def test_full_config(self):
        """Full config with all Phase 34 fields."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            change_events=PolicyChangeEventsConfig(
                type="sqlite",
                path=".agent_app/change_events.db",
                strict=True,
            ),
            reload=PolicyReloadConfig(auto_refresh=False),
            runtime=PolicyReleaseRuntimeConfig(
                environment="production",
                ring="stable",
                routing={
                    "enabled": True,
                    "canary_percentage": 10,
                    "canary_ring": "canary",
                    "stable_ring": "stable",
                    "hash_key": "user_id",
                },
            ),
        )
        assert cfg.change_events is not None
        assert cfg.change_events.type == "sqlite"
        assert cfg.change_events.strict is True
        assert cfg.reload is not None
        assert cfg.reload.auto_refresh is False
        assert cfg.runtime.routing is not None
        assert cfg.runtime.routing["canary_percentage"] == 10
        assert cfg.runtime.ring == "stable"
