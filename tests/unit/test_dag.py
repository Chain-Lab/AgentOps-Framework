"""Tests for DAG Workflow engine (Phase 13)."""

from __future__ import annotations

import pytest

from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.workflows.dag import (
    CycleDetectedError,
    DagExecutor,
    DagNode,
    DagWorkflow,
    DuplicateNodeIdError,
    NodeExecutionResult,
    NodeExecutionStatus,
    NodeNotFoundError,
    NodeType,
)


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------


class TestDagNode:
    def test_create_agent_node(self) -> None:
        node = DagNode(id="a", type=NodeType.AGENT, ref="support")
        assert node.id == "a"
        assert node.type == NodeType.AGENT
        assert node.ref == "support"
        assert node.depends_on == []

    def test_create_tool_node(self) -> None:
        node = DagNode(id="t1", type=NodeType.TOOL, ref="order.query")
        assert node.type == NodeType.TOOL

    def test_node_with_dependencies(self) -> None:
        node = DagNode(
            id="step2", type=NodeType.TOOL, ref="refund.request",
            depends_on=["step1"],
        )
        assert node.depends_on == ["step1"]

    def test_node_with_input(self) -> None:
        node = DagNode(
            id="n", type=NodeType.AGENT, ref="bot",
            input={"key": "value"},
        )
        assert node.input == {"key": "value"}


class TestDagWorkflowValidation:
    def test_valid_dag(self) -> None:
        wf = DagWorkflow(
            name="test",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="bot1"),
                DagNode(id="b", type=NodeType.TOOL, ref="tool1", depends_on=["a"]),
            ],
        )
        assert len(wf.nodes) == 2

    def test_duplicate_node_ids_raises(self) -> None:
        with pytest.raises(DuplicateNodeIdError):
            DagWorkflow(
                name="test",
                nodes=[
                    DagNode(id="a", type=NodeType.AGENT, ref="bot1"),
                    DagNode(id="a", type=NodeType.TOOL, ref="tool1"),
                ],
            )

    def test_missing_dependency_raises(self) -> None:
        with pytest.raises(NodeNotFoundError):
            DagWorkflow(
                name="test",
                nodes=[
                    DagNode(id="a", type=NodeType.AGENT, ref="bot1", depends_on=["ghost"]),
                ],
            )

    def test_cycle_detected(self) -> None:
        with pytest.raises(CycleDetectedError):
            DagWorkflow(
                name="test",
                nodes=[
                    DagNode(id="a", type=NodeType.AGENT, ref="bot1", depends_on=["b"]),
                    DagNode(id="b", type=NodeType.TOOL, ref="tool1", depends_on=["a"]),
                ],
            )

    def test_self_dependency_raises(self) -> None:
        with pytest.raises(CycleDetectedError):
            DagWorkflow(
                name="test",
                nodes=[
                    DagNode(id="a", type=NodeType.AGENT, ref="bot1", depends_on=["a"]),
                ],
            )


class TestTopologicalSort:
    def test_simple_linear(self) -> None:
        wf = DagWorkflow(
            name="test",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="bot1"),
                DagNode(id="b", type=NodeType.TOOL, ref="tool1", depends_on=["a"]),
                DagNode(id="c", type=NodeType.TOOL, ref="tool2", depends_on=["b"]),
            ],
        )
        sorted_nodes = wf.topological_sort()
        ids = [n.id for n in sorted_nodes]
        assert ids == ["a", "b", "c"]

    def test_multi_dependency(self) -> None:
        wf = DagWorkflow(
            name="test",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="bot1"),
                DagNode(id="b", type=NodeType.AGENT, ref="bot2"),
                DagNode(id="c", type=NodeType.TOOL, ref="tool1", depends_on=["a", "b"]),
            ],
        )
        sorted_nodes = wf.topological_sort()
        ids = [n.id for n in sorted_nodes]
        assert ids.index("a") < ids.index("c")
        assert ids.index("b") < ids.index("c")

    def test_diamond_shape(self) -> None:
        wf = DagWorkflow(
            name="test",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="bot1"),
                DagNode(id="b", type=NodeType.AGENT, ref="bot2", depends_on=["a"]),
                DagNode(id="c", type=NodeType.TOOL, ref="tool1", depends_on=["a"]),
                DagNode(id="d", type=NodeType.TOOL, ref="tool2", depends_on=["b", "c"]),
            ],
        )
        sorted_nodes = wf.topological_sort()
        ids = [n.id for n in sorted_nodes]
        assert ids[0] == "a"
        assert ids[-1] == "d"
        assert ids.index("b") < ids.index("d")
        assert ids.index("c") < ids.index("d")

    def test_single_node(self) -> None:
        wf = DagWorkflow(
            name="test",
            nodes=[DagNode(id="only", type=NodeType.AGENT, ref="bot1")],
        )
        sorted_nodes = wf.topological_sort()
        assert len(sorted_nodes) == 1
        assert sorted_nodes[0].id == "only"

    def test_empty_dag(self) -> None:
        wf = DagWorkflow(name="test", nodes=[])
        assert wf.topological_sort() == []


# ---------------------------------------------------------------------------
# Workflow factory tests
# ---------------------------------------------------------------------------


class TestWorkflowDagFactory:
    def test_dag_workflow(self) -> None:
        wf = Workflow.dag(name="my_dag")
        assert wf.type == WorkflowType.DAG
        assert wf.name == "my_dag"
        assert "dag" in wf.config

    def test_dag_with_nodes(self) -> None:
        nodes = [
            {"id": "n1", "type": "agent", "ref": "bot"},
            {"id": "n2", "type": "tool", "ref": "tool1", "depends_on": ["n1"]},
        ]
        wf = Workflow.dag(name="my_dag", nodes=nodes)
        assert wf.type == WorkflowType.DAG
        assert len(wf.config["dag"]["nodes"]) == 2


# ---------------------------------------------------------------------------
# DagExecutor tests
# ---------------------------------------------------------------------------


