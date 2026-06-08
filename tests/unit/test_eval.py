"""Tests for Eval schema, assertions, and runner."""

import pytest

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
from agent_app.evals.assertions import run_assertions
from agent_app.evals.runner import load_eval_suite
from agent_app.evals.schema import (
    EvalCase,
    EvalDefaults,
    EvalExpect,
    EvalSuite,
    EvalSuiteResult,
)
from agent_app.governance.approval import ApprovalRequest
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.approval_store import InMemoryApprovalStore
from agent_app.runtime.session import InMemorySessionStore


def _make_app():
    bundle = type("B", (), {})()
    bundle.agent_registry = AgentRegistry()
    bundle.tool_registry = ToolRegistry()
    bundle.workflow_registry = WorkflowRegistry()
    return AgentApp(
        registry=bundle,
        session_store=InMemorySessionStore(),
        approval_store=InMemoryApprovalStore(),
    )


def _register_tool(app, name, **spec_kwargs):
    spec = ToolSpec(name=name, description=f"Tool {name}", **spec_kwargs)

    async def _fn(**kwargs):
        return {"tool": name, "result": "ok"}

    app.register_tool(spec, fn=_fn)
    return spec


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestEvalSchema:
    def test_load_valid_suite(self, tmp_path):
        yaml_content = """
name: test_suite
description: Test
defaults:
  agent: support
  user_id: u1
cases:
  - id: case_1
    input: "hello"
    expect:
      status: completed
"""
        p = tmp_path / "eval.yaml"
        p.write_text(yaml_content)
        suite = load_eval_suite(str(p))
        assert suite.name == "test_suite"
        assert len(suite.cases) == 1
        assert suite.cases[0].id == "case_1"

    def test_defaults_apply(self, tmp_path):
        yaml_content = """
name: test
defaults:
  agent: support
  permissions:
    - order:read
cases:
  - id: c1
    input: "hi"
    expect:
      status: completed
"""
        p = tmp_path / "eval.yaml"
        p.write_text(yaml_content)
        suite = load_eval_suite(str(p))
        assert suite.defaults.agent == "support"
        assert suite.defaults.permissions == ["order:read"]

    def test_invalid_suite_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("name: test\ncases: not_a_list")
        with pytest.raises(ValueError, match="Invalid eval suite"):
            load_eval_suite(str(p))


# ---------------------------------------------------------------------------
# Assertion tests
# ---------------------------------------------------------------------------

