"""Tests for Workflow model."""

import pytest

from agent_app.core.workflow import Workflow, WorkflowType


class TestWorkflow:
    def test_single(self) -> None:
        wf = Workflow.single(agent="support")
        assert wf.type == WorkflowType.SINGLE
        assert wf.entry_agent_name() == "support"
        assert wf.name == "default"

    def test_single_custom_name(self) -> None:
        wf = Workflow.single(agent="support", name="cs_workflow")
        assert wf.name == "cs_workflow"
        assert wf.entry_agent_name() == "support"

    def test_handoff(self) -> None:
        wf = Workflow.handoff(
            entry="triage",
            agents=["billing", "refund"],
            name="support",
        )
        assert wf.type == WorkflowType.HANDOFF
        assert wf.entry == "triage"
        assert wf.agents == ["billing", "refund"]

    def test_orchestrator(self) -> None:
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "writer"],
        )
        assert wf.type == WorkflowType.ORCHESTRATOR
        assert wf.entry == "manager"
        assert "researcher" in wf.agents

    def test_dag_not_implemented(self) -> None:
        wf = Workflow.dag(name="test_dag")
        assert wf.type == WorkflowType.DAG
        assert wf.name == "test_dag"
        assert "dag" in wf.config

    def test_entry_agent_name_single(self) -> None:
        wf = Workflow(name="wf", type=WorkflowType.SINGLE, entry="bot")
        assert wf.entry_agent_name() == "bot"

    def test_entry_agent_name_handoff(self) -> None:
        wf = Workflow(name="wf", type=WorkflowType.HANDOFF, entry="triage")
        assert wf.entry_agent_name() == "triage"
