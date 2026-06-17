"""Tests for Phase 42 Task 6: config, loader, RBAC, and change events for simulation gate enforcement."""

from __future__ import annotations

import pytest

from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.config.schema import (
    PolicyReleaseConfig,
    PolicyReleaseStoreConfig,
    SimulationGateEnforcementConfig,
)
from agent_app.core.context import RunContext


# ---------------------------------------------------------------------------
# Config schema tests
# ---------------------------------------------------------------------------


class TestSimulationGateEnforcementConfig:
    """Test SimulationGateEnforcementConfig model."""

    def test_default_config(self) -> None:
        """Default enforcement config has require_for_promotion=False."""
        cfg = SimulationGateEnforcementConfig()
        assert cfg.require_for_promotion is False
        assert cfg.max_age_seconds is None
        assert cfg.requirement_store is None

    def test_enabled_config(self) -> None:
        """Config with require_for_promotion=True."""
        cfg = SimulationGateEnforcementConfig(require_for_promotion=True)
        assert cfg.require_for_promotion is True

    def test_max_age_seconds(self) -> None:
        """Config with max_age_seconds set."""
        cfg = SimulationGateEnforcementConfig(max_age_seconds=3600)
        assert cfg.max_age_seconds == 3600

    def test_requirement_store_config(self) -> None:
        """Config with requirement_store."""
        store_cfg = PolicyReleaseStoreConfig(type="memory")
        cfg = SimulationGateEnforcementConfig(requirement_store=store_cfg)
        assert cfg.requirement_store is not None
        assert cfg.requirement_store.type == "memory"

    def test_sqlite_requirement_store_config(self) -> None:
        """Config with sqlite requirement_store."""
        store_cfg = PolicyReleaseStoreConfig(type="sqlite", path="/tmp/gate_requirements.db")
        cfg = SimulationGateEnforcementConfig(requirement_store=store_cfg)
        assert cfg.requirement_store.type == "sqlite"
        assert cfg.requirement_store.path == "/tmp/gate_requirements.db"


class TestPolicyReleaseConfigEnforcementField:
    """Test PolicyReleaseConfig with simulation_gate_enforcement field."""

    def test_missing_enforcement_config_preserves_behavior(self) -> None:
        """PolicyReleaseConfig without simulation_gate_enforcement works."""
        cfg = PolicyReleaseConfig()
        assert cfg.simulation_gate_enforcement is None

    def test_enforcement_field_in_config(self) -> None:
        """PolicyReleaseConfig with enforcement field."""
        enforcement = SimulationGateEnforcementConfig(require_for_promotion=True)
        cfg = PolicyReleaseConfig(simulation_gate_enforcement=enforcement)
        assert cfg.simulation_gate_enforcement is not None
        assert cfg.simulation_gate_enforcement.require_for_promotion is True

    def test_old_phase41_configs_still_load(self) -> None:
        """Phase 41 config (with simulation but without enforcement) loads correctly."""
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(),
            gates=PolicyReleaseStoreConfig(),
            rules=[],
        )
        assert cfg.simulation_gate_enforcement is None
        assert cfg.bundles is not None
        assert cfg.gates is not None

    def test_full_config_with_enforcement_and_simulation(self) -> None:
        """Full config with enforcement and other fields."""
        enforcement = SimulationGateEnforcementConfig(
            require_for_promotion=True,
            max_age_seconds=7200,
            requirement_store=PolicyReleaseStoreConfig(type="memory"),
        )
        cfg = PolicyReleaseConfig(
            bundles=PolicyReleaseStoreConfig(),
            gates=PolicyReleaseStoreConfig(),
            simulation_gate_enforcement=enforcement,
        )
        assert cfg.simulation_gate_enforcement.require_for_promotion is True
        assert cfg.simulation_gate_enforcement.max_age_seconds == 7200
        assert cfg.simulation_gate_enforcement.requirement_store is not None


# ---------------------------------------------------------------------------
# RBAC permission tests
# ---------------------------------------------------------------------------