class TestAssertions:
    def _make_result(self, **kwargs):
        defaults = {
            "run_id": "r1",
            "status": "completed",
            "final_output": "order 123 is paid",
            "tool_calls": [],
            "interruptions": [],
            "error": None,
        }
        defaults.update(kwargs)
        from agent_app.core.result import AppRunResult
        return AppRunResult(**defaults)

    def _make_case(self, **expect_kwargs):
        defaults = {
            "id": "c1",
            "input": "hi",
            "expect": EvalExpect(status="completed"),
        }
        if "expect" in expect_kwargs:
            defaults["expect"] = expect_kwargs.pop("expect")
        defaults["expect"].__dict__.update(expect_kwargs)
        return EvalCase(**defaults)

    def test_status_pass(self):
        result = self._make_result(status="completed")
        case = self._make_case(expect=EvalExpect(status="completed"))
        errors = run_assertions(case, result)
        assert errors == []

    def test_status_fail(self):
        result = self._make_result(status="failed")
        case = self._make_case(expect=EvalExpect(status="completed"))
        errors = run_assertions(case, result)
        assert any("status" in e for e in errors)

    def test_output_contains_pass(self):
        result = self._make_result(final_output="order 123 is paid")
        case = self._make_case(expect=EvalExpect(output_contains=["order", "123"]))
        errors = run_assertions(case, result)
        assert errors == []

    def test_output_contains_fail(self):
        result = self._make_result(final_output="nothing here")
        case = self._make_case(expect=EvalExpect(output_contains=["order"]))
        errors = run_assertions(case, result)
        assert any("contain" in e for e in errors)

    def test_error_type_pass(self):
        result = self._make_result(status="failed", error={"type": "permission_denied"})
        case = self._make_case(expect=EvalExpect(error_type="permission_denied"))
        errors = run_assertions(case, result)
        assert errors == []

    def test_error_type_fail(self):
        result = self._make_result(status="failed", error={"type": "tool_not_found"})
        case = self._make_case(expect=EvalExpect(error_type="permission_denied"))
        errors = run_assertions(case, result)
        assert any("error_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Runner integration tests
# ---------------------------------------------------------------------------

class TestEvalRunner:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_order_query_eval(self, app):
        _register_tool(app, "order.query", risk_level="low", permissions=["order:read"])
        app.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["order.query"])
        )
        app.register_workflow(Workflow.single(agent="support", name="cs"))

        suite = EvalSuite(
            name="test",
            defaults=EvalDefaults(agent="support", permissions=["order:read"]),
            cases=[
                EvalCase(
                    id="order_query",
                    input="check my order 123",
                    expect=EvalExpect(status="completed", output_contains=["order"]),
                )
            ],
        )
        runner = type("R", (), {})()
        runner.app = app
        from agent_app.evals.runner import EvalRunner
        r = EvalRunner(app)
        result = await r.run_suite(suite)
        assert result.passed
        assert result.passed_count == 1

    @pytest.mark.asyncio
    async def test_permission_denied_eval(self, app):
        _register_tool(
            app, "refund.request",
            risk_level="high", permissions=["refund:create"],
        )
        app.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["refund.request"])
        )
        app.register_workflow(Workflow.single(agent="support", name="cs"))

        suite = EvalSuite(
            name="test",
            defaults=EvalDefaults(agent="support"),
            cases=[
                EvalCase(
                    id="perm_denied",
                    input="refund order 123",
                    permissions=[],
                    expect=EvalExpect(status="failed", error_type="permission_denied"),
                )
            ],
        )
        from agent_app.evals.runner import EvalRunner
        r = EvalRunner(app)
        result = await r.run_suite(suite)
        assert result.passed
        assert result.passed_count == 1

    @pytest.mark.asyncio
    async def test_approval_eval(self, app):
        _register_tool(
            app, "test.high",
            risk_level="high", requires_approval=True, permissions=[],
        )
        app.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app.register_workflow(Workflow.single(agent="support", name="cs"))

        suite = EvalSuite(
            name="test",
            defaults=EvalDefaults(agent="support"),
            cases=[
                EvalCase(
                    id="approval_required",
                    input="do high risk action",
                    expect=EvalExpect(
                        status="interrupted",
                        approvals_required=["test.high"],
                    ),
                )
            ],
        )
        from agent_app.evals.runner import EvalRunner
        r = EvalRunner(app)
        result = await r.run_suite(suite)
        assert result.passed

    @pytest.mark.asyncio
    async def test_approve_and_resume_eval(self, app):
        _register_tool(
            app, "test.high",
            risk_level="high", requires_approval=True, permissions=[],
        )
        app.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app.register_workflow(Workflow.single(agent="support", name="cs"))

        suite = EvalSuite(
            name="test",
            defaults=EvalDefaults(agent="support"),
            cases=[
                EvalCase(
                    id="approve_resume",
                    input="do high risk action",
                    expect=EvalExpect(
                        status="interrupted",
                        approvals_required=["test.high"],
                        approve_and_resume=True,
                        resumed_status="completed",
                    ),
                )
            ],
        )
        from agent_app.evals.runner import EvalRunner
        r = EvalRunner(app)
        result = await r.run_suite(suite)
        assert result.passed
        assert result.passed_count == 1

    @pytest.mark.asyncio
    async def test_failing_case_returns_errors(self, app):
        _register_tool(app, "order.query", risk_level="low")
        app.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["order.query"])
        )
        app.register_workflow(Workflow.single(agent="support", name="cs"))

        suite = EvalSuite(
            name="test",
            defaults=EvalDefaults(agent="support"),
            cases=[
                EvalCase(
                    id="should_fail",
                    input="query order",
                    expect=EvalExpect(status="interrupted"),  # Wrong expectation
                )
            ],
        )
        from agent_app.evals.runner import EvalRunner
        r = EvalRunner(app)
        result = await r.run_suite(suite)
        assert not result.passed
        assert result.failed_count == 1
        assert len(result.case_results[0].errors) > 0
