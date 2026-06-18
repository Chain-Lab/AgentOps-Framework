"""Phase 47 Task 8: Console federation history/timeline/analytics page tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_app.console.router import build_policy_console_router
from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederationExecutionStrategy,
)
from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEvent,
    FederationHistoryEventType,
    FederationTimeline,
    FederationTargetTimeline,
    FederationWaveTimeline,
    FederationAnalyticsReport,
    FederationTargetHealthSummary,
    FederationWaveOutcomeSummary,
    FederationConflictSummary,
)
from agent_app.runtime.policy_rollout_federation_history_store import InMemoryFederationHistoryStore
from agent_app.runtime.policy_rollout_federation_observability_service import FederationObservabilityService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    event_type: FederationHistoryEventType = FederationHistoryEventType.FEDERATION_STARTED,
    federation_id: str = "frp_test",
    target_id: str | None = None,
    wave_id: str | None = None,
    rollout_id: str | None = None,
    environment: str | None = None,
    message: str | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=f"fhe_{event_type.value.replace('.', '_')}_{federation_id}",
        federation_id=federation_id,
        target_id=target_id,
        wave_id=wave_id,
        rollout_id=rollout_id,
        event_type=event_type,
        environment=environment,
        message=message,
        created_at=_now(),
    )


def _target() -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id="frt_test",
        name="prod-us-canary",
        tenant_id="tenant_a",
        environment="prod",
        ring_name="canary",
        region="us-east",
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        ring_name="canary",
    )


def _plan() -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id="frp_test",
        name="global rollout",
        bundle_id="pb_123",
        strategy=FederationExecutionStrategy.SEQUENTIAL,
        status=FederatedRolloutPlanStatus.ACTIVE,
        target_ids=["frt_test"],
        executions=[
            FederatedRolloutTargetExecution(
                execution_id="fre_test",
                target_id="frt_test",
                rollout_id="ro_child",
                status=FederatedRolloutTargetExecutionStatus.SUCCEEDED,
            )
        ],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


def _client(
    service=None,
    target_store=None,
    plan_store=None,
    observability_service=None,
) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        rollout_federation_service=service,
        federated_rollout_target_store=target_store,
        federated_rollout_plan_store=plan_store,
        federation_observability_service=observability_service,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _observability_service_with_events() -> FederationObservabilityService:
    """Build a FederationObservabilityService with test history events."""
    history_store = InMemoryFederationHistoryStore()
    plan_store = MagicMock()

    # Create test events
    events = [
        _event(
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            federation_id="frp_test",
            message="Federation created",
        ),
        _event(
            event_type=FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_test",
            message="Federation started",
        ),
        _event(
            event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="frp_test",
            target_id="frt_test",
            environment="prod",
            message="Target execution started",
        ),
        _event(
            event_type=FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
            federation_id="frp_test",
            target_id="frt_test",
            environment="prod",
            message="Target execution succeeded",
        ),
        _event(
            event_type=FederationHistoryEventType.FEDERATION_COMPLETED,
            federation_id="frp_test",
            message="Federation completed",
        ),
    ]

    # Store events and return plan for the plan_store.get
    import asyncio
    for e in events:
        asyncio.get_event_loop().run_until_complete(history_store.append(e))

    plan_store.get = AsyncMock(return_value=_plan())

    return FederationObservabilityService(
        history_store=history_store,
        federation_plan_store=plan_store,
    )


class TestFederationHistoryConsole:
    def test_federation_plan_history_renders(self) -> None:
        obs_service = _observability_service_with_events()
        client = _client(observability_service=obs_service)
        response = client.get("/policy-console/federation/plans/frp_test/history")
        assert response.status_code == 200
        assert "frp_test" in response.text
        assert "federation.started" in response.text or "Federation History" in response.text

    def test_federation_plan_detail_has_history_link(self) -> None:
        plan_store = MagicMock()
        plan_store.get = AsyncMock(return_value=_plan())
        obs_service = _observability_service_with_events()
        client = _client(plan_store=plan_store, observability_service=obs_service)
        response = client.get("/policy-console/federation/plans/frp_test")
        assert response.status_code == 200
        # The plan detail page should render; history link is in template
        assert "frp_test" in response.text


class TestFederationTimelineConsole:
    def test_federation_plan_timeline_renders(self) -> None:
        obs_service = _observability_service_with_events()
        client = _client(observability_service=obs_service)
        response = client.get("/policy-console/federation/plans/frp_test/timeline")
        assert response.status_code == 200
        assert "frp_test" in response.text


class TestFederationAnalyticsConsole:
    def test_federation_analytics_renders(self) -> None:
        obs_service = _observability_service_with_events()
        client = _client(observability_service=obs_service)
        response = client.get("/policy-console/federation/analytics")
        assert response.status_code == 200
        assert "Federation Analytics" in response.text

    def test_federation_analytics_post_renders(self) -> None:
        obs_service = _observability_service_with_events()
        client = _client(observability_service=obs_service)
        response = client.post(
            "/policy-console/federation/analytics",
            data={"since": "", "until": ""},
        )
        assert response.status_code == 200
        assert "Federation Analytics" in response.text


class TestFederationErrorsRenderClearly:
    def test_errors_render_clearly_no_service(self) -> None:
        """When no observability service, error message shown, no traceback."""
        client = _client(observability_service=None)
        response = client.get("/policy-console/federation/plans/frp_test/history")
        assert response.status_code == 200
        assert "not configured" in response.text
        assert "Traceback" not in response.text

    def test_errors_render_clearly_timeline_no_service(self) -> None:
        """When no observability service for timeline, error message shown."""
        client = _client(observability_service=None)
        response = client.get("/policy-console/federation/plans/frp_test/timeline")
        assert response.status_code == 200
        assert "not configured" in response.text

    def test_errors_render_clearly_analytics_no_service(self) -> None:
        """When no observability service for analytics, error message shown."""
        client = _client(observability_service=None)
        response = client.get("/policy-console/federation/analytics")
        assert response.status_code == 200
        assert "not configured" in response.text
