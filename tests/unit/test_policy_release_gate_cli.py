"""Phase 42 Task 7: Tests for CLI promotion gate lifecycle commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule, PolicyGateStatus
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.policy_simulation import (
    PolicySimulationReport,
    PolicySimulationSummary,
)
from agent_app.governance.runtime_policy import RuntimePolicyRule
from agent_app.runtime.policy_validation import PolicyValidationReport
from tests.conftest import _run_async


# -- Test fixtures and helpers --


def _make_requirement(
    status: ReleaseGateRequirementStatus = ReleaseGateRequirementStatus.REQUIRED,
    **overrides,
) -> ReleaseGateRequirement:
    """Build a minimal gate requirement for testing."""
    defaults = dict(
        requirement_id="rgr_test001",
        source_type="promotion",
        source_id="promo_abc123",
        status=status,
        max_age_seconds=None,
        gate_result_id=None,
        simulation_id=None,
        satisfied_at=None,
    )
    defaults.update(overrides)
    return ReleaseGateRequirement(**defaults)


def _make_simulation_report(**overrides) -> PolicySimulationReport:
    """Build a minimal simulation report for testing."""
    defaults = dict(
        simulation_id="psim_promo_test001",
        generated_at=datetime.now(timezone.utc),
        candidate_rule_ids=["rpr_abc123"],
        summary=PolicySimulationSummary(
            total=0,
            unchanged=0,
            would_allow=0,
            would_deny=0,
            would_require_approval=0,
            would_change=0,
            errors=0,
        ),
        results=[],
    )
    defaults.update(overrides)
    return PolicySimulationReport(**defaults)


def _make_gate_result(passed: bool = True, **overrides) -> PolicyGateResult:
    """Build a minimal gate result for testing."""
    status = PolicyGateStatus.PASSED.value if passed else PolicyGateStatus.FAILED.value
    defaults = dict(
        gate_result_id="gr_promo_test001",
        bundle_id="simulation:psim_promo_test001",
        replay_id="psim_promo_test001",
        status=status,
        passed=passed,
        total_decisions=0,
        changed_decisions=0,
        failed_replays=0,
        changed_ratio=0.0,
        new_denies=0,
        new_approvals=0,
        missing_context_count=0,
        rule_results=[],
        summary={},
    )
    defaults.update(overrides)
    return PolicyGateResult(**defaults)


VALID_RULES_YAML = """\
rules:
  - name: deny_refunds_without_finance_role
    action_type: tool.execute
    effect: deny
    tool_name: refund.request
    required_roles:
      - finance_reviewer
"""

LENIENT_GATE_YAML = """\
gate_rules:
  - name: lenient_gate
    description: Very lenient gate for testing
    max_failed_replays: 100
    max_changed_decisions: 100
    max_new_denies: 100
"""

FAILING_GATE_YAML = """\
gate_rules:
  - name: impossible_gate
    description: Gate that always fails
    max_failed_replays: -1
