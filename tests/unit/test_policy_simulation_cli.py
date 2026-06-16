"""Phase 40 Task 7: Tests for CLI simulation commands."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_simulation import (
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.runtime.policy_validation import (
    PolicyValidationIssue,
    PolicyValidationReport,
    PolicyValidationSeverity,
)
from tests.conftest import _run_async


def _make_simulation_report(**overrides) -> PolicySimulationReport:
    """Build a minimal simulation report for testing."""
    defaults = dict(
        simulation_id="psim_test001",
        generated_at=datetime.now(timezone.utc),
        candidate_rule_ids=["rpr_abc123"],
        summary=PolicySimulationSummary(
            total=3,
            unchanged=2,
            would_allow=0,
            would_deny=1,
            would_require_approval=0,
            would_change=0,
            errors=0,
        ),
        results=[
            PolicySimulationResult(
                case_id="psc_001",
                baseline_status="allowed",
                candidate_status="allowed",
                outcome=PolicySimulationOutcome.UNCHANGED,
                reason="No matching rule",
            ),
            PolicySimulationResult(
                case_id="psc_002",
                baseline_status="allowed",
                candidate_status="allowed",
                outcome=PolicySimulationOutcome.UNCHANGED,
                reason="No matching rule",
            ),
            PolicySimulationResult(
                case_id="psc_003",
                baseline_status="allowed",
                candidate_status="denied",
                outcome=PolicySimulationOutcome.WOULD_DENY,
                reason="Denied by rule deny_refunds",
                decision_id="ped_sim001",
            ),
        ],
    )
    defaults.update(overrides)
    return PolicySimulationReport(**defaults)


def _make_valid_report(**overrides) -> PolicyValidationReport:
    """Build a valid validation report (no errors)."""
    defaults = dict(
        valid=True,
        issues=[],
    )
    defaults.update(overrides)
    return PolicyValidationReport(**defaults)


def _make_invalid_report() -> PolicyValidationReport:
    """Build an invalid validation report with an error."""
    return PolicyValidationReport(
        valid=False,
        issues=[
            PolicyValidationIssue(
                severity=PolicyValidationSeverity.ERROR,
                code="invalid_action_type",
                message="Invalid action_type 'bogus' in rule 'bad_rule'",
                rule_id="rpr_bad1",
            ),
        ],
    )


def _make_app(
    simulation_service=None,
    audit_logger=None,
    runtime_policy_store=None,
) -> MagicMock:
    """Create a mock app with simulation-related attributes."""
    app = MagicMock()
    app._audit_logger = audit_logger
    app._runtime_policy_store = runtime_policy_store
    return app


def _write_rules_file(tmp_path, rules_yaml: str) -> str:
    """Write a YAML rules file and return its path."""
    rules_file = tmp_path / "candidate_rules.yaml"
    rules_file.write_text(rules_yaml)
    return str(rules_file)


VALID_RULES_YAML = """\
rules:
  - name: deny_refunds_without_finance_role
    action_type: tool.execute
    effect: deny
    tool_name: refund.request
    required_roles:
      - finance_reviewer
"""

INVALID_ACTION_TYPE_YAML = """\
rules:
  - name: bad_rule
    action_type: bogus
    effect: deny
"""

INVALID_EFFECT_YAML = """\
rules:
  - name: bad_effect_rule
    action_type: tool.execute
    effect: explode
