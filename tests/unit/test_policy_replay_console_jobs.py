"""Tests for Phase 28: policy replay console job pages."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agent_app.adapters.fastapi import create_fastapi_app
from agent_app import AgentApp
from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
from agent_app.runtime.policy_replay_store import InMemoryPolicyReplayStore
from agent_app.runtime.policy_replay_jobs import (
    InMemoryPolicyReplayJobStore,
    PolicyReplayJob,
    PolicyReplayJobStatus,
)
from agent_app.governance.policy_replay import (
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
    PolicyReplayDecisionChange,
)
from datetime import datetime, timezone


class TestReplayJobConsoleRoutes:
    """Test replay job pages in the policy console."""

    def _make_app_with_console(self, job_store=None):
        app = AgentApp()
        app._console_config = type("C", (), {
            "enabled": True,
            "base_path": "/policy-console",
            "title": "Test Console",
            "page_size": 50,
        })()
        app.policy_decision_store = InMemoryPolicyDecisionStore()
        app._replay_store = InMemoryPolicyReplayStore()
        app._replay_job_store = job_store or InMemoryPolicyReplayJobStore()
        return app

    def test_disabled_console_no_job_routes(self):
        """Job routes return 404 when console is disabled."""
        app = AgentApp()
        client = TestClient(create_fastapi_app(app))
        assert client.get("/policy-console/replay-jobs").status_code == 404

    def test_enabled_jobs_page_returns_200(self):
        """Jobs index page returns 200 when console enabled."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs")
        assert resp.status_code == 200
        assert "Replay Job" in resp.text or "Replay Jobs" in resp.text

    def test_jobs_page_empty_state(self):
        """Jobs page shows empty state when no jobs."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs")
        assert resp.status_code == 200
        assert "No replay jobs" in resp.text or "not configured" in resp.text

    def test_jobs_page_with_jobs(self):
        """Jobs page shows jobs when present."""
        import asyncio
        store = InMemoryPolicyReplayJobStore()
        app = self._make_app_with_console(job_store=store)
        asyncio.run(store.create(PolicyReplayJob(
            job_id="job_show_1",
            status=PolicyReplayJobStatus.COMPLETED,
            replay_id="replay_test_1",
            requested_by="admin",
        )))
        asyncio.run(store.create(PolicyReplayJob(
            job_id="job_show_2",
            status=PolicyReplayJobStatus.RUNNING,
            tenant_id="tenant_a",
        )))

        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs")
        assert resp.status_code == 200
        assert "job_show_1" in resp.text
        assert "job_show_2" in resp.text
        assert "completed" in resp.text.lower()
        assert "running" in resp.text.lower()

    def test_job_detail_returns_200(self):
        """Job detail page returns 200 for existing job."""
        import asyncio
        store = InMemoryPolicyReplayJobStore()
        app = self._make_app_with_console(job_store=store)
        job = PolicyReplayJob(
            job_id="job_detail_1",
            status=PolicyReplayJobStatus.COMPLETED,
            replay_id="replay_detail_1",
            limit=50,
            tenant_id="tenant_a",
            tool_name="refund.request",
            requested_by="admin",
        )
        asyncio.run(store.create(job))

        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs/job_detail_1")
        assert resp.status_code == 200
        assert "job_detail_1" in resp.text
        assert "replay_detail_1" in resp.text

    def test_job_detail_missing_returns_friendly_error(self):
        """Job detail returns friendly message for missing job."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_job_detail_shows_error_for_failed_job(self):
        """Job detail page shows error for failed jobs."""
        import asyncio
        store = InMemoryPolicyReplayJobStore()
        app = self._make_app_with_console(job_store=store)
        job = PolicyReplayJob(
            job_id="job_failed_1",
            status=PolicyReplayJobStatus.FAILED,
            error={"message": "Policy engine not configured"},
        )
        asyncio.run(store.create(job))

        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs/job_failed_1")
        assert resp.status_code == 200
        assert "failed" in resp.text.lower()
        assert "Policy engine not configured" in resp.text

    def test_job_routes_disabled_when_console_disabled(self):
        """Job routes are not registered when console is disabled."""
        app = AgentApp()
        client = TestClient(create_fastapi_app(app))
        assert client.get("/policy-console/replay-jobs").status_code == 404
        assert client.get("/policy-console/replay-jobs/any_id").status_code == 404

    def test_jobs_page_without_store_available(self):
        """Jobs page shows empty state when no job store attribute is set."""
        app = AgentApp()
        app._console_config = type("C", (), {
            "enabled": True,
            "base_path": "/policy-console",
            "title": "Test Console",
            "page_size": 50,
        })()
        app.policy_decision_store = InMemoryPolicyDecisionStore()
        app._replay_store = InMemoryPolicyReplayStore()
        # Don't set _replay_job_store at all — adapter creates default

        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replay-jobs")
        assert resp.status_code == 200
        # Adapter creates default in-memory store, so shows "no jobs"
        assert "No replay jobs" in resp.text
