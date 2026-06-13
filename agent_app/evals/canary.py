"""Canary eval runner -- evaluates a candidate activation via eval suite."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class CanaryEvalResult(BaseModel):
    """Result of a canary evaluation."""

    environment: str
    ring_name: str
    activation_id: str
    suite_name: str
    passed: bool
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    errors: list[str] = Field(default_factory=list)


class CanaryEvalRunner:
    """Runs eval suites against a specific activation in a ring context.

    Args:
        app: The AgentApp to test against.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def run_for_activation(
        self,
        activation_id: str,
        environment: str,
        ring_name: str,
        suite_path: str | Path,
    ) -> CanaryEvalResult:
        """Run an eval suite in a ring context.

        Note: EvalRunner.run_suite does not accept a metadata parameter,
        so environment/ring context is not injected into the run itself.
        The caller should ensure the app is configured for the target
        ring/environment before invoking this method.

        Args:
            activation_id: The activation being evaluated.
            environment: The target environment.
            ring_name: The target ring.
            suite_path: Path to the eval suite YAML.

        Returns:
            CanaryEvalResult with pass/fail summary.
        """
        from agent_app.evals.runner import EvalRunner, load_eval_suite

        try:
            suite = load_eval_suite(suite_path)
        except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
            return CanaryEvalResult(
                environment=environment,
                ring_name=ring_name,
                activation_id=activation_id,
                suite_name=str(suite_path),
                passed=False,
                errors=[str(exc)],
            )

        eval_runner = EvalRunner(self.app)
        try:
            suite_result = await eval_runner.run_suite(suite)
        except Exception as exc:
            return CanaryEvalResult(
                environment=environment,
                ring_name=ring_name,
                activation_id=activation_id,
                suite_name=suite.name,
                passed=False,
                errors=[str(exc)],
            )

        # Collect per-case errors from the suite result
        case_errors: list[str] = []
        for case_result in suite_result.case_results:
            case_errors.extend(case_result.errors)

        return CanaryEvalResult(
            environment=environment,
            ring_name=ring_name,
            activation_id=activation_id,
            suite_name=suite_result.suite_name,
            passed=suite_result.passed,
            total=suite_result.total,
            passed_count=suite_result.passed_count,
            failed_count=suite_result.failed_count,
            errors=case_errors,
        )
