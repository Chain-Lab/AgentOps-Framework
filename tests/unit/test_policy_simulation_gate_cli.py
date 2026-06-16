"""Phase 41 Task 5: Tests for CLI simulation gate command."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateRule, PolicyGateStatus
from agent_app.governance.policy_simulation import (
    PolicySimulationOutcome,
    PolicySimulationReport,
    PolicySimulationResult,
    PolicySimulationSummary,
)
from agent_app.runtime.policy_validation import (
    PolicyValidationReport,
)
from tests.conftest import _run_async


# -- Test fixtures and helpers --


def _make_simulation_report(**overrides) -> PolicySimulationReport:
    """Build a minimal simulation report for testing."""
    defaults = dict(
        simulation_id="psim_gate_test001",
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
        gate_result_id="gr_test001",
        bundle_id="simulation:psim_gate_test001",
        replay_id="psim_gate_test001",
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
    simulation_service=None,
    simulation_gate_evaluator=None,
) -> MagicMock:
    """Create a mock app with simulation-related attributes."""
    app = MagicMock()
    app.policy_simulation_service = simulation_service
    app.simulation_gate_evaluator = simulation_gate_evaluator
    return app


# -- Tests --


class TestSimulationGateCli:
    def test_gate_passes_exit_0(self, tmp_path, capsys):
        """Gate with lenient rules returns exit code 0."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)

        sim_report = _make_simulation_report()
        val_report = PolicyValidationReport(valid=True, issues=[])
        gate_result = _make_gate_result(passed=True)

        mock_service = MagicMock()
        mock_service.validate_and_gate = AsyncMock(
            return_value=(sim_report, val_report, gate_result)
        )

        app = _make_app(simulation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "passed" in captured.out.lower() or "PASSED" in captured.out

    def test_gate_fails_exit_nonzero(self, tmp_path, capsys):
        """Gate with impossible threshold returns non-zero exit code."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", FAILING_GATE_YAML)

        sim_report = _make_simulation_report()
        val_report = PolicyValidationReport(valid=True, issues=[])
        gate_result = _make_gate_result(
            passed=False,
            status="failed",
            rule_results=[
                {
                    "rule_name": "impossible_gate",
                    "status": "failed",
                    "failures": ["failed_replays 0 > max -1"],
                }
            ],
        )

        mock_service = MagicMock()
        mock_service.validate_and_gate = AsyncMock(
            return_value=(sim_report, val_report, gate_result)
        )

        app = _make_app(simulation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_json_output(self, tmp_path, capsys):
        """--json flag produces valid JSON output."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)

        sim_report = _make_simulation_report()
        val_report = PolicyValidationReport(valid=True, issues=[])
        gate_result = _make_gate_result(passed=True)

        mock_service = MagicMock()
        mock_service.validate_and_gate = AsyncMock(
            return_value=(sim_report, val_report, gate_result)
        )

        app = _make_app(simulation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            since=None,
            until=None,
            limit=None,
            json=True,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "simulation_report" in data or "simulation" in data or "gate_result" in data

    def test_output_writes_file(self, tmp_path, capsys):
        """--output writes JSON output to a file."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)
        output_path = str(tmp_path / "gate_output.json")

        sim_report = _make_simulation_report()
        val_report = PolicyValidationReport(valid=True, issues=[])
        gate_result = _make_gate_result(passed=True)

        mock_service = MagicMock()
        mock_service.validate_and_gate = AsyncMock(
            return_value=(sim_report, val_report, gate_result)
        )

        app = _make_app(simulation_service=mock_service)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=output_path,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc == 0
        assert os.path.exists(output_path)
        with open(output_path) as f:
            data = json.load(f)
        assert "gate_result" in data or "simulation" in data or "simulation_report" in data

    def test_invalid_gate_rules_file(self, tmp_path, capsys):
        """Broken YAML in gate rules file returns non-zero exit."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)
        gate_file = _write_file(tmp_path, "gate_rules.yaml", INVALID_YAML)

        app = _make_app()

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=gate_file,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc != 0

    def test_no_gate_rules_returns_nonzero(self, tmp_path, capsys):
        """Missing gate rules file with no app config gates returns non-zero."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)

        # App has no simulation_gate_evaluator
        app = MagicMock(spec=[])
        app.policy_simulation_service = MagicMock()

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=None,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "gate" in captured.err.lower() or "gate" in captured.out.lower()

    def test_gate_uses_app_config_gates(self, tmp_path, capsys):
        """When --gate-rules-file not provided, uses app config gates."""
        from agent_app.cli import _cmd_policy_simulation_gate

        rules_file = _write_file(tmp_path, "candidate_rules.yaml", VALID_RULES_YAML)

        sim_report = _make_simulation_report()
        val_report = PolicyValidationReport(valid=True, issues=[])
        gate_result = _make_gate_result(passed=True)

        mock_service = MagicMock()
        mock_service.validate_and_gate = AsyncMock(
            return_value=(sim_report, val_report, gate_result)
        )

        # App has a simulation_gate_evaluator with rules
        mock_evaluator = MagicMock()
        mock_evaluator._rules = [PolicyGateRule(name="from_config", max_failed_replays=100)]

        app = MagicMock()
        app.policy_simulation_service = mock_service
        app.simulation_gate_evaluator = mock_evaluator

        args = argparse.Namespace(
            config="agentapp.yaml",
            rules_file=rules_file,
            gate_rules_file=None,
            since=None,
            until=None,
            limit=None,
            json=False,
            output=None,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_simulation_gate(args))

        assert rc == 0


class TestParseGateRules:
    def test_parse_gate_rules_from_file(self, tmp_path):
        """_parse_gate_rules parses a YAML gate rules file."""
        from agent_app.cli import _parse_gate_rules

        gate_file = _write_file(tmp_path, "gate_rules.yaml", LENIENT_GATE_YAML)
        rules = _parse_gate_rules(gate_file)

        assert len(rules) == 1
        assert rules[0].name == "lenient_gate"
        assert rules[0].max_failed_replays == 100
        assert rules[0].max_changed_decisions == 100
        assert rules[0].max_new_denies == 100

    def test_parse_gate_rules_empty_file(self, tmp_path):
        """_parse_gate_rules handles empty file."""
        from agent_app.cli import _parse_gate_rules

        gate_file = _write_file(tmp_path, "empty_gate.yaml", "")
        rules = _parse_gate_rules(gate_file)

        assert rules == []

    def test_parse_gate_rules_gates_key(self, tmp_path):
        """_parse_gate_rules handles 'gates' key as well as 'gate_rules'."""
        from agent_app.cli import _parse_gate_rules

        yaml_content = """\
gates:
  - name: my_gate
    max_failed_replays: 10
"""
        gate_file = _write_file(tmp_path, "gates.yaml", yaml_content)
        rules = _parse_gate_rules(gate_file)

        assert len(rules) == 1
        assert rules[0].name == "my_gate"
        assert rules[0].max_failed_replays == 10

    def test_parse_gate_rules_broken_yaml(self, tmp_path):
        """_parse_gate_rules raises on broken YAML."""
        from agent_app.cli import _parse_gate_rules

        gate_file = _write_file(tmp_path, "bad.yaml", INVALID_YAML)
        with pytest.raises(Exception):
            _parse_gate_rules(gate_file)