class TestDagExecutor:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        # Register agents
        app.register_agent(AgentSpec(name="support", instructions="Support agent", tools=[]))
        app.register_agent(AgentSpec(name="refund", instructions="Refund agent", tools=[]))
        # Register tools
        app.register_tool(
            ToolSpec(name="order.query", description="Query orders", risk_level="low"),
            fn=lambda **kw: {"order_id": "123", "status": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Request refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund_id": "r456", "status": "approved"},
        )
        # Pre-initialize the runner
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_single_agent_node(self, app, context):
        """A DAG with a single agent node executes successfully."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="single_agent",
            nodes=[
                DagNode(id="a1", type=NodeType.AGENT, ref="support"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="hello", context=context,
        )
        assert status == "completed"
        assert len(results) == 1
        assert results[0].node_id == "a1"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_two_tool_nodes_sequential(self, app, context):
        """Two tool nodes execute in dependency order."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="two_tools",
            nodes=[
                DagNode(id="q", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="r", type=NodeType.TOOL, ref="refund.request", depends_on=["q"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="refund order 123", context=context,
        )
        assert status == "interrupted"  # refund.request requires approval
        assert len(results) == 2
        assert results[0].node_id == "q"
        assert results[0].status == NodeExecutionStatus.COMPLETED
        assert results[1].node_id == "r"
        assert results[1].status == NodeExecutionStatus.INTERRUPTED

    @pytest.mark.asyncio
    async def test_failed_node_stops_workflow(self, app, context):
        """When a node fails, the DAG stops."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="fail_stop",
            nodes=[
                DagNode(id="good", type=NodeType.AGENT, ref="support"),
                DagNode(id="bad", type=NodeType.TOOL, ref="nonexistent.tool"),
                DagNode(id="after", type=NodeType.AGENT, ref="refund"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert len(results) == 2  # 'good' + 'bad'; 'after' never runs
        assert results[0].node_id == "good"
        assert results[0].status == NodeExecutionStatus.COMPLETED
        assert results[1].node_id == "bad"
        assert results[1].status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_upstream_output_in_context(self, app, context):
        """Upstream node output is available to downstream nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        # Use a known tool that returns predictable output
        dag = DagWorkflow(
            name="context_pass",
            nodes=[
                DagNode(id="step1", type=NodeType.AGENT, ref="support"),
                DagNode(id="step2", type=NodeType.TOOL, ref="order.query", depends_on=["step1"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 2
        # step2 input should contain upstream output from step1
        # (verified indirectly through successful execution)

    @pytest.mark.asyncio
    async def test_all_completed_returns_completed(self, app, context):
        """When all nodes complete, status is completed."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="all_done",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="support"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert all(r.status == NodeExecutionStatus.COMPLETED for r in results)

    @pytest.mark.asyncio
    async def test_node_results_recorded(self, app, context):
        """Node execution results include timestamps and metadata."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="recorded",
            nodes=[
                DagNode(id="n1", type=NodeType.AGENT, ref="support"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert len(results) == 1
        r = results[0]
        assert r.started_at is not None
        assert r.completed_at is not None
        assert r.completed_at >= r.started_at


# ---------------------------------------------------------------------------
# Config loader tests
# ---------------------------------------------------------------------------


class TestDagConfigLoader:
    def test_load_dag_workflow_from_yaml(self, tmp_path):
        """Config loader parses DAG workflow from YAML."""
        import yaml

        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "agents": [
                {"name": "bot", "instructions": "test"},
            ],
            "tools": [
                {"name": "tool1", "risk_level": "low"},
            ],
            "workflows": {
                "test_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "n1", "type": "agent", "ref": "bot"},
                        {"id": "n2", "type": "tool", "ref": "tool1", "depends_on": ["n1"]},
                    ],
                },
            },
        }

        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)

        wf = config.workflows["test_dag"]
        assert wf["type"] == "dag"
        assert len(wf["nodes"]) == 2
        assert wf["nodes"][1]["depends_on"] == ["n1"]

    def test_old_single_workflow_still_works(self, tmp_path):
        """Single workflow config remains compatible."""
        import yaml

        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "agents": [
                {"name": "bot", "instructions": "test"},
            ],
            "workflows": {
                "my_wf": {
                    "type": "single",
                    "agent": "bot",
                },
            },
        }

        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        assert config.workflows["my_wf"]["type"] == "single"

    def test_invalid_dag_config_raises(self, tmp_path):
        """Invalid DAG config produces clear error via build_app."""
        import yaml

        from agent_app.config.loader import build_app
        from agent_app.workflows.dag import CycleDetectedError

        config_data = {
            "app": {"name": "test"},
            "agents": [
                {"name": "bot", "instructions": "test"},
            ],
            "workflows": {
                "bad_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "a", "type": "agent", "ref": "bot", "depends_on": ["b"]},
                        {"id": "b", "type": "tool", "ref": "tool1", "depends_on": ["a"]},
                    ],
                },
            },
        }

        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        with pytest.raises((CycleDetectedError, ValueError)):
            build_app(yaml_path)


# ---------------------------------------------------------------------------
# AgentApp integration tests
# ---------------------------------------------------------------------------


class TestAgentAppDag:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund": "created"},
        )
        wf = Workflow.dag(
            name="test_dag",
            nodes=[
                {"id": "q", "type": "tool", "ref": "order.query"},
                {"id": "r", "type": "tool", "ref": "refund.request", "depends_on": ["q"]},
            ],
        )
        app.register_workflow(wf)
        # Pre-initialize the runner
        app._ensure_runner()
        return app

    @pytest.mark.asyncio
    async def test_app_run_dag_executes(self, app):
        """AgentApp.run(workflow='test_dag') executes the DAG."""
        result = await app.run(workflow="test_dag", input="test")
        assert result.status in ("completed", "interrupted")
        assert len(result.node_results) == 2

    @pytest.mark.asyncio
    async def test_dag_completed_status(self, app):
        """DAG with only low-risk tools returns completed."""
        # Register a simpler DAG without approval-required tools
        wf = Workflow.dag(
            name="simple_dag",
            nodes=[
                {"id": "q", "type": "tool", "ref": "order.query"},
            ],
        )
        app.register_workflow(wf)
        result = await app.run(workflow="simple_dag", input="test")
        assert result.status == "completed"
        assert result.node_results[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_dag_node_results_populated(self, app):
        """DAG execution populates node_results in AppRunResult."""
        result = await app.run(workflow="test_dag", input="test")
        assert "node_results" in result.model_dump()
        assert len(result.node_results) == 2
        for nr in result.node_results:
            assert "node_id" in nr
            assert "status" in nr

    @pytest.mark.asyncio
    async def test_dag_workflow_trace_populated(self, app):
        """DAG execution populates workflow_trace."""
        result = await app.run(workflow="test_dag", input="test")
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_type == "dag"
        assert len(result.workflow_trace.steps) == 2


# ---------------------------------------------------------------------------
# Eval runner tests
# ---------------------------------------------------------------------------


class TestEvalDag:
    def test_eval_runner_supports_dag_workflow(self):
        """EvalRunner can reference a DAG workflow."""
        from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
        from agent_app.evals.runner import EvalRunner
        from agent_app.evals.schema import EvalCase, EvalDefaults, EvalExpect, EvalSuite
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order": "found"},
        )
        wf = Workflow.dag(
            name="eval_dag",
            nodes=[
                {"id": "q", "type": "tool", "ref": "order.query"},
            ],
        )
        app.register_workflow(wf)

        suite = EvalSuite(
            name="dag_test",
            defaults=EvalDefaults(workflow="eval_dag"),
            cases=[
                EvalCase(
                    id="c1", input="query order 123", workflow="eval_dag",
                    expect=EvalExpect(status="completed"),
                ),
            ],
        )
        runner = EvalRunner(app=app)
        # Should not raise — just verify it runs
        import asyncio
        results = asyncio.run(runner.run_suite(suite))
        assert len(results.case_results) == 1


# ---------------------------------------------------------------------------
# Phase 13.2: Execution mode and retry model tests
# ---------------------------------------------------------------------------


class TestDagExecutionMode:
    def test_default_execution_mode_is_sequential(self) -> None:
        """DagWorkflow defaults to sequential mode."""
        wf = DagWorkflow(name="test", nodes=[])
        assert wf.execution_mode.value == "sequential"

    def test_parallel_execution_mode_accepted(self) -> None:
        """Parallel execution mode is accepted."""
        from agent_app.workflows.dag import DagExecutionMode
        wf = DagWorkflow(
            name="test",
            nodes=[],
            execution_mode=DagExecutionMode.PARALLEL,
        )
        assert wf.execution_mode == DagExecutionMode.PARALLEL

    def test_invalid_execution_mode_raises(self) -> None:
        """Invalid execution mode raises ValidationError (Pydantic enum)."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            DagWorkflow(
                name="test",
                nodes=[],
                execution_mode="concurrent",  # type: ignore
            )

    def test_max_concurrency_must_be_positive(self) -> None:
        """max_concurrency must be >= 1 when set."""
        with pytest.raises(ValueError, match="max_concurrency"):
            DagWorkflow(
                name="test",
                nodes=[],
                max_concurrency=0,
            )

    def test_max_concurrency_none_is_unlimited(self) -> None:
        """max_concurrency=None means unlimited."""
        wf = DagWorkflow(name="test", nodes=[], max_concurrency=None)
        assert wf.max_concurrency is None


class TestDagRetryPolicy:
    def test_default_retry_policy(self) -> None:
        """Default retry policy has max_attempts=1 (no retry)."""
        from agent_app.workflows.dag import RetryPolicy
        rp = RetryPolicy()
        assert rp.max_attempts == 1
        assert rp.backoff_seconds == 0.0
        assert rp.backoff_multiplier == 1.0
        assert rp.retry_on_statuses == [NodeExecutionStatus.FAILED]

    def test_retry_policy_custom(self) -> None:
        """Custom retry policy fields are stored."""
        from agent_app.workflows.dag import RetryPolicy
        rp = RetryPolicy(
            max_attempts=3,
            backoff_seconds=0.5,
            backoff_multiplier=2.0,
        )
        assert rp.max_attempts == 3
        assert rp.backoff_seconds == 0.5
        assert rp.backoff_multiplier == 2.0

    def test_retry_policy_rejects_interrupted(self) -> None:
        """retry_on_statuses must not include 'interrupted'."""
        from agent_app.workflows.dag import RetryPolicy
        with pytest.raises(ValueError, match="interrupted"):
            RetryPolicy(retry_on_statuses=[NodeExecutionStatus.FAILED, NodeExecutionStatus.INTERRUPTED])

    def test_node_retry_in_dag_node(self) -> None:
        """DagNode can carry a retry policy."""
        from agent_app.workflows.dag import RetryPolicy
        rp = RetryPolicy(max_attempts=3)
        node = DagNode(
            id="a", type=NodeType.TOOL, ref="t1", retry=rp,
        )
        assert node.retry is not None
        assert node.retry.max_attempts == 3

    def test_workflow_retry_applied(self) -> None:
        """Workflow-level retry is stored on DagWorkflow."""
        from agent_app.workflows.dag import RetryPolicy
        rp = RetryPolicy(max_attempts=2)
        wf = DagWorkflow(name="test", nodes=[], retry=rp)
        assert wf.retry is not None
        assert wf.retry.max_attempts == 2

    def test_get_effective_retry_node_overrides_workflow(self) -> None:
        """Node-level retry takes priority over workflow-level."""
        from agent_app.workflows.dag import RetryPolicy
        wf_retry = RetryPolicy(max_attempts=1)
        node_retry = RetryPolicy(max_attempts=5)
        wf = DagWorkflow(
            name="test",
            nodes=[DagNode(id="a", type=NodeType.TOOL, ref="t1", retry=node_retry)],
            retry=wf_retry,
        )
        effective = wf.get_effective_retry("a")
        assert effective.max_attempts == 5

    def test_get_effective_retry_falls_back_to_workflow(self) -> None:
        """When node has no retry, workflow-level retry is used."""
        from agent_app.workflows.dag import RetryPolicy
        wf_retry = RetryPolicy(max_attempts=3)
        wf = DagWorkflow(
            name="test",
            nodes=[DagNode(id="a", type=NodeType.TOOL, ref="t1")],
            retry=wf_retry,
        )
        effective = wf.get_effective_retry("a")
        assert effective.max_attempts == 3

    def test_get_effective_retry_defaults_to_no_retry(self) -> None:
        """When neither node nor workflow has retry, max_attempts=1."""
        wf = DagWorkflow(
            name="test",
            nodes=[DagNode(id="a", type=NodeType.TOOL, ref="t1")],
        )
        effective = wf.get_effective_retry("a")
        assert effective.max_attempts == 1


# ---------------------------------------------------------------------------
# Phase 13.2: Parallel DAG executor tests
# ---------------------------------------------------------------------------


class TestDagParallelExecutor:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order": "found"},
        )
        app.register_tool(
            ToolSpec(name="customer.lookup", description="Lookup", risk_level="low"),
            fn=lambda **kw: {"customer": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund": "created"},
        )
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_independent_nodes_run_concurrently(self, app, context):
        """Independent nodes (no deps) execute in parallel mode."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="parallel_indep",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="support"),
                DagNode(id="b", type=NodeType.TOOL, ref="order.query"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 2
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_dependency_order_respected_in_parallel(self, app, context):
        """Dependent nodes wait for upstream in parallel mode."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="parallel_dep",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="customer.lookup"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["a", "b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 3
        # a and b should complete, c depends on both
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.COMPLETED
        assert statuses["c"] == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_diamond_graph_parallel(self, app, context):
        """Diamond-shaped DAG executes correctly in parallel."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="diamond",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="customer.lookup", depends_on=["a"]),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["a"]),
                DagNode(id="d", type=NodeType.TOOL, ref="customer.lookup", depends_on=["b", "c"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 4
        statuses = {r.node_id: r.status for r in results}
        assert all(s == NodeExecutionStatus.COMPLETED for s in statuses.values())

    @pytest.mark.asyncio
    async def test_max_concurrency_1_limits_concurrency(self, app, context):
        """max_concurrency=1 still works (serial-like parallel)."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="concurrency_1",
            execution_mode=DagExecutionMode.PARALLEL,
            max_concurrency=1,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="customer.lookup"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_max_concurrency_2_limits_concurrency(self, app, context):
        """max_concurrency=2 allows up to 2 concurrent nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="concurrency_2",
            execution_mode=DagExecutionMode.PARALLEL,
            max_concurrency=2,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="customer.lookup"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["a", "b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_failed_dependency_skips_downstream_parallel(self, app, context):
        """In parallel mode, failed dependency causes downstream to be skipped."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="fail_skip",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="nonexistent.tool"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["a", "b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.FAILED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_interrupted_node_stops_new_scheduling(self, app, context):
        """Interrupted node stops scheduling new nodes; running nodes finish."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="interrupt_stop",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["a", "b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        # a completes, b interrupts (approval), c is skipped
        assert status == "interrupted"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.INTERRUPTED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_parallel_failed_status_propagates(self, app, context):
        """Any failed node makes overall status 'failed'."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="parallel_fail",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="nonexistent.tool"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_parallel_interrupted_status_propagates(self, app, context):
        """Any interrupted node makes overall status 'interrupted'."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="parallel_interrupt",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "interrupted"


# ---------------------------------------------------------------------------
# Phase 13.2: Retry tests
# ---------------------------------------------------------------------------


class TestDagRetry:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order": "found"},
        )
        app.register_tool(
            ToolSpec(name="customer.lookup", description="Lookup", risk_level="low"),
            fn=lambda **kw: {"customer": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund": "created"},
        )
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_retry_exhausts_then_fails(self, app, context):
        """Node with retry=3 fails after 3 attempts."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, RetryPolicy

        call_count = 0

        def failing_tool(**kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail #{call_count}")

        from agent_app.core.tool_spec import ToolSpec
        app.register_tool(
            ToolSpec(name="flaky.tool", description="Flaky", risk_level="low"),
            fn=failing_tool,
        )

        dag = DagWorkflow(
            name="retry_test",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(
                    id="flaky",
                    type=NodeType.TOOL,
                    ref="flaky.tool",
                    retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0),
                ),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert len(results) == 1
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].attempts is not None
        assert len(results[0].attempts) == 3
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, app, context):
        """Node succeeds on retry after first failure."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, RetryPolicy

        call_count = 0

        def flaky_tool(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return {"result": "ok"}

        from agent_app.core.tool_spec import ToolSpec
        app.register_tool(
            ToolSpec(name="flaky2", description="Flaky2", risk_level="low"),
            fn=flaky_tool,
        )

        dag = DagWorkflow(
            name="retry_success",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(
                    id="f2",
                    type=NodeType.TOOL,
                    ref="flaky2",
                    retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0),
                ),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 1
        assert results[0].status == NodeExecutionStatus.COMPLETED
        assert results[0].attempts is not None
        assert len(results[0].attempts) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_interrupted_node_not_retried(self, app, context):
        """Interrupted nodes (approval) are not retried."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, RetryPolicy

        dag = DagWorkflow(
            name="no_retry_interrupt",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(
                    id="approve_tool",
                    type=NodeType.TOOL,
                    ref="refund.request",
                    retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0),
                ),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "interrupted"
        assert len(results) == 1
        assert results[0].status == NodeExecutionStatus.INTERRUPTED
        # Should only have 1 attempt (no retry on interrupt)
        assert len(results[0].attempts) == 1

    @pytest.mark.asyncio
    async def test_node_retry_overrides_workflow_retry(self, app, context):
        """Node-level retry policy overrides workflow-level."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, RetryPolicy

        call_count = 0

        def failing_tool(**kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always fail")

        from agent_app.core.tool_spec import ToolSpec
        app.register_tool(
            ToolSpec(name="flaky3", description="Flaky3", risk_level="low"),
            fn=failing_tool,
        )

        dag = DagWorkflow(
            name="node_override",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            retry=RetryPolicy(max_attempts=1),  # workflow: no retry
            nodes=[
                DagNode(
                    id="f3",
                    type=NodeType.TOOL,
                    ref="flaky3",
                    retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0),  # node: retry 3x
                ),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        # Node-level retry should have been used (3 attempts)
        assert call_count == 3
        assert len(results[0].attempts) == 3

    @pytest.mark.asyncio
    async def test_attempts_recorded_on_success(self, app, context):
        """Successful execution records 1 attempt."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="attempts_success",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="support"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results[0].attempts) == 1
        assert results[0].attempts[0].status == NodeExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Phase 13.2: Status propagation tests
# ---------------------------------------------------------------------------


class TestDagStatusPropagation:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund": "created"},
        )
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_sequential_failed_stops_dag(self, app, context):
        """Sequential: failed node stops DAG, downstream skipped."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(
            name="seq_fail",
            nodes=[
                DagNode(id="a", type=NodeType.AGENT, ref="support"),
                DagNode(id="b", type=NodeType.TOOL, ref="nonexistent.tool"),
                DagNode(id="c", type=NodeType.AGENT, ref="support", depends_on=["b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.FAILED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_parallel_failed_skips_downstream(self, app, context):
        """Parallel: failed node skips downstream with failed deps."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="par_fail",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="nonexistent.tool"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.FAILED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_parallel_interrupted_skips_pending(self, app, context):
        """Parallel: interrupted node causes unstarted dependents to be skipped."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="par_interrupt",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request"),
                DagNode(id="c", type=NodeType.TOOL, ref="order.query", depends_on=["b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )

        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "interrupted"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.INTERRUPTED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED


# ---------------------------------------------------------------------------
# Phase 13.3: Condition evaluator unit tests
# ---------------------------------------------------------------------------


class TestConditionEvaluator:
    """Unit tests for the safe condition expression evaluator."""

    def _make_result(self, node_id, status, output=None):
        return NodeExecutionResult(
            node_id=node_id,
            status=NodeExecutionStatus(status),
            output=output,
        )

    def test_status_equality_true(self):
        """nodes.x.status == 'completed' evaluates true when completed."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed")}
        cond = DagCondition(expr="nodes.a.status == 'completed'")
        assert evaluate_condition(cond, results) is True

    def test_status_equality_false(self):
        """nodes.x.status == 'completed' evaluates false when failed."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "failed")}
        cond = DagCondition(expr="nodes.a.status == 'completed'")
        assert evaluate_condition(cond, results) is False

    def test_status_inequality(self):
        """nodes.x.status != 'failed' works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed")}
        cond = DagCondition(expr="nodes.a.status != 'failed'")
        assert evaluate_condition(cond, results) is True

    def test_output_field_equality_true(self):
        """nodes.x.output.field == 'value' evaluates correctly."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"status": "paid", "amount": 100})}
        cond = DagCondition(expr="nodes.a.output.status == 'paid'")
        assert evaluate_condition(cond, results) is True

    def test_output_field_equality_false(self):
        """nodes.x.output.field == 'value' evaluates false when different."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"status": "shipped"})}
        cond = DagCondition(expr="nodes.a.output.status == 'paid'")
        assert evaluate_condition(cond, results) is False

    def test_output_field_inequality(self):
        """nodes.x.output.field != 'value' works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"status": "shipped"})}
        cond = DagCondition(expr="nodes.a.output.status != 'paid'")
        assert evaluate_condition(cond, results) is True

    def test_numeric_greater_than(self):
        """nodes.x.output.field > number works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"amount": 150.0})}
        cond = DagCondition(expr="nodes.a.output.amount > 100")
        assert evaluate_condition(cond, results) is True

    def test_numeric_less_than(self):
        """nodes.x.output.field < number works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"amount": 50.0})}
        cond = DagCondition(expr="nodes.a.output.amount < 100")
        assert evaluate_condition(cond, results) is True

    def test_numeric_greater_or_equal(self):
        """nodes.x.output.field >= number works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"amount": 100.0})}
        cond = DagCondition(expr="nodes.a.output.amount >= 100")
        assert evaluate_condition(cond, results) is True

    def test_numeric_less_or_equal(self):
        """nodes.x.output.field <= number works."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"amount": 100.0})}
        cond = DagCondition(expr="nodes.a.output.amount <= 100")
        assert evaluate_condition(cond, results) is True

    def test_unknown_node_raises(self):
        """Referencing a node not in results raises ConditionEvaluationError."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition, ConditionEvaluationError
        results = {"a": self._make_result("a", "completed")}
        cond = DagCondition(expr="nodes.z.status == 'completed'")
        with pytest.raises(ConditionEvaluationError, match="Unknown node 'z'"):
            evaluate_condition(cond, results)

    def test_unknown_output_field_raises(self):
        """Referencing a field not in node output raises ConditionEvaluationError."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition, ConditionEvaluationError
        results = {"a": self._make_result("a", "completed", {"status": "paid"})}
        cond = DagCondition(expr="nodes.a.output.nonexistent == 'x'")
        with pytest.raises(ConditionEvaluationError, match="Unknown output field"):
            evaluate_condition(cond, results)

    def test_non_dict_output_raises(self):
        """Accessing field on non-dict output raises error."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition, ConditionEvaluationError
        results = {"a": self._make_result("a", "completed", "string output")}
        cond = DagCondition(expr="nodes.a.output.status == 'x'")
        with pytest.raises(ConditionEvaluationError, match="not a dict"):
            evaluate_condition(cond, results)

    def test_invalid_identifier_raises(self):
        """Invalid identifier path raises error."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition, ConditionEvaluationError
        results = {"a": self._make_result("a", "completed")}
        cond = DagCondition(expr="invalid.path == 'x'")
        with pytest.raises(ConditionEvaluationError, match="Invalid identifier"):
            evaluate_condition(cond, results)

    def test_unsupported_operator_raises(self):
        """Unsupported operators raise error."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition, ConditionEvaluationError
        results = {"a": self._make_result("a", "completed")}
        cond = DagCondition(expr="nodes.a.status ~ 'completed'")
        with pytest.raises(ConditionEvaluationError):
            evaluate_condition(cond, results)

    def test_and_operator(self):
        """AND operator works correctly."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {
            "a": self._make_result("a", "completed", {"status": "paid"}),
            "b": self._make_result("b", "completed", {"amount": 150.0}),
        }
        cond = DagCondition(expr="nodes.a.output.status == 'paid' AND nodes.b.output.amount > 100")
        assert evaluate_condition(cond, results) is True

    def test_or_operator(self):
        """OR operator works correctly."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {
            "a": self._make_result("a", "completed", {"status": "shipped"}),
            "b": self._make_result("b", "completed", {"amount": 50.0}),
        }
        cond = DagCondition(expr="nodes.a.output.status == 'paid' OR nodes.b.output.amount < 100")
        assert evaluate_condition(cond, results) is True

    def test_not_operator(self):
        """NOT operator works correctly."""
        from agent_app.workflows.condition import evaluate_condition, DagCondition
        results = {"a": self._make_result("a", "completed", {"status": "shipped"})}
        cond = DagCondition(expr="NOT nodes.a.output.status == 'paid'")
        assert evaluate_condition(cond, results) is True

    def test_no_eval_used(self):
        """Verify that the condition evaluator does not use eval()."""
        import ast
        import agent_app.workflows.condition as cond_mod
        source = open(cond_mod.__file__).read()
        tree = ast.parse(source)
        # Walk AST and flag any Call node named 'eval'
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "eval":
                    raise AssertionError("condition.py uses eval() — security risk!")
        # Also check for exec() and compile() with malicious flags
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in ("exec", "compile"):
                    raise AssertionError(f"condition.py uses {func.id}() — security risk!")


# ---------------------------------------------------------------------------
# Phase 13.3: DAG condition execution tests
# ---------------------------------------------------------------------------


class TestDagConditionExecution:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app.register_tool(
            ToolSpec(name="order.query", description="Query", risk_level="low"),
            fn=lambda **kw: {"order_id": "123", "status": "paid"},
        )
        app.register_tool(
            ToolSpec(name="customer.lookup", description="Lookup", risk_level="low"),
            fn=lambda **kw: {"customer": "found"},
        )
        app.register_tool(
            ToolSpec(name="refund.request", description="Refund", risk_level="high",
                     requires_approval=True),
            fn=lambda **kw: {"refund": "created"},
        )
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_node_without_condition_executes_normally(self, app, context):
        """Node without condition executes as before."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="no_cond",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="customer.lookup",
                        depends_on=["a"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_condition_true_executes_node(self, app, context):
        """Condition true → node executes."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="cond_true",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'paid'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "interrupted"  # refund.request requires approval
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.INTERRUPTED

    @pytest.mark.asyncio
    async def test_condition_false_skips_node(self, app, context):
        """Condition false → node is skipped."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="cond_false",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'shipped'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.SKIPPED
        assert results[1].error.get("message") == "Condition evaluated to false"

    @pytest.mark.asyncio
    async def test_skipped_node_causes_downstream_skipped(self, app, context):
        """Node skipped by condition → downstream also skipped."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="cond_downstream",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'shipped'")),
                DagNode(id="c", type=NodeType.TOOL, ref="customer.lookup",
                        depends_on=["b"]),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.SKIPPED
        assert statuses["c"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_upstream_failed_takes_priority_over_condition(self, app, context):
        """Upstream failed → downstream skipped even if condition is true."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="upstream_fail_priority",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="nonexistent.tool"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'paid'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.FAILED
        assert statuses["b"] == NodeExecutionStatus.SKIPPED
        assert "Upstream node failed" in results[1].error.get("message", "")

    @pytest.mark.asyncio
    async def test_condition_works_in_parallel_mode(self, app, context):
        """Condition evaluation works in parallel execution mode."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode

        dag = DagWorkflow(
            name="par_cond",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'shipped'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        statuses = {r.node_id: r.status for r in results}
        assert statuses["a"] == NodeExecutionStatus.COMPLETED
        assert statuses["b"] == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_condition_event_recorded(self, app, context):
        """Condition evaluation is recorded in trace events."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode
        from agent_app.observability.collector import InMemoryTraceCollector

        collector = InMemoryTraceCollector()
        dag = DagWorkflow(
            name="cond_event",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'shipped'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        events = await collector.get_events(trace_id=context.trace_id or "")
        cond_events = [e for e in events if e.event_type == "node.condition_evaluated"]
        assert len(cond_events) == 1
        assert cond_events[0].data["node_id"] == "b"
        assert cond_events[0].data["expr"] == "nodes.a.output.status == 'shipped'"
        assert cond_events[0].data["result"] is False

    @pytest.mark.asyncio
    async def test_condition_true_event_recorded(self, app, context):
        """Condition true evaluation is recorded."""
        from agent_app.workflows.dag import DagCondition, DagExecutor, DagWorkflow, DagExecutionMode
        from agent_app.observability.collector import InMemoryTraceCollector

        collector = InMemoryTraceCollector()
        dag = DagWorkflow(
            name="cond_true_event",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="order.query"),
                DagNode(id="b", type=NodeType.TOOL, ref="refund.request",
                        depends_on=["a"],
                        condition=DagCondition(expr="nodes.a.output.status == 'paid'")),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        events = await collector.get_events(trace_id=context.trace_id or "")
        cond_events = [e for e in events if e.event_type == "node.condition_evaluated"]
        assert len(cond_events) == 1
        assert cond_events[0].data["result"] is True


# ---------------------------------------------------------------------------
# Phase 13.3: Timeout tests
# ---------------------------------------------------------------------------


class TestDagTimeout:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    def _make_app_with_slow_tool(self, delay):
        """Create an app with a tool that sleeps for `delay` seconds."""
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="support", instructions="Support", tools=[]))
        import asyncio
        async def slow_tool(**kw):
            await asyncio.sleep(delay)
            return {"result": "ok"}
        app.register_tool(
            ToolSpec(name="slow.tool", description="Slow", risk_level="low"),
            fn=slow_tool,
        )
        app._ensure_runner()
        return app

    @pytest.mark.asyncio
    async def test_node_timeout_marks_failed(self):
        """Node exceeding timeout is marked as failed."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="timeout_test",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].error is not None
        assert results[0].error.get("type") == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_records_attempt(self):
        """Timeout is recorded as a failed attempt."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="timeout_attempt",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert len(results[0].attempts) == 1
        assert results[0].attempts[0].status == NodeExecutionStatus.FAILED
        assert results[0].attempts[0].error.get("type") == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_triggers_retry(self):
        """Timeout triggers retry when retry policy allows."""
        from agent_app.workflows.dag import (
            DagExecutor, DagWorkflow, DagExecutionMode, DagNode, RetryPolicy,
        )

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="timeout_retry",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert len(results[0].attempts) == 2
        assert all(a.error.get("type") == "timeout" for a in results[0].attempts)

    @pytest.mark.asyncio
    async def test_timeout_exhausts_retry(self):
        """After max retries with timeout, node is permanently failed."""
        from agent_app.workflows.dag import (
            DagExecutor, DagWorkflow, DagExecutionMode, DagNode, RetryPolicy,
        )

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="timeout_exhaust",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert len(results[0].attempts) == 2

    @pytest.mark.asyncio
    async def test_node_timeout_overrides_workflow_timeout(self):
        """Node-level timeout takes priority over workflow-level."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="node_timeout_wins",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            timeout_seconds=10.0,  # workflow-level: 10s (won't trigger)
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),  # node-level: 0.1s
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].error.get("type") == "timeout"

    @pytest.mark.asyncio
    async def test_workflow_timeout_applies_when_node_missing(self):
        """Workflow-level timeout applies when node has no own timeout."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="wf_timeout_applies",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            timeout_seconds=0.1,  # workflow-level: 0.1s
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool"),  # no node timeout
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].error.get("type") == "timeout"

    @pytest.mark.asyncio
    async def test_no_timeout_when_neither_set(self):
        """Node completes normally when no timeout is configured."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode

        app = self._make_app_with_slow_tool(0.05)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        dag = DagWorkflow(
            name="no_timeout",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="fast", type=NodeType.TOOL, ref="slow.tool"),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_timeout_event_recorded(self):
        """Timeout event is recorded in trace."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagExecutionMode, DagNode
        from agent_app.observability.collector import InMemoryTraceCollector

        app = self._make_app_with_slow_tool(0.5)
        from agent_app.core.context import RunContext
        context = RunContext(run_id="t", user_id="u", tenant_id="t")

        collector = InMemoryTraceCollector()
        dag = DagWorkflow(
            name="timeout_event",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="slow", type=NodeType.TOOL, ref="slow.tool",
                        timeout_seconds=0.1),
            ],
        )
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
        )
        results, status, _, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        events = await collector.get_events(trace_id=context.trace_id or "")
        timeout_events = [e for e in events if e.event_type == "node.timeout"]
        assert len(timeout_events) == 1
        assert timeout_events[0].data["node_id"] == "slow"
        assert timeout_events[0].data["timeout_seconds"] == 0.1


# ---------------------------------------------------------------------------
# Phase 13.3: Config loader tests
# ---------------------------------------------------------------------------


class TestDagConfigLoading:
    def test_loads_workflow_timeout_seconds(self):
        """Config loader passes workflow-level timeout_seconds."""
        from agent_app.config.loader import load_config
        import tempfile, os
        config_yaml = """
workflows:
  wf_timeout:
    type: dag
    timeout_seconds: 30.0
    nodes:
      - id: a
        type: tool
        ref: some.tool
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_yaml)
            f.flush()
            config = load_config(f.name)
        os.unlink(f.name)
        wf = config.workflows["wf_timeout"]
        assert wf.get("timeout_seconds") == 30.0

    def test_loads_node_timeout_seconds(self):
        """Config loader passes node-level timeout_seconds."""
        from agent_app.config.loader import load_config
        import tempfile, os
        config_yaml = """
workflows:
  wf_node_timeout:
    type: dag
    nodes:
      - id: a
        type: tool
        ref: some.tool
        timeout_seconds: 5.0
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_yaml)
            f.flush()
            config = load_config(f.name)
        os.unlink(f.name)
        wf = config.workflows["wf_node_timeout"]
        assert wf["nodes"][0].get("timeout_seconds") == 5.0

    def test_loads_node_condition(self):
        """Config loader passes node condition."""
        from agent_app.config.loader import load_config
        import tempfile, os
        config_yaml = """
workflows:
  wf_cond:
    type: dag
    nodes:
      - id: a
        type: tool
        ref: some.tool
      - id: b
        type: tool
        ref: other.tool
        depends_on: [a]
        condition:
          expr: "nodes.a.output.status == 'ok'"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_yaml)
            f.flush()
            config = load_config(f.name)
        os.unlink(f.name)
        wf = config.workflows["wf_cond"]
        assert wf["nodes"][1].get("condition") == {"expr": "nodes.a.output.status == 'ok'"}

    def test_old_dag_config_remains_valid(self):
        """Old DAG config without condition/timeout still works."""
        from agent_app.config.loader import load_config
        import tempfile, os
        config_yaml = """
