"""Tests for book_publisher.build_app.build_app."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.build_app import build_app


def test_build_app_registers_one_agent_per_persona(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    for persona in bp_app.personas.all():
        agent_name = f"book_writer__{persona.name}"
        spec = bp_app.app.agent_registry.get(agent_name)
        assert spec.name == agent_name
        assert spec.metadata["tone"] == persona.tone


def test_build_app_registers_one_tool_per_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    for platform in bp_app.platforms.all():
        tool_name = f"publish_{platform.name}"
        spec = bp_app.app.tool_registry.get_spec(tool_name)
        assert spec.risk_level == platform.risk_level
        assert spec.requires_approval == platform.requires_approval


def test_build_app_registers_book_generation_workflow(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    wf = bp_app.app.workflow_registry.get("book_generation")
    node_ids = {node["id"] for node in wf.config["dag"]["nodes"]}
    expected = {f"write_{p.name}" for p in bp_app.personas.all()}
    assert node_ids == expected


def test_build_app_tool_executor_shares_the_apps_approval_store(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    assert bp_app.tool_executor.approval_store is bp_app.app.approval_store


def test_build_app_uses_isolated_registries_not_the_global_default(tmp_path):
    """AgentApp() with no registry= kwarg falls back to the process-global
    default ToolRegistry; build_app() must pass its own bundle to avoid
    cross-test / cross-app tool-name collisions."""
    first = build_app(log_path=tmp_path / "log1.jsonl")
    second = build_app(log_path=tmp_path / "log2.jsonl")
    assert first.app.tool_registry is not second.app.tool_registry
