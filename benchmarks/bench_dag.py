"""DAG execution benchmark (Phase 13.2).

Compares sequential vs parallel DAG execution with varying concurrency.
Uses no-op tools — does not call any real API.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.workflows.dag import DagExecutionMode


def _build_app():
    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(registry=type("B", (), {
        "agent_registry": ar, "tool_registry": tr, "workflow_registry": wr
    })())
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr

    # Register no-op tools
    async def noop_tool(**kw):
        return {"result": "ok"}

    for name in ["order.query", "customer.lookup", "refund.request"]:
        app.register_tool(
            ToolSpec(name=name, description=f"No-op {name}", risk_level="low"),
            fn=noop_tool,
        )

    # Phase 13.4: Register benchmark functions in global registry
    from agent_app.workflows.function_registry import get_default_function_registry
    global_fr = get_default_function_registry()
    global_fr.register("bench.double", lambda **kw: {"result": kw.get("x", 0) * 2})
    global_fr.register("bench.add", lambda a=0, b=0: {"result": a + b})
    # Phase 13.9: Compensation benchmark functions
    global_fr.register("bench.negate", lambda **kw: {"result": -kw.get("x", 0)})
    global_fr.register("bench.fail", lambda **kw: (_ for _ in ()).throw(ValueError("benchmark fail")))

    app._ensure_runner()
    return app


async def _run_dag(app, workflow, runs: int) -> tuple[float, int, int]:
    """Run DAG benchmark and return (total_ms, nodes_per_run, events)."""
    t0 = time.perf_counter()
    total_nodes = 0
    total_events = 0
    for _ in range(runs):
        result = await app.run(
            workflow=workflow.name,
            input="benchmark input",
            user_id="bench",
            tenant_id="bench",
        )
        total_nodes += len(result.node_results or [])
        total_events += len(getattr(result, "trace_events", []) or [])
    total_ms = (time.perf_counter() - t0) * 1000
    return total_ms, total_nodes // max(runs, 1), total_events


async def main_async(args: argparse.Namespace) -> None:
    app = _build_app()
    runs = args.runs

    # Define DAGs to benchmark
    dag_configs = [
        ("sequential_3_nodes", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "customer.lookup", "depends_on": ["a"]},
            {"id": "c", "type": "tool", "ref": "refund.request", "depends_on": ["b"]},
        ]),
        ("parallel_3_independent", DagExecutionMode.PARALLEL, None, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "customer.lookup"},
            {"id": "c", "type": "tool", "ref": "refund.request"},
        ]),
        ("parallel_diamond", DagExecutionMode.PARALLEL, None, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "customer.lookup", "depends_on": ["a"]},
            {"id": "c", "type": "tool", "ref": "order.query", "depends_on": ["a"]},
            {"id": "d", "type": "tool", "ref": "refund.request", "depends_on": ["b", "c"]},
        ]),
        ("parallel_concurrency_2", DagExecutionMode.PARALLEL, 2, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "customer.lookup"},
            {"id": "c", "type": "tool", "ref": "refund.request"},
        ]),
        ("parallel_concurrency_1", DagExecutionMode.PARALLEL, 1, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "customer.lookup"},
            {"id": "c", "type": "tool", "ref": "refund.request"},
        ]),
        # Phase 13.3: Condition benchmarks
        ("conditional_dag_true", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "refund.request",
             "depends_on": ["a"],
             "condition": {"expr": "nodes.a.output.status == 'paid'"}},
        ]),
        ("conditional_dag_false", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "tool", "ref": "order.query"},
            {"id": "b", "type": "tool", "ref": "refund.request",
             "depends_on": ["a"],
             "condition": {"expr": "nodes.a.output.status == 'shipped'"}},
        ]),
        # Phase 13.3: Timeout + retry benchmark (uses fast tool, timeout won't trigger)
        ("timeout_retry_dag", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "tool", "ref": "order.query", "timeout_seconds": 5.0},
        ]),
        # Phase 13.4: FUNCTION node benchmarks
        ("function_3_nodes", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "f1", "type": "function", "function": "bench.double",
             "inputs": {"x": 42}},
            {"id": "f2", "type": "function", "function": "bench.add",
             "inputs": {"a": 1, "b": 2}, "depends_on": ["f1"]},
            {"id": "f3", "type": "function", "function": "bench.double",
             "inputs": {"x": 10}, "depends_on": ["f2"]},
        ]),
        ("function_with_condition", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "f1", "type": "function", "function": "bench.add",
             "inputs": {"a": 5, "b": 3}},
            {"id": "f2", "type": "function", "function": "bench.double",
             "condition": {"expr": "nodes.f1.output.result > 5"},
             "inputs": {"x": 2}, "depends_on": ["f1"]},
        ]),
        ("function_input_mapping", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "f1", "type": "function", "function": "bench.add",
             "inputs": {"a": "nodes.f1.output.value", "b": 10}},
        ]),
        # Phase 13.9: Compensation benchmarks
        ("compensation_configured_not_triggered", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "function", "function": "bench.double",
             "inputs": {"x": 1},
             "compensate": {"function": "bench.negate", "inputs": {}}},
            {"id": "b", "type": "function", "function": "bench.add",
             "inputs": {"a": 1, "b": 2}, "depends_on": ["a"]},
        ], None, {"enabled": True}),
        ("compensation_triggered_sequential", DagExecutionMode.SEQUENTIAL, None, [
            {"id": "a", "type": "function", "function": "bench.double",
             "inputs": {"x": 1},
             "compensate": {"function": "bench.negate", "inputs": {}}},
            {"id": "b", "type": "function", "function": "bench.add",
             "inputs": {"a": 1, "b": 2}, "depends_on": ["a"],
             "compensate": {"function": "bench.negate", "inputs": {}}},
            {"id": "c", "type": "function", "function": "bench.fail"},
        ], None, {"enabled": True}),
    ]

    print(f"runs: {runs}")
    print()
    print(f"{'DAG':<30} {'mode':<12} {'conc':>5} {'total_ms':>10} {'avg_ms':>10} {'nodes':>6}")
    print("-" * 80)

    for name, mode, max_conc, nodes, *extra in dag_configs:
        wf_timeout = extra[0] if len(extra) > 0 else None
        compensation = extra[1] if len(extra) > 1 else None
        wf = Workflow.dag(
            name=name,
            nodes=nodes,
            execution_mode=mode.value,
            max_concurrency=max_conc,
            timeout_seconds=wf_timeout,
            compensation=compensation,
        )
        app.workflow_registry.register(wf.name, wf)

        total_ms, nodes_per_run, events = await _run_dag(app, wf, runs)
        avg = total_ms / runs if runs else 0
        conc_str = str(max_conc) if max_conc else "∞"
        print(f"{name:<30} {mode.value:<12} {conc_str:>5} {total_ms:>10.1f} {avg:>10.3f} {nodes_per_run:>6}")


def main() -> int:
    parser = argparse.ArgumentParser(description="DAG benchmark")
    parser.add_argument("--runs", type=int, default=100, help="Number of runs")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
