"""Phase 46 Task 6: Federation config schema, loader wiring, AgentApp properties."""

from __future__ import annotations

import textwrap

import pytest

from agent_app.config.loader import build_app, load_config
from agent_app.config.schema import (
    PolicyReleaseStoreConfig,
    RolloutFederationConfig,
    RolloutFederationConflictPolicyConfig,
)
from agent_app.core.app import AgentApp
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED


class TestRolloutFederationConfig:
    def test_rollout_federation_config_defaults(self) -> None:
        cfg = RolloutFederationConfig()
        assert cfg.enabled is False
        assert cfg.target_store is None
        assert cfg.plan_store is None
        assert cfg.conflict_policy.fail_on_error is True
        assert cfg.conflict_policy.warn_on_bundle_conflict is True

    def test_rollout_federation_config_with_sqlite_stores(self) -> None:
        cfg = RolloutFederationConfig(
            enabled=True,
            target_store=PolicyReleaseStoreConfig(
                type="sqlite",
                path=".agent_app/federated_rollout_targets.db",
            ),
            plan_store=PolicyReleaseStoreConfig(
                type="sqlite",
                path=".agent_app/federated_rollout_plans.db",
            ),
            conflict_policy=RolloutFederationConflictPolicyConfig(
                fail_on_error=True,
                warn_on_bundle_conflict=False,
            ),
        )
        assert cfg.enabled is True
        assert cfg.target_store.type == "sqlite"
        assert cfg.plan_store.path == ".agent_app/federated_rollout_plans.db"
        assert cfg.conflict_policy.warn_on_bundle_conflict is False

    def test_phase45_config_still_loads_without_federation(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollout_history:
                      enabled: true
                      store:
                        type: memory
                """
            )
        )
        cfg = load_config(config_path)
        assert cfg.governance.policy_release.rollout_history.enabled is True
        assert cfg.governance.policy_release.rollout_federation is None


class TestFederationRBAC:
    def test_federation_permissions_exist(self) -> None:
        expected = {
            "FEDERATION_TARGET_CREATE": "policy.federation.target.create",
            "FEDERATION_TARGET_VIEW": "policy.federation.target.view",
            "FEDERATION_TARGET_ENABLE": "policy.federation.target.enable",
            "FEDERATION_TARGET_DISABLE": "policy.federation.target.disable",
            "FEDERATION_PLAN_CREATE": "policy.federation.plan.create",
            "FEDERATION_PLAN_START": "policy.federation.plan.start",
            "FEDERATION_PLAN_EXECUTE": "policy.federation.plan.execute",
            "FEDERATION_PLAN_CANCEL": "policy.federation.plan.cancel",
            "FEDERATION_PLAN_VIEW": "policy.federation.plan.view",
            "FEDERATION_CONFLICT_VIEW": "policy.federation.conflict.view",
        }
        for name, value in expected.items():
            assert getattr(PolicyReleasePermission, name).value == value

    def test_federation_view_permissions_default_allowed(self) -> None:
        assert PolicyReleasePermission.FEDERATION_TARGET_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_PLAN_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_CONFLICT_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.FEDERATION_PLAN_CREATE not in _DEFAULT_ALLOWED


class TestFederationChangeEvents:
    def test_federation_change_events_exist(self) -> None:
        assert PolicyChangeEventType.FEDERATION_TARGET_CREATED.value == "policy.federation.target.created"
        assert PolicyChangeEventType.FEDERATION_TARGET_ENABLED.value == "policy.federation.target.enabled"
        assert PolicyChangeEventType.FEDERATION_TARGET_DISABLED.value == "policy.federation.target.disabled"
        assert PolicyChangeEventType.FEDERATION_PLAN_CREATED.value == "policy.federation.plan.created"
        assert PolicyChangeEventType.FEDERATION_PLAN_STARTED.value == "policy.federation.plan.started"
        assert PolicyChangeEventType.FEDERATION_PLAN_COMPLETED.value == "policy.federation.plan.completed"
        assert PolicyChangeEventType.FEDERATION_PLAN_FAILED.value == "policy.federation.plan.failed"
        assert PolicyChangeEventType.FEDERATION_PLAN_CANCELLED.value == "policy.federation.plan.cancelled"
        assert PolicyChangeEventType.FEDERATION_CONFLICT_DETECTED.value == "policy.federation.conflict.detected"


class TestAgentAppFederationProperties:
    def test_agent_app_federation_properties(self) -> None:
        app = AgentApp()
        target_store = object()
        plan_store = object()
        service = object()
        app.federated_rollout_target_store = target_store
        app.federated_rollout_plan_store = plan_store
        app.rollout_federation_service = service
        assert app.federated_rollout_target_store is target_store
        assert app.federated_rollout_plan_store is plan_store
        assert app.rollout_federation_service is service


class TestLoaderFederationWiring:
    def test_missing_federation_config_preserves_behavior(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                """
            )
        )
        app = build_app(config_path)
        assert getattr(app, "rollout_federation_service", None) is None

    def test_enabled_federation_config_wires_service(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        config_path.write_text(
            textwrap.dedent(
                """\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollouts:
                      type: memory
                    rollout_federation:
                      enabled: true
                      target_store:
                        type: memory
                      plan_store:
                        type: memory
                      conflict_policy:
                        fail_on_error: true
                        warn_on_bundle_conflict: true
                """
            )
        )
        app = build_app(config_path)
        assert app.federated_rollout_target_store is not None
        assert app.federated_rollout_plan_store is not None
        assert app.rollout_federation_service is not None

    def test_enabled_sqlite_federation_config_wires_sqlite_stores(self, tmp_path) -> None:
        config_path = tmp_path / "agentapp.yaml"
        targets_db = tmp_path / "targets.db"
        plans_db = tmp_path / "plans.db"
        config_path.write_text(
            textwrap.dedent(
                f"""\
                governance:
                  policy_release:
                    bundles:
                      type: memory
                    gates:
                      type: memory
                    rollouts:
                      type: memory
                    rollout_federation:
                      enabled: true
                      target_store:
                        type: sqlite
                        path: {targets_db}
                      plan_store:
                        type: sqlite
                        path: {plans_db}
                """
            )
        )
        app = build_app(config_path)
        assert type(app.federated_rollout_target_store).__name__ == "SQLiteFederatedRolloutTargetStore"
        assert type(app.federated_rollout_plan_store).__name__ == "SQLiteFederatedRolloutPlanStore"
