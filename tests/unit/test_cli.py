"""Tests for CLI."""

import json
import subprocess
import sys

import pytest


def _run_cli(*args):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_app.cli", *args],
        capture_output=True,
        text=True,
        cwd="/home/ymj68520/projects/Python/AgentOps Framework",
    )
    return result.returncode, result.stdout, result.stderr


def _setup_traced_app(tmp_path):
    """Create a config + app with JSONLTraceCollector and pre-recorded events.

    Uses JSONL so events survive the subprocess boundary (CLI runs in a
    separate process with its own InMemoryTraceCollector).
    """
    import asyncio
    from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
    from agent_app.observability.exporters import JSONLTraceCollector
    from agent_app.observability.events import RunEvent
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry
    from agent_app.registry.workflow_registry import WorkflowRegistry

    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})())
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr

    # Use JSONL collector so events survive subprocess boundary
    jsonl_path = tmp_path / "traces.jsonl"
    jsonl_collector = JSONLTraceCollector(jsonl_path)
    app.trace_collector = jsonl_collector

    app.register_agent(
        AgentSpec(name="support", description="Support agent", model="gpt-4o", instructions="You are a support agent.")
    )
    app.register_workflow(Workflow.single(agent="support", name="cs"))

    # Write config that points to the same JSONL file
    config_file = tmp_path / "agentapp.yaml"
    config_file.write_text(
        f"app:\n  name: test\nmodels:\n  default: gpt-4o\n"
        f"observability:\n  tracing:\n    type: jsonl\n    path: {jsonl_path}\n",
        encoding="utf-8",
    )

    # Record events directly to the JSONL file
    asyncio.run(jsonl_collector.record(
        RunEvent(event_type="run.started", trace_id="tr-1", run_id="run-1", user_id="u1", tenant_id="t1")
    ))
    asyncio.run(jsonl_collector.record(
        RunEvent(event_type="run.completed", trace_id="tr-1", run_id="run-1", user_id="u1", tenant_id="t1")
    ))
    asyncio.run(jsonl_collector.record(
        RunEvent(event_type="run.started", trace_id="tr-2", run_id="run-2", user_id="u1", tenant_id="t2")
    ))
    asyncio.run(jsonl_collector.record(
        RunEvent(event_type="run.interrupted", trace_id="tr-2", run_id="run-2", user_id="u1", tenant_id="t2")
    ))
    asyncio.run(jsonl_collector.record(
        RunEvent(event_type="run.started", trace_id="tr-3", run_id="run-3", user_id="u2", tenant_id="t1")
    ))

    return app, str(config_file), jsonl_path


class TestCLI:
    def test_eval_run_success(self, tmp_path):
        """Running a passing eval suite should exit 0."""
        import os
        eval_file = "examples/customer_support/evals/customer_support.yaml"
        config_file = "examples/customer_support/agentapp.yaml"
        if not os.path.exists(eval_file):
            pytest.skip("Eval file not found")

        code, stdout, stderr = _run_cli(
            "eval", "run", eval_file, "--config", config_file
        )
        assert code == 0, f"Expected exit 0, got {code}\nstderr: {stderr}"

    def test_eval_run_missing_file(self):
        """Running with missing file should exit non-zero."""
        code, _, stderr = _run_cli(
            "eval", "run", "/nonexistent/eval.yaml", "--config", "examples/customer_support/agentapp.yaml"
        )
        assert code != 0

    def test_help_exits_zero(self):
        code, _, _ = _run_cli("--help")
        assert code == 0

    def test_trace_help_exits_zero(self):
        code, _, _ = _run_cli("trace", "--help")
        assert code == 0