"""

EMPTY_RULES_YAML = """\
rules: []
"""

NO_RULES_KEY_YAML = """\
something_else: true
"""


# -- Validate command tests --


class TestSimulationValidate:
    def test_validate_success(self, tmp_path, capsys):
        """validate with valid rules file returns 0."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Validation passed" in captured.out

    def test_validate_with_warnings(self, tmp_path, capsys):
        """validate with warnings (but no errors) returns 0."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        # RuntimePolicyValidator only produces warnings for the test rules,
        # so the validate command should still return 0
        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 0

    def test_validate_errors_exit_nonzero(self, tmp_path, capsys):
        """validate with invalid rules file exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, INVALID_ACTION_TYPE_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error parsing rules file" in captured.err

    def test_validate_invalid_effect(self, tmp_path, capsys):
        """validate with invalid effect exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, INVALID_EFFECT_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error parsing rules file" in captured.err

    def test_validate_empty_rules(self, tmp_path, capsys):
        """validate with empty rules list exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, EMPTY_RULES_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "No rules found" in captured.err

    def test_validate_no_rules_key(self, tmp_path, capsys):
        """validate with no 'rules' key in YAML exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, NO_RULES_KEY_YAML)
        app = _make_app()
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 1

    def test_validate_build_app_failure(self, tmp_path, capsys):
        """validate returns 1 when build_app raises."""
        from agent_app.cli import _cmd_policy_simulation_validate

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        args = argparse.Namespace(config="agentapp.yaml", rules_file=rules_file)

        with patch("agent_app.config.loader.build_app", side_effect=RuntimeError("bad config")):
            rc = _run_async(_cmd_policy_simulation_validate(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error loading config" in captured.err


# -- Replay command tests --


class TestSimulationReplay:
    def test_replay_success(self, tmp_path, capsys):
        """replay with audit events prints summary."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since=None,
            until=None,
            limit=None,
            json=False,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "Policy Simulation Report" in captured.out
        assert "psim_test001" in captured.out
        assert "Unchanged" in captured.out
        assert "Would Deny" in captured.out

    def test_replay_json_output(self, tmp_path, capsys):
        """replay --json produces valid JSON output."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since=None,
            until=None,
            limit=None,
            json=True,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["simulation_id"] == "psim_test001"
        assert data["summary"]["total"] == 3
        assert data["summary"]["would_deny"] == 1

    def test_replay_with_window(self, tmp_path):
        """replay --since and --until are parsed correctly."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since="2026-01-01T00:00:00Z",
            until="2026-06-01T00:00:00Z",
            limit=10,
            json=True,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 0
        call_kwargs = mock_service.simulate_from_audit.call_args[1]
        assert call_kwargs["window_start"] == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert call_kwargs["window_end"] == datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert call_kwargs["limit"] == 10

    def test_replay_invalid_datetime_exits_nonzero(self, tmp_path, capsys):
        """replay with invalid --since exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since="not-a-date",
            until=None,
            limit=None,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid datetime" in captured.err

    def test_replay_invalid_rules_file_exits_nonzero(self, tmp_path, capsys):
        """replay with invalid rules file exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, INVALID_ACTION_TYPE_YAML)
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since=None,
            until=None,
            limit=None,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error parsing rules file" in captured.err

    def test_replay_build_app_failure(self, tmp_path, capsys):
        """replay returns 1 when build_app raises."""
        from agent_app.cli import _cmd_policy_simulation_replay

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            since=None,
            until=None,
            limit=None,
            json=False,
        )

        with patch("agent_app.config.loader.build_app", side_effect=RuntimeError("bad config")):
            rc = _run_async(_cmd_policy_simulation_replay(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error loading config" in captured.err


# -- Export command tests --


class TestSimulationExport:
    def test_export_json(self, tmp_path):
        """export --format json writes valid JSON to file."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        output_path = str(tmp_path / "sim_report.json")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="json",
            output=output_path,
            since=None,
            until=None,
            limit=None,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 0
        assert os.path.exists(output_path)
        with open(output_path) as f:
            data = json.load(f)
        assert data["simulation_id"] == "psim_test001"
        assert data["summary"]["total"] == 3

    def test_export_csv(self, tmp_path):
        """export --format csv writes CSV with headers to file."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        output_path = str(tmp_path / "sim_report.csv")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="csv",
            output=output_path,
            since=None,
            until=None,
            limit=None,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 0
        assert os.path.exists(output_path)
        with open(output_path) as f:
            content = f.read()
        assert "case_id,baseline_status,candidate_status,outcome" in content
        assert "psc_003" in content
        assert "would_deny" in content

    def test_export_csv_empty_report(self, tmp_path):
        """export --format csv on empty report writes header only."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report(results=[])
        app = _make_app()
        output_path = str(tmp_path / "empty_report.csv")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="csv",
            output=output_path,
            since=None,
            until=None,
            limit=None,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 0
        with open(output_path) as f:
            content = f.read()
        assert content == "case_id,baseline_status,candidate_status,outcome,reason,decision_id,errors\n"

    def test_export_invalid_rules_file(self, tmp_path, capsys):
        """export with invalid rules file exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, INVALID_ACTION_TYPE_YAML)
        app = _make_app()
        output_path = str(tmp_path / "out.json")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="json",
            output=output_path,
            since=None,
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error parsing rules file" in captured.err

    def test_export_invalid_datetime(self, tmp_path, capsys):
        """export with invalid --since exits non-zero."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        app = _make_app()
        output_path = str(tmp_path / "out.json")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="json",
            output=output_path,
            since="bad-date",
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid datetime" in captured.err

    def test_export_build_app_failure(self, tmp_path, capsys):
        """export returns 1 when build_app raises."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        output_path = str(tmp_path / "out.json")
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="json",
            output=output_path,
            since=None,
            until=None,
            limit=None,
        )

        with patch("agent_app.config.loader.build_app", side_effect=RuntimeError("bad config")):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error loading config" in captured.err

    def test_export_write_failure(self, tmp_path, capsys):
        """export returns 1 when file write fails."""
        from agent_app.cli import _cmd_policy_simulation_export

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        report = _make_simulation_report()
        app = _make_app()
        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            format="json",
            output="/nonexistent/dir/report.json",
            since=None,
            until=None,
            limit=None,
        )

        mock_service = MagicMock()
        mock_service.simulate_from_audit = AsyncMock(return_value=report)

        with patch("agent_app.config.loader.build_app", return_value=app), \
             patch("agent_app.runtime.policy_simulation_service.PolicySimulationService", return_value=mock_service):
            rc = _run_async(_cmd_policy_simulation_export(args))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error writing file" in captured.err


