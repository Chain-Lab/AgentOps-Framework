"""Tests for Phase 17 auto-recovery policy model.

Tests cover:
  - Default values (disabled, dry_run, no completed)
  - Custom construction
  - Invalid values rejected
  - Serialization
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_app.runtime.recovery_models import AutoRecoveryPolicy


class TestAutoRecoveryPolicyDefaults:
    """Default values are conservative."""

    def test_default_disabled(self):
        policy = AutoRecoveryPolicy()
        assert policy.enabled is False

    def test_default_dry_run(self):
        policy = AutoRecoveryPolicy()
        assert policy.dry_run is True

    def test_default_interval(self):
        policy = AutoRecoveryPolicy()
        assert policy.interval_seconds == 30.0

    def test_default_stale_after(self):
        policy = AutoRecoveryPolicy()
        assert policy.stale_after_seconds == 300.0

    def test_default_statuses(self):
        policy = AutoRecoveryPolicy()
        assert "running" in policy.statuses
        assert "failed" in policy.statuses
        assert "compensating" in policy.statuses
        assert "completed" not in policy.statuses

    def test_default_include_completed_false(self):
        policy = AutoRecoveryPolicy()
        assert policy.include_completed is False

    def test_default_max_candidates(self):
        policy = AutoRecoveryPolicy()
        assert policy.max_candidates_per_scan == 50

    def test_default_max_recoveries(self):
        policy = AutoRecoveryPolicy()
        assert policy.max_recoveries_per_scan == 5

    def test_default_max_concurrent(self):
        policy = AutoRecoveryPolicy()
        assert policy.max_concurrent_recoveries == 1

    def test_default_recover_flags(self):
        policy = AutoRecoveryPolicy()
        assert policy.recover_failed is True
        assert policy.recover_stale_running is True
        assert policy.recover_compensating is True

    def test_default_workflow_name_none(self):
        policy = AutoRecoveryPolicy()
        assert policy.workflow_name is None

    def test_default_tenant_id_none(self):
        policy = AutoRecoveryPolicy()
        assert policy.tenant_id is None


class TestAutoRecoveryPolicyCustom:
    """Custom construction works."""

    def test_enabled_true(self):
        policy = AutoRecoveryPolicy(enabled=True)
        assert policy.enabled is True

    def test_dry_run_false(self):
        policy = AutoRecoveryPolicy(dry_run=False)
        assert policy.dry_run is False

    def test_custom_interval(self):
        policy = AutoRecoveryPolicy(interval_seconds=60.0)
        assert policy.interval_seconds == 60.0

    def test_custom_statuses(self):
        policy = AutoRecoveryPolicy(statuses=["failed", "compensating"])
        assert "failed" in policy.statuses
        assert "compensating" in policy.statuses
        assert "running" not in policy.statuses

    def test_include_completed_true(self):
        policy = AutoRecoveryPolicy(include_completed=True)
        assert policy.include_completed is True

    def test_max_values(self):
        policy = AutoRecoveryPolicy(
            max_candidates_per_scan=200,
            max_recoveries_per_scan=10,
            max_concurrent_recoveries=4,
        )
        assert policy.max_candidates_per_scan == 200
        assert policy.max_recoveries_per_scan == 10
        assert policy.max_concurrent_recoveries == 4

    def test_workflow_name_filter(self):
        policy = AutoRecoveryPolicy(workflow_name="my_wf")
        assert policy.workflow_name == "my_wf"

    def test_tenant_id_filter(self):
        policy = AutoRecoveryPolicy(tenant_id="tenant-a")
        assert policy.tenant_id == "tenant-a"

    def test_disable_recover_flags(self):
        policy = AutoRecoveryPolicy(
            recover_failed=False,
            recover_stale_running=False,
            recover_compensating=False,
        )
        assert policy.recover_failed is False
        assert policy.recover_stale_running is False
        assert policy.recover_compensating is False


class TestAutoRecoveryPolicyValidation:
    """Invalid values are rejected."""

    def test_negative_interval_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(interval_seconds=-1.0)

    def test_zero_interval_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(interval_seconds=0.0)

    def test_negative_stale_after_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(stale_after_seconds=-1.0)

    def test_zero_stale_after_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(stale_after_seconds=0.0)

    def test_zero_max_candidates_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(max_candidates_per_scan=0)

    def test_negative_max_candidates_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(max_candidates_per_scan=-1)

    def test_zero_max_recoveries_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(max_recoveries_per_scan=0)

    def test_zero_max_concurrent_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(max_concurrent_recoveries=0)

    def test_negative_max_concurrent_fails(self):
        with pytest.raises(ValidationError):
            AutoRecoveryPolicy(max_concurrent_recoveries=-1)


class TestAutoRecoveryPolicySerialization:
    """Policy serializes correctly."""

    def test_model_dump(self):
        policy = AutoRecoveryPolicy(enabled=True, dry_run=False)
        data = policy.model_dump()
        assert data["enabled"] is True
        assert data["dry_run"] is False

    def test_model_dump_json(self):
        policy = AutoRecoveryPolicy(interval_seconds=60.0)
        json_str = policy.model_dump_json()
        assert "60.0" in json_str or "60" in json_str
