"""Tests for Phase 59 CLI commands — multi-instance production readiness."""

from __future__ import annotations

import argparse
import asyncio

import pytest

from agent_app.cli import (
    _cmd_policy_federation_notification_idempotency_check,
    _cmd_policy_federation_notification_idempotency_list,
    _cmd_policy_federation_notification_idempotency_prune,
    _cmd_policy_federation_notification_rate_limit_check,
    _cmd_policy_federation_notification_rate_limit_reset,
    _cmd_policy_federation_notification_rate_limit_list,
    _cmd_policy_federation_notification_dead_letter_evaluate,
    _cmd_policy_federation_notification_dead_letter_list,
    _cmd_policy_federation_notification_metrics_snapshot,
    _cmd_policy_federation_notification_key_rotation_status,
    _cmd_policy_federation_notification_key_rotation_rotate,
    _cmd_policy_federation_notification_key_rotation_history,
)


def _make_args(**kwargs):
    """Create a minimal argparse.Namespace with defaults."""
    defaults = {
        "config": "agentapp.yaml",
        "json": False,
        "limit": 20,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(cmd, args):
    """Run an async CLI command and return its exit code."""
    return asyncio.run(cmd(args))


# ---------------------------------------------------------------------------
# Idempotency CLI
# ---------------------------------------------------------------------------


class TestIdempotencyCLI:
    """Phase 59: DLQ replay idempotency CLI commands."""

    def test_check_not_configured(self, tmp_path):
        """Idempotency check returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_idempotency_check, args)
        assert rc == 1

    def test_list_not_configured(self, tmp_path):
        """Idempotency list returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_idempotency_list, args)
        assert rc == 1

    def test_prune_not_configured(self, tmp_path):
        """Idempotency prune returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_idempotency_prune, args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Rate Limiter CLI
# ---------------------------------------------------------------------------


class TestRateLimitCLI:
    """Phase 59: DLQ replay rate limiter CLI commands."""

    def test_check_not_configured(self, tmp_path):
        """Rate limit check returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(
            config=str(config),
            target_id="tgt_001",
            max_attempts=10,
            window_seconds=60,
        )
        rc = _run(_cmd_policy_federation_notification_rate_limit_check, args)
        assert rc == 1

    def test_reset_not_configured(self, tmp_path):
        """Rate limit reset returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config), target_id="tgt_001")
        rc = _run(_cmd_policy_federation_notification_rate_limit_reset, args)
        assert rc == 1

    def test_list_not_configured(self, tmp_path):
        """Rate limit list returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_rate_limit_list, args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Dead Letter Policy CLI
# ---------------------------------------------------------------------------


class TestDeadLetterCLI:
    """Phase 59: Dead letter policy CLI commands."""

    def test_evaluate_not_configured(self, tmp_path):
        """Dead letter evaluate returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config), pq_item_id="pq_001", attempt_count=1)
        rc = _run(_cmd_policy_federation_notification_dead_letter_evaluate, args)
        assert rc == 1

    def test_list_not_configured(self, tmp_path):
        """Dead letter list returns 1 when store not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_dead_letter_list, args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Enhanced Metrics CLI
# ---------------------------------------------------------------------------


class TestMetricsCLI:
    """Phase 59: Enhanced metrics CLI command."""

    def test_snapshot_not_configured(self, tmp_path):
        """Enhanced metrics snapshot returns 1 when metrics not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_metrics_snapshot, args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Webhook Key Rotation CLI
# ---------------------------------------------------------------------------


class TestKeyRotationCLI:
    """Phase 59: Webhook key rotation CLI commands."""

    def test_status_not_configured(self, tmp_path):
        """Key rotation status returns 1 when service not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_key_rotation_status, args)
        assert rc == 1

    def test_rotate_not_configured(self, tmp_path):
        """Key rotation rotate returns 1 when service not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config), actor_id="admin", reason="test")
        rc = _run(_cmd_policy_federation_notification_key_rotation_rotate, args)
        assert rc == 1

    def test_history_not_configured(self, tmp_path):
        """Key rotation history returns 1 when service not configured."""
        config = tmp_path / "agentapp.yaml"
        config.write_text("runtime:\n  backend: dry_run\n")
        args = _make_args(config=str(config))
        rc = _run(_cmd_policy_federation_notification_key_rotation_history, args)
        assert rc == 1