"""

INVALID_YAML = """\
gate_rules:
  - name: broken
    description: [
"""


def _write_file(tmp_path, filename: str, content: str) -> str:
    """Write content to a file and return its path."""
    filepath = tmp_path / filename
    filepath.write_text(content)
    return str(filepath)


def _make_app(
    gate_automation_service=None,
    simulation_service=None,
    simulation_gate_evaluator=None,
) -> MagicMock:
    """Create a mock app with gate-automation-related attributes."""
    app = MagicMock()
    app._release_gate_automation_service = gate_automation_service
    app.policy_simulation_service = simulation_service
    app.simulation_gate_evaluator = simulation_gate_evaluator
    return app


# -- Tests --


class TestPromotionGateRequire:
    def test_require_creates_requirement_exit_0(self, capsys):
        """policy promotion gate require creates requirement, exits 0."""
        from agent_app.cli import _cmd_policy_promotion_gate_require

        req = _make_requirement()
        mock_service = MagicMock()
        mock_service.require_gate_for_promotion = AsyncMock(return_value=req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            max_age_seconds=None,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_require(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "rgr_test001" in captured.out
        assert "promo_abc123" in captured.out
        assert "required" in captured.out.lower()

    def test_require_with_max_age_seconds(self, capsys):
        """policy promotion gate require with --max-age-seconds."""
        from agent_app.cli import _cmd_policy_promotion_gate_require

        req = _make_requirement(max_age_seconds=3600)
        mock_service = MagicMock()
        mock_service.require_gate_for_promotion = AsyncMock(return_value=req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            max_age_seconds=3600,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_require(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "3600" in captured.out
        mock_service.require_gate_for_promotion.assert_called_once_with(
            promotion_id="promo_abc123",
            max_age_seconds=3600,
        )

    def test_require_no_service_returns_nonzero(self, capsys):
        """policy promotion gate require with no service returns non-zero."""
        from agent_app.cli import _cmd_policy_promotion_gate_require

        app = _make_app(gate_automation_service=None)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            max_age_seconds=None,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_require(args))

        assert rc != 0


class TestPromotionGateRun:
    def test_run_passing_gate_exit_0(self, tmp_path, capsys):
        """policy promotion gate run with passing gate exits 0."""
        from agent_app.cli import _cmd_policy_promotion_gate_run

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)

        satisfied_req = _make_requirement(
            status=ReleaseGateRequirementStatus.SATISFIED,
            gate_result_id="gr_promo_test001",
            simulation_id="psim_promo_test001",
        )
        mock_service = MagicMock()
        mock_service.check_requirement = AsyncMock(
            return_value=_make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
        )
        mock_service.run_and_attach_simulation_gate_for_promotion = AsyncMock(
            return_value=satisfied_req
        )

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            actor_id=None,
            permissions=[],
            since=None,
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_run(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "satisfied" in captured.out.lower()
        assert "gr_promo_test001" in captured.out
        assert "psim_promo_test001" in captured.out

    def test_run_failing_gate_exit_nonzero(self, tmp_path, capsys):
        """policy promotion gate run with failing gate exits non-zero."""
        from agent_app.cli import _cmd_policy_promotion_gate_run

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", FAILING_GATE_YAML)

        failed_req = _make_requirement(
            status=ReleaseGateRequirementStatus.FAILED,
            gate_result_id="gr_promo_test001",
        )
        mock_service = MagicMock()
        mock_service.check_requirement = AsyncMock(
            return_value=_make_requirement(status=ReleaseGateRequirementStatus.REQUIRED)
        )
        mock_service.run_and_attach_simulation_gate_for_promotion = AsyncMock(
            return_value=failed_req
        )

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            actor_id=None,
            permissions=[],
            since=None,
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_run(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_run_invalid_rules_file_exit_nonzero(self, tmp_path, capsys):
        """policy promotion gate run with invalid rules file exits non-zero."""
        from agent_app.cli import _cmd_policy_promotion_gate_run

        rules_file = _write_file(tmp_path, "bad_rules.yaml", "not: valid yaml {{{")
        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)

        mock_service = MagicMock()

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            actor_id=None,
            permissions=[],
            since=None,
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_run(args))

        assert rc != 0


class TestPromotionGateAttach:
    def test_attach_passed_gate_exit_0(self, capsys):
        """policy promotion gate attach with passed gate exits 0."""
        from agent_app.cli import _cmd_policy_promotion_gate_attach

        satisfied_req = _make_requirement(
            status=ReleaseGateRequirementStatus.SATISFIED,
            gate_result_id="gr_attach001",
        )
        mock_service = MagicMock()
        mock_service.attach_gate_result = AsyncMock(return_value=satisfied_req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            gate_result_id="gr_attach001",
            simulation_id=None,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_attach(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "satisfied" in captured.out.lower()
        assert "gr_attach001" in captured.out

    def test_attach_failed_gate_exit_nonzero(self, capsys):
        """policy promotion gate attach with failed gate exits non-zero."""
        from agent_app.cli import _cmd_policy_promotion_gate_attach

        failed_req = _make_requirement(
            status=ReleaseGateRequirementStatus.FAILED,
            gate_result_id="gr_attach002",
        )
        mock_service = MagicMock()
        mock_service.attach_gate_result = AsyncMock(return_value=failed_req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            gate_result_id="gr_attach002",
            simulation_id=None,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_attach(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_attach_no_requirement_exit_nonzero(self, capsys):
        """policy promotion gate attach with no existing requirement exits non-zero."""
        from agent_app.cli import _cmd_policy_promotion_gate_attach

        mock_service = MagicMock()
        mock_service.attach_gate_result = AsyncMock(
            side_effect=KeyError("No gate requirement found for promotion/promo_missing")
        )

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_missing",
            gate_result_id="gr_nonexist",
            simulation_id=None,
            actor_id=None,
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_attach(args))

        assert rc != 0


class TestPromotionGateStatus:
    def test_status_shows_status(self, capsys):
        """policy promotion gate status shows status."""
        from agent_app.cli import _cmd_policy_promotion_gate_status

        req = _make_requirement(
            status=ReleaseGateRequirementStatus.SATISFIED,
            gate_result_id="gr_status001",
            simulation_id="psim_status001",
            max_age_seconds=7200,
            satisfied_at=datetime.now(timezone.utc),
        )
        mock_service = MagicMock()
        mock_service.check_requirement = AsyncMock(return_value=req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_status(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "rgr_test001" in captured.out
        assert "promo_abc123" in captured.out
        assert "satisfied" in captured.out.lower()
        assert "gr_status001" in captured.out
        assert "7200" in captured.out

    def test_status_json_output(self, capsys):
        """policy promotion gate status --json outputs JSON."""
        from agent_app.cli import _cmd_policy_promotion_gate_status

        now = datetime.now(timezone.utc)
        req = _make_requirement(
            status=ReleaseGateRequirementStatus.SATISFIED,
            gate_result_id="gr_status002",
            simulation_id="psim_status002",
            max_age_seconds=3600,
            satisfied_at=now,
        )
        mock_service = MagicMock()
        mock_service.check_requirement = AsyncMock(return_value=req)

        app = _make_app(gate_automation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            promotion_id="promo_abc123",
            json=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_promotion_gate_status(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["requirement_id"] == "rgr_test001"
        assert data["promotion_id"] == "promo_abc123"
        assert data["status"] == "satisfied"
        assert data["gate_result_id"] == "gr_status002"
        assert data["simulation_id"] == "psim_status002"
        assert data["max_age_seconds"] == 3600
        assert data["satisfied_at"] is not None