class TestCLITraceList:
    """Tests for 'agentapp trace list' command."""

    def test_trace_list_empty(self, tmp_path):
        """Empty collector shows 'No traces found.' and exits 0."""
        empty_path = tmp_path / "empty.jsonl"
        empty_path.write_text("", encoding="utf-8")
        empty_config = tmp_path / "empty.yaml"
        empty_config.write_text(
            f"app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            f"observability:\n  tracing:\n    type: jsonl\n    path: {empty_path}\n",
            encoding="utf-8",
        )
        code, stdout, stderr = _run_cli("trace", "list", "--config", str(empty_config))
        assert code == 0
        assert "No traces found" in stdout

    def test_trace_list_empty_json(self, tmp_path):
        """Empty collector with --json outputs valid JSON."""
        empty_path = tmp_path / "empty.jsonl"
        empty_path.write_text("", encoding="utf-8")
        empty_config = tmp_path / "empty.yaml"
        empty_config.write_text(
            f"app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            f"observability:\n  tracing:\n    type: jsonl\n    path: {empty_path}\n",
            encoding="utf-8",
        )
        code, stdout, stderr = _run_cli("trace", "list", "--config", str(empty_config), "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data["total"] == 0
        assert data["traces"] == []

    def test_trace_list_shows_traces(self, tmp_path):
        """Traces appear in table output after recording events."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "list", "--config", config_file)
        assert code == 0
        assert "tr-1" in stdout
        assert "run-1" in stdout

    def test_trace_list_limit(self, tmp_path):
        """--limit controls how many traces are shown."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "list", "--config", config_file, "--limit", "2")
        assert code == 0
        # Count trace ID lines in output
        data_lines = [l for l in stdout.split("\n") if "tr-" in l]
        assert len(data_lines) == 2

    def test_trace_list_json_output(self, tmp_path):
        """--json outputs parseable JSON with trace summaries."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "list", "--config", config_file, "--json")
        assert code == 0
        data = json.loads(stdout)
        assert "traces" in data
        assert "total" in data
        assert data["total"] == 3
        # tr-1 has 2 events and status completed
        tr1 = [t for t in data["traces"] if t["trace_id"] == "tr-1"]
        assert len(tr1) == 1
        assert tr1[0]["event_count"] == 2
        assert tr1[0]["status"] == "completed"

    def test_trace_list_filter_run_id(self, tmp_path):
        """--run-id filters traces by run_id."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "list", "--config", config_file, "--run-id", "run-1")
        assert code == 0
        assert "tr-1" in stdout
        # tr-2 and tr-3 should not appear
        assert "tr-2" not in stdout
        assert "tr-3" not in stdout

    def test_trace_list_filter_event_type(self, tmp_path):
        """--event-type filters traces containing that event type."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "list", "--config", config_file, "--event-type", "run.interrupted")
        assert code == 0
        assert "tr-2" in stdout
        # tr-1 (only started+completed) and tr-3 (only started) should not appear
        assert "tr-1" not in stdout
        assert "tr-3" not in stdout

    def test_trace_list_missing_config(self):
        """Missing config file exits non-zero."""
        code, _, stderr = _run_cli("trace", "list", "--config", "/nonexistent.yaml")
        assert code != 0


class TestCLITraceShow:
    """Tests for 'agentapp trace show' command."""

    def test_trace_show_missing_trace_exits_nonzero(self, tmp_path):
        """Showing a nonexistent trace exits with code 1."""
        _, config_file, _ = _setup_traced_app(tmp_path)
        code, stdout, stderr = _run_cli("trace", "show", "nonexistent-id", "--config", config_file)
        assert code == 1
        assert "not found" in stdout.lower() or "not found" in stderr.lower()

    def test_trace_show_existing_trace(self, tmp_path):
        """Showing an existing trace prints event details."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "show", "tr-1", "--config", config_file)
        assert code == 0
        assert "run.started" in stdout
        assert "run.completed" in stdout

    def test_trace_show_json_output(self, tmp_path):
        """--json outputs parseable JSON array of events."""
        _, config_file, _ = _setup_traced_app(tmp_path)

        code, stdout, stderr = _run_cli("trace", "show", "tr-1", "--config", config_file, "--json")
        assert code == 0
        data = json.loads(stdout)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["event_type"] == "run.started"
        assert data[0]["run_id"] == "run-1"
        assert data[1]["event_type"] == "run.completed"
