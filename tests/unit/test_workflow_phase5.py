"""Tests for Workflow model extensions (Phase 5)."""

import pytest

from agent_app.core.workflow import Workflow, WorkflowType


class TestWorkflowModel:
    def test_single_workflow(self):
        wf = Workflow.single(agent="support", name="cs")
        assert wf.type == WorkflowType.SINGLE
        assert wf.entry == "support"
        assert wf.entry_agent_name() == "support"

    def test_handoff_workflow(self):
        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing"],
            name="cs",
        )
        assert wf.type == WorkflowType.HANDOFF
        assert wf.entry == "triage"
        assert wf.agents == ["refund", "billing"]
        assert wf.entry_agent_name() == "triage"

    def test_handoff_empty_agents(self):
        """Handoff with no candidate agents is allowed (falls back to entry)."""
        wf = Workflow.handoff(entry="triage", agents=[], name="cs")
        assert wf.agents == []

    def test_orchestrator_workflow(self):
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "writer"],
            name="ra",
        )
        assert wf.type == WorkflowType.ORCHESTRATOR
        assert wf.entry == "manager"
        assert wf.agents == ["manager", "researcher", "writer"]
        assert wf.config["agents_as_tools"] == ["researcher", "writer"]
        assert wf.entry_agent_name() == "manager"

    def test_dag_workflow(self):
        """DAG workflow can be created with nodes (Phase 13)."""
        wf = Workflow.dag(
            name="test_dag",
            nodes=[
                {"id": "n1", "type": "agent", "ref": "support"},
                {"id": "n2", "type": "tool", "ref": "tool1", "depends_on": ["n1"]},
            ],
        )
        assert wf.type == WorkflowType.DAG
        assert wf.name == "test_dag"
        assert "dag" in wf.config
        assert len(wf.config["dag"]["nodes"]) == 2

    def test_workflow_type_string(self):
        """WorkflowType values are strings."""
        assert WorkflowType.SINGLE == "single"
        assert WorkflowType.HANDOFF == "handoff"
        assert WorkflowType.ORCHESTRATOR == "orchestrator"
        assert WorkflowType.DAG == "dag"
