"""Config tests for Phase 16.3 DagLeaseConfig metrics/health."""

from __future__ import annotations

import pytest

from agent_app.config.schema import (
    DagLeaseConfig,
    DagLeaseHealthConfig,
    DagLeaseMetricsConfig,
    RuntimeConfig,
)


class TestDagLeaseMetricsConfig:
    """Tests for DagLeaseMetricsConfig."""

    def test_default_disabled(self):
        cfg = DagLeaseMetricsConfig()
        assert cfg.enabled is False

    def test_enabled_true(self):
        cfg = DagLeaseMetricsConfig(enabled=True)
        assert cfg.enabled is True


class TestDagLeaseHealthConfig:
    """Tests for DagLeaseHealthConfig."""

    def test_default_enabled(self):
        cfg = DagLeaseHealthConfig()
        assert cfg.enabled is True

    def test_disabled(self):
        cfg = DagLeaseHealthConfig(enabled=False)
        assert cfg.enabled is False


class TestDagLeaseConfigPhase16_3:
    """Tests for DagLeaseConfig with metrics/health."""

    def test_default_config_no_metrics_no_health(self):
        cfg = DagLeaseConfig()
        assert cfg.backend == "state_store"
        assert cfg.metrics is None
        assert cfg.health is None

    def test_config_with_metrics_enabled(self):
        cfg = DagLeaseConfig(metrics={"enabled": True})
        assert cfg.metrics is not None
        assert cfg.metrics.enabled is True

    def test_config_with_metrics_disabled(self):
        cfg = DagLeaseConfig(metrics={"enabled": False})
        assert cfg.metrics is not None
        assert cfg.metrics.enabled is False

    def test_config_with_health_enabled(self):
        cfg = DagLeaseConfig(health={"enabled": True})
        assert cfg.health is not None
        assert cfg.health.enabled is True

    def test_config_with_health_disabled(self):
        cfg = DagLeaseConfig(health={"enabled": False})
        assert cfg.health is not None
        assert cfg.health.enabled is False

    def test_config_with_both(self):
        cfg = DagLeaseConfig(
            backend="sqlite",
            db_path="/tmp/leases.db",
            metrics={"enabled": True},
            health={"enabled": True},
        )
        assert cfg.backend == "sqlite"
        assert cfg.metrics.enabled is True
        assert cfg.health.enabled is True

    def test_runtime_config_with_lease_metrics(self):
        cfg = RuntimeConfig(
            dag_lease_config={
                "backend": "memory",
                "metrics": {"enabled": True},
            }
        )
        assert cfg.dag_lease_config is not None
        assert cfg.dag_lease_config.metrics is not None
        assert cfg.dag_lease_config.metrics.enabled is True

    def test_runtime_config_with_lease_health(self):
        cfg = RuntimeConfig(
            dag_lease_config={
                "backend": "memory",
                "health": {"enabled": False},
            }
        )
        assert cfg.dag_lease_config.health is not None
        assert cfg.dag_lease_config.health.enabled is False

    def test_old_config_still_valid(self):
        """Old dag_lease config without metrics/health still works."""
        cfg = RuntimeConfig(dag_lease={"backend": "state_store"})
        assert cfg.dag_lease_config.backend == "state_store"
        assert cfg.dag_lease_config.metrics is None
        assert cfg.dag_lease_config.health is None

    def test_flat_dag_lease_with_metrics_normalized(self):
        cfg = RuntimeConfig(
            dag_lease={
                "backend": "sqlite",
                "metrics": {"enabled": True},
                "health": {"enabled": True},
            }
        )
        assert cfg.dag_lease_config.backend == "sqlite"
        assert cfg.dag_lease_config.metrics.enabled is True
        assert cfg.dag_lease_config.health.enabled is True
