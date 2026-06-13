"""Tests for canary eval runner (Phase 33)."""
import pytest
from pathlib import Path
from agent_app.evals.canary import CanaryEvalResult, CanaryEvalRunner


class TestCanaryEvalResult:
    def test_defaults(self):
        result = CanaryEvalResult(
            environment="prod", ring_name="canary",
            activation_id="pa_001", suite_name="test", passed=True,
        )
        assert result.total == 0
        assert result.passed_count == 0
        assert result.failed_count == 0
        assert result.errors == []

    def test_with_errors(self):
        result = CanaryEvalResult(
            environment="prod", ring_name="canary",
            activation_id="pa_001", suite_name="test", passed=False,
            errors=["Connection refused"],
        )
        assert result.passed is False
        assert len(result.errors) == 1


class TestCanaryEvalRunner:
    @pytest.mark.asyncio
    async def test_missing_suite_returns_failed_result(self, tmp_path):
        """Missing suite file returns a failed CanaryEvalResult."""
        from unittest.mock import MagicMock
        app = MagicMock()
        runner = CanaryEvalRunner(app)
        result = await runner.run_for_activation(
            activation_id="pa_001",
            environment="prod",
            ring_name="canary",
            suite_path=str(tmp_path / "nonexistent.yaml"),
        )
        assert result.passed is False
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower() or "No such file" in result.errors[0]

    @pytest.mark.asyncio
    async def test_invalid_suite_returns_failed_result(self, tmp_path):
        """Invalid suite file returns a failed CanaryEvalResult."""
        from unittest.mock import MagicMock
        app = MagicMock()
        suite_file = tmp_path / "bad.yaml"
        suite_file.write_text("invalid: [yaml: content", encoding="utf-8")
        runner = CanaryEvalRunner(app)
        result = await runner.run_for_activation(
            activation_id="pa_001",
            environment="prod",
            ring_name="canary",
            suite_path=str(suite_file),
        )
        assert result.passed is False
        assert len(result.errors) > 0
