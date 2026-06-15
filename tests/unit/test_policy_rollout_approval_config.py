"""Tests for config schema Phase 36/37: rollout approval store config, policy config, and loader wiring."""
import pytest
from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    RolloutStoreConfig,
    RolloutApprovalConfig,
    RolloutApprovalPolicyConfig,
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


class TestRolloutApprovalPolicyConfig:
    def test_policy_config_defaults(self):
        """RolloutApprovalPolicyConfig defaults to SINGLE, required_approvals=1."""
        cfg = RolloutApprovalPolicyConfig()
        assert cfg.policy_type == "single"
        assert cfg.required_approvals == 1
        assert cfg.allowed_approver_roles == []
        assert cfg.allowed_approver_permissions == []
        assert cfg.prohibit_requester_approval is True
        assert cfg.prohibit_creator_approval is False
        assert cfg.expires_after_seconds is None
        assert cfg.require_reason is False

    def test_policy_config_quorum(self):
        """Quorum config with all fields set."""
        cfg = RolloutApprovalPolicyConfig(
            policy_type="quorum",
            required_approvals=3,
            allowed_approver_roles=["admin", "release_manager"],
            allowed_approver_permissions=["policy:approve"],
            prohibit_requester_approval=False,
            prohibit_creator_approval=True,
            expires_after_seconds=3600,
            require_reason=True,
        )
        assert cfg.policy_type == "quorum"
        assert cfg.required_approvals == 3
        assert cfg.allowed_approver_roles == ["admin", "release_manager"]
        assert cfg.allowed_approver_permissions == ["policy:approve"]
        assert cfg.prohibit_requester_approval is False
        assert cfg.prohibit_creator_approval is True
        assert cfg.expires_after_seconds == 3600
        assert cfg.require_reason is True


class TestApprovalConfigWithPolicy:
    def test_approval_config_with_policy(self):
        """RolloutApprovalConfig accepts policy field."""
        cfg = RolloutApprovalConfig(
            type="memory",
            policy=RolloutApprovalPolicyConfig(
                policy_type="quorum",
                required_approvals=2,
            ),
        )
        assert cfg.policy is not None
        assert cfg.policy.policy_type == "quorum"
        assert cfg.policy.required_approvals == 2

    def test_approval_config_without_policy(self):
        """RolloutApprovalConfig without policy field defaults to None (backward compat)."""
        cfg = RolloutApprovalConfig(type="memory")
        assert cfg.policy is None

    def test_require_reason_inherited(self):
        """Parent require_reason maps into policy when policy doesn't explicitly set it."""
        cfg = RolloutApprovalConfig(
            type="memory",
            require_reason=True,
            policy=RolloutApprovalPolicyConfig(
                policy_type="single",
            ),
        )
        # The config itself stores the values; the loader does the mapping.
        # Verify the config stores both values correctly.
        assert cfg.require_reason is True
        assert cfg.policy.require_reason is False  # policy default
        # The loader should map parent require_reason into policy when policy's is False.

    def test_require_reason_policy_overrides_parent(self):
        """Policy require_reason=True should not be overridden by parent require_reason=False."""
        cfg = RolloutApprovalConfig(
            type="memory",
            require_reason=False,
            policy=RolloutApprovalPolicyConfig(
                policy_type="single",
                require_reason=True,
            ),
        )
        assert cfg.require_reason is False
        assert cfg.policy.require_reason is True


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

    def test_loader_wires_policy_from_config(self):
        """Build app with policy config, verify service gets approval_policy."""
        from agent_app.config.loader import build_app
        from agent_app.governance.policy_rollout_approval import (
            RolloutApprovalPolicy,
            RolloutApprovalPolicyType,
        )
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
        policy:
          policy_type: quorum
          required_approvals: 3
          allowed_approver_roles:
            - admin
            - release_manager
          allowed_approver_permissions:
            - policy:approve
          prohibit_requester_approval: false
          prohibit_creator_approval: true
          expires_after_seconds: 3600
          require_reason: true
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                policy = app._rollout_service._approval_policy
                assert policy is not None
                assert isinstance(policy, RolloutApprovalPolicy)
                assert policy.policy_type == RolloutApprovalPolicyType.QUORUM
                assert policy.required_approvals == 3
                assert policy.allowed_approver_roles == ["admin", "release_manager"]
                assert policy.allowed_approver_permissions == ["policy:approve"]
                assert policy.prohibit_requester_approval is False
                assert policy.prohibit_creator_approval is True
                assert policy.expires_after_seconds == 3600
                assert policy.require_reason is True
            finally:
                os.unlink(f.name)

    def test_no_policy_defaults_to_single(self):
        """If no policy config, service gets default SINGLE policy (None)."""
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
                # When no policy config is provided, approval_policy should be None
                # (the service uses its default SINGLE policy internally)
                assert app._rollout_service._approval_policy is None
            finally:
                os.unlink(f.name)

    def test_require_reason_inherited_into_policy(self):
        """Parent require_reason=True maps into policy's require_reason when policy doesn't set it."""
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
        policy:
          policy_type: single
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                policy = app._rollout_service._approval_policy
                assert policy is not None
                # require_reason should be inherited from parent config
                assert policy.require_reason is True
            finally:
                os.unlink(f.name)
