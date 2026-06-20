"""Tests for FederationApprovalStore — InMemory, SQLite, and factory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalDashboardSummary,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.runtime.policy_rollout_federation_approval_store import (
    FederationApprovalStore,
    InMemoryFederationApprovalStore,
    SQLiteFederationApprovalStore,
    create_federation_approval_store,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_request(
    approval_id: str = "fap_001",
    federation_id: str = "fed_a",
    rollout_id: str | None = None,
    target_id: str | None = None,
    wave_id: str | None = None,
    tenant_id: str | None = "tenant_a",
    environment: str | None = "prod",
    region: str | None = "us-east",
    ring: str | None = "canary",
    action: str = "federation.plan.start",
    requested_by: str = "actor_1",
    required_approvers: list[str] | None = None,
    delegated_approvers: list[str] | None = None,
    status: FederationApprovalStatus = FederationApprovalStatus.PENDING,
    reason: str | None = None,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> FederationApprovalRequest:
    return FederationApprovalRequest(
        approval_id=approval_id,
        federation_id=federation_id,
        rollout_id=rollout_id,
        target_id=target_id,
        wave_id=wave_id,
        tenant_id=tenant_id,
        environment=environment,
        region=region,
        ring=ring,
        action=action,
        requested_by=requested_by,
        required_approvers=required_approvers or ["approver_1"],
        delegated_approvers=delegated_approvers or [],
        status=status,
        reason=reason,
        expires_at=expires_at,
        created_at=created_at or _now(),
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# InMemoryFederationApprovalStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryFederationApprovalStore:
    async def test_create_and_get(self) -> None:
        store = InMemoryFederationApprovalStore()
        req = _make_request()
        result = await store.create(req)

        assert result == req
        assert await store.get("fap_001") == req

    async def test_get_missing_returns_none(self) -> None:
        store = InMemoryFederationApprovalStore()
        assert await store.get("fap_missing") is None

    async def test_list_no_filters(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", created_at=_now(0))
        r2 = _make_request(approval_id="fap_002", created_at=_now(10))

        await store.create(r1)
        await store.create(r2)

        result = await store.list()
        assert len(result) == 2
        assert result[0].approval_id == "fap_001"
        assert result[1].approval_id == "fap_002"

    async def test_list_filter_by_federation_id(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", federation_id="fed_a")
        r2 = _make_request(approval_id="fap_002", federation_id="fed_b")

        await store.create(r1)
        await store.create(r2)

        assert await store.list(federation_id="fed_a") == [r1]
        assert await store.list(federation_id="fed_b") == [r2]
        assert await store.list(federation_id="fed_c") == []

    async def test_list_filter_by_status(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", status=FederationApprovalStatus.PENDING)
        r2 = _make_request(approval_id="fap_002", status=FederationApprovalStatus.APPROVED)

        await store.create(r1)
        await store.create(r2)

        assert await store.list(status=FederationApprovalStatus.PENDING) == [r1]
        assert await store.list(status=FederationApprovalStatus.APPROVED) == [r2]

    async def test_list_filter_by_tenant_id(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", tenant_id="tenant_a")
        r2 = _make_request(approval_id="fap_002", tenant_id="tenant_b")

        await store.create(r1)
        await store.create(r2)

        assert await store.list(tenant_id="tenant_a") == [r1]
        assert await store.list(tenant_id="tenant_b") == [r2]

    async def test_list_filter_by_action(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", action="federation.plan.start")
        r2 = _make_request(approval_id="fap_002", action="federation.target.enable")

        await store.create(r1)
        await store.create(r2)

        assert await store.list(action="federation.plan.start") == [r1]
        assert await store.list(action="federation.target.enable") == [r2]

    async def test_list_filter_by_environment_and_ring(self) -> None:
        store = InMemoryFederationApprovalStore()
        r1 = _make_request(approval_id="fap_001", environment="prod", ring="canary")
        r2 = _make_request(approval_id="fap_002", environment="staging", ring="full")
        r3 = _make_request(approval_id="fap_003", environment="prod", ring="full")

        await store.create(r1)
        await store.create(r2)
        await store.create(r3)

        assert await store.list(environment="prod") == [r1, r3]
        assert await store.list(ring="canary") == [r1]
        assert await store.list(environment="prod", ring="full") == [r3]

    async def test_approve_request(self) -> None:
        store = InMemoryFederationApprovalStore()
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.approve("fap_001", "approver_1", reason="Looks good")

        assert result.status == FederationApprovalStatus.APPROVED
        assert "approver_1" in result.approvers_who_approved
        assert result.resolved_by == "approver_1"
        assert result.resolved_at is not None
        assert result.reason == "Looks good"

    async def test_reject_request(self) -> None:
        store = InMemoryFederationApprovalStore()
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.reject("fap_001", "approver_1", reason="Not ready")

        assert result.status == FederationApprovalStatus.REJECTED
        assert "approver_1" in result.approvers_who_rejected
        assert result.resolved_by == "approver_1"
        assert result.resolved_at is not None
        assert result.rejection_reason == "Not ready"

    async def test_escalate_request(self) -> None:
        store = InMemoryFederationApprovalStore()
        req = _make_request(approval_id="fap_001", required_approvers=["approver_1"])
        await store.create(req)

        result = await store.escalate(
            "fap_001",
            escalated_by="admin_1",
            new_required_approvers=["approver_2", "approver_3"],
            reason="No response after timeout",
        )

        assert result.status == FederationApprovalStatus.ESCALATED
        assert result.escalation_level == 1
        assert result.escalation_reason == "No response after timeout"
        assert "approver_2" in result.required_approvers
        assert "approver_3" in result.required_approvers
        assert "approver_1" in result.required_approvers

    async def test_cancel_request(self) -> None:
        store = InMemoryFederationApprovalStore()
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.cancel("fap_001", "admin_1", reason="No longer needed")

        assert result.status == FederationApprovalStatus.CANCELLED
        assert result.resolved_by == "admin_1"
        assert result.resolved_at is not None
        assert result.reason == "No longer needed"

    async def test_expire_pending(self) -> None:
        store = InMemoryFederationApprovalStore()
        now = _now(0)
        r1 = _make_request(approval_id="fap_001", expires_at=now - timedelta(seconds=60))
        r2 = _make_request(approval_id="fap_002", expires_at=now + timedelta(seconds=600))
        r3 = _make_request(approval_id="fap_003", status=FederationApprovalStatus.APPROVED, expires_at=now - timedelta(seconds=60))

        await store.create(r1)
        await store.create(r2)
        await store.create(r3)

        expired = await store.expire_pending(now=now)

        assert len(expired) == 1
        assert expired[0].approval_id == "fap_001"
        assert expired[0].status == FederationApprovalStatus.EXPIRED
        assert expired[0].resolved_at == now

        # r2 still pending
        r2_check = await store.get("fap_002")
        assert r2_check is not None
        assert r2_check.status == FederationApprovalStatus.PENDING

        # r3 was already approved, not expired
        r3_check = await store.get("fap_003")
        assert r3_check is not None
        assert r3_check.status == FederationApprovalStatus.APPROVED

    async def test_get_dashboard_summary(self) -> None:
        store = InMemoryFederationApprovalStore()
        base = _now(0)

        # Pending
        await store.create(_make_request(approval_id="fap_001", tenant_id="t1", action="federation.plan.start", created_at=base))
        # Approved (with resolvable latency)
        r2 = _make_request(approval_id="fap_002", status=FederationApprovalStatus.APPROVED, created_at=base)
        r2.resolved_at = base + timedelta(seconds=30)
        await store.create(r2)
        # Rejected
        await store.create(_make_request(approval_id="fap_003", status=FederationApprovalStatus.REJECTED, created_at=base))
        # Expired
        await store.create(_make_request(approval_id="fap_004", status=FederationApprovalStatus.EXPIRED, created_at=base))
        # Escalated
        await store.create(_make_request(approval_id="fap_005", tenant_id="t2", status=FederationApprovalStatus.ESCALATED, action="federation.target.enable", created_at=base))
        # Cancelled
        await store.create(_make_request(approval_id="fap_006", status=FederationApprovalStatus.CANCELLED, created_at=base))

        summary = await store.get_dashboard_summary()

        assert summary.total_pending == 1
        assert summary.total_approved == 1
        assert summary.total_rejected == 1
        assert summary.total_expired == 1
        assert summary.total_escalated == 1
        assert summary.total_cancelled == 1
        assert summary.average_approval_latency_seconds == 30.0
        assert summary.by_tenant == {"t1": 1}
        assert summary.by_action == {"federation.plan.start": 1}
        assert summary.blocked_federation_actions == 1

    async def test_get_dashboard_summary_with_tenant_filter(self) -> None:
        store = InMemoryFederationApprovalStore()

        await store.create(_make_request(approval_id="fap_001", tenant_id="t1", action="a1"))
        await store.create(_make_request(approval_id="fap_002", tenant_id="t2", action="a2"))

        summary = await store.get_dashboard_summary(tenant_id="t1")
        assert summary.total_pending == 1
        assert summary.by_action == {"a1": 1}

    async def test_approve_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationApprovalStore()
        with pytest.raises(ValueError, match="not found"):
            await store.approve("fap_missing", "approver_1")

    async def test_reject_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationApprovalStore()
        with pytest.raises(ValueError, match="not found"):
            await store.reject("fap_missing", "approver_1")

    async def test_escalate_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationApprovalStore()
        with pytest.raises(ValueError, match="not found"):
            await store.escalate("fap_missing")

    async def test_cancel_nonexistent_raises_value_error(self) -> None:
        store = InMemoryFederationApprovalStore()
        with pytest.raises(ValueError, match="not found"):
            await store.cancel("fap_missing", "admin_1")


# ---------------------------------------------------------------------------
# SQLiteFederationApprovalStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSQLiteFederationApprovalStore:
    async def test_create_and_get(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request()
        await store.create(req)

        result = await store.get("fap_001")
        assert result is not None
        assert result.approval_id == "fap_001"
        assert result.federation_id == "fed_a"
        assert result.action == "federation.plan.start"
        assert result.required_approvers == ["approver_1"]
        assert result.delegated_approvers == []
        assert result.status == FederationApprovalStatus.PENDING
        assert result.metadata == {}
        store.close()

    async def test_list_with_filters(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))

        r1 = _make_request(approval_id="fap_001", federation_id="fed_a", tenant_id="t1", action="a1", environment="prod", ring="canary", created_at=_now(0))
        r2 = _make_request(approval_id="fap_002", federation_id="fed_b", tenant_id="t2", action="a2", environment="staging", ring="full", created_at=_now(10))

        await store.create(r1)
        await store.create(r2)

        # Filter by federation_id
        result = await store.list(federation_id="fed_a")
        assert len(result) == 1
        assert result[0].approval_id == "fap_001"

        # Filter by tenant_id
        result = await store.list(tenant_id="t2")
        assert len(result) == 1
        assert result[0].approval_id == "fap_002"

        # Filter by action
        result = await store.list(action="a1")
        assert len(result) == 1
        assert result[0].approval_id == "fap_001"

        # Filter by environment
        result = await store.list(environment="prod")
        assert len(result) == 1
        assert result[0].approval_id == "fap_001"

        # Filter by ring
        result = await store.list(ring="full")
        assert len(result) == 1
        assert result[0].approval_id == "fap_002"

        # No filters
        result = await store.list()
        assert len(result) == 2

        store.close()

    async def test_approve(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.approve("fap_001", "approver_1", reason="LGTM")
        assert result.status == FederationApprovalStatus.APPROVED
        assert "approver_1" in result.approvers_who_approved
        assert result.resolved_by == "approver_1"
        assert result.resolved_at is not None
        assert result.reason == "LGTM"

        # Verify persistence
        loaded = await store.get("fap_001")
        assert loaded is not None
        assert loaded.status == FederationApprovalStatus.APPROVED
        store.close()

    async def test_reject(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.reject("fap_001", "approver_1", reason="Bad")
        assert result.status == FederationApprovalStatus.REJECTED
        assert "approver_1" in result.approvers_who_rejected
        assert result.rejection_reason == "Bad"
        store.close()

    async def test_escalate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request(approval_id="fap_001", required_approvers=["approver_1"])
        await store.create(req)

        result = await store.escalate("fap_001", escalated_by="admin", new_required_approvers=["approver_2"], reason="Timeout")
        assert result.status == FederationApprovalStatus.ESCALATED
        assert result.escalation_level == 1
        assert "approver_2" in result.required_approvers
        assert result.escalation_reason == "Timeout"
        store.close()

    async def test_cancel(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request(approval_id="fap_001")
        await store.create(req)

        result = await store.cancel("fap_001", "admin_1", reason="Obsolete")
        assert result.status == FederationApprovalStatus.CANCELLED
        assert result.resolved_by == "admin_1"
        assert result.reason == "Obsolete"
        store.close()

    async def test_persistence_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = SQLiteFederationApprovalStore(str(db_path))
        req = _make_request(approval_id="fap_001", metadata={"key": "value"})
        await store.create(req)
        store.close()

        # Reopen same DB
        store2 = SQLiteFederationApprovalStore(str(db_path))
        loaded = await store2.get("fap_001")

        assert loaded is not None
        assert loaded.approval_id == "fap_001"
        assert loaded.federation_id == "fed_a"
        assert loaded.action == "federation.plan.start"
        assert loaded.required_approvers == ["approver_1"]
        assert loaded.metadata == {"key": "value"}
        assert loaded.status == FederationApprovalStatus.PENDING
        store2.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationApprovalStore:
    def test_create_memory(self) -> None:
        store = create_federation_approval_store("memory")
        assert isinstance(store, InMemoryFederationApprovalStore)
        assert isinstance(store, FederationApprovalStore)

    def test_create_sqlite(self, tmp_path: Path) -> None:
        db_path = tmp_path / "approvals.db"
        store = create_federation_approval_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteFederationApprovalStore)
        assert isinstance(store, FederationApprovalStore)
        store.close()

    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown federation approval store type"):
            create_federation_approval_store("redis")
