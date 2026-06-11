"""Tests for Phase 27: policy replay console pages."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agent_app.adapters.fastapi import create_fastapi_app
from agent_app import AgentApp
from agent_app.governance.policy_decision_store import InMemoryPolicyDecisionStore
from agent_app.runtime.policy_replay_store import InMemoryPolicyReplayStore
from agent_app.governance.policy_replay import (
    PolicyReplayResult,
    PolicyReplayRun,
    PolicyReplayStatus,
    PolicyReplayDecisionChange,
)
from datetime import datetime, timezone


class TestReplayConsoleRoutes:
    """Test replay pages in the policy console."""

    def _make_app_with_console(self):
        app = AgentApp()
        app._console_config = type("C", (), {
            "enabled": True,
            "base_path": "/policy-console",
            "title": "Test Console",
            "page_size": 50,
        })()
        app.policy_decision_store = InMemoryPolicyDecisionStore()
        app._replay_store = InMemoryPolicyReplayStore()
        return app

    def test_disabled_console_no_replay_routes(self):
        """Replay routes return 404 when console is disabled."""
        app = AgentApp()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replays")
        assert resp.status_code == 404

    def test_enabled_replays_page_returns_200(self):
        """Replays index page returns 200 when console enabled."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replays")
        assert resp.status_code == 200
        assert "Replay" in resp.text

    def test_replays_page_empty_state(self):
        """Replays page shows empty state when no replays."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replays")
        assert resp.status_code == 200
        assert "No replay results" in resp.text or "not configured" in resp.text

    def test_replay_detail_returns_200(self):
        """Replay detail page returns 200 for existing replay."""
        app = self._make_app_with_console()
        # Pre-populate a replay result
        run = PolicyReplayRun(
            replay_id="replay_test_1",
            status=PolicyReplayStatus.COMPLETED,
            source_decision_count=2,
            changed_count=1,
            unchanged_count=1,
            failed_count=0,
            created_at=datetime.now(timezone.utc),
        )
        changes = [
            PolicyReplayDecisionChange(
                decision_id="dec_1",
                original_action="allow",
                replayed_action="deny",
                changed=True,
                original_rule_id=None,
                replayed_rule_id="deny_rule",
            ),
        ]
        result = PolicyReplayResult(replay=run, changes=changes)

        import asyncio
        asyncio.run(app._replay_store.save(result))

        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replays/replay_test_1")
        assert resp.status_code == 200
        assert "replay_test_1" in resp.text
        assert "Changed" in resp.text

    def test_replay_detail_missing_returns_friendly_error(self):
        """Replay detail returns friendly message for missing replay."""
        app = self._make_app_with_console()
        client = TestClient(create_fastapi_app(app))
        resp = client.get("/policy-console/replays/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_replay_routes_disabled_when_console_disabled(self):
        """Replay routes are not registered when console is disabled."""
        app = AgentApp()
        client = TestClient(create_fastapi_app(app))
        assert client.get("/policy-console/replays").status_code == 404
        assert client.get("/policy-console/replays/any_id").status_code == 404