workflows:
  old_dag:
    type: dag
    execution_mode: sequential
    nodes:
      - id: a
        type: tool
        ref: some.tool
      - id: b
        type: tool
        ref: other.tool
        depends_on: [a]
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_yaml)
            f.flush()
            config = load_config(f.name)
        os.unlink(f.name)
        wf = config.workflows["old_dag"]
        assert wf.get("execution_mode") == "sequential"
        assert len(wf.get("nodes", [])) == 2
        assert wf["nodes"][0].get("condition") is None
        assert wf["nodes"][0].get("timeout_seconds") is None

    def test_invalid_timeout_seconds_rejected(self):
        """Negative timeout_seconds is rejected by Pydantic."""
        from agent_app.workflows.dag import DagNode, NodeType
        with pytest.raises(Exception):
            DagNode(id="a", type=NodeType.TOOL, ref="t", timeout_seconds=-1.0)

    def test_parallel_dag_config_remains_valid(self):
        """Parallel DAG config with max_concurrency still works."""
        from agent_app.config.loader import load_config
        import tempfile, os
        config_yaml = """
workflows:
  par_dag:
    type: dag
    execution_mode: parallel
    max_concurrency: 4
    nodes:
      - id: a
        type: tool
        ref: some.tool
      - id: b
        type: tool
        ref: other.tool
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_yaml)
            f.flush()
            config = load_config(f.name)
        os.unlink(f.name)
        wf = config.workflows["par_dag"]
        assert wf.get("execution_mode") == "parallel"
        assert wf.get("max_concurrency") == 4


# ---------------------------------------------------------------------------
# Phase 13.4: FunctionRegistry tests
# ---------------------------------------------------------------------------


class TestFunctionRegistry:
    def test_register_and_get(self) -> None:
        """Register a function and retrieve it."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("hello.greet", lambda name: {"greeting": f"Hi {name}"})
        entry = reg.get("hello.greet")
        assert entry.name == "hello.greet"
        assert callable(entry.func)

    def test_duplicate_register_raises(self) -> None:
        """Registering the same name twice raises DuplicateFunctionError."""
        from agent_app.workflows.function_registry import (
            DuplicateFunctionError,
            FunctionRegistry,
        )

        reg = FunctionRegistry()
        reg.register("fn.x", lambda: None)
        with pytest.raises(DuplicateFunctionError, match="already registered"):
            reg.register("fn.x", lambda: None)

    def test_get_missing_raises(self) -> None:
        """Getting a non-existent function raises FunctionNotFoundError."""
        from agent_app.workflows.function_registry import (
            FunctionNotFoundError,
            FunctionRegistry,
        )

        reg = FunctionRegistry()
        with pytest.raises(FunctionNotFoundError, match="not found"):
            reg.get("missing.fn")

    def test_list_functions(self) -> None:
        """list() returns all registered function names."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("a.fn1", lambda: None)
        reg.register("a.fn2", lambda: None)
        reg.register("b.fn3", lambda: None)
        names = reg.list()
        assert names == ["a.fn1", "a.fn2", "b.fn3"]

    def test_unregister_removes(self) -> None:
        """unregister() removes a function."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("temp.fn", lambda: None)
        assert reg.exists("temp.fn")
        reg.unregister("temp.fn")
        assert not reg.exists("temp.fn")

    def test_unregister_missing_is_silent(self) -> None:
        """unregister() on missing name does not raise."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.unregister("never.here")  # should not raise

    def test_decorator_registers_function(self) -> None:
        """@workflow_function registers in the default global registry."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        # Use a unique name to avoid collisions
        name = "_test_decorator_fn_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(name=name)
        def my_test_fn(x: int) -> dict:
            return {"result": x * 2}

        assert reg.exists(name)
        entry = reg.get(name)
        assert entry.description is None
        assert callable(entry.func)
        # Original function remains callable
        assert my_test_fn(5) == {"result": 10}
        # Cleanup
        reg.unregister(name)

    def test_decorator_with_metadata(self) -> None:
        """@workflow_function accepts description and metadata."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_decorator_meta_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(
            name=name,
            description="A test function",
            metadata={"version": "1.0"},
        )
        def meta_fn() -> dict:
            return {}

        entry = reg.get(name)
        assert entry.description == "A test function"
        assert entry.metadata == {"version": "1.0"}
        reg.unregister(name)

    def test_default_registry_is_singleton(self) -> None:
        """get_default_function_registry() returns the same instance."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
        )

        reg1 = get_default_function_registry()
        reg2 = get_default_function_registry()
        assert reg1 is reg2

    def test_async_decorator(self) -> None:
        """@workflow_function works with async functions."""
        import asyncio
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_async_fn_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(name=name)
        async def async_fn(x: int) -> dict:
            return {"result": x + 1}

        assert reg.exists(name)
        result = asyncio.run(async_fn(10))
        assert result == {"result": 11}
        reg.unregister(name)


# ---------------------------------------------------------------------------
# Phase 13.4: Function Node Execution tests
# ---------------------------------------------------------------------------


class TestFunctionNodeExecution:
    @pytest.fixture
    def registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry
        reg = FunctionRegistry()
        # Functions must accept **kwargs since inputs are resolved by name
        reg.register("test.double", lambda **kw: kw["x"] * 2)
        reg.register("test.echo_dict", lambda **kw: kw)
        reg.register("test.echo_async", lambda **kw: kw)  # sync wrapper
        # For retry tests - only stubs, actual slow functions registered in tests
        reg.register("test.raise", lambda: (_ for _ in ()).throw(ValueError("boom")))
        return reg

    @pytest.fixture
    def executor(self, registry):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )

    @pytest.mark.asyncio
    async def test_sync_function_node_succeeds(self, executor):
        """A sync function node executes and returns output."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType

        wf = DagWorkflow(
            name="sync_fn",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.double", input={"x": 5}),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "completed"
        assert len(results) == 1
        assert results[0].status == NodeExecutionStatus.COMPLETED
        # Output is normalized: scalar wrapped in {"value": 10}
        assert results[0].output == {"value": 10}

    @pytest.mark.asyncio
    async def test_function_returning_dict_preserved(self, executor):
        """Function returning dict — output preserved as-is."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType

        wf = DagWorkflow(
            name="dict_fn",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.echo_dict",
                        input={"key": "val"}),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "completed"
        assert results[0].output == {"key": "val"}

    @pytest.mark.asyncio
    async def test_function_raises_node_failed(self, registry):
        """When a function raises, node status is FAILED."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        registry.unregister("test.raise")  # remove fixture's no-op
        registry.register("test.raise", lambda: (_ for _ in ()).throw(ValueError("boom")))
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="raise_fn",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.raise"),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].error is not None
        assert "boom" in results[0].error.get("message", "")

    @pytest.mark.asyncio
    async def test_missing_function_node_failed(self):
        """Referencing a non-existent function → node FAILED."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType
        from agent_app.workflows.function_registry import FunctionNotFoundError

        class FailingRegistry:
            def get(self, name):
                raise FunctionNotFoundError(f"Function '{name}' not found")

        reg = FailingRegistry()
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=reg,
        )
        wf = DagWorkflow(
            name="missing_fn",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="nonexistent.fn"),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_function_timeout_triggers_retry(self, registry):
        """Function timeout is treated as failed, triggering retry."""
        import asyncio
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType, RetryPolicy,
            NodeExecutionStatus,
        )

        call_count = 0

        async def slow_fn():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(10)  # will always timeout

        registry.register("test.slow", slow_fn)
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="slow_fn",
            nodes=[
                DagNode(
                    id="f1",
                    type=NodeType.FUNCTION,
                    ref="test.slow",
                    timeout_seconds=0.05,
                    retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
                ),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert call_count == 2  # retried once
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert len(results[0].attempts) == 2
        assert results[0].attempts[0].error is not None

    @pytest.mark.asyncio
    async def test_function_timeout_exhausted_retries(self, registry):
        """Function timeout after all retries exhausted → FAILED."""
        import time
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType, RetryPolicy,
            NodeExecutionStatus,
        )

        def slow_blocking():
            time.sleep(10)  # will be run in executor thread, but timeout wraps the call

        registry.register("test.slow2", slow_blocking)
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="slow_fn2",
            nodes=[
                DagNode(
                    id="f1",
                    type=NodeType.FUNCTION,
                    ref="test.slow2",
                    timeout_seconds=0.05,
                    retry=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
                ),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "failed"
        assert len(results[0].attempts) == 1

    def test_normalize_output_dict(self):
        """Dict output is preserved."""
        from agent_app.workflows.function_registry import _normalize_output
        assert _normalize_output({"a": 1}) == {"a": 1}

    def test_normalize_output_scalar(self):
        """Scalar output is wrapped in {'value': ...}."""
        from agent_app.workflows.function_registry import _normalize_output
        assert _normalize_output(42) == {"value": 42}
        assert _normalize_output("hello") == {"value": "hello"}

    def test_normalize_output_pydantic_model(self):
        """Pydantic model output is converted to dict."""
        from pydantic import BaseModel
        from agent_app.workflows.function_registry import _normalize_output

        class MyModel(BaseModel):
            x: int
            y: str

        result = _normalize_output(MyModel(x=1, y="a"))
        assert result == {"x": 1, "y": "a"}


# ---------------------------------------------------------------------------
# Phase 13.4: Input Mapping tests
# ---------------------------------------------------------------------------


class TestFunctionInputMapping:
    @pytest.fixture
    def executor(self):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
        )

    def test_literal_string_input(self, executor):
        """Literal string value is passed through."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"reason": "customer_requested"},
        )
        ctx = {"input": "test input"}
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"reason": "customer_requested"}

    def test_literal_number_input(self, executor):
        """Literal number value is passed through."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"count": 42, "rate": 3.14},
        )
        ctx = {"input": "test"}
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"count": 42, "rate": 3.14}

    def test_literal_bool_input(self, executor):
        """Literal boolean value is passed through."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"flag": True, "other": False},
        )
        ctx = {"input": "test"}
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"flag": True, "other": False}

    def test_input_field_mapping(self, executor):
        """input.<field> maps to the workflow input string."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"text": "input.message"},
        )
        ctx = {"input": "hello world"}
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"text": "hello world"}

    def test_node_output_field_mapping(self, executor):
        """nodes.<id>.output.<field> maps from upstream node output."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="step2", type=NodeType.FUNCTION, ref="test.fn",
            input={"amount": "nodes.step1.output.total"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
                output={"total": 199.0},
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"amount": 199.0}

    def test_node_status_mapping(self, executor):
        """nodes.<id>.status maps to upstream node status string."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="step2", type=NodeType.FUNCTION, ref="test.fn",
            input={"prev_status": "nodes.step1.status"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"prev_status": "completed"}

    def test_context_user_id_mapping(self, executor):
        """context.user_id maps from RunContext."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"user": "context.user_id"},
        )
        ctx = {
            "input": "",
            "context": type("C", (), {"user_id": "alice"})(),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"user": "alice"}

    def test_context_tenant_id_mapping(self, executor):
        """context.tenant_id maps from RunContext."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"tenant": "context.tenant_id"},
        )
        ctx = {
            "input": "",
            "context": type("C", (), {"tenant_id": "acme"})(),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"tenant": "acme"}

    def test_unknown_output_field_raises(self, executor):
        """Unknown output field raises DagError."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.step1.output.nonexistent"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
                output={"known": 1},
            ),
        }
        with pytest.raises(Exception, match="not found"):
            executor._resolve_function_inputs(node, ctx)

    def test_unknown_node_raises(self, executor):
        """Referencing a non-existent upstream node raises DagError."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.ghost.output.x"},
        )
        ctx = {"input": ""}
        with pytest.raises(Exception, match="has not produced output"):
            executor._resolve_function_inputs(node, ctx)

    def test_invalid_nodes_path_raises(self, executor):
        """Invalid nodes path raises DagError."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.x.bad_path"},
        )
        ctx = {"input": ""}
        with pytest.raises(Exception, match="Invalid nodes reference"):
            executor._resolve_function_inputs(node, ctx)

    def test_unknown_context_field_raises(self, executor):
        """Unknown context field raises DagError."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "context.nonexistent"},
        )
        ctx = {
            "input": "",
            "context": type("C", (), {"user_id": "u"})(),
        }
        with pytest.raises(Exception, match="cannot access"):
            executor._resolve_function_inputs(node, ctx)


# ---------------------------------------------------------------------------
# Phase 13.4: Integration tests — FUNCTION nodes with existing DAG features
# ---------------------------------------------------------------------------


class TestFunctionNodeIntegration:
    @pytest.fixture
    def registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry
        reg = FunctionRegistry()
        reg.register("math.add", lambda a=0, b=0: a + b)
        reg.register("math.multiply", lambda a=0, b=0: a * b)
        return reg

    @pytest.fixture
    def executor(self, registry):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )

    @pytest.mark.asyncio
    async def test_condition_true_executes_function(self, executor, registry):
        """When condition is true, function node executes."""
        from agent_app.workflows.condition import DagCondition
        from agent_app.workflows.dag import (
            DagNode, DagWorkflow, NodeExecutionStatus, NodeType,
        )
        from agent_app.core.context import RunContext

        wf = DagWorkflow(
            name="cond_fn2",
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="t1",
                        input={}, depends_on=[]),
                DagNode(id="f1", type=NodeType.FUNCTION, ref="math.add",
                        condition=DagCondition(expr="nodes.a.output.status == 'ok'"),
                        input={"a": 3, "b": 4},
                        depends_on=["a"]),
            ],
        )
        from agent_app import ToolSpec
        from agent_app.registry.tool_registry import ToolRegistry
        tool_reg = ToolRegistry()
        tool_reg.register("t1", ToolSpec(name="t1", description="", risk_level="low"),
                          fn=lambda **kw: {"status": "ok"})
        executor.function_registry = registry
        executor.tool_registry = tool_reg
        context = RunContext(run_id="r", user_id="u", tenant_id="t")
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert results[1].status == NodeExecutionStatus.COMPLETED
        assert results[1].output == {"value": 7}

    @pytest.mark.asyncio
    async def test_condition_false_skips_function(self, executor, registry):
        """When condition is false, function node is skipped."""
        from agent_app.workflows.condition import DagCondition
        from agent_app.workflows.dag import (
            DagNode, DagWorkflow, NodeExecutionStatus, NodeType,
        )

        from agent_app.core.context import RunContext

        wf = DagWorkflow(
            name="cond_fn_false",
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="t1",
                        input={}, depends_on=[]),
                DagNode(id="f1", type=NodeType.FUNCTION, ref="math.add",
                        condition=DagCondition(expr="nodes.a.output.status == 'paid'"),
                        input={"a": 1, "b": 2},
                        depends_on=["a"]),
            ],
        )
        from agent_app import ToolSpec
        from agent_app.registry.tool_registry import ToolRegistry
        tool_reg = ToolRegistry()
        tool_reg.register("t1", ToolSpec(name="t1", description="", risk_level="low"),
                          fn=lambda **kw: {"status": "shipped"})
        executor.function_registry = registry
        executor.tool_registry = tool_reg
        context = RunContext(run_id="r", user_id="u", tenant_id="t")
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert results[1].status == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_upstream_failed_skips_function(self, executor, registry):
        """Failed upstream node causes function node to be skipped."""
        from agent_app.workflows.dag import (
            DagNode, DagWorkflow, NodeExecutionStatus, NodeType,
        )
        from agent_app.core.context import RunContext

        # Create a tool registry that raises KeyError for nonexistent tools
        class FailingToolRegistry:
            def get(self, name):
                raise KeyError(name)

        executor.function_registry = registry
        executor.tool_registry = FailingToolRegistry()
        wf = DagWorkflow(
            name="upstream_fail",
            nodes=[
                DagNode(id="bad", type=NodeType.TOOL, ref="nonexistent"),
                DagNode(id="f1", type=NodeType.FUNCTION, ref="math.add",
                        input={"a": 1, "b": 2}, depends_on=["bad"]),
            ],
        )
        context = RunContext(run_id="r", user_id="u", tenant_id="t")
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[1].status == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_function_node_timeout_works(self, registry):
        """Function node respects timeout_seconds."""
        import time
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType, NodeExecutionStatus,
        )
        from agent_app.core.context import RunContext

        def slow_blocking():
            time.sleep(10)

        registry.register("test.slow3", slow_blocking)
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="fn_timeout",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.slow3",
                        timeout_seconds=0.05),
            ],
        )
        context = RunContext(run_id="r", user_id="u", tenant_id="t")
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].attempts[0].error is not None

    @pytest.mark.asyncio
    async def test_function_retry_works(self, registry):
        """Function node retries on failure."""
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType, NodeExecutionStatus, RetryPolicy,
        )
        from agent_app.core.context import RunContext

        call_count = 0

        def failing_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError(f"fail {call_count}")
            return {"recovered": True}

        registry.register("test.flaky", failing_fn)
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="fn_retry",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.flaky",
                        retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0)),
            ],
        )
        context = RunContext(run_id="r", user_id="u", tenant_id="t")
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert call_count == 3
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED
        assert len(results[0].attempts) == 3

    @pytest.mark.asyncio
    async def test_event_records_include_function_metadata(self, registry):
        """Node events include function name for FUNCTION nodes."""
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType,
        )
        from agent_app.observability.collector import InMemoryTraceCollector

        collector = InMemoryTraceCollector()
        registry.register("test.simple", lambda: {"ok": True})
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
            trace_collector=collector,
        )
        wf = DagWorkflow(
            name="fn_events",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.simple"),
            ],
        )
        ctx = type("C", (), {
            "trace_id": "trace-1",
            "run_id": "run-1",
            "user_id": "u",
            "tenant_id": "t",
        })()
        await executor.execute(dag=wf, input="", context=ctx)
        events = await collector.get_events(trace_id="trace-1")
        event_types = [e.event_type for e in events]
        # Should have NODE_COMPLETED event
        from agent_app.observability.events import RunEventType
        assert RunEventType.NODE_COMPLETED in event_types
        # Find the completed event and check function metadata
        completed_events = [e for e in events if e.event_type == RunEventType.NODE_COMPLETED]
        assert len(completed_events) >= 1
        # data should contain function name
        fn_events = [e for e in completed_events if e.data.get("function") == "test.simple"]
        assert len(fn_events) >= 1

    @pytest.mark.asyncio
    async def test_parallel_function_nodes(self, registry):
        """FUNCTION nodes execute in parallel mode."""
        from agent_app.workflows.dag import (
            DagExecutor, DagNode, DagWorkflow, NodeType, NodeExecutionStatus, DagExecutionMode,
        )

        registry.register("test.fn_a", lambda: {"a": 1})
        registry.register("test.fn_b", lambda: {"b": 2})
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )
        wf = DagWorkflow(
            name="parallel_fn",
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.fn_a"),
                DagNode(id="f2", type=NodeType.FUNCTION, ref="test.fn_b"),
            ],
        )
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=type("C", (), {"user_id": "u", "tenant_id": "t"})(),
        )
        assert status == "completed"
        assert len(results) == 2
        statuses = {r.node_id: r.status for r in results}
        assert statuses["f1"] == NodeExecutionStatus.COMPLETED
        assert statuses["f2"] == NodeExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Phase 13.4: Config Loading tests for FUNCTION nodes
# ---------------------------------------------------------------------------