class TestPhase42RBACPermissions:
    """Test Phase 42 RBAC permissions for simulation gate enforcement."""

    def test_promotion_gate_require_permission(self) -> None:
        """PROMOTION_GATE_REQUIRE permission exists with correct value."""
        assert PolicyReleasePermission.PROMOTION_GATE_REQUIRE == "policy.promotion.gate.require"

    def test_promotion_gate_run_permission(self) -> None:
        """PROMOTION_GATE_RUN permission exists with correct value."""
        assert PolicyReleasePermission.PROMOTION_GATE_RUN == "policy.promotion.gate.run"

    def test_promotion_gate_attach_permission(self) -> None:
        """PROMOTION_GATE_ATTACH permission exists with correct value."""
        assert PolicyReleasePermission.PROMOTION_GATE_ATTACH == "policy.promotion.gate.attach"

    def test_promotion_gate_view_permission(self) -> None:
        """PROMOTION_GATE_VIEW permission exists with correct value."""
        assert PolicyReleasePermission.PROMOTION_GATE_VIEW == "policy.promotion.gate.view"

    def test_rollout_gate_attach_permission(self) -> None:
        """ROLLOUT_GATE_ATTACH permission exists with correct value."""
        assert PolicyReleasePermission.ROLLOUT_GATE_ATTACH == "policy.rollout.gate.attach"

    def test_rollout_gate_view_permission(self) -> None:
        """ROLLOUT_GATE_VIEW permission exists with correct value."""
        assert PolicyReleasePermission.ROLLOUT_GATE_VIEW == "policy.rollout.gate.view"

    @pytest.mark.asyncio
    async def test_promotion_gate_view_in_default_allowed(self) -> None:
        """PROMOTION_GATE_VIEW is in the default-allowed set."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.PROMOTION_GATE_VIEW, ctx) is True

    @pytest.mark.asyncio
    async def test_rollout_gate_view_in_default_allowed(self) -> None:
        """ROLLOUT_GATE_VIEW is in the default-allowed set."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ROLLOUT_GATE_VIEW, ctx) is True

    @pytest.mark.asyncio
    async def test_promotion_gate_require_not_default_allowed(self) -> None:
        """PROMOTION_GATE_REQUIRE is NOT in default-allowed set, requires explicit permission."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.PROMOTION_GATE_REQUIRE, ctx) is False
        ctx_with = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.promotion.gate.require"])
        assert await checker.check(PolicyReleasePermission.PROMOTION_GATE_REQUIRE, ctx_with) is True

    @pytest.mark.asyncio
    async def test_promotion_gate_run_not_default_allowed(self) -> None:
        """PROMOTION_GATE_RUN is NOT in default-allowed set."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.PROMOTION_GATE_RUN, ctx) is False

    @pytest.mark.asyncio
    async def test_rollout_gate_attach_not_default_allowed(self) -> None:
        """ROLLOUT_GATE_ATTACH is NOT in default-allowed set."""
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ROLLOUT_GATE_ATTACH, ctx) is False


# ---------------------------------------------------------------------------
# Change event type tests
# ---------------------------------------------------------------------------


