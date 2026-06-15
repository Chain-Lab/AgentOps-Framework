"""Tests for config schema Phase 36: rollout approval store config and loader wiring."""
import pytest
from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutStoreConfig,
    RolloutApprovalConfig,
)


class TestRolloutApprovalConfig:
    def test_rollout_approval_config_defaults(self):
        """RolloutApprovalConfig defaults to type='memory', path=None, require_reason=False."""
        cfg = RolloutApprovalConfig()
        assert cfg.type == "memory"
        assert cfg.path is None
        assert cfg.require_reason is False

    def test_rollout_approval_config_sqlite(self):
        """RolloutApprovalConfig with type='sqlite', path, and require_reason=True."""
        cfg = RolloutApprovalConfig(
            type="sqlite",
            path=".agent_app/approvals.db",
            require_reason=True,
        )
        assert cfg.type == "sqlite"
        assert cfg.path == ".agent_app/approvals.db"
        assert cfg.require_reason is True


class TestRolloutStoreConfigWithApprovals:
    def test_rollout_store_config_with_approvals(self):
        """RolloutStoreConfig with approvals field set."""
        cfg = RolloutStoreConfig(
            type="memory",
            approvals=RolloutApprovalConfig(
                type="sqlite",
                path=".agent_app/approvals.db",
                require_reason=True,
            ),
        )
        assert cfg.approvals is not None
        assert cfg.approvals.type == "sqlite"
        assert cfg.approvals.path == ".agent_app/approvals.db"
        assert cfg.approvals.require_reason is True

    def test_backward_compat_no_approvals(self):
        """RolloutStoreConfig without approvals still loads (approvals defaults to None)."""
        cfg = RolloutStoreConfig(type="memory")
        assert cfg.approvals is None


class TestApprovalLoaderWiring:
    def test_loader_wires_approval_store(self):
        """Build app from config with approvals section, verify approval store is wired."""
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
      approvals:
        type: memory
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._rollout_approval_store is not None
                assert app._rollout_service._approval_store is not None
            finally:
                os.unlink(f.name)

    def test_loader_require_reason(self):
        """Build app with require_reason=True, verify service._approval_require_reason is True."""
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
      approvals:
        type: memory
        require_reason: true
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._rollout_service._approval_require_reason is True
            finally:
                os.unlink(f.name)