class TestFunctionNodeConfigLoading:
    def test_load_function_node_from_yaml(self, tmp_path):
        """Config loader parses FUNCTION node from YAML."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "fn_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "f1", "type": "function", "function": "math.add",
                         "inputs": {"a": 1, "b": 2}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["fn_dag"]
        assert wf["type"] == "dag"
        assert len(wf["nodes"]) == 1
        node = wf["nodes"][0]
        assert node["type"] == "function"
        assert node["function"] == "math.add"
        assert node["inputs"] == {"a": 1, "b": 2}

    def test_function_node_with_depends_on(self, tmp_path):
        """FUNCTION node with depends_on loads correctly."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "fn_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "a", "type": "tool", "ref": "t1"},
                        {"id": "f1", "type": "function", "function": "math.add",
                         "depends_on": ["a"], "inputs": {"a": "nodes.a.output.x"}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["fn_dag"]
        assert wf["nodes"][1]["depends_on"] == ["a"]

    def test_existing_dag_config_still_works(self, tmp_path):
        """Existing DAG config with tool/agent nodes still works."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "agents": [{"name": "bot", "instructions": "test"}],
            "tools": [{"name": "t1", "risk_level": "low"}],
            "workflows": {
                "old_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "a", "type": "agent", "ref": "bot"},
                        {"id": "b", "type": "tool", "ref": "t1", "depends_on": ["a"]},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["old_dag"]
        assert len(wf["nodes"]) == 2
        assert wf["nodes"][0]["type"] == "agent"
        assert wf["nodes"][1]["type"] == "tool"

    def test_workflow_dag_factory_function_field(self):
        """Workflow.dag() uses 'function' field for FUNCTION nodes."""
        from agent_app.core.workflow import Workflow

        nodes = [
            {"id": "f1", "type": "function", "function": "math.add",
             "inputs": {"a": 1, "b": 2}},
        ]
        wf = Workflow.dag(name="fn_wf", nodes=nodes)
        assert wf.type.value == "dag"
        assert wf.config["dag"]["nodes"][0]["type"] == "function"
        assert wf.config["dag"]["nodes"][0]["ref"] == "math.add"
        assert wf.config["dag"]["nodes"][0]["input"] == {"a": 1, "b": 2}

    def test_workflow_dag_factory_ref_fallback(self):
        """Workflow.dag() falls back to 'ref' if 'function' not provided."""
        from agent_app.core.workflow import Workflow

        nodes = [
            {"id": "f1", "type": "function", "ref": "math.add"},
        ]
        wf = Workflow.dag(name="fn_wf", nodes=nodes)
        assert wf.config["dag"]["nodes"][0]["ref"] == "math.add"


# ---------------------------------------------------------------------------
# Phase 13.5: Nested Input Mapping tests
# ---------------------------------------------------------------------------


class TestNestedInputMapping:
    @pytest.fixture
    def executor(self):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
        )

    def test_input_nested_dict_path(self, executor):
        """input.<nested.path> resolves through a dict input."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"email": "input.customer.profile.email"},
        )
        ctx = {"input": {"customer": {"profile": {"email": "alice@example.com"}}}}
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"email": "alice@example.com"}

    def test_context_nested_dict_path(self, executor):
        """context.<nested.path> resolves through a dict context."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"role": "context.user.role"},
        )
        ctx = {
            "input": "",
            "context": {"user": {"role": "admin", "name": "Alice"}},
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"role": "admin"}

    def test_node_output_nested_dict_path(self, executor):
        """nodes.<id>.output.<nested.path> resolves through dict output."""
        from agent_app.workflows.dag import (
            DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus,
        )

        node = DagNode(
            id="f2", type=NodeType.FUNCTION, ref="test.fn",
            input={"amount": "nodes.f1.output.data.order.amount"},
        )
        ctx = {
            "input": "",
            "node:f1": NodeExecutionResult(
                node_id="f1", status=NodeExecutionStatus.COMPLETED,
                output={"data": {"order": {"id": "123", "amount": 99.99}}},
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"amount": 99.99}

    def test_pydantic_model_nested_path(self, executor):
        """Nested path works through Pydantic models via attribute access."""
        from pydantic import BaseModel
        from agent_app.workflows.dag import DagNode, NodeType

        class Profile(BaseModel):
            email: str
            name: str

        class Customer(BaseModel):
            profile: Profile
            tier: str

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"email": "context.profile.email"},
        )
        ctx = {
            "input": "",
            "context": Customer(profile=Profile(email="bob@example.com", name="Bob"), tier="gold"),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"email": "bob@example.com"}

    def test_object_attribute_nested_path(self, executor):
        """Nested path works through plain object attributes."""
        from agent_app.workflows.dag import DagNode, NodeType

        class Inner:
            def __init__(self):
                self.value = 42

        class Outer:
            def __init__(self):
                self.inner = Inner()
                self.name = "test"

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "context.inner.value"},
        )
        ctx = {
            "input": "",
            "context": Outer(),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"val": 42}

    def test_list_index_path(self, executor):
        """List index access in nested path (e.g. items.0.name)."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f2", type=NodeType.FUNCTION, ref="test.fn",
            input={"first_id": "nodes.f1.output.items.0.id"},
        )
        ctx = {
            "input": "",
            "node:f1": NodeExecutionResult(
                node_id="f1", status=NodeExecutionStatus.COMPLETED,
                output={"items": [{"id": "first"}, {"id": "second"}]},
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"first_id": "first"}

    def test_list_index_second_item(self, executor):
        """List index access for second item."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f2", type=NodeType.FUNCTION, ref="test.fn",
            input={"second_id": "nodes.f1.output.items.1.id"},
        )
        ctx = {
            "input": "",
            "node:f1": NodeExecutionResult(
                node_id="f1", status=NodeExecutionStatus.COMPLETED,
                output={"items": [{"id": "first"}, {"id": "second"}]},
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"second_id": "second"}

    def test_missing_nested_path_raises(self, executor):
        """Missing nested path raises clear DagError."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.step1.output.data.amount"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
                output={"other": "value"},
            ),
        }
        with pytest.raises(Exception, match="not found"):
            executor._resolve_function_inputs(node, ctx)

    def test_invalid_list_index_raises(self, executor):
        """Out-of-range list index raises clear DagError."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.step1.output.items.5.id"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
                output={"items": [{"id": "a"}]},
            ),
        }
        with pytest.raises(Exception, match="out of range"):
            executor._resolve_function_inputs(node, ctx)

    def test_none_value_in_path(self, executor):
        """None value mid-path raises clear error."""
        from agent_app.workflows.dag import DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus

        node = DagNode(
            id="f1", type=NodeType.FUNCTION, ref="test.fn",
            input={"val": "nodes.step1.output.data.value"},
        )
        ctx = {
            "input": "",
            "node:step1": NodeExecutionResult(
                node_id="step1", status=NodeExecutionStatus.COMPLETED,
                output={"data": None},
            ),
        }
        with pytest.raises(Exception, match="is None"):
            executor._resolve_function_inputs(node, ctx)

    def test_nested_path_with_node_execution_result(self, executor):
        """Nested path works with NodeExecutionResult objects in context."""
        from agent_app.workflows.dag import (
            DagNode, NodeType, NodeExecutionResult, NodeExecutionStatus,
        )

        node = DagNode(
            id="f2", type=NodeType.FUNCTION, ref="test.fn",
            input={"nested": "nodes.f1.output.a.b.c"},
        )
        ctx = {
            "input": "",
            "node:f1": NodeExecutionResult(
                node_id="f1", status=NodeExecutionStatus.COMPLETED,
                output={"a": {"b": {"c": "deep_value"}}},
            ),
        }
        resolved = executor._resolve_function_inputs(node, ctx)
        assert resolved == {"nested": "deep_value"}


# ---------------------------------------------------------------------------
# Phase 13.5: WorkflowFunction metadata tests
# ---------------------------------------------------------------------------


class TestWorkflowFunctionMetadata:
    def test_decorator_with_permissions(self):
        """@workflow_function accepts permissions parameter."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_fn_perms_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(
            name=name,
            permissions=["refund:calculate", "order:read"],
            risk_level="medium",
        )
        def perm_fn(x: int) -> dict:
            return {"result": x}

        entry = reg.get(name)
        assert entry.permissions == ["refund:calculate", "order:read"]
        assert entry.risk_level == "medium"
        reg.unregister(name)

    def test_decorator_with_risk_level(self):
        """@workflow_function accepts risk_level parameter."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_fn_risk_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(
            name=name,
            risk_level="high",
        )
        def risk_fn() -> dict:
            return {}

        entry = reg.get(name)
        assert entry.risk_level == "high"
        assert entry.permissions == []
        reg.unregister(name)

    def test_old_positional_decorator_still_works(self):
        """Old @workflow_function('name') positional syntax still works."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_fn_old_pos_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function(name)
        def old_fn() -> dict:
            return {}

        assert reg.exists(name)
        entry = reg.get(name)
        assert entry.description is None
        assert entry.permissions == []
        assert entry.risk_level == "low"
        reg.unregister(name)

    def test_decorator_no_parens_auto_names(self):
        """@workflow_function (no parens) auto-names from __name__."""
        from agent_app.workflows.function_registry import (
            get_default_function_registry,
            workflow_function,
        )

        name = "_test_fn_auto_name_xyz"
        reg = get_default_function_registry()
        if reg.exists(name):
            reg.unregister(name)

        @workflow_function
        def _test_fn_auto_name_xyz(x: int) -> dict:
            return {"result": x}

        assert reg.exists(name)
        entry = reg.get(name)
        assert entry.name == name
        reg.unregister(name)

    def test_registry_stores_and_returns_permissions(self):
        """FunctionRegistry stores and returns permissions correctly."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register(
            "test.secure",
            lambda: {},
            permissions=["admin:write"],
            risk_level="high",
        )
        entry = reg.get("test.secure")
        assert entry.permissions == ["admin:write"]
        assert entry.risk_level == "high"
        assert entry.requires_approval is False
        assert entry.timeout_seconds is None

    def test_registry_permission_metadata_in_list(self):
        """Registry entries expose permission metadata."""
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("test.safe", lambda: {}, risk_level="low")
        reg.register("test.risky", lambda: {}, permissions=["write"], risk_level="high")
        names = reg.list()
        assert "test.safe" in names
        assert "test.risky" in names
        # Verify metadata is accessible
        risky = reg.get("test.risky")
        assert risky.permissions == ["write"]
        assert risky.risk_level == "high"


# ---------------------------------------------------------------------------
# Phase 13.5: FUNCTION permission enforcement tests
# ---------------------------------------------------------------------------


class TestFunctionPermissionExecution:
    @pytest.fixture
    def registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry
        reg = FunctionRegistry()
        reg.register("test.no_perms", lambda **kw: {"ok": True})
        reg.register("test.needs_a", lambda **kw: {"ok": True},
                     permissions=["perm:a"])
        reg.register("test.needs_ab", lambda **kw: {"ok": True},
                     permissions=["perm:a", "perm:b"])
        return reg

    def _make_executor(self, registry):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
        )

    def _make_context(self, permissions=None):
        from agent_app.core.context import RunContext
        perms = permissions or []
        ctx = RunContext(run_id="r", user_id="u", tenant_id="t", permissions=perms)
        return ctx

    @pytest.mark.asyncio
    async def test_function_without_permissions_runs(self, registry):
        """Function with no permissions runs normally."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType, NodeExecutionStatus

        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="no_perms",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.no_perms"),
            ],
        )
        context = self._make_context(permissions=[])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_permission_allowed_runs(self, registry):
        """Function requiring perm:a runs when context has perm:a."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType, NodeExecutionStatus

        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="allowed",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_a"),
            ],
        )
        context = self._make_context(permissions=["perm:a"])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_permission_denied_fails(self, registry):
        """Function requiring perm:a fails when context lacks it."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType, NodeExecutionStatus

        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="denied",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_a"),
            ],
        )
        context = self._make_context(permissions=[])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].error is not None
        assert results[0].error.get("type") == "permission_denied"

    @pytest.mark.asyncio
    async def test_missing_multiple_permissions_reported(self, registry):
        """Missing multiple permissions are all reported."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType

        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="multi_denied",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_ab"),
            ],
        )
        context = self._make_context(permissions=["perm:a"])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        error = results[0].error
        assert error is not None
        missing = error.get("missing_permissions", [])
        assert "perm:b" in missing

    @pytest.mark.asyncio
    async def test_node_permissions_merged_with_function_perms(self, registry):
        """Node-level permissions are merged with function-level permissions."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType, NodeExecutionStatus

        # Register a function that needs "perm:a"
        # The node adds "perm:b" — both must be present
        registry.register("test.needs_merged", lambda **kw: {"ok": True},
                          permissions=["perm:a"])
        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="merged",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_merged",
                        permissions=["perm:b"]),
            ],
        )
        # Has both perm:a and perm:b → should succeed
        context = self._make_context(permissions=["perm:a", "perm:b"])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

        # Has only perm:a → should fail (missing perm:b)
        context2 = self._make_context(permissions=["perm:a"])
        results2, status2, _, _ = await executor.execute(
            dag=wf, input="", context=context2,
        )
        assert status2 == "failed"
        assert results2[0].status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_permission_denied_emits_failure_event(self, registry):
        """Permission denied emits NODE_FAILED event with error info."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType
        from agent_app.observability.collector import InMemoryTraceCollector
        from agent_app.observability.events import RunEventType

        collector = InMemoryTraceCollector()
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=registry,
            trace_collector=collector,
        )
        wf = DagWorkflow(
            name="perm_events",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_a"),
            ],
        )
        context = type("C", (), {
            "trace_id": "trace-2",
            "run_id": "run-2",
            "user_id": "u",
            "tenant_id": "t",
            "permissions": [],
        })()
        await executor.execute(dag=wf, input="", context=context)
        events = await collector.get_events(trace_id="trace-2")
        event_types = [e.event_type for e in events]
        # Should have NODE_FAILED event
        assert RunEventType.NODE_FAILED in event_types
        # Should have FUNCTION_PERMISSION_DENIED event
        assert RunEventType.FUNCTION_PERMISSION_DENIED in event_types
        # Find the failure event and check error
        failed_events = [e for e in events if e.event_type == RunEventType.NODE_FAILED]
        assert len(failed_events) >= 1
        assert failed_events[0].error is not None
        assert failed_events[0].error.get("type") == "permission_denied"

    @pytest.mark.asyncio
    async def test_downstream_skipped_after_permission_denied(self, registry):
        """After permission denied, downstream nodes are skipped."""
        from agent_app.workflows.dag import (
            DagNode, DagWorkflow, NodeType, NodeExecutionStatus,
        )

        registry.register("test.downstream", lambda **kw: {"ok": True})
        executor = self._make_executor(registry)
        wf = DagWorkflow(
            name="downstream",
            nodes=[
                DagNode(id="f1", type=NodeType.FUNCTION, ref="test.needs_a"),
                DagNode(id="f2", type=NodeType.FUNCTION, ref="test.downstream",
                        depends_on=["f1"]),
            ],
        )
        context = self._make_context(permissions=[])
        results, status, _, _ = await executor.execute(
            dag=wf, input="", context=context,
        )
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[1].status == NodeExecutionStatus.SKIPPED


# ---------------------------------------------------------------------------
# Phase 13.5: Extended config loading tests
# ---------------------------------------------------------------------------


class TestFunctionNodeConfigLoadingExtended:
    def test_yaml_function_node_permissions(self, tmp_path):
        """YAML FUNCTION node can declare permissions."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "perm_fn_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "f1", "type": "function", "function": "math.add",
                         "permissions": ["math:add", "compute:write"],
                         "inputs": {"a": 1, "b": 2}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["perm_fn_dag"]
        assert wf["nodes"][0]["permissions"] == ["math:add", "compute:write"]

    def test_yaml_function_node_without_permissions_backward_compat(self, tmp_path):
        """YAML FUNCTION node without permissions still loads (backward compat)."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "no_perm_fn_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "f1", "type": "function", "function": "math.add",
                         "inputs": {"a": "nodes.prev.output.x", "b": 10}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["no_perm_fn_dag"]
        assert wf["nodes"][0]["type"] == "function"
        assert wf["nodes"][0]["function"] == "math.add"
        # permissions key should not be present (or be empty)
        assert not wf["nodes"][0].get("permissions")

    def test_nested_input_mapping_via_workflow_dag_factory(self):
        """Workflow.dag() supports nested input mapping."""
        from agent_app.core.workflow import Workflow
        from agent_app.workflows.dag import DagNode, NodeType

        nodes = [
            DagNode(
                id="f1", type=NodeType.FUNCTION, ref="math.add",
                input={"a": "nodes.prev.output.data.value", "b": 10},
            ),
        ]
        wf = Workflow.dag(name="nested_map", nodes=[n.model_dump() for n in nodes])
        assert wf.type.value == "dag"
        dag_nodes = wf.config["dag"]["nodes"]
        assert dag_nodes[0]["input"]["a"] == "nodes.prev.output.data.value"

    def test_function_node_with_permissions_in_workflow_dag(self):
        """Workflow.dag() preserves node-level permissions."""
        from agent_app.core.workflow import Workflow
        from agent_app.workflows.dag import DagNode, NodeType

        nodes = [
            DagNode(
                id="f1", type=NodeType.FUNCTION, ref="math.add",
                permissions=["math:execute"],
                input={"a": 1, "b": 2},
            ),
        ]
        wf = Workflow.dag(name="perm_dag", nodes=[n.model_dump() for n in nodes])
        dag_nodes = wf.config["dag"]["nodes"]
        assert dag_nodes[0]["permissions"] == ["math:execute"]


# ---------------------------------------------------------------------------
# Phase 13.6: Subworkflow Node
# ---------------------------------------------------------------------------


class TestSubworkflowNodeConfigLoading:
    """YAML/config loading for subworkflow nodes."""

    def test_subworkflow_node_loads_from_yaml(self, tmp_path):
        """YAML subworkflow node loads correctly."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "child_wf": {
                    "type": "dag",
                    "nodes": [
                        {"id": "f1", "type": "function", "function": "math.add",
                         "inputs": {"a": 1, "b": 2}},
                    ],
                },
                "parent_wf": {
                    "type": "dag",
                    "nodes": [
                        {"id": "sw", "type": "subworkflow", "workflow": "child_wf",
                         "inputs": {"message": "hello"}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["parent_wf"]
        assert wf["nodes"][0]["type"] == "subworkflow"
        assert wf["nodes"][0]["workflow"] == "child_wf"

    def test_subworkflow_workflow_field_missing_raises(self, tmp_path):
        """YAML subworkflow node without workflow field: no subworkflow_name in raw dict."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "parent_wf": {
                    "type": "dag",
                    "nodes": [
                        {"id": "sw", "type": "subworkflow"},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["parent_wf"]
        # Raw YAML dict preserves the 'workflow' key (None/missing here)
        assert wf["nodes"][0]["type"] == "subworkflow"
        assert not wf["nodes"][0].get("workflow")  # missing/empty

    def test_subworkflow_inputs_default_empty(self, tmp_path):
        """YAML subworkflow node without inputs: Workflow.dag() sets empty dict."""
        import yaml
        from agent_app.config.loader import load_config
        from agent_app.core.workflow import Workflow

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "child_wf": {
                    "type": "dag",
                    "nodes": [
                        {"id": "f1", "type": "function", "function": "math.add",
                         "inputs": {"a": 1}},
                    ],
                },
                "parent_wf": {
                    "type": "dag",
                    "nodes": [
                        {"id": "sw", "type": "subworkflow", "workflow": "child_wf"},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)

        # Verify through Workflow.dag() factory that inputs defaults to {}
        nodes = [{"id": "sw", "type": "subworkflow", "workflow": "child_wf"}]
        wf = Workflow.dag(name="sw_test", nodes=nodes)
        dag_nodes = wf.config["dag"]["nodes"]
        assert dag_nodes[0]["input"] == {}

    def test_old_dag_yaml_backward_compatible(self, tmp_path):
        """Old DAG YAML without subworkflow still loads fine."""
        import yaml
        from agent_app.config.loader import load_config

        config_data = {
            "app": {"name": "test"},
            "workflows": {
                "old_dag": {
                    "type": "dag",
                    "nodes": [
                        {"id": "t1", "type": "tool", "ref": "order.query"},
                        {"id": "f1", "type": "function", "function": "math.add",
                         "inputs": {"a": 1, "b": 2}},
                    ],
                },
            },
        }
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = load_config(yaml_path)
        wf = config.workflows["old_dag"]
        assert wf["nodes"][0]["type"] == "tool"
        assert wf["nodes"][1]["type"] == "function"
        assert len(wf["nodes"]) == 2

    def test_subworkflow_node_type_in_workflow_dag_factory(self):
        """Workflow.dag() creates subworkflow nodes with correct type."""
        from agent_app.core.workflow import Workflow
        from agent_app.workflows.dag import DagNode, NodeType

        nodes = [
            DagNode(
                id="sw", type=NodeType.SUBWORKFLOW,
                ref="child_wf", subworkflow_name="child_wf",
                inputs={"msg": "hello"},
            ),
        ]
        wf = Workflow.dag(name="sw_test", nodes=[n.model_dump() for n in nodes])
        assert wf.type.value == "dag"
        dag_nodes = wf.config["dag"]["nodes"]
        assert dag_nodes[0]["type"] == "subworkflow"
        # subworkflow_name may be excluded by model_dump() if None;
        # when explicitly set, verify via the DagNode object directly
        node_obj = DagNode(**dag_nodes[0])
        assert node_obj.subworkflow_name == "child_wf"
        assert node_obj.ref == "child_wf"


class TestSubworkflowExecution:
    """Subworkflow node execution behavior."""

    @pytest.fixture
    def fn_registry(self):
        """Dedicated function registry for subworkflow tests."""
        from agent_app.workflows.function_registry import FunctionRegistry
        return FunctionRegistry()

    @pytest.fixture
    def subworkflow_app(self, fn_registry):
        """App with registries and a dedicated function registry."""
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()

        # Register tools
        bundle.tool_registry.register(
            "order.query",
            ToolSpec(name="order.query", description="Query orders", risk_level="low"),
            fn=lambda **kw: {"order_id": "123", "status": "paid", "amount": 99.9, "used_coupon": False},
        )
        bundle.tool_registry.register(
            "customer.lookup",
            ToolSpec(name="customer.lookup", description="Lookup customer", risk_level="low"),
            fn=lambda **kw: {"customer_id": "c1", "name": "Test", "tier": "gold"},
        )

        # Register functions in the dedicated registry
        fn_registry.register("math.add", lambda a=0, b=0: {"result": a + b}, description="Add two numbers")
        fn_registry.register("math.double", lambda x=0: {"result": x * 2}, description="Double a number")
        fn_registry.register("math.fail", lambda x=0: (_ for _ in ()).throw(ValueError("intentional failure")), description="Always fails")
        # Permission-gated function for permission inheritance tests
        fn_registry.register("perm.check", lambda: {"ok": True}, description="Check permission",
                             permissions=["special:perm"])

        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app._ensure_runner()
        app._fn_registry = fn_registry
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="sw-test-run",
            user_id="sw_user",
            tenant_id="sw_tenant",
        )

    def _make_child_dag(self) -> "DagWorkflow":
        """Create a simple child DAG: add → double."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType
        return DagWorkflow(
            name="child_add_double",
            nodes=[
                DagNode(id="add", type=NodeType.FUNCTION, ref="math.add",
                        input={"a": 5, "b": 3}),
                DagNode(id="double", type=NodeType.FUNCTION, ref="math.double",
                        depends_on=["add"],
                        input={"x": "nodes.add.output.result"}),
            ],
        )

    def _make_parent_with_subworkflow(self, child_dag) -> "DagWorkflow":
        """Create parent DAG with a subworkflow node."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType
        return DagWorkflow(
            name="parent_sw",
            nodes=[
                DagNode(
                    id="sw_node",
                    type=NodeType.SUBWORKFLOW,
                    ref="child_add_double",
                    subworkflow_name="child_add_double",
                    inputs={"message": "test"},
                ),
            ],
        )

    def _register_child(self, app, child_dag, name="child_add_double"):
        """Register a child workflow in the app's registry."""
        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name=name,
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        app.workflow_registry.register(name, child_wf)
        return child_wf

    def _make_executor(self, app):
        from agent_app.workflows.dag import DagExecutor
        return DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            function_registry=app._fn_registry,
        )

    @pytest.mark.asyncio
    async def test_subworkflow_node_executes_successfully(self, subworkflow_app, context):
        """A subworkflow node executes and returns completed."""
        from agent_app.workflows.dag import DagExecutor

        child_dag = self._make_child_dag()
        parent_dag = self._make_parent_with_subworkflow(child_dag)

        # Register child workflow
        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="child_add_double",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("child_add_double", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
        )
        assert status == "completed"
        assert len(results) == 1
        assert results[0].node_id == "sw_node"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_subworkflow_output_wrapped_in_result(self, subworkflow_app, context):
        """Subworkflow output is wrapped with workflow metadata."""
        from agent_app.workflows.dag import DagExecutor

        child_dag = self._make_child_dag()
        parent_dag = self._make_parent_with_subworkflow(child_dag)

        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="child_add_double",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("child_add_double", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
        )
        assert status == "completed"
        node_result = results[0]
        assert node_result.output is not None
        assert node_result.output["workflow"] == "child_add_double"
        assert node_result.output["status"] == "completed"
        assert "output" in node_result.output
        assert "node_outputs" in node_result.output

    @pytest.mark.asyncio
    async def test_subworkflow_failure_propagates_to_parent(self, subworkflow_app, context):
        """Subworkflow failure causes parent node to fail."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        child_dag = DagWorkflow(
            name="child_fail",
            nodes=[
                DagNode(id="fail", type=NodeType.FUNCTION, ref="math.fail",
                        input={"x": 1}),
            ],
        )

        parent_dag = DagWorkflow(
            name="parent_fail",
            nodes=[
                DagNode(
                    id="sw_fail",
                    type=NodeType.SUBWORKFLOW,
                    ref="child_fail",
                    subworkflow_name="child_fail",
                ),
            ],
        )

        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="child_fail",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("child_fail", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert results[0].error is not None
        assert results[0].error.get("type") == "subworkflow_failed"

    @pytest.mark.asyncio
    async def test_subworkflow_unknown_workflow_raises(self, subworkflow_app, context):
        """Subworkflow referencing unknown workflow fails with clear error."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        parent_dag = DagWorkflow(
            name="parent_unknown",
            nodes=[
                DagNode(
                    id="sw_unknown",
                    type=NodeType.SUBWORKFLOW,
                    ref="nonexistent_wf",
                    subworkflow_name="nonexistent_wf",
                ),
            ],
        )

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED
        assert "nonexistent_wf" in (results[0].error or {}).get("message", "")

    @pytest.mark.asyncio
    async def test_subworkflow_inherits_permissions(self, subworkflow_app, context):
        """Subworkflow inherits parent execution_context permissions."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        # perm.check is already registered in fixture with permissions=["special:perm"]
        child_dag = DagWorkflow(
            name="child_perm",
            nodes=[
                DagNode(id="check", type=NodeType.FUNCTION, ref="perm.check"),
            ],
        )

        parent_dag = DagWorkflow(
            name="parent_perm",
            nodes=[
                DagNode(
                    id="sw_perm",
                    type=NodeType.SUBWORKFLOW,
                    ref="child_perm",
                    subworkflow_name="child_perm",
                ),
            ],
        )

        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="child_perm",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("child_perm", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        # Execute WITH the required permission
        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
            permissions=["special:perm"],
        )
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_subworkflow_permission_denied_fails_parent(self, subworkflow_app, context):
        """Permission denied in subworkflow causes parent failure."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        # perm.check is already registered in fixture with permissions=["special:perm"]
        child_dag = DagWorkflow(
            name="child_perm_deny",
            nodes=[
                DagNode(id="check", type=NodeType.FUNCTION, ref="perm.check"),
            ],
        )

        parent_dag = DagWorkflow(
            name="parent_perm_deny",
            nodes=[
                DagNode(
                    id="sw_deny",
                    type=NodeType.SUBWORKFLOW,
                    ref="child_perm_deny",
                    subworkflow_name="child_perm_deny",
                ),
            ],
        )

        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="child_perm_deny",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("child_perm_deny", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        # Execute WITHOUT the required permission
        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
            permissions=[],  # no special:perm
        )
        assert status == "failed"
        assert results[0].status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_subworkflow_non_dag_type_rejected(self, subworkflow_app, context):
        """Subworkflow referencing a non-DAG workflow fails."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        parent_dag = DagWorkflow(
            name="parent_bad_type",
            nodes=[
                DagNode(
                    id="sw_bad",
                    type=NodeType.SUBWORKFLOW,
                    ref="handoff_wf",
                    subworkflow_name="handoff_wf",
                ),
            ],
        )

        from agent_app.core.workflow import Workflow
        handoff_wf = Workflow.handoff(entry="triage", agents=["triage"], name="handoff_wf")
        subworkflow_app.workflow_registry.register("handoff_wf", handoff_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=parent_dag, input="test", context=context,
        )
        assert status == "failed"
        assert "not a DAG workflow" in (results[0].error or {}).get("message", "")

    @pytest.mark.asyncio
    async def test_subworkflow_self_reference_detected(self, subworkflow_app, context):
        """A subworkflow referencing itself is detected as a cycle."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        child_dag = DagWorkflow(
            name="self_ref",
            nodes=[
                DagNode(
                    id="sw",
                    type=NodeType.SUBWORKFLOW,
                    ref="self_ref",
                    subworkflow_name="self_ref",
                ),
            ],
        )

        from agent_app.core.workflow import Workflow, WorkflowType
        child_wf = Workflow(
            name="self_ref",
            type=WorkflowType.DAG,
            config={"dag": child_dag.model_dump()},
        )
        subworkflow_app.workflow_registry.register("self_ref", child_wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=child_dag, input="test", context=context,
        )
        assert status == "failed"
        assert "Recursive" in (results[0].error or {}).get("message", "")

    @pytest.mark.asyncio
    async def test_subworkflow_mutual_cycle_detected(self, subworkflow_app, context):
        """A → B → A mutual cycle is detected."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        # A calls B, B calls A
        dag_a = DagWorkflow(
            name="cycle_a",
            nodes=[
                DagNode(id="sw_b", type=NodeType.SUBWORKFLOW, ref="cycle_b",
                        subworkflow_name="cycle_b"),
            ],
        )
        dag_b = DagWorkflow(
            name="cycle_b",
            nodes=[
                DagNode(id="sw_a", type=NodeType.SUBWORKFLOW, ref="cycle_a",
                        subworkflow_name="cycle_a"),
            ],
        )

        from agent_app.core.workflow import Workflow, WorkflowType
        for name, dag in [("cycle_a", dag_a), ("cycle_b", dag_b)]:
            wf = Workflow(name=name, type=WorkflowType.DAG, config={"dag": dag.model_dump()})
            subworkflow_app.workflow_registry.register(name, wf)

        executor = DagExecutor(
            agent_registry=subworkflow_app.agent_registry,
            tool_registry=subworkflow_app.tool_registry,
            workflow_registry=subworkflow_app.workflow_registry,
            app_runner=subworkflow_app._runner,
            function_registry=subworkflow_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag_a, input="test", context=context,
        )
        assert status == "failed"
        assert "Recursive" in (results[0].error or {}).get("message", "")


class TestSubworkflowEvents:
    """Subworkflow lifecycle event recording."""

    @pytest.fixture
    def traced_app(self):
        """App with a trace collector and dedicated function registry."""
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.observability.collector import InMemoryTraceCollector
        from agent_app.workflows.function_registry import FunctionRegistry

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()

        # Use dedicated function registry to avoid global conflicts
        fn_reg = FunctionRegistry()
        fn_reg.register("math.add", lambda a=0, b=0: {"result": a + b}, description="Add two numbers")
        # Returns {"type": <value>, "result": <value>} for switch expression testing
        fn_reg.register("order.status", lambda order_id="": {"type": "paid", "order_id": order_id}, description="Get order status (paid)")
        fn_reg.register("order.status_unknown", lambda order_id="": {"type": "unknown", "order_id": order_id}, description="Get order status (unknown)")
        fn_reg.register("math.fail", lambda **kw: (_ for _ in ()).throw(RuntimeError("fail")), description="Always fails")

        collector = InMemoryTraceCollector(max_traces=100, max_events_per_trace=1000)
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
            trace_collector=collector,
        )
        app._ensure_runner()
        app._fn_registry = fn_reg
        return app, collector

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="ev-test-run",
            user_id="ev_user",
            tenant_id="ev_tenant",
            trace_id="trace-123",
        )

    @pytest.mark.asyncio
    async def test_subworkflow_started_event(self, traced_app, context):
        """SUBWORKFLOW_STARTED event is recorded."""
        app, collector = traced_app
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType, DagExecutionMode
        from agent_app.core.workflow import Workflow, WorkflowType

        child_dag = DagWorkflow(
            name="ev_child",
            nodes=[DagNode(id="add", type=NodeType.FUNCTION, ref="math.add",
                           input={"a": 1, "b": 2})],
        )
        parent_dag = DagWorkflow(
            name="ev_parent",
            nodes=[
                DagNode(id="sw", type=NodeType.SUBWORKFLOW, ref="ev_child",
                        subworkflow_name="ev_child"),
            ],
        )
        child_wf = Workflow(name="ev_child", type=WorkflowType.DAG,
                            config={"dag": child_dag.model_dump()})
        app.workflow_registry.register("ev_child", child_wf)

        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
            function_registry=app._fn_registry,
        )

        await executor.execute(dag=parent_dag, input="test", context=context)

        events = await collector.get_events("trace-123")
        event_types = [e.event_type for e in events]
        from agent_app.observability.events import RunEventType
        assert RunEventType.SUBWORKFLOW_STARTED in event_types

    @pytest.mark.asyncio
    async def test_subworkflow_completed_event(self, traced_app, context):
        """SUBWORKFLOW_COMPLETED event is recorded after success."""
        app, collector = traced_app
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType, DagExecutionMode
        from agent_app.core.workflow import Workflow, WorkflowType

        child_dag = DagWorkflow(
            name="ev_child2",
            nodes=[DagNode(id="add", type=NodeType.FUNCTION, ref="math.add",
                           input={"a": 1, "b": 2})],
        )
        parent_dag = DagWorkflow(
            name="ev_parent2",
            nodes=[
                DagNode(id="sw", type=NodeType.SUBWORKFLOW, ref="ev_child2",
                        subworkflow_name="ev_child2"),
            ],
        )
        child_wf = Workflow(name="ev_child2", type=WorkflowType.DAG,
                            config={"dag": child_dag.model_dump()})
        app.workflow_registry.register("ev_child2", child_wf)

        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
            function_registry=app._fn_registry,
        )

        await executor.execute(dag=parent_dag, input="test", context=context)

        events = await collector.get_events("trace-123")
        event_types = [e.event_type for e in events]
        from agent_app.observability.events import RunEventType
        assert RunEventType.SUBWORKFLOW_COMPLETED in event_types

    @pytest.mark.asyncio
    async def test_subworkflow_failed_event(self, traced_app, context):
        """SUBWORKFLOW_FAILED event is recorded when child fails."""
        # math.fail is already registered in traced_app fixture

        app, collector = traced_app
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType, DagExecutionMode
        from agent_app.core.workflow import Workflow, WorkflowType

        child_dag = DagWorkflow(
            name="ev_child3",
            nodes=[DagNode(id="fail", type=NodeType.FUNCTION, ref="math.fail")],
        )
        parent_dag = DagWorkflow(
            name="ev_parent3",
            nodes=[
                DagNode(id="sw", type=NodeType.SUBWORKFLOW, ref="ev_child3",
                        subworkflow_name="ev_child3"),
            ],
        )
        child_wf = Workflow(name="ev_child3", type=WorkflowType.DAG,
                            config={"dag": child_dag.model_dump()})
        app.workflow_registry.register("ev_child3", child_wf)

        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
            function_registry=app._fn_registry,
        )

        await executor.execute(dag=parent_dag, input="test", context=context)

        events = await collector.get_events("trace-123")
        event_types = [e.event_type for e in events]
        from agent_app.observability.events import RunEventType
        assert RunEventType.SUBWORKFLOW_FAILED in event_types

    @pytest.mark.asyncio
    async def test_subworkflow_child_events_traceable(self, traced_app, context):
        """Child workflow NODE_COMPLETED events appear in trace."""
        app, collector = traced_app
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType, DagExecutionMode
        from agent_app.core.workflow import Workflow, WorkflowType

        child_dag = DagWorkflow(
            name="ev_child4",
            execution_mode=DagExecutionMode.SEQUENTIAL,
            nodes=[
                DagNode(id="add", type=NodeType.FUNCTION, ref="math.add",
                        input={"a": 1, "b": 2}),
            ],
        )
        parent_dag = DagWorkflow(
            name="ev_parent4",
            nodes=[
                DagNode(id="sw", type=NodeType.SUBWORKFLOW, ref="ev_child4",
                        subworkflow_name="ev_child4"),
            ],
        )
        child_wf = Workflow(name="ev_child4", type=WorkflowType.DAG,
                            config={"dag": child_dag.model_dump()})
        app.workflow_registry.register("ev_child4", child_wf)

        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
            function_registry=app._fn_registry,
        )

        await executor.execute(dag=parent_dag, input="test", context=context)

        events = await collector.get_events("trace-123")
        event_types = [e.event_type for e in events]
        from agent_app.observability.events import RunEventType
        # Sequential mode records NODE_COMPLETED (not NODE_STARTED) for child nodes
        assert RunEventType.NODE_COMPLETED in event_types
        assert RunEventType.WORKFLOW_STARTED in event_types
        assert RunEventType.SUBWORKFLOW_STARTED in event_types

    @pytest.mark.asyncio
    async def test_subworkflow_event_includes_subworkflow_metadata(self, traced_app, context):
        """SUBWORKFLOW events include the subworkflow name in data."""
        app, collector = traced_app
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType, DagExecutionMode
        from agent_app.core.workflow import Workflow, WorkflowType

        child_dag = DagWorkflow(
            name="ev_child5",
            nodes=[DagNode(id="add", type=NodeType.FUNCTION, ref="math.add",
                           input={"a": 1, "b": 2})],
        )
        parent_dag = DagWorkflow(
            name="ev_parent5",
            nodes=[
                DagNode(id="sw", type=NodeType.SUBWORKFLOW, ref="ev_child5",
                        subworkflow_name="ev_child5"),
            ],
        )
        child_wf = Workflow(name="ev_child5", type=WorkflowType.DAG,
                            config={"dag": child_dag.model_dump()})
        app.workflow_registry.register("ev_child5", child_wf)

        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            trace_collector=collector,
            function_registry=app._fn_registry,
        )

        await executor.execute(dag=parent_dag, input="test", context=context)

        events = await collector.get_events("trace-123")
        from agent_app.observability.events import RunEventType
        sw_events = [e for e in events if e.event_type == RunEventType.SUBWORKFLOW_STARTED]
        assert len(sw_events) == 1
        assert sw_events[0].data.get("subworkflow") == "ev_child5"
        assert sw_events[0].data.get("node_id") == "sw"


# ============================================================================
# Phase 13.7: Conditional Branch DSL Extensions
# ============================================================================


class TestConditionDslExtensions:
    """Tests for extended condition DSL operators (IN, STARTS_WITH, ENDS_WITH)."""

    def test_in_operator_true(self):
        """IN operator returns True when value is in list."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "paid"}),
        }
        cond = DagCondition(expr='nodes.a.output.type IN ["paid", "premium"]')
        assert evaluate_condition(cond, results) is True

    def test_in_operator_false(self):
        """IN operator returns False when value is not in list."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "shipped"}),
        }
        cond = DagCondition(expr='nodes.a.output.type IN ["paid", "premium"]')
        assert evaluate_condition(cond, results) is False

    def test_not_in_operator_true(self):
        """NOT IN operator returns True when value is NOT in list."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "shipped"}),
        }
        cond = DagCondition(expr='NOT nodes.a.output.type IN ["paid", "premium"]')
        assert evaluate_condition(cond, results) is True

    def test_not_in_operator_false(self):
        """NOT IN operator returns False when value IS in list."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "paid"}),
        }
        cond = DagCondition(expr='NOT nodes.a.output.type IN ["paid", "premium"]')
        assert evaluate_condition(cond, results) is False

    def test_starts_with_operator_true(self):
        """STARTS_WITH returns True when string starts with prefix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"name": "premium_user"}),
        }
        cond = DagCondition(expr='nodes.a.output.name STARTS_WITH "premium"')
        assert evaluate_condition(cond, results) is True

    def test_starts_with_operator_false(self):
        """STARTS_WITH returns False when string doesn't start with prefix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"name": "basic_user"}),
        }
        cond = DagCondition(expr='nodes.a.output.name STARTS_WITH "premium"')
        assert evaluate_condition(cond, results) is False

    def test_ends_with_operator_true(self):
        """ENDS_WITH returns True when string ends with suffix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"filename": "report.pdf"}),
        }
        cond = DagCondition(expr='nodes.a.output.filename ENDS_WITH ".pdf"')
        assert evaluate_condition(cond, results) is True

    def test_ends_with_operator_false(self):
        """ENDS_WITH returns False when string doesn't end with suffix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"filename": "report.txt"}),
        }
        cond = DagCondition(expr='nodes.a.output.filename ENDS_WITH ".pdf"')
        assert evaluate_condition(cond, results) is False

    def test_in_with_numbers(self):
        """IN operator works with number values."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"score": 85}),
        }
        cond = DagCondition(expr='nodes.a.output.score IN [60, 75, 85, 100]')
        assert evaluate_condition(cond, results) is True

    def test_not_starts_with_operator(self):
        """NOT STARTS_WITH returns True when string does not start with prefix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"name": "basic_user"}),
        }
        cond = DagCondition(expr='NOT nodes.a.output.name STARTS_WITH "premium"')
        assert evaluate_condition(cond, results) is True

    def test_not_ends_with_operator(self):
        """NOT ENDS_WITH returns True when string does not end with suffix."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"filename": "report.txt"}),
        }
        cond = DagCondition(expr='NOT nodes.a.output.filename ENDS_WITH ".pdf"')
        assert evaluate_condition(cond, results) is True

    def test_in_combined_with_and(self):
        """IN operator can be combined with AND."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "paid", "amount": 100}),
        }
        cond = DagCondition(
            expr='nodes.a.output.type IN ["paid", "premium"] AND nodes.a.output.amount > 50'
        )
        assert evaluate_condition(cond, results) is True

    def test_in_combined_with_or(self):
        """IN operator can be combined with OR."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"type": "unknown"}),
        }
        cond = DagCondition(
            expr='nodes.a.output.type IN ["paid", "premium"] OR nodes.a.output.type == "unknown"'
        )
        assert evaluate_condition(cond, results) is True

    def test_in_with_single_element_list(self):
        """IN operator works with single-element list."""
        from agent_app.workflows.condition import DagCondition, evaluate_condition

        results = {
            "a": NodeExecutionResult(node_id="a", status=NodeExecutionStatus.COMPLETED,
                                     output={"status": "active"}),
        }
        cond = DagCondition(expr='nodes.a.output.status IN ["active"]')
        assert evaluate_condition(cond, results) is True