# -- Helper function tests --


class TestParseCandidateRules:
    def test_parse_valid_rules(self, tmp_path):
        """_parse_candidate_rules parses valid YAML into RuntimePolicyRule objects."""
        from agent_app.cli import _parse_candidate_rules

        rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
        rules = _parse_candidate_rules(rules_file)

        assert len(rules) == 1
        assert rules[0].name == "deny_refunds_without_finance_role"
        assert rules[0].action_type.value == "tool.execute"
        assert rules[0].effect.value == "deny"
        assert rules[0].tool_name == "refund.request"
        assert rules[0].required_roles == ["finance_reviewer"]

    def test_parse_invalid_action_type(self, tmp_path):
        """_parse_candidate_rules raises ValueError for invalid action_type."""
        from agent_app.cli import _parse_candidate_rules

        rules_file = _write_rules_file(tmp_path, INVALID_ACTION_TYPE_YAML)
        with pytest.raises(ValueError, match="Invalid or missing action_type"):
            _parse_candidate_rules(rules_file)

    def test_parse_invalid_effect(self, tmp_path):
        """_parse_candidate_rules raises ValueError for invalid effect."""
        from agent_app.cli import _parse_candidate_rules

        rules_file = _write_rules_file(tmp_path, INVALID_EFFECT_YAML)
        with pytest.raises(ValueError, match="Invalid or missing effect"):
            _parse_candidate_rules(rules_file)

    def test_parse_no_rules_key(self, tmp_path):
        """_parse_candidate_rules raises ValueError when no 'rules' key."""
        from agent_app.cli import _parse_candidate_rules

        rules_file = _write_rules_file(tmp_path, NO_RULES_KEY_YAML)
        with pytest.raises(ValueError, match="top-level 'rules' key"):
            _parse_candidate_rules(rules_file)

    def test_parse_optional_fields(self, tmp_path):
        """_parse_candidate_rules handles optional fields correctly."""
        from agent_app.cli import _parse_candidate_rules

        yaml_content = """\
rules:
  - name: allow_all_tools
    action_type: tool.execute
    effect: allow
"""
        rules_file = _write_rules_file(tmp_path, yaml_content)
        rules = _parse_candidate_rules(rules_file)

        assert len(rules) == 1
        assert rules[0].tool_name is None
        assert rules[0].required_roles == []
        assert rules[0].required_permissions == []


class TestParseWindow:
    def test_parse_window_no_args(self):
        """_parse_window returns None, None when no --since/--until."""
        from agent_app.cli import _parse_window

        args = argparse.Namespace(since=None, until=None)
        window_start, window_end, parse_error = _parse_window(args)

        assert window_start is None
        assert window_end is None
        assert parse_error is False

    def test_parse_window_valid_args(self):
        """_parse_window correctly parses valid ISO 8601 datetimes."""
        from agent_app.cli import _parse_window

        args = argparse.Namespace(
            since="2026-01-01T00:00:00Z",
            until="2026-06-01T00:00:00Z",
        )
        window_start, window_end, parse_error = _parse_window(args)

        assert parse_error is False
        assert window_start == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert window_end == datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_parse_window_invalid_since(self, capsys):
        """_parse_window returns error for invalid --since."""
        from agent_app.cli import _parse_window

        args = argparse.Namespace(since="bad-date", until=None)
        window_start, window_end, parse_error = _parse_window(args)

        assert parse_error is True

    def test_parse_window_invalid_until(self, capsys):
        """_parse_window returns error for invalid --until."""
        from agent_app.cli import _parse_window

        args = argparse.Namespace(since=None, until="not-a-date")
        window_start, window_end, parse_error = _parse_window(args)

        assert parse_error is True
