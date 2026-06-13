"""Tests for config schema Phase 33: rings and ring assignments config."""
import pytest
from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseStoreConfig, PolicyReleaseRuntimeConfig


class TestPhase33Config:
    def test_rings_config(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            rings=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.rings is not None
        assert cfg.rings.type == "memory"

    def test_ring_assignments_config(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            ring_assignments=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.ring_assignments is not None

    def test_defaults_none(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.rings is None
        assert cfg.ring_assignments is None

    def test_runtime_ring_field(self):
        cfg = PolicyReleaseRuntimeConfig(ring="canary")
        assert cfg.ring == "canary"

    def test_runtime_ring_default_none(self):
        cfg = PolicyReleaseRuntimeConfig()
        assert cfg.ring is None

    def test_backward_compat(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            environments=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.rings is None
        assert cfg.ring_assignments is None