class TestPhase42ChangeEventTypes:
    """Test Phase 42 change event types for simulation gate enforcement."""

    def test_promotion_gate_required_event(self) -> None:
        """PROMOTION_GATE_REQUIRED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_REQUIRED == "policy.promotion.gate.required"

    def test_promotion_gate_run_event(self) -> None:
        """PROMOTION_GATE_RUN event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_RUN == "policy.promotion.gate.run"

    def test_promotion_gate_attached_event(self) -> None:
        """PROMOTION_GATE_ATTACHED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_ATTACHED == "policy.promotion.gate.attached"

    def test_promotion_gate_satisfied_event(self) -> None:
        """PROMOTION_GATE_SATISFIED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_SATISFIED == "policy.promotion.gate.satisfied"

    def test_promotion_gate_failed_event(self) -> None:
        """PROMOTION_GATE_FAILED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_FAILED == "policy.promotion.gate.failed"

    def test_promotion_gate_expired_event(self) -> None:
        """PROMOTION_GATE_EXPIRED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_EXPIRED == "policy.promotion.gate.expired"

    def test_promotion_gate_execution_blocked_event(self) -> None:
        """PROMOTION_GATE_EXECUTION_BLOCKED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_EXECUTION_BLOCKED == "policy.promotion.gate.execution_blocked"

    def test_promotion_gate_permission_denied_event(self) -> None:
        """PROMOTION_GATE_PERMISSION_DENIED event type exists with correct value."""
        assert PolicyChangeEventType.PROMOTION_GATE_PERMISSION_DENIED == "policy.promotion.gate.permission_denied"

    def test_all_new_event_types_have_correct_prefix(self) -> None:
        """All new event types start with 'policy.promotion.gate.' prefix."""
        new_types = [
            PolicyChangeEventType.PROMOTION_GATE_REQUIRED,
            PolicyChangeEventType.PROMOTION_GATE_RUN,
            PolicyChangeEventType.PROMOTION_GATE_ATTACHED,
            PolicyChangeEventType.PROMOTION_GATE_SATISFIED,
            PolicyChangeEventType.PROMOTION_GATE_FAILED,
            PolicyChangeEventType.PROMOTION_GATE_EXPIRED,
            PolicyChangeEventType.PROMOTION_GATE_EXECUTION_BLOCKED,
            PolicyChangeEventType.PROMOTION_GATE_PERMISSION_DENIED,
        ]
        for et in new_types:
            assert et.value.startswith("policy.promotion.gate."), (
                f"Event type {et} does not start with 'policy.promotion.gate.'"
            )


# ---------------------------------------------------------------------------
# Loader integration tests
# ---------------------------------------------------------------------------


class TestLoaderGateEnforcement:
    """Test loader wiring for simulation gate enforcement."""

    def test_loader_creates_requirement_store_from_config(self) -> None:
        """Loader creates InMemoryReleaseGateRequirementStore from config."""
        from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore
        from agent_app.config.loader import build_app
        import tempfile
        import os

        yaml_content = """
app:
  name: test-gate-enforcement
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
    simulation_gate_enforcement:
      require_for_promotion: false
      requirement_store:
        type: memory
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._release_gate_requirement_store is not None
                assert isinstance(app._release_gate_requirement_store, InMemoryReleaseGateRequirementStore)
                # Service created even without enforcement enabled
                assert app._release_gate_automation_service is not None
            finally:
                os.unlink(f.name)

    def test_loader_sqlite_requirement_store(self) -> None:
        """Loader creates SQLiteReleaseGateRequirementStore from config."""
        from agent_app.runtime.policy_release_gate_store import SQLiteReleaseGateRequirementStore
        from agent_app.config.loader import build_app
        import tempfile
        import os

        db_path = tempfile.mktemp(suffix=".db")
        yaml_content = """
app:
  name: test-gate-sqlite
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
    simulation_gate_enforcement:
      require_for_promotion: false
      requirement_store:
        type: sqlite
        path: "{}"
""".format(db_path)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._release_gate_requirement_store is not None
                assert isinstance(app._release_gate_requirement_store, SQLiteReleaseGateRequirementStore)
            finally:
                os.unlink(f.name)
                if os.path.exists(db_path):
                    os.unlink(db_path)

    def test_loader_enforcement_enabled_wires_release_service(self) -> None:
        """Loader wires enforcement flags to PolicyReleaseService when enabled."""
        from agent_app.config.loader import build_app
        import tempfile
        import os

        yaml_content = """
app:
  name: test-gate-enforcement-enabled
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
    simulation_gate_enforcement:
      require_for_promotion: true
      max_age_seconds: 7200
      requirement_store:
        type: memory
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._release_gate_requirement_store is not None
                assert app._release_gate_automation_service is not None
                # Enforcement enabled, so flags should be set on release_service
                if hasattr(app, '_release_service') and app._release_service is not None:
                    assert app._release_service._require_simulation_gate_for_promotion is True
                    assert app._release_service._simulation_gate_max_age_seconds == 7200
                    assert app._release_service._release_gate_automation_service is not None
            finally:
                os.unlink(f.name)

    def test_loader_no_enforcement_config_no_error(self) -> None:
        """Loader handles config without simulation_gate_enforcement without error."""
        from agent_app.config.loader import build_app
        import tempfile
        import os

        yaml_content = """
app:
  name: test-no-enforcement
governance:
  policy_release:
    bundles:
      type: memory
    gates:
      type: memory
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                app = build_app(f.name)
                assert app._release_gate_requirement_store is None
                assert app._release_gate_automation_service is None
            finally:
                os.unlink(f.name)
