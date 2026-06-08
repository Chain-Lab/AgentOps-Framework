"""Lightweight tracing overhead benchmark.

Usage:
    python scripts/benchmark_tracing.py --runs 100
    python scripts/benchmark_tracing.py --runs 1000 --collector noop
    python scripts/benchmark_tracing.py --runs 1000 --collector jsonl --path .agent_app/bench_traces.jsonl

This is a rough benchmark, not a rigorous performance test.
It uses DryRunBackend and does not call any real OpenAI API.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
from agent_app.observability.collector import InMemoryTraceCollector, NoOpTraceCollector
from agent_app.observability.exporters import JSONLTraceCollector
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry


def _build_app(collector):
    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})())
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr
    app.trace_collector = collector
    app.register_agent(
        AgentSpec(name="bench_agent", description="Benchmark agent", model="gpt-4o", instructions="You are a benchmark agent.", tools=["order.query"])
    )
    app.register_tool(
        ToolSpec(name="order.query", description="Query order", risk_level="low", permissions=["order:read"])
    )
    app.register_workflow(Workflow.single(agent="bench_agent", name="bench"))
    return app


async def _run_benchmark(app, runs: int) -> tuple[float, int]:
    """Run benchmark and return (total_ms, events_recorded)."""
    t0 = time.perf_counter()
    total_events = 0
    for _ in range(runs):
        result = await app.run(workflow="bench", input="query order 123", user_id="bench", tenant_id="bench")
        total_events += len(getattr(result, "trace_events", []) or [])
    total_ms = (time.perf_counter() - t0) * 1000
    return total_ms, total_events


async def main_async(args: argparse.Namespace) -> None:
    # Create collector
    if args.collector == "noop":
        collector = NoOpTraceCollector()
    elif args.collector == "jsonl":
        collector = JSONLTraceCollector(args.path or ".agent_app/bench_traces.jsonl")
    else:
        collector = InMemoryTraceCollector()

    app = _build_app(collector)
    runs = args.runs

    print(f"collector: {args.collector}")
    print(f"runs: {runs}")
    print("running...")

    total_ms, events = await _run_benchmark(app, runs)
    avg = total_ms / runs if runs else 0

    print(f"total_ms: {total_ms:.1f}")
    print(f"avg_ms_per_run: {avg:.2f}")
    print(f"events_recorded: {events}")

    # JSONL-specific info
    if args.collector == "jsonl":
        count = await collector.count_events()
        print(f"jsonl_events_on_disk: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tracing overhead benchmark")
    parser.add_argument("--runs", type=int, default=100, help="Number of runs")
    parser.add_argument("--collector", choices=["memory", "noop", "jsonl"], default="memory")
    parser.add_argument("--path", type=str, default=None, help="Path for jsonl collector")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
