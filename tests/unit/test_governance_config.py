"""Tests for governance config schema."""

import pytest

from agent_app.config.schema import (
    ApprovalConfig,
    AuditConfig,
    GovernanceConfig,
    PermissionConfig,
)


class TestApprovalConfig:
    def test_default_memory(self):
        cfg = ApprovalConfig()
        assert cfg.type == "memory"
        assert cfg.path is None

    def test_sqlite_with_path(self):
        cfg = ApprovalConfig(type="sqlite", path=".agent_app/approvals.db")
        assert cfg.type == "sqlite"
        assert cfg.path == ".agent_app/approvals.db"


class TestAuditConfig:
    def test_default_memory(self):
        cfg = AuditConfig()
        assert cfg.type == "memory"

    def test_sqlite(self):
        cfg = AuditConfig(type="sqlite", path=".agent_app/audit.db")
        assert cfg.type == "sqlite"


class TestPermissionConfig:
    def test_default(self):
        cfg = PermissionConfig()
        assert cfg.mode == "default"


class TestGovernanceConfig:
    def test_defaults(self):
        cfg = GovernanceConfig()
        assert cfg.approvals.type == "memory"
        assert cfg.audit.type == "memory"
        assert cfg.permissions.mode == "default"

    def test_from_dict(self):
        cfg = GovernanceConfig(
            approvals={"type": "sqlite", "path": ".agent_app/approvals.db"},
            audit={"type": "sqlite", "path": ".agent_app/audit.db"},
            permissions={"mode": "default"},
        )
        assert cfg.approvals.type == "sqlite"
        assert cfg.audit.type == "sqlite"


class TestAppConfigWithGovernance:
    def test_governance_flat(self):
        from agent_app.config.schema import AppConfig

        raw = {
            "agents": [{"name": "bot", "instructions": "help"}],
            "governance": {
                "approvals": {"type": "sqlite", "path": ".agent_app/a.db"},
                "audit": {"type": "sqlite", "path": ".agent_app/au.db"},
            },
        }
        cfg = AppConfig(**raw)
        assert cfg.governance is not None
        assert cfg.governance.approvals.type == "sqlite"

    def test_no_governance_uses_defaults(self):
        from agent_app.config.schema import AppConfig

        raw = {"agents": [{"name": "bot", "instructions": "help"}]}
        cfg = AppConfig(**raw)
        assert cfg.governance is None