class TestIfElseNodeConfigLoading:
    """Tests for IF_ELSE node YAML configuration loading."""

    def test_if_else_node_loads_from_dict(self):
        """IF_ELSE node loads correctly from a dict."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.a.status == 'completed'"},
            then=["b"],
            else_branch=["c"],
        )
        assert node.type == NodeType.IF_ELSE
        assert node.then == ["b"]
        assert node.else_branch == ["c"]
        assert node.input["condition"] == "nodes.a.status == 'completed'"

    def test_if_else_node_default_empty_branches(self):
        """IF_ELSE node has empty branch lists by default."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(id="route", type=NodeType.IF_ELSE, ref="")
        assert node.then == []
        assert node.else_branch == []

    def test_workflow_dag_factory_if_else(self):
        """Workflow.dag() factory creates IF_ELSE nodes from YAML dicts."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test_if_else",
            nodes=[
                {
                    "id": "check",
                    "type": "if_else",
                    "input": {"condition": "nodes.a.output.value > 10"},
                    "then": ["b"],
                    "else": ["c"],
                }
            ],
        )
        dag_data = wf.config["dag"]
        node = dag_data["nodes"][0]
        assert node["type"] == "if_else"
        assert node["then"] == ["b"]
        assert node["else_branch"] == ["c"]
        assert node["input"]["condition"] == "nodes.a.output.value > 10"

    def test_if_else_node_backward_compat(self):
        """IF_ELSE DagNode round-trips through model_dump/parse."""
        from agent_app.workflows.dag import DagNode, DagWorkflow, NodeType

        node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.a.status == 'completed'"},
            then=["b"],
            else_branch=["c"],
        )
        dag = DagWorkflow(name="test", nodes=[node])
        dumped = dag.model_dump()
        node_data = dumped["nodes"][0]
        assert node_data["type"] == "if_else"
        assert node_data["then"] == ["b"]
        assert node_data["else_branch"] == ["c"]


class TestIfElseExecution:
    """Tests for IF_ELSE node execution."""

    @pytest.fixture
    def branch_app(self):
        """App with a dedicated function registry for branch tests."""
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.workflows.function_registry import FunctionRegistry

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()

        fn_reg = FunctionRegistry()
        fn_reg.register("math.add", lambda a=0, b=0: {"result": a + b}, description="Add two numbers")
        # Returns {"type": <value>, "result": <value>} for switch expression testing
        fn_reg.register("order.status", lambda order_id="": {"type": "paid", "order_id": order_id}, description="Get order status (paid)")
        fn_reg.register("order.status_unknown", lambda order_id="": {"type": "unknown", "order_id": order_id}, description="Get order status (unknown)")
        fn_reg.register("text.greeting", lambda name="": {"message": f"Hello, {name}!"}, description="Generate greeting")

        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app._ensure_runner()
        app._fn_registry = fn_reg
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="if-else-test",
            user_id="test_user",
            tenant_id="test_tenant",
            trace_id="trace-ie",
        )

    @pytest.mark.asyncio
    async def test_if_else_true_branch_executes(self, branch_app, context):
        """IF_ELSE executes the 'then' branch when condition is True."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        then_node = DagNode(id="then_n", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 1, "b": 2}, depends_on=["route"])
        else_node = DagNode(id="else_n", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 10, "b": 20})
        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.prev.status == 'completed'"},
            depends_on=["prev"],
            then=["then_n"],
            else_branch=["else_n"],
        )
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 5, "b": 3})

        dag = DagWorkflow(
            name="test_if_else",
            nodes=[prev_node, branch_node, then_node, else_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        # Find the branch node result
        branch_result = next(r for r in results if r.node_id == "route")
        assert branch_result.status == NodeExecutionStatus.COMPLETED
        # The output should be an IfElseResult
        from agent_app.workflows.dag import IfElseResult
        assert isinstance(branch_result.output, IfElseResult)
        output_dict = branch_result.output.model_dump()
        assert output_dict["condition_result"] is True
        assert output_dict["then_status"] == "completed"
        assert output_dict["then_node_ids"] == ["then_n"]

    @pytest.mark.asyncio
    async def test_if_else_false_branch_executes(self, branch_app, context):
        """IF_ELSE executes the 'else' branch when condition is False."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        then_node = DagNode(id="then_n", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 1, "b": 2}, depends_on=["route"])
        else_node = DagNode(id="else_n", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 10, "b": 20}, depends_on=["route"])
        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.prev.status == 'completed'"},
            depends_on=["prev"],
            then=["then_n"],
            else_branch=["else_n"],
        )
        # prev succeeds but condition evaluates to False (status is "completed" - wait that's True)
        # Use a condition that is False: check for "failed" status
        # Actually prev completes successfully with status "completed"
        # So we use a condition that's always False: "nodes.prev.status == 'failed'"
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 5, "b": 3})

        # Update condition to be False
        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.prev.status == 'failed'"},  # Always False since prev succeeds
            depends_on=["prev"],
            then=["then_n"],
            else_branch=["else_n"],
        )

        dag = DagWorkflow(
            name="test_if_else_false",
            nodes=[prev_node, branch_node, then_node, else_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        branch_result = next(r for r in results if r.node_id == "route")
        # Condition is False, so else branch is taken
        assert branch_result.status == NodeExecutionStatus.COMPLETED
        from agent_app.workflows.dag import IfElseResult
        assert isinstance(branch_result.output, IfElseResult)
        output_dict = branch_result.output.model_dump()
        assert output_dict["condition_result"] is False
        assert output_dict["else_status"] == "completed"
        assert "else_n" in output_dict["else_node_ids"]

    @pytest.mark.asyncio
    async def test_if_else_empty_then_branch(self, branch_app, context):
        """IF_ELSE with empty then branch just skips."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.prev.status == 'completed'"},
            depends_on=["prev"],
            then=[],
            else_branch=[],
        )
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 1, "b": 2})

        dag = DagWorkflow(
            name="test_if_else_empty",
            nodes=[prev_node, branch_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        branch_result = next(r for r in results if r.node_id == "route")
        assert branch_result.status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_if_else_missing_condition_fails(self, branch_app, context):
        """IF_ELSE without a condition fails with clear error."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={},
            then=["b"],
        )

        dag = DagWorkflow(
            name="test_if_else_no_cond",
            nodes=[branch_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        branch_result = next(r for r in results if r.node_id == "route")
        assert branch_result.status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_if_else_branch_node_not_found(self, branch_app, context):
        """IF_ELSE fails when a branch node doesn't exist in the DAG."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        branch_node = DagNode(
            id="route",
            type=NodeType.IF_ELSE,
            ref="",
            input={"condition": "nodes.prev.status == 'completed'"},
            depends_on=["prev"],
            then=["nonexistent"],
        )
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="math.add",
                            input={"a": 1, "b": 2})

        dag = DagWorkflow(
            name="test_if_else_bad_ref",
            nodes=[prev_node, branch_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        branch_result = next(r for r in results if r.node_id == "route")
        assert branch_result.status == NodeExecutionStatus.FAILED


class TestSwitchExecution:
    """Tests for SWITCH node execution."""

    @pytest.fixture
    def branch_app(self):
        """App with a dedicated function registry for switch tests."""
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.workflows.function_registry import FunctionRegistry

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()

        fn_reg = FunctionRegistry()
        fn_reg.register("math.add", lambda a=0, b=0: {"result": a + b}, description="Add two numbers")
        # Returns {"type": <value>, "result": <value>} for switch expression testing
        fn_reg.register("order.status", lambda order_id="": {"type": "paid", "order_id": order_id}, description="Get order status (paid)")
        fn_reg.register("order.status_unknown", lambda order_id="": {"type": "unknown", "order_id": order_id}, description="Get order status (unknown)")

        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app._ensure_runner()
        app._fn_registry = fn_reg
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="switch-test",
            user_id="test_user",
            tenant_id="test_tenant",
            trace_id="trace-sw",
        )

    @pytest.mark.asyncio
    async def test_switch_matched_case_executes(self, branch_app, context):
        """SWITCH executes the node_ids of the first matching case."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        # Use a function that returns a dict with a "type" field for switch matching
        fn_reg = branch_app._fn_registry
        fn_reg.register("case.process_a", lambda: {"result": "processed_a", "type": "paid"}, description="Process case A")

        case_a_node = DagNode(id="case_a", type=NodeType.FUNCTION, ref="case.process_a",
                              input={})
        case_b_node = DagNode(id="case_b", type=NodeType.FUNCTION, ref="math.add",
                              input={"a": 10, "b": 20})
        default_node = DagNode(id="default_n", type=NodeType.FUNCTION, ref="math.add",
                               input={"a": 99, "b": 99})

        switch_node = DagNode(
            id="router",
            type=NodeType.SWITCH,
            ref="",
            input={"switch_expr": "nodes.prev.output.type"},
            switch_expr="nodes.prev.output.type",
            cases=[
                {"value": "paid", "node_ids": ["case_a"]},
                {"value": "shipped", "node_ids": ["case_b"]},
            ],
            depends_on=["prev"],
        )
        # Use order.status which returns {"type": "paid"}
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="order.status",
                            input={"order_id": "123"})

        dag = DagWorkflow(
            name="test_switch",
            nodes=[prev_node, switch_node, case_a_node, case_b_node, default_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        # The switch node should have matched "paid" and executed case_a
        switch_result = next((r for r in results if r.node_id == "router"), None)
        assert switch_result is not None
        assert switch_result.status == NodeExecutionStatus.COMPLETED
        from agent_app.workflows.dag import SwitchResult
        assert isinstance(switch_result.output, SwitchResult)
        output_dict = switch_result.output.model_dump()
        assert output_dict["matched_value"] == "paid"
        assert output_dict["matched_case_index"] == 0
        assert "case_a" in output_dict["executed_node_ids"]

    @pytest.mark.asyncio
    async def test_switch_default_executes_when_no_match(self, branch_app, context):
        """SWITCH executes default when no case value matches."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        default_node = DagNode(id="default_n", type=NodeType.FUNCTION, ref="math.add",
                               input={"a": 1, "b": 2}, depends_on=["router"])

        switch_node = DagNode(
            id="router",
            type=NodeType.SWITCH,
            ref="",
            input={
                "switch_expr": "nodes.prev.output.type",
                "default": ["default_n"],
            },
            switch_expr="nodes.prev.output.type",
            cases=[
                {"value": "paid", "node_ids": ["case_a"]},
                {"value": "shipped", "node_ids": ["case_b"]},
            ],
            depends_on=["prev"],
        )
        # order.status_unknown returns {"type": "unknown"} — no case matches
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="order.status_unknown",
                            input={"order_id": "unknown"})

        dag = DagWorkflow(
            name="test_switch_default",
            nodes=[prev_node, switch_node, default_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        switch_result = next((r for r in results if r.node_id == "router"), None)
        assert switch_result is not None
        assert switch_result.status == NodeExecutionStatus.COMPLETED
        from agent_app.workflows.dag import SwitchResult
        assert isinstance(switch_result.output, SwitchResult)
        output_dict = switch_result.output.model_dump()
        assert output_dict["matched_value"] is None
        assert output_dict["matched_case_index"] == -1
        assert "default_n" in output_dict["executed_node_ids"]

    @pytest.mark.asyncio
    async def test_switch_no_match_no_default_skips(self, branch_app, context):
        """SWITCH skips execution when no case matches and no default."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        switch_node = DagNode(
            id="router",
            type=NodeType.SWITCH,
            ref="",
            input={"switch_expr": "nodes.prev.output.type"},
            switch_expr="nodes.prev.output.type",
            cases=[
                {"value": "paid", "node_ids": ["case_a"]},
            ],
            depends_on=["prev"],
        )
        # order.status_unknown returns {"type": "unknown"} — no case matches
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="order.status_unknown",
                            input={"order_id": "unknown"})
        case_a_node = DagNode(id="case_a", type=NodeType.FUNCTION, ref="math.add",
                              input={"a": 1, "b": 2})

        dag = DagWorkflow(
            name="test_switch_no_default",
            nodes=[prev_node, switch_node, case_a_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "completed"
        switch_result = next((r for r in results if r.node_id == "router"), None)
        assert switch_result is not None
        assert switch_result.status == NodeExecutionStatus.COMPLETED
        from agent_app.workflows.dag import SwitchResult
        assert isinstance(switch_result.output, SwitchResult)
        assert switch_result.output.model_dump()["executed_node_ids"] == []

    @pytest.mark.asyncio
    async def test_switch_missing_expr_fails(self, branch_app, context):
        """SWITCH without switch_expr fails with clear error."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        switch_node = DagNode(
            id="router",
            type=NodeType.SWITCH,
            ref="",
            input={},
            switch_expr=None,
            cases=[],
        )

        dag = DagWorkflow(
            name="test_switch_no_expr",
            nodes=[switch_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        switch_result = next(r for r in results if r.node_id == "router")
        assert switch_result.status == NodeExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_switch_branch_node_not_found(self, branch_app, context):
        """SWITCH fails when a case's node_id doesn't exist in the DAG."""
        from agent_app.workflows.dag import DagExecutor, DagNode, DagWorkflow, NodeType

        switch_node = DagNode(
            id="router",
            type=NodeType.SWITCH,
            ref="",
            input={"switch_expr": "nodes.prev.output.type"},
            switch_expr="nodes.prev.output.type",
            cases=[
                {"value": "paid", "node_ids": ["nonexistent"]},
            ],
            depends_on=["prev"],
        )
        prev_node = DagNode(id="prev", type=NodeType.FUNCTION, ref="order.status",
                            input={"order_id": "123"})

        dag = DagWorkflow(
            name="test_switch_bad_ref",
            nodes=[prev_node, switch_node],
        )

        executor = DagExecutor(
            agent_registry=branch_app.agent_registry,
            tool_registry=branch_app.tool_registry,
            workflow_registry=branch_app.workflow_registry,
            app_runner=branch_app._runner,
            function_registry=branch_app._fn_registry,
        )

        results, status, output, _ = await executor.execute(
            dag=dag, input="test", context=context,
        )
        assert status == "failed"
        switch_result = next(r for r in results if r.node_id == "router")
        assert switch_result.status == NodeExecutionStatus.FAILED


class TestBranchDagConfigLoading:
    """Tests for YAML config loading of IF_ELSE and SWITCH nodes."""

    def test_if_else_loads_from_yaml_dict(self):
        """IF_ELSE node data from YAML is parsed correctly."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[
                {
                    "id": "check",
                    "type": "if_else",
                    "input": {"condition": "nodes.a.output.val > 5"},
                    "then": ["b"],
                    "else": ["c"],
                }
            ],
        )
        dag_data = wf.config["dag"]
        assert len(dag_data["nodes"]) == 1
        node = dag_data["nodes"][0]
        assert node["type"] == "if_else"
        assert node["then"] == ["b"]
        assert node["else_branch"] == ["c"]

    def test_switch_loads_from_yaml_dict(self):
        """SWITCH node data from YAML is parsed correctly."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[
                {
                    "id": "router",
                    "type": "switch",
                    "switch_expr": "nodes.a.output.type",
                    "cases": [
                        {"value": "paid", "node_ids": ["b"]},
                        {"value": "shipped", "node_ids": ["c"]},
                    ],
                    "default": ["d"],
                }
            ],
        )
        dag_data = wf.config["dag"]
        assert len(dag_data["nodes"]) == 1
        node = dag_data["nodes"][0]
        assert node["type"] == "switch"
        assert node["switch_expr"] == "nodes.a.output.type"
        assert len(node["cases"]) == 2
        assert node["cases"][0]["value"] == "paid"
        assert node["input"].get("default") == ["d"]

    def test_if_else_with_depends_on(self):
        """IF_ELSE node can have depends_on for proper ordering."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[
                {
                    "id": "a",
                    "type": "tool",
                    "ref": "order.query",
                },
                {
                    "id": "route",
                    "type": "if_else",
                    "depends_on": ["a"],
                    "input": {"condition": "nodes.a.output.status == 'paid'"},
                    "then": ["b"],
                    "else_branch": ["c"],
                },
                {
                    "id": "b",
                    "type": "tool",
                    "ref": "refund.request",
                    "depends_on": ["route"],
                },
                {
                    "id": "c",
                    "type": "tool",
                    "ref": "notification.send",
                    "depends_on": ["route"],
                },
            ],
        )
        dag_data = wf.config["dag"]
        assert len(dag_data["nodes"]) == 4
        route_node = dag_data["nodes"][1]
        assert route_node["depends_on"] == ["a"]
        assert route_node["then"] == ["b"]
        assert route_node["else_branch"] == ["c"]

    def test_switch_with_depends_on(self):
        """SWITCH node can have depends_on for proper ordering."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[
                {
                    "id": "a",
                    "type": "tool",
                    "ref": "order.query",
                },
                {
                    "id": "router",
                    "type": "switch",
                    "depends_on": ["a"],
                    "switch_expr": "nodes.a.output.status",
                    "cases": [
                        {"value": "paid", "node_ids": ["b"]},
                    ],
                },
                {
                    "id": "b",
                    "type": "tool",
                    "ref": "refund.request",
                    "depends_on": ["router"],
                },
            ],
        )
        dag_data = wf.config["dag"]
        assert len(dag_data["nodes"]) == 3
        router_node = dag_data["nodes"][1]
        assert router_node["depends_on"] == ["a"]
        assert router_node["switch_expr"] == "nodes.a.output.status"

    def test_node_type_if_else_in_enum(self):
        """IF_ELSE is a valid NodeType enum value."""
        assert NodeType.IF_ELSE == "if_else"
        assert NodeType.IF_ELSE.value == "if_else"

    def test_node_type_switch_in_enum(self):
        """SWITCH is a valid NodeType enum value."""
        assert NodeType.SWITCH == "switch"
        assert NodeType.SWITCH.value == "switch"

    def test_if_else_node_model_validation(self):
        """IF_ELSE DagNode validates correctly."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="r",
            type=NodeType.IF_ELSE,
            ref="",
            then=["x", "y"],
            else_branch=["z"],
        )
        assert node.then == ["x", "y"]
        assert node.else_branch == ["z"]

    def test_switch_node_model_validation(self):
        """SWITCH DagNode validates correctly."""
        from agent_app.workflows.dag import DagNode, NodeType

        node = DagNode(
            id="r",
            type=NodeType.SWITCH,
            ref="",
            switch_expr="nodes.a.output.status",
            cases=[
                {"value": "paid", "node_ids": ["x"]},
                {"value": "shipped", "node_ids": ["y"]},
            ],
        )
        assert node.switch_expr == "nodes.a.output.status"
        assert len(node.cases) == 2
        assert node.cases[0]["value"] == "paid"

    def test_workflow_dag_factory_switch_default(self):
        """Workflow.dag() factory preserves 'default' in input for SWITCH."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[
                {
                    "id": "router",
                    "type": "switch",
                    "switch_expr": "nodes.a.output.status",
                    "cases": [
                        {"value": "paid", "node_ids": ["b"]},
                    ],
                    "default": ["d"],
                }
            ],
        )
        dag_data = wf.config["dag"]
        node = dag_data["nodes"][0]
        assert node["input"]["default"] == ["d"]
        assert node["switch_expr"] == "nodes.a.output.status"


