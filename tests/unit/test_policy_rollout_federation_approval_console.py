"""Phase 48 Task 7: Console federation approval page tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_app.console.router import build_policy_console_router
from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalPolicy,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.runtime.policy_rollout_federation_approval_store import InMemoryFederationApprovalStore
from agent_app.runtime.policy_rollout_federation_approval_service import FederationApprovalService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _approval_request(
    approval_id: str = "fap_test001",
    federation_id: str = "frp_test",
    action: str = "federation.plan.start",
    status: FederationApprovalStatus = FederationApprovalStatus.PENDING,
    requested_by: str = "release_manager",
    required_approvers: list[str] | None = None,
    tenant_id: str | None = "tenant_a",
) -> FederationApprovalRequest:
    return FederationApprovalRequest(
        approval_id=approval_id,
        federation_id=federation_id,
        action=action,
        status=status,
        requested_by=requested_by,
        required_approvers=required_approvers or ["approver_a", "approver_b"],
        tenant_id=tenant_id,
        created_at=_now(),
    )


def _client(
    approval_store=None,
    approval_service=None,
) -> TestClient:
    app = FastAPI()
    router = build_policy_console_router(
        store=None,
        federation_approval_store=approval_store,
        federation_approval_service=approval_service,
    )
    app.include_router(router, prefix="/policy-console")
    return TestClient(app)


def _store_with_approval() -> InMemoryFederationApprovalStore:
    """Build an InMemoryFederationApprovalStore with a test approval request."""
    store = InMemoryFederationApprovalStore()
    request = _approval_request()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.create(request))
    finally:
        loop.close()
    return store


def _service_with_approval() -> tuple[FederationApprovalService, InMemoryFederationApprovalStore]:
    """Build a FederationApprovalService with a test approval request."""
    store = _store_with_approval()
    policy = FederationApprovalPolicy(
        enabled=True,
        require_approval_for=["federation.plan.start"],
        default_required_approvers=["approver_a", "approver_b"],
    )
    service = FederationApprovalService(
        approval_store=store,
        approval_policy=policy,
    )
    return service, store


class TestFederationApprovalListPage:
    def test_approval_list_page_renders(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/approvals")
        assert response.status_code == 200
        assert "fap_test001" in response.text
        assert "frp_test" in response.text

    def test_approval_list_page_no_store(self) -> None:
        """When approval store is None, the approval routes are not registered (404)."""
        client = _client(approval_store=None)
        response = client.get("/policy-console/federation/approvals")
        # Routes are not registered when store is None, so 404 is expected
        assert response.status_code == 404

    def test_approval_list_page_with_status_filter(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/approvals?status=pending")
        assert response.status_code == 200
        assert "fap_test001" in response.text

    def test_approval_list_page_with_federation_id_filter(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/approvals?federation_id=frp_test")
        assert response.status_code == 200
        assert "fap_test001" in response.text


class TestFederationApprovalDetailPage:
    def test_approval_detail_page_renders(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/approvals/fap_test001")
        assert response.status_code == 200
        assert "fap_test001" in response.text
        assert "federation.plan.start" in response.text

    def test_approval_detail_page_not_found(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/approvals/fap_nonexistent")
        assert response.status_code == 200
        assert "not found" in response.text.lower()


class TestFederationPlanApprovalsPage:
    def test_plan_approvals_page_renders(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store)
        response = client.get("/policy-console/federation/plans/frp_test/approvals")
        assert response.status_code == 200
        assert "frp_test" in response.text
        assert "fap_test001" in response.text


class TestFederationApprovalApproveAction:
    def test_approve_action_works(self) -> None:
        service, store = _service_with_approval()
        client = _client(approval_store=store, approval_service=service)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/approve",
            data={"actor_id": "approver_a", "reason": "Looks good"},
        )
        assert response.status_code == 200
        assert "fap_test001" in response.text

    def test_approve_action_unauthorized_actor(self) -> None:
        service, store = _service_with_approval()
        client = _client(approval_store=store, approval_service=service)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/approve",
            data={"actor_id": "unauthorized_user"},
        )
        # PermissionError should result in 400
        assert response.status_code == 400

    def test_approve_action_no_service(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store, approval_service=None)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/approve",
            data={"actor_id": "approver_a"},
        )
        assert response.status_code == 400


class TestFederationApprovalRejectAction:
    def test_reject_action_works(self) -> None:
        service, store = _service_with_approval()
        client = _client(approval_store=store, approval_service=service)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/reject",
            data={"actor_id": "approver_a", "reason": "Not ready"},
        )
        assert response.status_code == 200
        assert "fap_test001" in response.text

    def test_reject_action_unauthorized_actor(self) -> None:
        service, store = _service_with_approval()
        client = _client(approval_store=store, approval_service=service)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/reject",
            data={"actor_id": "unauthorized_user"},
        )
        assert response.status_code == 400

    def test_reject_action_no_service(self) -> None:
        store = _store_with_approval()
        client = _client(approval_store=store, approval_service=None)
        response = client.post(
            "/policy-console/federation/approvals/fap_test001/reject",
            data={"actor_id": "approver_a"},
        )
        assert response.status_code == 400
