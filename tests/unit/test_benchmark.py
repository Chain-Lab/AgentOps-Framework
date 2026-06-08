"""Tests for scripts/benchmark_tracing.py."""

from __future__ import annotations

import importlib.util
import sys

import pytest


def _load_benchmark_module():
    """Load benchmark_tracing.py as a module without importing it at top level."""
    spec = importlib.util.spec_from_file_location(
        "benchmark_tracing",
        "/home/ymj68520/projects/Python/AgentOps Framework/scripts/benchmark_tracing.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBenchmarkScript:
    """Lightweight tests for the tracing benchmark script."""

    @pytest.mark.asyncio
    async def test_noop_collector_small_run(self):
        """Benchmark can run with noop collector and small run count."""
        mod = _load_benchmark_module()
        app = mod._build_app(mod.NoOpTraceCollector())
        total_ms, events = await mod._run_benchmark(app, 3)
        assert total_ms > 0
        assert events == 6  # run.started + run.completed per run

    @pytest.mark.asyncio
    async def test_memory_collector_small_run(self):
        """Benchmark can run with memory collector."""
        mod = _load_benchmark_module()
        collector = mod.InMemoryTraceCollector()
        app = mod._build_app(collector)
        total_ms, events = await mod._run_benchmark(app, 3)
        assert total_ms > 0
        assert events == 6

    @pytest.mark.asyncio
    async def test_jsonl_collector_small_run(self, tmp_path):
        """Benchmark can run with jsonl collector."""
        mod = _load_benchmark_module()
        path = tmp_path / "bench_traces.jsonl"
        collector = mod.JSONLTraceCollector(path)
        app = mod._build_app(collector)
        total_ms, events = await mod._run_benchmark(app, 3)
        assert total_ms > 0
        assert events == 6
        count = await collector.count_events()
        assert count >= 6

    def test_import_does_not_fail(self):
        """The benchmark script can be imported without errors."""
        mod = _load_benchmark_module()
        assert hasattr(mod, "main")
        assert hasattr(mod, "_run_benchmark")
        assert hasattr(mod, "_build_app")
