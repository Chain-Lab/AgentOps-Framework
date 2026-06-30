"""Tests for Phase 63 PersistentApprovalStore.

Phase 63: Persistent Approval / Control Plane — SQLite-backed approval
request store with approve/reject/expire lifecycle.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_approval_store import (
    PersistentApprovalRequest,
    PersistentApprovalStatus,
    PersistentApprovalStore,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh PersistentApprovalStore in a temporary directory."""
    db = str(tmp_path / "approval.db")
    s = PersistentApprovalStore(db)
    yield s
    s.close()


class TestPersistentApprovalStoreCreate:
    def test_create_approval(self, store):
        approval = store.create(
            approval_id="appr_001",
            approval_type="shutdown",
            requested_by="operator",
        )
        assert approval.approval_id == "appr_001"
        assert approval.approval_type == "shutdown"
        assert approval.status == PersistentApprovalStatus.PENDING
        assert approval.requested_by == "operator"

    def test_create_with_payload(self, store):
        approval = store.create(
            approval_id="appr_002",
            approval_type="deploy",
            reason="rollout to prod",
            payload={"version": "1.2.3"},
            daemon_id="daemon-1",
        )
        assert approval.payload == {"version": "1.2.3"}
        assert approval.daemon_id == "daemon-1"

    def test_create_with_expiry(self, store):
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        approval = store.create(
            approval_id="appr_003",
            approval_type="pause",
            expires_at=expires,
        )
        assert approval.expires_at is not None


class TestPersistentApprovalStoreGet:
    def test_get_existing(self, store):
        store.create("appr_001", "shutdown")
        approval = store.get("appr_001")
        assert approval is not None
        assert approval.approval_type == "shutdown"

    def test_get_missing(self, store):
        approval = store.get("appr_nonexistent")
        assert approval is None


class TestPersistentApprovalStoreList:
    def test_list_pending(self, store):
        store.create("appr_001", "shutdown")
        store.create("appr_002", "deploy")
        pending = store.list_pending()
        assert len(pending) == 2

    def test_list_pending_empty_after_approve(self, store):
        store.create("appr_001", "shutdown")
        store.approve("appr_001", "admin")
        pending = store.list_pending()
        assert len(pending) == 0

    def test_list_by_daemon(self, store):
        store.create("appr_001", "shutdown", daemon_id="d1")
        store.create("appr_002", "pause", daemon_id="d2")
        d1_approvals = store.list_by_daemon("d1")
        assert len(d1_approvals) == 1
        assert d1_approvals[0].approval_id == "appr_001"


class TestPersistentApprovalStoreTransitions:
    def test_approve(self, store):
        store.create("appr_001", "shutdown", requested_by="operator")
        approval = store.approve("appr_001", "admin", reason="approved")
        assert approval.status == PersistentApprovalStatus.APPROVED
        assert approval.resolved_by == "admin"
        assert approval.reason == "approved"
        assert approval.resolved_at is not None

    def test_reject(self, store):
        store.create("appr_001", "deploy", requested_by="operator")
        approval = store.reject("appr_001", "admin", reason="not now")
        assert approval.status == PersistentApprovalStatus.REJECTED
        assert approval.resolved_by == "admin"

    def test_cannot_approve_twice(self, store):
        store.create("appr_001", "shutdown")
        store.approve("appr_001", "admin")
        with pytest.raises(ValueError, match="terminal state"):
            store.approve("appr_001", "admin2")

    def test_cannot_reject_twice(self, store):
        store.create("appr_001", "shutdown")
        store.reject("appr_001", "admin")
        with pytest.raises(ValueError, match="terminal state"):
            store.reject("appr_001", "admin2")

    def test_cannot_approve_missing(self, store):
        with pytest.raises(KeyError, match="not found"):
            store.approve("appr_nonexistent", "admin")

    def test_cannot_transition_from_non_pending(self, store):
        store.create("appr_001", "shutdown")
        store.approve("appr_001", "admin")
        with pytest.raises(ValueError, match="terminal state"):
            store.reject("appr_001", "admin")


class TestPersistentApprovalStoreExpire:
    def test_expire_old(self, store):
        from datetime import datetime, timezone, timedelta
        # Create approval with past expiry
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        store.create(
            "appr_001",
            "shutdown",
            expires_at=past,
        )
        count = store.expire_old(now=datetime.now(timezone.utc))
        assert count == 1
        approval = store.get("appr_001")
        assert approval.status == PersistentApprovalStatus.EXPIRED

    def test_expire_does_not_affect_approved(self, store):
        store.create("appr_001", "shutdown")
        store.approve("appr_001", "admin")
        count = store.expire_old()
        assert count == 0

    def test_expire_does_not_affect_no_expiry(self, store):
        store.create("appr_001", "shutdown")  # no expires_at
        count = store.expire_old()
        assert count == 0


class TestPersistentApprovalStorePersistence:
    def test_sqlite_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "approval.db")
        # Write in first instance
        s1 = PersistentApprovalStore(db)
        s1.create("appr_001", "shutdown", requested_by="op1")
        s1.close()

        # Read in second instance
        s2 = PersistentApprovalStore(db)
        approval = s2.get("appr_001")
        assert approval is not None
        assert approval.requested_by == "op1"
        assert approval.status == PersistentApprovalStatus.PENDING
        s2.close()
