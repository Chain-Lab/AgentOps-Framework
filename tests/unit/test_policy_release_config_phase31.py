"""Tests for PolicyReleaseConfig Phase 31 — activations and runtime fields."""

from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseRuntimeConfig


def test_default_runtime_config():
    cfg = PolicyReleaseConfig()
    assert cfg.runtime.environment == "dev"
    assert cfg.runtime.require_active_policy is False
    assert cfg.runtime.cache_ttl_seconds == 5


def test_activations_defaults_to_none():
    cfg = PolicyReleaseConfig()
    assert cfg.activations is None


def test_activations_can_be_set():
    cfg = PolicyReleaseConfig(
        activations={"type": "sqlite", "path": ".agent_app/activations.db"}
    )
    assert cfg.activations.type == "sqlite"
    assert cfg.activations.path == ".agent_app/activations.db"


def test_existing_phase30_config_still_loads():
    cfg = PolicyReleaseConfig(
        require_promotion_approval=True,
        allow_gate_bypass=False,
    )
    assert cfg.require_promotion_approval is True
    assert cfg.runtime.environment == "dev"
    assert cfg.activations is None