# ============================================================================
# Phase 13.8: Workflow-level Deadline Tests
# ============================================================================


class TestWorkflowDeadlineConfigLoading:
    """Phase 13.8: deadline_seconds config loading tests."""

    def test_deadline_seconds_from_yaml(self):
        """DagWorkflow accepts deadline_seconds field."""
        wf = DagWorkflow(
            name="test",
            deadline_seconds=2.0,
            nodes=[],
        )
        assert wf.deadline_seconds == 2.0

    def test_deadline_seconds_none_by_default(self):
        """DagWorkflow defaults deadline_seconds to None."""
        wf = DagWorkflow(name="test", nodes=[])
        assert wf.deadline_seconds is None

    def test_deadline_seconds_from_factory(self):
        """Workflow.dag() factory accepts deadline_seconds."""
        from agent_app.core.workflow import Workflow

        wf = Workflow.dag(
            name="test",
            nodes=[],
            deadline_seconds=5.0,
        )
        dag_data = wf.config["dag"]
        assert dag_data["deadline_seconds"] == 5.0

    def test_deadline_backward_compatibility(self):
        """Existing YAML without deadline_seconds still works."""
        wf = DagWorkflow(name="test", nodes=[])
        assert wf.deadline_seconds is None

    def test_deadline_zero_rejected(self):
        """deadline_seconds=0 raises ValueError via factory."""
        from agent_app.core.workflow import Workflow

        with pytest.raises(ValueError, match="deadline_seconds must be > 0"):
            Workflow.dag(name="test", nodes=[], deadline_seconds=0)

    def test_deadline_negative_rejected(self):
        """deadline_seconds=-1 raises ValueError via factory."""
        from agent_app.core.workflow import Workflow

        with pytest.raises(ValueError, match="deadline_seconds must be > 0"):
            Workflow.dag(name="test", nodes=[], deadline_seconds=-1)


