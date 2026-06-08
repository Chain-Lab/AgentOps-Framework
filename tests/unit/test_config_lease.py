"""Config tests for Phase 16.2 DagLeaseConfig."""

from __future__ import annotations

import pytest

from agent_app.config.schema import DagLeaseConfig, RuntimeConfig


class TestDagLeaseConfig:
    """Tests for DagLeaseConfig validation and defaults."""

    def test_default_config(self):
        """Default config uses state_store backend."""
        cfg = DagLeaseConfig()
        assert cfg.backend == "state_store"
        assert cfg.ttl_seconds == 300
        assert cfg.allow_steal_expired is True
        assert cfg.renew_before_seconds == 60
        assert cfg.db_path is None

    def test_memory_backend(self):
        cfg = DagLeaseConfig(backend="memory")
        assert cfg.backend == "memory"

    def test_sqlite_backend(self):
        cfg = DagLeaseConfig(backend="sqlite", db_path="/tmp/leases.db")
        assert cfg.backend == "sqlite"
        assert cfg.db_path == "/tmp/leases.db"

    def test_invalid_backend_raises(self):
        with pytest.raises(Exception):  # Pydantic ValidationError
            DagLeaseConfig(backend="redis")

    def test_ttl_seconds_must_be_positive(self):
        with pytest.raises(Exception):
            DagLeaseConfig(ttl_seconds=0)

    def test_runtime_config_default(self):
        """RuntimeConfig accepts DagLeaseConfig."""
        cfg = RuntimeConfig()
        assert cfg.dag_lease_config is None

    def test_runtime_config_with_lease(self):
        cfg = RuntimeConfig(dag_lease_config={"backend": "memory"})
        assert cfg.dag_lease_config is not None
        assert cfg.dag_lease_config.backend == "memory"

    def test_flat_dag_lease_normalized(self):
        """Flat 'dag_lease' key is normalized to 'dag_lease_config'."""
        cfg = RuntimeConfig(dag_lease={"backend": "sqlite", "db_path": "/tmp/x.db"})
        assert cfg.dag_lease_config is not None
        assert cfg.dag_lease_config.backend == "sqlite"
        assert cfg.dag_lease_config.db_path == "/tmp/x.db"

    def test_old_config_remains_valid(self):
        """Old config without dag_lease still works."""
        cfg = RuntimeConfig(backend="dry_run")
        assert cfg.backend == "dry_run"
        assert cfg.dag_lease_config is None

    def test_state_store_backend_valid(self):
        cfg = DagLeaseConfig(backend="state_store")
        assert cfg.backend == "state_store"

    def test_sqlite_without_db_path_ok_at_config_level(self):
        """db_path is not required at config level (only at factory level)."""
        cfg = DagLeaseConfig(backend="sqlite")
        assert cfg.backend == "sqlite"
        assert cfg.db_path is None
