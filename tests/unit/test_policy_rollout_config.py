"""Tests for config schema Phase 35: rollout store config and loader wiring."""
import pytest
from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutStoreConfig,
)


class TestRolloutStoreConfig:
    def test_rollout_store_config_defaults(self):
        """RolloutStoreConfig defaults to type='memory', path=None."""
        cfg = RolloutStoreConfig()
        assert cfg.type == "memory"
        assert cfg.path is None

    def test_rollout_store_config_sqlite(self):
        """RolloutStoreConfig with type='sqlite' and a path."""
        cfg = RolloutStoreConfig(
            type="sqlite",
            path=".agent_app/rollouts.db",
        )
        assert cfg.type == "sqlite"
        assert cfg.path == ".agent_app/rollouts.db"


class TestPolicyReleaseConfigRollouts:
    def test_backward_compat_no_rollouts(self):
        """PolicyReleaseConfig without rollouts field still works."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
        )
        assert cfg.rollouts is None
        # Existing fields still accessible
        assert cfg.bundles is not None
        assert cfg.gates is not None

    def test_rollout_field_in_release_config(self):
        """PolicyReleaseConfig with rollouts config."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(type="memory"),
            gates=PolicyReleaseStoreConfig(type="memory"),
            rollouts=RolloutStoreConfig(
                type="sqlite",
                path=".agent_app/rollouts.db",
            ),
        )
        assert cfg.rollouts is not None
        assert cfg.rollouts.type == "sqlite"
        assert cfg.rollouts.path == ".agent_app/rollouts.db"


class TestRolloutLoaderWiring:
    def test_rollout_store_wired(self):
        """When rollouts config is provided, rollout_store is not None."""
        from agent_app.config.loader import build_app
        import tempfile
        import os

        yaml_content = """
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
    rollouts:
      type: memory
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app.rollout_store is not None
            finally:
                os.unlink(f.name)

    def test_rollout_service_wired(self):
        """When rollouts config is provided, rollout_service is not None."""
        from agent_app.config.loader import build_app
        import tempfile
        import os

        yaml_content = """
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
    rollouts:
      type: memory
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app.rollout_service is not None
            finally:
                os.unlink(f.name)
