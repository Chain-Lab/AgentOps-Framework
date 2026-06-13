"""Tests for config schema Phase 32: environments store config."""
import pytest
from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseStoreConfig


class TestPhase32Config:
    def test_environments_config(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            environments=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.environments is not None
        assert cfg.environments.type == "memory"

    def test_environments_defaults_none(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.environments is None

    def test_phase32_backward_compat(self):
        """Phase 31 configs still load."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            activations=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.environments is None
        assert cfg.activations is not None

    def test_full_phase32_config(self):
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/bundles.db"),
            gates=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/gates.db"),
            activations=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/activations.db"),
            environments=PolicyReleaseStoreConfig(type="sqlite", path=".agent_app/environments.db"),
        )
        assert cfg.environments.type == "sqlite"
        assert cfg.environments.path == ".agent_app/environments.db"