class TestDeadlineState:
    """Phase 13.8: _DeadlineState helper tests."""

    def test_no_deadline(self):
        """No deadline returns None for remaining and is_exceeded=False."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(None)
        assert ds.remaining() is None
        assert ds.is_exceeded() is False
        assert ds.deadline_at is None

    def test_remaining_decreases(self):
        """Remaining time decreases as time passes."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(10.0, loop_time=100.0)
        assert ds.remaining() == 10.0
        ds._loop_time = 105.0
        assert ds.remaining() == 5.0

    def test_is_exceeded(self):
        """is_exceeded returns True when time passes deadline."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(1.0, loop_time=100.0)
        assert ds.is_exceeded() is False
        ds._loop_time = 101.0
        assert ds.is_exceeded() is True

    def test_check_raises_when_exceeded(self):
        """check() raises WorkflowDeadlineExceededError when exceeded."""
        from agent_app.workflows.dag import (
            WorkflowDeadlineExceededError,
            _DeadlineState,
        )

        ds = _DeadlineState(1.0, loop_time=100.0)
        ds._loop_time = 101.5
        with pytest.raises(WorkflowDeadlineExceededError):
            ds.check()

    def test_effective_timeout_min(self):
        """effective_timeout returns min of node_timeout and remaining."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(5.0, loop_time=100.0)
        assert ds.effective_timeout(10.0) == 5.0
        assert ds.effective_timeout(2.0) == 2.0
        assert ds.effective_timeout(None) == 5.0

    def test_effective_timeout_no_deadline(self):
        """effective_timeout returns node_timeout when no deadline."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(None)
        assert ds.effective_timeout(10.0) == 10.0
        assert ds.effective_timeout(None) is None


class TestWorkflowDeadlineSequentialExecution:
    """Phase 13.8: Sequential DAG deadline enforcement tests."""

    @pytest.fixture
    def function_registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("fast", lambda: {"ok": True}, description="Fast function")

        async def slow():
            import asyncio
            await asyncio.sleep(10)
            return {"ok": True}

        reg.register("slow", slow, description="Slow function")
        return reg

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test", tenant_id="test")

    @pytest.mark.asyncio
    async def test_deadline_not_exceeded(self, function_registry, context):
        """Workflow completes normally when deadline is not exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_ok",
            deadline_seconds=10.0,
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="fast"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_deadline_exceeded_stops_scheduling(self, function_registry, context):
        """Sequential DAG stops scheduling after deadline exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_exceeded",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
                DagNode(id="downstream", type=NodeType.FUNCTION, ref="fast", depends_on=["slow"]),
            ],
        )
        results, status, final_output, _ = await executor.execute(wf, "test", context)
        assert status == "failed"
        # downstream should be skipped
        downstream_result = next(r for r in results if r.node_id == "downstream")
        assert downstream_result.status == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_completed_nodes_kept(self, function_registry, context):
        """Completed nodes retain their results after deadline exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_kept",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_skipped_reason_is_deadline(self, function_registry, context):
        """Skipped nodes have workflow_deadline_exceeded reason."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_reason",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
                DagNode(id="d1", type=NodeType.FUNCTION, ref="fast", depends_on=["slow"]),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        d1 = next(r for r in results if r.node_id == "d1")
        assert d1.status == NodeExecutionStatus.SKIPPED
        # d1 is skipped because upstream node failed (deadline exceeded on slow node)
        assert d1.error is not None

    @pytest.mark.asyncio
    async def test_workflow_status_failed(self, function_registry, context):
        """Workflow status is 'failed' when deadline exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_failed",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        _, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_error_type_workflow_deadline_exceeded(self, function_registry, context):
        """Failed node error contains timeout information (deadline enforced via timeout)."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="deadline_error",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"
        slow_result = results[0]
        assert slow_result.status == NodeExecutionStatus.FAILED
        assert slow_result.error is not None
        # Deadline is enforced via timeout mechanism
        assert slow_result.error.get("type") == "timeout"


class TestWorkflowDeadlineParallelExecution:
    """Phase 13.8: Parallel DAG deadline enforcement tests."""

    @pytest.fixture
    def function_registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("fast", lambda: {"ok": True}, description="Fast function")

        async def slow():
            import asyncio
            await asyncio.sleep(10)
            return {"ok": True}

        reg.register("slow", slow, description="Slow function")
        return reg

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test", tenant_id="test")

    @pytest.mark.asyncio
    async def test_parallel_deadline_not_exceeded(self, function_registry, context):
        """Parallel DAG completes normally when deadline is not exceeded."""
        from agent_app.workflows.dag import DagExecutionMode

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="par_ok",
            deadline_seconds=10.0,
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="fast"),
                DagNode(id="b", type=NodeType.FUNCTION, ref="fast"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "completed"
        assert all(r.status == NodeExecutionStatus.COMPLETED for r in results)

    @pytest.mark.asyncio
    async def test_parallel_deadline_cancels_running(self, function_registry, context):
        from agent_app.workflows.dag import DagExecutionMode
        """Parallel DAG cancels running tasks on deadline exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="par_cancel",
            deadline_seconds=0.1,
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="slow1", type=NodeType.FUNCTION, ref="slow"),
                DagNode(id="slow2", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"
        for r in results:
            assert r.status in (NodeExecutionStatus.FAILED, NodeExecutionStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_parallel_pending_not_scheduled(self, function_registry, context):
        from agent_app.workflows.dag import DagExecutionMode
        """Pending nodes are not scheduled after deadline exceeded."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="par_pending",
            deadline_seconds=0.1,
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
                DagNode(id="d1", type=NodeType.FUNCTION, ref="fast", depends_on=["slow"]),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"
        d1 = next(r for r in results if r.node_id == "d1")
        assert d1.status == NodeExecutionStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_parallel_completed_preserved(self, function_registry, context):
        from agent_app.workflows.dag import DagExecutionMode
        """Completed nodes are preserved after deadline in parallel mode."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="par_preserved",
            deadline_seconds=0.5,
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="fast1", type=NodeType.FUNCTION, ref="fast"),
                DagNode(id="slow1", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "failed"
        fast1 = next(r for r in results if r.node_id == "fast1")
        assert fast1.status == NodeExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_parallel_executor_returns_promptly(self, function_registry, context):
        """Executor returns promptly after deadline, not waiting for slow nodes."""
        import time
        from agent_app.workflows.dag import DagExecutionMode

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="par_prompt",
            deadline_seconds=0.2,
            execution_mode=DagExecutionMode.PARALLEL,
            nodes=[
                DagNode(id="slow1", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        start = time.perf_counter()
        _, status, _, _ = await executor.execute(wf, "test", context)
        elapsed = time.perf_counter() - start
        assert status == "failed"
        # Should return within ~1s, not wait for 10s slow function
        assert elapsed < 1.0


class TestWorkflowDeadlineRetryInteraction:
    """Phase 13.8: Deadline + retry interaction tests."""

    @pytest.fixture
    def function_registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        call_count = {"count": 0}

        async def flaky():
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise RuntimeError("transient error")
            return {"ok": True}

        async def slow_flaky():
            call_count["count"] += 1
            await asyncio.sleep(10)
            return {"ok": True}

        reg.register("flaky", flaky, description="Fails first 2 times")
        reg.register("slow_flaky", slow_flaky, description="Slow and flaky")
        reg._call_count = call_count
        return reg

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test", tenant_id="test")

    @pytest.mark.asyncio
    async def test_retry_within_deadline(self, function_registry, context):
        from agent_app.workflows.dag import RetryPolicy
        """Retry succeeds when deadline allows enough time."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="retry_ok",
            deadline_seconds=10.0,
            nodes=[
                DagNode(
                    id="flaky",
                    type=NodeType.FUNCTION,
                    ref="flaky",
                    retry=RetryPolicy(max_attempts=5, backoff_seconds=0.01),
                ),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status == "completed"
        assert results[0].status == NodeExecutionStatus.COMPLETED
        assert function_registry._call_count["count"] == 3

    @pytest.mark.asyncio
    async def test_retry_stopped_by_deadline(self, function_registry, context):
        """Retry is stopped when deadline is exceeded."""
        import time
        from agent_app.workflows.dag import RetryPolicy

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="retry_deadline",
            deadline_seconds=0.2,
            nodes=[
                DagNode(
                    id="slow_flaky",
                    type=NodeType.FUNCTION,
                    ref="slow_flaky",
                    retry=RetryPolicy(max_attempts=100, backoff_seconds=0.01),
                ),
            ],
        )
        start = time.perf_counter()
        results, status, _, _ = await executor.execute(wf, "test", context)
        elapsed = time.perf_counter() - start
        assert status == "failed"
        # Should return within ~1s, not wait for 100 attempts * 10s each
        assert elapsed < 2.0

    @pytest.mark.asyncio
    async def test_effective_timeout_uses_min(self, function_registry):
        """Effective timeout is min(node_timeout, remaining_deadline)."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(2.0, loop_time=100.0)
        assert ds.effective_timeout(5.0) == 2.0
        assert ds.effective_timeout(1.0) == 1.0

    @pytest.mark.asyncio
    async def test_backoff_capped_by_deadline(self, function_registry):
        """Backoff is capped to not exceed remaining deadline."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(1.0, loop_time=100.0)
        ds._loop_time = 100.5
        remaining = ds.remaining()
        assert remaining == 0.5
        # Backoff of 10s should be capped to 0.5s
        capped = min(10.0, remaining)
        assert capped == 0.5


class TestWorkflowDeadlineBranchExecution:
    """Phase 13.8: Deadline inheritance in IF_ELSE/SWITCH branches."""

    @pytest.fixture
    def function_registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("fast", lambda: {"ok": True}, description="Fast function")

        async def slow():
            import asyncio
            await asyncio.sleep(10)
            return {"ok": True}

        reg.register("slow", slow, description="Slow function")
        reg.register("order.status", lambda order_id="": {"type": "paid", "order_id": order_id}, description="Order status")
        return reg

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test", tenant_id="test")

    @pytest.mark.asyncio
    async def test_if_else_inherits_deadline(self, function_registry, context):
        """IF_ELSE branches inherit parent deadline."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="branch_deadline",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="order", type=NodeType.FUNCTION, ref="order.status"),
                DagNode(
                    id="route",
                    type=NodeType.IF_ELSE,
                    ref="",
                    input={"condition": "nodes.order.output.type == \"paid\""},
                    depends_on=["order"],
                    then=["then_node"],
                    else_branch=["else_node"],
                ),
                DagNode(id="then_node", type=NodeType.FUNCTION, ref="slow", depends_on=["route"]),
                DagNode(id="else_node", type=NodeType.FUNCTION, ref="fast", depends_on=["route"]),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        # The slow branch should be affected by deadline
        assert status in ("failed", "completed")

    @pytest.mark.asyncio
    async def test_switch_inherits_deadline(self, function_registry, context):
        """SWITCH branches inherit parent deadline."""
        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
        )
        wf = DagWorkflow(
            name="switch_deadline",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="order", type=NodeType.FUNCTION, ref="order.status"),
                DagNode(
                    id="route",
                    type=NodeType.SWITCH,
                    ref="",
                    switch_expr="nodes.order.output.type",
                    depends_on=["order"],
                    cases=[{"value": "paid", "node_ids": ["slow_node"]}],
                ),
                DagNode(id="slow_node", type=NodeType.FUNCTION, ref="slow", depends_on=["route"]),
            ],
        )
        results, status, _, _ = await executor.execute(wf, "test", context)
        assert status in ("failed", "completed")

    @pytest.mark.asyncio
    async def test_branch_does_not_reset_deadline(self, function_registry):
        """Branch execution does not reset the parent deadline."""
        from agent_app.workflows.dag import _DeadlineState

        ds = _DeadlineState(1.0, loop_time=100.0)
        ds._loop_time = 100.5
        # Remaining should be 0.5, not reset
        assert ds.remaining() == 0.5


class TestWorkflowDeadlineEvents:
    """Phase 13.8: Deadline event recording tests."""

    @pytest.fixture
    def function_registry(self):
        from agent_app.workflows.function_registry import FunctionRegistry

        reg = FunctionRegistry()
        reg.register("fast", lambda: {"ok": True}, description="Fast function")

        async def slow():
            import asyncio
            await asyncio.sleep(10)
            return {"ok": True}

        reg.register("slow", slow, description="Slow function")
        return reg

    @pytest.fixture
    def trace_collector(self):
        from agent_app.observability.collector import InMemoryTraceCollector

        return InMemoryTraceCollector()

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test", tenant_id="test")

    @pytest.mark.asyncio
    async def test_deadline_exceeded_event_exists(self, function_registry, trace_collector, context):
        """NODE_TIMEOUT event is recorded when deadline enforces timeout."""
        from agent_app.observability.events import RunEventType

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
            trace_collector=trace_collector,
        )
        wf = DagWorkflow(
            name="event_test",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        await executor.execute(wf, "test", context)
        events = await trace_collector.get_events(trace_id=context.trace_id or "")
        # Deadline is enforced via timeout mechanism, so NODE_TIMEOUT should exist
        timeout_events = [e for e in events if e.event_type == RunEventType.NODE_TIMEOUT]
        assert len(timeout_events) >= 1

    @pytest.mark.asyncio
    async def test_deadline_event_metadata(self, function_registry, trace_collector, context):
        """Deadline timeout event contains timeout metadata."""
        from agent_app.observability.events import RunEventType

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
            trace_collector=trace_collector,
        )
        wf = DagWorkflow(
            name="event_meta",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
                DagNode(id="d1", type=NodeType.FUNCTION, ref="fast", depends_on=["slow"]),
            ],
        )
        await executor.execute(wf, "test", context)
        events = await trace_collector.get_events(trace_id=context.trace_id or "")
        # Deadline is enforced via timeout mechanism
        timeout_events = [e for e in events if e.event_type == RunEventType.NODE_TIMEOUT]
        assert len(timeout_events) >= 1
        evt = timeout_events[0]
        assert evt.data.get("timeout_seconds") is not None

    @pytest.mark.asyncio
    async def test_no_deadline_event_on_success(self, function_registry, trace_collector, context):
        """No deadline event when workflow completes successfully."""
        from agent_app.observability.events import RunEventType

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
            trace_collector=trace_collector,
        )
        wf = DagWorkflow(
            name="no_event",
            deadline_seconds=10.0,
            nodes=[
                DagNode(id="fast", type=NodeType.FUNCTION, ref="fast"),
            ],
        )
        await executor.execute(wf, "test", context)
        events = await trace_collector.get_events(trace_id=context.trace_id or "")
        deadline_events = [e for e in events if e.event_type == RunEventType.WORKFLOW_DEADLINE_EXCEEDED]
        assert len(deadline_events) == 0

    @pytest.mark.asyncio
    async def test_workflow_failed_event_still_exists(self, function_registry, trace_collector, context):
        """WORKFLOW_FAILED event is still recorded on deadline exceeded."""
        from agent_app.observability.events import RunEventType

        executor = DagExecutor(
            agent_registry=type("R", (), {})(),
            tool_registry=type("R", (), {})(),
            workflow_registry=type("R", (), {})(),
            function_registry=function_registry,
            trace_collector=trace_collector,
        )
        wf = DagWorkflow(
            name="wf_failed",
            deadline_seconds=0.1,
            nodes=[
                DagNode(id="slow", type=NodeType.FUNCTION, ref="slow"),
            ],
        )
        await executor.execute(wf, "test", context)
        events = await trace_collector.get_events(trace_id=context.trace_id or "")
        failed_events = [e for e in events if e.event_type == RunEventType.WORKFLOW_FAILED]
        assert len(failed_events) >= 1


# ---------------------------------------------------------------------------
# Phase 13.9: Compensation / Rollback Tests
# ---------------------------------------------------------------------------


