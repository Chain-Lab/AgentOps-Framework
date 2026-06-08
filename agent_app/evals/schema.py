"""Eval schema — data models for eval suites and cases."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvalDefaults(BaseModel):
    """Default values applied to all cases in a suite."""

    agent: str | None = Field(default=None)
    workflow: str | None = Field(default=None)
    user_id: str = Field(default="eval_user")
    tenant_id: str = Field(default="eval_tenant")
    permissions: list[str] = Field(default_factory=list)


class EvalExpect(BaseModel):
    """Expected outcomes for an eval case."""

    status: str | None = Field(default=None)
    output_contains: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    approvals_required: list[str] = Field(default_factory=list)
    error_type: str | None = Field(default=None)
    approve_and_resume: bool = Field(default=False)
    resumed_status: str | None = Field(default=None)
    handoffs: list[dict[str, str]] = Field(default_factory=list)
    agent_calls: list[str] = Field(default_factory=list)
    routing_decisions: list[str] = Field(
        default_factory=list, description="Expected routing rule names"
    )
    workflow_steps: list[str] = Field(
        default_factory=list, description="Expected workflow step types"
    )
    trace_events: list[str] = Field(
        default_factory=list, description="Expected trace event types"
    )


class EvalCase(BaseModel):
    """A single eval case."""

    id: str = Field(..., description="Unique case identifier")
    input: str = Field(..., description="User input to test")
    agent: str | None = Field(default=None, description="Override default agent")
    workflow: str | None = Field(default=None, description="Override default workflow")
    user_id: str | None = Field(default=None)
    tenant_id: str | None = Field(default=None)
    permissions: list[str] = Field(default_factory=list)
    expect: EvalExpect = Field(..., description="Expected outcomes")


class EvalSuite(BaseModel):
    """A collection of eval cases."""

    name: str = Field(..., description="Suite name")
    description: str | None = Field(default=None)
    defaults: EvalDefaults = Field(default_factory=EvalDefaults)
    cases: list[EvalCase] = Field(..., description="Eval cases")


class EvalCaseResult(BaseModel):
    """Result of running a single eval case."""

    case_id: str
    passed: bool
    errors: list[str] = Field(default_factory=list)
    run_result: Any = None  # AppRunResult


class EvalSuiteResult(BaseModel):
    """Summary result of running an entire eval suite."""

    suite_name: str
    passed: bool
    total: int
    passed_count: int
    failed_count: int
    case_results: list[EvalCaseResult] = Field(default_factory=list)

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        print(f"\nEval suite: {self.suite_name}")
        print(f"Passed: {self.passed_count}/{self.total}")
        for cr in self.case_results:
            status = "✓" if cr.passed else "✗"
            print(f"  {status} {cr.case_id}", end="")
            if not cr.passed:
                print(f" — {', '.join(cr.errors)}", end="")
            print()
        print()
