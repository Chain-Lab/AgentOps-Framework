"""Eval Runner — regression testing framework for AgentApp.

Supports YAML-defined eval suites with assertions for status, output,
tool calls, approvals, and error types.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_app.core.app import AgentApp
from agent_app.core.result import AppRunResult
from agent_app.evals.schema import EvalCaseResult, EvalSuite, EvalSuiteResult


def load_eval_suite(path: str | Path) -> EvalSuite:
    """Load and validate an eval suite from a YAML file.

    Args:
        path: Path to the YAML eval suite file.

    Returns:
        A validated EvalSuite instance.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Eval file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    from agent_app.evals.schema import EvalSuite
    try:
        return EvalSuite(**raw)
    except Exception as exc:
        raise ValueError(f"Invalid eval suite '{path}': {exc}") from exc


class EvalRunner:
    """Runs eval suites against an AgentApp.

    Args:
        app: The AgentApp to test.
    """

    def __init__(self, app: AgentApp) -> None:
        self.app = app

    async def run_suite(self, suite: EvalSuite) -> EvalSuiteResult:
        """Run all cases in an eval suite.

        Args:
            suite: The eval suite to run.

        Returns:
            EvalSuiteResult with per-case results and summary.
        """
        from agent_app.evals.assertions import run_assertions

        case_results: list[EvalCaseResult] = []

        for case in suite.cases:
            result = await self._run_case(suite.defaults, case)
            errors = run_assertions(case, result)
            case_results.append(
                EvalCaseResult(
                    case_id=case.id,
                    passed=len(errors) == 0,
                    errors=errors,
                    run_result=result,
                )
            )

        passed_count = sum(1 for c in case_results if c.passed)
        failed_count = len(case_results) - passed_count

        return EvalSuiteResult(
            suite_name=suite.name,
            passed=failed_count == 0,
            total=len(case_results),
            passed_count=passed_count,
            failed_count=failed_count,
            case_results=case_results,
        )

    async def run_file(self, path: str | Path) -> EvalSuiteResult:
        """Load and run an eval suite from a file.

        Args:
            path: Path to the YAML eval suite file.

        Returns:
            EvalSuiteResult.
        """
        suite = load_eval_suite(path)
        return await self.run_suite(suite)

    async def _run_case(self, defaults: Any, case: Any) -> AppRunResult:
        """Execute a single eval case and return the run result."""
        agent = case.agent or defaults.agent
        workflow = case.workflow or defaults.workflow
        user_id = case.user_id or defaults.user_id
        tenant_id = case.tenant_id or defaults.tenant_id
        permissions = case.permissions or list(defaults.permissions)

        if case.expect.approve_and_resume:
            # First run: expect interrupted
            result = await self.app.run(
                agent=agent,
                workflow=workflow,
                input=case.input,
                user_id=user_id,
                tenant_id=tenant_id,
                permissions=permissions,
            )
            if result.status != "interrupted" or not result.interruptions:
                return result
            # Approve and resume
            approval_id = result.interruptions[0]["approval_id"]
            await self.app.approve(approval_id, "eval_approver")
            return await self.app.resume(result.run_id, approval_id)

        return await self.app.run(
            agent=agent,
            workflow=workflow,
            input=case.input,
            user_id=user_id,
            tenant_id=tenant_id,
            permissions=permissions,
        )