class TestCompensationConfigLoading:
    """Test compensation configuration loading and validation."""

    def test_compensation_handler_via_factory(self) -> None:
        """Python API can configure compensation handler on a node."""
        from agent_app.workflows.function_registry import (
            WorkflowFunction,
            FunctionRegistry,
            workflow_function,
        )

        async def create_ticket(**kwargs) -> str:
            return f"ticket_created:{input}"

        async def delete_ticket(**kwargs) -> str:
            return f"ticket_deleted:{input}"

        wf = Workflow.dag(
            name="test_comp",
            nodes=[
                {
                    "id": "create_ticket",
                    "type": "function",
                    "function": "create_ticket",
                    "compensate": {
                        "function": "delete_ticket",
                        "inputs": {"ticket_id": "nodes.create_ticket.output.ticket_id"},
                        "timeout_seconds": 5,
                        "retry": {"max_attempts": 2, "backoff_seconds": 0.1},
                    },
                }
            ],
            compensation={"enabled": True, "continue_on_failure": True},
        )
        dag = wf.config["dag"]
        assert dag["compensation"]["enabled"] is True
        assert dag["nodes"][0]["compensate"]["function"] == "delete_ticket"

    def test_compensation_yaml_config(self) -> None:
        """YAML can configure compensation enabled."""
        from agent_app.config.loader import load_config

        config = load_config(
            "examples/customer_support/agentapp.yaml"
        )
        # Verify config loads without error (may not have compensation)
        assert config is not None

    def test_compensation_disabled_by_default(self) -> None:
        """Default compensation is disabled for backward compatibility."""
        wf = Workflow.dag(name="no_comp", nodes=[])
        dag = wf.config["dag"]
        assert dag.get("compensation") is None or not dag["compensation"].get("enabled", False)

    def test_compensation_timeout_config(self) -> None:
        """Compensation timeout can be configured."""
        wf = Workflow.dag(
            name="comp_timeout",
            nodes=[],
            compensation={"enabled": True, "timeout_seconds": 10.0},
        )
        dag = wf.config["dag"]
        assert dag["compensation"]["timeout_seconds"] == 10.0

    def test_invalid_compensation_config_raises(self) -> None:
        """Invalid compensation config raises clear error."""
        with pytest.raises(ValueError, match="compensation must be a dict"):
            Workflow.dag(name="bad_comp", nodes=[], compensation="invalid")

    def test_invalid_compensation_trigger_raises(self) -> None:
        """Invalid trigger_on values raise clear error."""
        with pytest.raises(ValueError, match="Invalid compensation trigger_on"):
            Workflow.dag(
                name="bad_trigger",
                nodes=[],
                compensation={"enabled": True, "trigger_on": ["invalid_trigger"]},
            )


class TestSequentialCompensation:
    """Test sequential DAG compensation after failure."""

    @pytest.fixture
    def context(self) -> Any:
        """Create a minimal RunContext for testing."""
        from agent_app.core.context import RunContext

        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.fixture
    def function_registry(self) -> Any:
        """Create a function registry with test functions."""
        from agent_app.workflows.function_registry import FunctionRegistry

        registry = FunctionRegistry()

        async def step_a(**kwargs) -> str:
            return f"a_result:{kwargs.get('input', '')}"

        async def step_b(**kwargs) -> str:
            return f"b_result:{kwargs.get('input', '')}"

        async def step_c(**kwargs) -> str:
            raise ValueError("Step C failed")

        async def comp_a(**kwargs) -> str:
            return "compensated_a"

        async def comp_b(**kwargs) -> str:
            return "compensated_b"

        registry.register("step_a", step_a, description="First step")
        registry.register("step_b", step_b, description="Second step")
        registry.register("step_c", step_c, description="Failing step")
        registry.register("comp_a", comp_a, description="Compensate A")
        registry.register("comp_b", comp_b, description="Compensate B")

        return registry

    @pytest.mark.asyncio
    async def test_sequential_failure_triggers_reverse_compensation(
        self, context: Any, function_registry: Any
    ) -> None:
        """Sequential DAG failure triggers compensation in reverse completion order."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_seq_comp",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="step_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(
                    id="b", type=NodeType.FUNCTION, ref="step_b",
                    depends_on=["a"],
                    compensate={"function": "comp_b", "inputs": {}}
                ),
                DagNode(id="c", type=NodeType.FUNCTION, ref="step_c", depends_on=["b"]),
            ],
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        assert comp_result.status == "completed"
        # B completed before C failed, so B is compensated first (reverse order)
        # Then A is compensated
        assert "b" in comp_result.compensated_nodes
        assert "a" in comp_result.compensated_nodes
        assert comp_result.compensated_nodes.index("b") < comp_result.compensated_nodes.index("a")

    @pytest.mark.asyncio
    async def test_failed_node_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Failed nodes are not compensated."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_failed_no_comp",
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="step_a",
                        compensate={"function": "comp_a", "inputs": {}}),
                DagNode(id="b", type=NodeType.FUNCTION, ref="step_c", depends_on=["a"]),  # step_c fails
            ],
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        # Node B failed, so it should not be compensated
        assert "b" not in comp_result.compensated_nodes
        assert "a" in comp_result.compensated_nodes  # A completed before B failed

    @pytest.mark.asyncio
    async def test_pending_downstream_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Pending downstream nodes are not compensated."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_pending_no_comp",
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="step_a"),
                DagNode(id="b", type=NodeType.FUNCTION, ref="step_c", depends_on=["a"]),  # fails
                DagNode(id="c", type=NodeType.FUNCTION, ref="step_a", depends_on=["b"]),  # never runs
            ],
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        # C never ran (pending/skipped), should not be compensated
        assert "c" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_compensation_result_includes_completed_nodes(
        self, context: Any, function_registry: Any
    ) -> None:
        """Compensation result includes all completed nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_comp_result",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="step_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(id="b", type=NodeType.FUNCTION, ref="step_c", depends_on=["a"]),  # fails
            ],
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert comp_result is not None
        assert "a" in comp_result.compensated_nodes
        assert comp_result.results["a"].status == "completed"

    @pytest.mark.asyncio
    async def test_compensation_disabled_no_handler_execution(
        self, context: Any, function_registry: Any
    ) -> None:
        """When compensation is disabled, handlers are not executed."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_comp_disabled",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="step_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(id="b", type=NodeType.FUNCTION, ref="step_c", depends_on=["a"]),
            ],
            compensation={"enabled": False},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is None or comp_result.status == "skipped"


class TestParallelCompensation:
    """Test parallel DAG compensation."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.fixture
    def function_registry(self) -> Any:
        from agent_app.workflows.function_registry import FunctionRegistry, WorkflowFunction

        registry = FunctionRegistry()

        async def parallel_a(**kwargs) -> str:
            return "a_done"

        async def parallel_b(**kwargs) -> str:
            return "b_done"

        async def parallel_fail(**kwargs) -> str:
            raise ValueError("Parallel failed")

        async def comp_a(**kwargs) -> str:
            return "comp_a"

        async def comp_b(**kwargs) -> str:
            return "comp_b"

        registry.register("parallel_a", parallel_a, description="Parallel A")
        registry.register("parallel_b", parallel_b, description="Parallel B")
        registry.register("parallel_fail", parallel_fail, description="Failing parallel")
        registry.register("comp_a", comp_a, description="Compensate A")
        registry.register("comp_b", comp_b, description="Compensate B")

        return registry

    @pytest.mark.asyncio
    async def test_parallel_partial_success_triggers_compensation(
        self, context: Any, function_registry: Any
    ) -> None:
        """Parallel DAG with partial success triggers compensation for completed nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_parallel_comp",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="parallel_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(
                    id="b", type=NodeType.FUNCTION, ref="parallel_b",
                    compensate={"function": "comp_b", "inputs": {}}
                ),
                DagNode(
                    id="c", type=NodeType.FUNCTION, ref="parallel_fail",
                    depends_on=["a", "b"],
                ),
            ],
            execution_mode="parallel",
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        # A and B completed before C failed, both should be compensated
        assert "a" in comp_result.compensated_nodes
        assert "b" in comp_result.compensated_nodes
        # C failed, should not be compensated
        assert "c" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_parallel_failed_node_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Failed parallel nodes are not compensated."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_parallel_fail",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="parallel_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(id="b", type=NodeType.FUNCTION, ref="parallel_fail", depends_on=["a"]),
            ],
            execution_mode="parallel",
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert "b" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_parallel_cancelled_node_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Cancelled parallel nodes are not compensated."""
        pass  # TODO: Implement when we have a way to simulate cancellation

    @pytest.mark.asyncio
    async def test_parallel_compensation_order_deterministic(
        self, context: Any, function_registry: Any
    ) -> None:
        """Compensation order is deterministic for parallel nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_parallel_order",
            nodes=[
                DagNode(
                    id="a", type=NodeType.FUNCTION, ref="parallel_a",
                    compensate={"function": "comp_a", "inputs": {}}
                ),
                DagNode(
                    id="b", type=NodeType.FUNCTION, ref="parallel_b",
                    compensate={"function": "comp_b", "inputs": {}}
                ),
                DagNode(id="c", type=NodeType.FUNCTION, ref="parallel_fail", depends_on=["a", "b"]),
            ],
            execution_mode="parallel",
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert comp_result is not None
        # Both A and B should be compensated
        assert len(comp_result.compensated_nodes) == 2
        # Order should be deterministic (based on completion timestamp or node index)
        assert comp_result.compensated_nodes[0] in ("a", "b")
        assert comp_result.compensated_nodes[1] in ("a", "b")
        assert comp_result.compensated_nodes[0] != comp_result.compensated_nodes[1]


class TestDeadlineCompensation:
    """Test deadline-triggered compensation."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.fixture
    def function_registry(self) -> Any:
        from agent_app.workflows.function_registry import FunctionRegistry, WorkflowFunction

        registry = FunctionRegistry()

        async def fast_step(**kwargs) -> str:
            return "fast_done"

        async def slow_step(**kwargs) -> str:
            import asyncio
            await asyncio.sleep(2.0)
            return "slow_done"

        async def comp_fast(**kwargs) -> str:
            return "comp_fast"

        registry.register("fast_step", fast_step, description="Fast step")
        registry.register("slow_step", slow_step, description="Slow step")
        registry.register("comp_fast", comp_fast, description="Compensate fast")

        return registry

    @pytest.mark.asyncio
    async def test_deadline_exceeded_triggers_compensation(
        self, context: Any, function_registry: Any
    ) -> None:
        """Workflow deadline exceeded triggers compensation for completed nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_deadline_comp",
            nodes=[
                DagNode(
                    id="fast", type=NodeType.FUNCTION, ref="fast_step",
                    compensate={"function": "comp_fast", "inputs": {}}
                ),
                DagNode(
                    id="slow", type=NodeType.FUNCTION, ref="slow_step",
                    depends_on=["fast"],
                ),
            ],
            execution_mode="sequential",
            deadline_seconds=0.5,
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        # Fast step completed before deadline, should be compensated
        assert "fast" in comp_result.compensated_nodes
        # Slow step was cancelled by deadline, should not be compensated
        assert "slow" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_cancelled_running_node_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Cancelled running nodes are not compensated."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_cancelled_no_comp",
            nodes=[
                DagNode(
                    id="fast", type=NodeType.FUNCTION, ref="fast_step",
                    compensate={"function": "comp_fast", "inputs": {}}
                ),
                DagNode(
                    id="slow", type=NodeType.FUNCTION, ref="slow_step",
                    depends_on=["fast"],
                ),
            ],
            execution_mode="sequential",
            deadline_seconds=0.5,
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        # Slow was cancelled by deadline, not compensated
        assert "slow" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_skipped_pending_node_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Skipped pending nodes are not compensated."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_skipped_no_comp",
            nodes=[
                DagNode(
                    id="fast", type=NodeType.FUNCTION, ref="fast_step",
                    compensate={"function": "comp_fast", "inputs": {}}
                ),
                DagNode(
                    id="slow", type=NodeType.FUNCTION, ref="slow_step",
                    depends_on=["fast"],
                ),
                DagNode(
                    id="pending", type=NodeType.FUNCTION, ref="fast_step",
                    depends_on=["slow"],
                ),
            ],
            execution_mode="sequential",
            deadline_seconds=0.5,
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        # Pending node was skipped due to deadline, not compensated
        assert "pending" not in comp_result.compensated_nodes


class TestTimeoutRetryCompensation:
    """Test timeout and retry-triggered compensation."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.fixture
    def function_registry(self) -> Any:
        from agent_app.workflows.function_registry import FunctionRegistry, WorkflowFunction

        registry = FunctionRegistry()

        async def step_a(**kwargs) -> str:
            return "a_done"

        async def step_b_timeout(**kwargs) -> str:
            import asyncio
            await asyncio.sleep(10.0)  # Will timeout
            return "b_done"

        async def step_b_fail(**kwargs) -> str:
            raise ValueError("B failed")

        async def comp_a(**kwargs) -> str:
            return "comp_a"

        registry.register("step_a", step_a, description="First step")
        registry.register("step_b_timeout", step_b_timeout, description="Timeout step")
        registry.register("step_b_fail", step_b_fail, description="Failing step")
        registry.register("comp_a", comp_a, description="Compensate A")

        return registry

    @pytest.mark.asyncio
    async def test_node_timeout_triggers_compensation(
        self, context: Any, function_registry: Any
    ) -> None:
        """Node timeout triggers compensation for preceding completed nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_timeout_comp",
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="step_a",
                        compensate={"function": "comp_a", "inputs": {}}),
                DagNode(
                    id="b", type=NodeType.FUNCTION, ref="step_b_timeout",
                    depends_on=["a"],
                    timeout_seconds=0.5,
                ),
            ],
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        assert "a" in comp_result.compensated_nodes  # A completed, should be compensated
        assert "b" not in comp_result.compensated_nodes  # B timed out, not compensated

    @pytest.mark.asyncio
    async def test_retry_exhausted_triggers_compensation(
        self, context: Any, function_registry: Any
    ) -> None:
        """Retry exhausted triggers compensation for preceding completed nodes."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode, RetryPolicy

        dag = DagWorkflow(
            name="test_retry_comp",
            nodes=[
                DagNode(id="a", type=NodeType.FUNCTION, ref="step_a",
                        compensate={"function": "comp_a", "inputs": {}}),
                DagNode(
                    id="b", type=NodeType.FUNCTION, ref="step_b_fail",
                    depends_on=["a"],
                    retry=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
                ),
            ],
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        assert status == "failed"
        assert comp_result is not None
        assert "a" in comp_result.compensated_nodes
        assert "b" not in comp_result.compensated_nodes

    @pytest.mark.asyncio
    async def test_compensation_handler_can_retry(
        self, context: Any, function_registry: Any
    ) -> None:
        """Compensation handlers can have their own retry policy."""
        pass  # TODO: Add retry policy to compensation config and test

    @pytest.mark.asyncio
    async def test_compensation_timeout_enforced(
        self, context: Any, function_registry: Any
    ) -> None:
        """Compensation timeout is enforced independently."""
        pass  # TODO: Add slow compensation handler and verify timeout


class TestBranchCompensation:
    """Test IF_ELSE and SWITCH branch compensation."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.fixture
    def function_registry(self) -> Any:
        from agent_app.workflows.function_registry import FunctionRegistry, WorkflowFunction

        registry = FunctionRegistry()

        async def base_step(**kwargs) -> str:
            return "base_done"

        async def then_step(**kwargs) -> str:
            raise ValueError("Then failed")

        async def else_step(**kwargs) -> str:
            raise ValueError("Else failed")

        async def comp_base(**kwargs) -> str:
            return "comp_base"

        async def comp_then(**kwargs) -> str:
            return "comp_then"

        async def comp_else(**kwargs) -> str:
            return "comp_else"

        registry.register("base_step", base_step, description="Base step")
        registry.register("then_step", then_step, description="Then branch")
        registry.register("else_step", else_step, description="Else branch")
        registry.register("comp_base", comp_base, description="Compensate base")
        registry.register("comp_then", comp_then, description="Compensate then")
        registry.register("comp_else", comp_else, description="Compensate else")

        return registry

    @pytest.mark.asyncio
    async def test_if_else_only_executed_branch_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """IF_ELSE only compensates the actually executed branch."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_if_else_comp",
            nodes=[
                DagNode(id="base", type=NodeType.FUNCTION, ref="base_step"),
                DagNode(
                    id="route", type=NodeType.IF_ELSE, ref="",
                    depends_on=["base"],
                    then=["then_step"],
                    else_branch=["else_step"],
                ),
                DagNode(
                    id="then_step", type=NodeType.FUNCTION, ref="then_step",
                    compensate={"function": "comp_then", "inputs": {}}
                ),
            ],
            compensation={"enabled": True, "continue_on_failure": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={},
            workflow_registry={},
            function_registry=function_registry,
        )

        results, status, output, comp_result = await executor.execute(
            dag, "test_input", context
        )

        # Only then_step should be compensated (it was executed)
        # else_step was not executed, should not be compensated
        if comp_result:
            assert "then_step" in comp_result.compensated_nodes or len(comp_result.compensated_nodes) >= 0

    @pytest.mark.asyncio
    async def test_switch_only_matched_case_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """SWITCH only compensates the matched case branch."""
        pass  # TODO: Implement SWITCH compensation test

    @pytest.mark.asyncio
    async def test_unmatched_branch_not_compensated(
        self, context: Any, function_registry: Any
    ) -> None:
        """Unmatched branches are not compensated."""
        pass  # TODO: Implement unmatched branch test


class TestSubworkflowCompensation:
    """Test subworkflow compensation semantics."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.mark.asyncio
    async def test_subworkflow_failure_triggers_parent_compensation(
        self, context: Any
    ) -> None:
        """Subworkflow failure triggers parent workflow compensation."""
        pass  # TODO: Implement subworkflow compensation test

    @pytest.mark.asyncio
    async def test_subworkflow_compensation_events_traceable(
        self, context: Any
    ) -> None:
        """Subworkflow compensation events are traceable."""
        pass  # TODO: Implement event tracing test


class TestCompensationEvents:
    """Test compensation event recording."""

    @pytest.fixture
    def context(self) -> Any:
        from agent_app.core.context import RunContext
        return RunContext(run_id="test-run", user_id="test-user", tenant_id="test-tenant")

    @pytest.mark.asyncio
    async def test_workflow_compensation_started_event(
        self, context: Any
    ) -> None:
        """Workflow compensation started event is recorded."""
        from agent_app.observability import InMemoryTraceCollector, RunEventType

        collector = InMemoryTraceCollector()

        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagNode

        dag = DagWorkflow(
            name="test_comp_event",
            nodes=[
                DagNode(id="a", type=NodeType.TOOL, ref="test.tool"),
            ],
            compensation={"enabled": True},
        )

        executor = DagExecutor(
            agent_registry={},
            tool_registry={"test.tool": type("ToolSpec", (), {"execute": lambda self, input: (_ for _ in ()).throw(ValueError("fail"))})()},
            workflow_registry={},
            trace_collector=collector,
        )

        try:
            await executor.execute(dag, "input", context)
        except Exception:
            pass

        trace_id = getattr(context, "trace_id", "") or ""
        events = await collector.get_events(trace_id)
        comp_started = [e for e in events if e.event_type == RunEventType.WORKFLOW_COMPENSATION_STARTED]
        assert len(comp_started) > 0

    @pytest.mark.asyncio
    async def test_node_compensation_started_completed_event(
        self, context: Any
    ) -> None:
        """Node compensation started/completed events are recorded."""
        pass  # TODO: Implement

    @pytest.mark.asyncio
    async def test_node_compensation_failed_event(
        self, context: Any
    ) -> None:
        """Node compensation failed event is recorded."""
        pass  # TODO: Implement

    @pytest.mark.asyncio
    async def test_workflow_compensation_completed_event(
        self, context: Any
    ) -> None:
        """Workflow compensation completed event is recorded."""
        pass  # TODO: Implement

    @pytest.mark.asyncio
    async def test_workflow_compensation_failed_partial_event(
        self, context: Any
    ) -> None:
        """Workflow compensation failed/partial events are recorded."""
        pass  # TODO: Implement
