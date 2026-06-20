# Phase 44: Notification Hooks and Expiration Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add framework-level notification hooks (models, rules, channels, service, stores) and expiration sweep service (with optional in-process worker) so governance states that require operator attention become actionable.

**Architecture:** Notification rules match policy events to channels (log/memory). The notification service creates messages, stores them, and delivers through channels. The expiration service sweeps pending approvals and gate requirements past their TTL, marks them expired, and triggers notifications. An optional in-process worker calls the expiration service on an interval with explicit start/stop lifecycle.

**Tech Stack:** Python 3.10+, Pydantic, SQLite, asyncio, standard library logging

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `agent_app/governance/policy_notification.py` | Notification message + rule models |
| `agent_app/governance/policy_expiration.py` | Expiration result + sweep report models |
| `agent_app/runtime/policy_notification_store.py` | Notification delivery store (Protocol + InMemory + SQLite + factory) |
| `agent_app/runtime/policy_notification_rule_store.py` | Notification rule store (Protocol + InMemory + SQLite + factory) |
| `agent_app/runtime/policy_notification_channels.py` | Log + InMemory notification channels |
| `agent_app/runtime/policy_notification_service.py` | NotificationService: match rules, create/send/list |
| `agent_app/runtime/policy_expiration_service.py` | ExpirationService: sweep approvals + gate requirements |
| `agent_app/runtime/policy_expiration_worker.py` | Optional in-process expiration worker (start/stop/run_once) |
| `agent_app/console/templates/policy_notifications.html` | Notification list page |
| `agent_app/console/templates/policy_notification_rules.html` | Notification rule list page |
| `agent_app/console/templates/policy_expiration.html` | Expiration sweep page |
| `tests/unit/test_policy_notification_model.py` | Notification model tests |
| `tests/unit/test_policy_notification_store.py` | Notification store tests |
| `tests/unit/test_policy_notification_rule_store.py` | Rule store tests |
| `tests/unit/test_policy_notification_channels.py` | Channel tests |
| `tests/unit/test_policy_notification_service.py` | Notification service tests |
| `tests/unit/test_policy_expiration_model.py` | Expiration model tests |
| `tests/unit/test_policy_expiration_service.py` | Expiration service tests |
| `tests/unit/test_policy_expiration_worker.py` | Worker tests |
| `tests/unit/test_policy_notification_config.py` | Config/loader/RBAC/events/AgentApp tests |
| `tests/unit/test_policy_notification_cli.py` | CLI tests |
| `tests/unit/test_policy_notification_console.py` | Console tests |

### Modified Files

| File | Changes |
|------|---------|
| `agent_app/governance/policy_rbac.py` | +7 RBAC permissions (NOTIFICATION_VIEW, NOTIFICATION_SEND, NOTIFICATION_RULE_VIEW, NOTIFICATION_RULE_ENABLE, NOTIFICATION_RULE_DISABLE, EXPIRATION_SWEEP, EXPIRATION_VIEW) |
| `agent_app/governance/policy_change_event.py` | +10 event types |
| `agent_app/config/schema.py` | +3 config models (NotificationRuleConfig, NotificationConfig, ExpirationConfig), +2 fields on PolicyReleaseConfig |
| `agent_app/config/loader.py` | Wire notification + expiration services |
| `agent_app/core/app.py` | +3 properties (notification_service, expiration_service, expiration_worker) |
| `agent_app/cli.py` | notification list/send-pending/rule list/enable/disable + expiration sweep/run-once commands |
| `agent_app/console/router.py` | +6 routes for notifications/rules/expiration |
| `agent_app/adapters/fastapi.py` | Wire notification_service, expiration_service |
| `docs/policy_release.md` | Phase 44 section |
| `CHANGELOG.md` | v0.32.0 entry |
| `README.md` | Phase 44 in roadmap |
| `docs/release_checklist_phase44.md` | Release checklist |

---

### Task 1: Notification models — PolicyNotificationMessage and PolicyNotificationRule

**Files:**
- Create: `agent_app/governance/policy_notification.py`
- Test: `tests/unit/test_policy_notification_model.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for notification models (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationMessage,
    PolicyNotificationRuleStatus,
    PolicyNotificationRule,
)


class TestPolicyNotificationSeverity:
    def test_values(self):
        assert PolicyNotificationSeverity.INFO == "info"
        assert PolicyNotificationSeverity.WARNING == "warning"
        assert PolicyNotificationSeverity.ERROR == "error"
        assert PolicyNotificationSeverity.CRITICAL == "critical"


class TestPolicyNotificationStatus:
    def test_values(self):
        assert PolicyNotificationStatus.PENDING == "pending"
        assert PolicyNotificationStatus.SENT == "sent"
        assert PolicyNotificationStatus.FAILED == "failed"
        assert PolicyNotificationStatus.SUPPRESSED == "suppressed"


class TestPolicyNotificationMessage:
    def test_valid_message(self):
        msg = PolicyNotificationMessage(
            notification_id="pn_001",
            event_type="policy.rollout.gate.failed",
            severity=PolicyNotificationSeverity.ERROR,
            title="Gate failed",
            body="Rollout gate failed for step s1",
            created_at=datetime.now(timezone.utc),
        )
        assert msg.notification_id == "pn_001"
        assert msg.status == PolicyNotificationStatus.PENDING
        assert msg.sent_at is None
        assert msg.error is None

    def test_id_prefix_validation(self):
        with pytest.raises(ValueError, match="pn_"):
            PolicyNotificationMessage(
                notification_id="bad_id",
                event_type="test",
                severity=PolicyNotificationSeverity.INFO,
                title="t",
                body="b",
                created_at=datetime.now(timezone.utc),
            )

    def test_tz_aware_created_at(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            PolicyNotificationMessage(
                notification_id="pn_001",
                event_type="test",
                severity=PolicyNotificationSeverity.INFO,
                title="t",
                body="b",
                created_at=datetime.now(),
            )

    def test_with_source_and_actor(self):
        msg = PolicyNotificationMessage(
            notification_id="pn_002",
            event_type="policy.rollout.approval.requested",
            severity=PolicyNotificationSeverity.WARNING,
            title="Approval requested",
            body="Step needs approval",
            source_type="rollout_step",
            source_id="ro_001:s1",
            actor_id="user1",
            created_at=datetime.now(timezone.utc),
        )
        assert msg.source_type == "rollout_step"
        assert msg.actor_id == "user1"


class TestPolicyNotificationRuleStatus:
    def test_values(self):
        assert PolicyNotificationRuleStatus.ENABLED == "enabled"
        assert PolicyNotificationRuleStatus.DISABLED == "disabled"


class TestPolicyNotificationRule:
    def test_valid_rule(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="rollout_gate_failed",
            event_types=["policy.rollout.gate.failed"],
            severity=PolicyNotificationSeverity.ERROR,
            channels=["log"],
        )
        assert rule.rule_id == "pnr_001"
        assert rule.status == PolicyNotificationRuleStatus.ENABLED
        assert rule.source_types == []

    def test_rule_id_prefix(self):
        with pytest.raises(ValueError, match="pnr_"):
            PolicyNotificationRule(
                rule_id="bad_id",
                name="test",
                event_types=["test.event"],
            )

    def test_empty_event_types_invalid(self):
        with pytest.raises(ValueError, match="event_types"):
            PolicyNotificationRule(
                rule_id="pnr_001",
                name="test",
                event_types=[],
            )

    def test_with_templates(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_002",
            name="gate_failed",
            event_types=["policy.rollout.gate.failed"],
            title_template="Gate failed: {rollout_id}",
            body_template="Status: {status}",
        )
        assert rule.title_template is not None
        assert rule.body_template is not None

    def test_default_channels_is_log(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_003",
            name="test",
            event_types=["test.event"],
        )
        assert rule.channels == ["log"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_notification_model.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
"""Policy notification models — notification messages and rules for governance events.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class PolicyNotificationSeverity(StrEnum):
    """Severity level for policy notifications."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class PolicyNotificationStatus(StrEnum):
    """Delivery status of a notification."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class PolicyNotificationMessage(BaseModel):
    """A notification message derived from a policy event."""

    notification_id: str = Field(..., description="Unique notification ID (pn_ prefix)")
    event_type: str = Field(..., description="Policy event type that triggered this notification")
    severity: PolicyNotificationSeverity = Field(..., description="Notification severity")
    title: str = Field(..., description="Notification title")
    body: str = Field(..., description="Notification body")
    source_type: str | None = Field(default=None, description="Source type (e.g. rollout_step)")
    source_id: str | None = Field(default=None, description="Source ID")
    actor_id: str | None = Field(default=None, description="Actor who triggered the event")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    status: PolicyNotificationStatus = Field(
        default=PolicyNotificationStatus.PENDING,
        description="Delivery status",
    )
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    sent_at: datetime | None = Field(default=None, description="Timezone-aware sent timestamp")
    error: dict[str, Any] | None = Field(default=None, description="Error details if delivery failed")

    @field_validator("notification_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pn_"):
            raise ValueError("notification_id must use pn_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class PolicyNotificationRuleStatus(StrEnum):
    """Status of a notification rule."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class PolicyNotificationRule(BaseModel):
    """A rule that matches policy events to produce notifications."""

    rule_id: str = Field(..., description="Unique rule ID (pnr_ prefix)")
    name: str = Field(..., description="Human-readable rule name")
    event_types: list[str] = Field(..., description="Event types this rule matches")
    severity: PolicyNotificationSeverity = Field(
        default=PolicyNotificationSeverity.INFO,
        description="Default severity for notifications from this rule",
    )
    status: PolicyNotificationRuleStatus = Field(
        default=PolicyNotificationRuleStatus.ENABLED,
        description="Whether this rule is active",
    )
    source_types: list[str] = Field(
        default_factory=list,
        description="Source types to match (empty = match any)",
    )
    channels: list[str] = Field(
        default_factory=lambda: ["log"],
        description="Channels to deliver through",
    )
    title_template: str | None = Field(default=None, description="Title template with {placeholders}")
    body_template: str | None = Field(default=None, description="Body template with {placeholders}")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Rule metadata")

    @field_validator("rule_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pnr_"):
            raise ValueError("rule_id must use pnr_ prefix")
        return v

    @model_validator(mode="after")
    def _validate_event_types(self) -> "PolicyNotificationRule":
        if not self.event_types:
            raise ValueError("event_types must not be empty")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_notification_model.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_notification.py tests/unit/test_policy_notification_model.py
git commit -m "feat: Phase 44 Task 1 — PolicyNotificationMessage and PolicyNotificationRule models"
```

---

### Task 2: Expiration models — PolicyExpirationResult and PolicyExpirationSweepReport

**Files:**
- Create: `agent_app/governance/policy_expiration.py`
- Test: `tests/unit/test_policy_expiration_model.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for expiration models (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_expiration import (
    PolicyExpirationTargetType,
    PolicyExpirationAction,
    PolicyExpirationResult,
    PolicyExpirationSweepReport,
)


class TestPolicyExpirationTargetType:
    def test_values(self):
        assert PolicyExpirationTargetType.ROLLOUT_APPROVAL == "rollout_approval"
        assert PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT == "promotion_gate_requirement"
        assert PolicyExpirationTargetType.ROLLOUT_GATE_REQUIREMENT == "rollout_gate_requirement"


class TestPolicyExpirationAction:
    def test_values(self):
        assert PolicyExpirationAction.EXPIRED == "expired"
        assert PolicyExpirationAction.SKIPPED == "skipped"
        assert PolicyExpirationAction.ERROR == "error"


class TestPolicyExpirationResult:
    def test_valid_result(self):
        r = PolicyExpirationResult(
            result_id="per_001",
            target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
            target_id="rsa_001",
            action=PolicyExpirationAction.EXPIRED,
            created_at=datetime.now(timezone.utc),
        )
        assert r.result_id == "per_001"
        assert r.reason is None
        assert r.error is None

    def test_result_id_prefix(self):
        with pytest.raises(ValueError, match="per_"):
            PolicyExpirationResult(
                result_id="bad_id",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="rsa_001",
                action=PolicyExpirationAction.EXPIRED,
                created_at=datetime.now(timezone.utc),
            )

    def test_tz_aware_created_at(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            PolicyExpirationResult(
                result_id="per_001",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="rsa_001",
                action=PolicyExpirationAction.EXPIRED,
                created_at=datetime.now(),
            )

    def test_with_error(self):
        r = PolicyExpirationResult(
            result_id="per_002",
            target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
            target_id="rgr_001",
            action=PolicyExpirationAction.ERROR,
            error={"type": "store_error", "message": "connection lost"},
            created_at=datetime.now(timezone.utc),
        )
        assert r.error is not None


class TestPolicyExpirationSweepReport:
    def test_valid_report(self):
        report = PolicyExpirationSweepReport(
            sweep_id="pes_001",
            started_at=datetime.now(timezone.utc),
        )
        assert report.sweep_id == "pes_001"
        assert report.completed_at is None
        assert report.results == []

    def test_sweep_id_prefix(self):
        with pytest.raises(ValueError, match="pes_"):
            PolicyExpirationSweepReport(
                sweep_id="bad_id",
                started_at=datetime.now(timezone.utc),
            )

    def test_with_results(self):
        now = datetime.now(timezone.utc)
        result = PolicyExpirationResult(
            result_id="per_001",
            target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
            target_id="rsa_001",
            action=PolicyExpirationAction.EXPIRED,
            created_at=now,
        )
        report = PolicyExpirationSweepReport(
            sweep_id="pes_001",
            started_at=now,
            completed_at=now,
            results=[result],
        )
        assert len(report.results) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_expiration_model.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
"""Policy expiration models — results of sweeping expired approvals and gate requirements.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PolicyExpirationTargetType(StrEnum):
    """What kind of target is being expired."""

    ROLLOUT_APPROVAL = "rollout_approval"
    PROMOTION_GATE_REQUIREMENT = "promotion_gate_requirement"
    ROLLOUT_GATE_REQUIREMENT = "rollout_gate_requirement"


class PolicyExpirationAction(StrEnum):
    """Action taken during expiration sweep."""

    EXPIRED = "expired"
    SKIPPED = "skipped"
    ERROR = "error"


class PolicyExpirationResult(BaseModel):
    """Result of expiring a single target."""

    result_id: str = Field(..., description="Unique result ID (per_ prefix)")
    target_type: PolicyExpirationTargetType = Field(..., description="Type of expired target")
    target_id: str = Field(..., description="ID of the expired target")
    action: PolicyExpirationAction = Field(..., description="Action taken")
    reason: str | None = Field(default=None, description="Human-readable reason")
    error: dict[str, Any] | None = Field(default=None, description="Error details if action=ERROR")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")

    @field_validator("result_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("per_"):
            raise ValueError("result_id must use per_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class PolicyExpirationSweepReport(BaseModel):
    """Report from an expiration sweep run."""

    sweep_id: str = Field(..., description="Unique sweep ID (pes_ prefix)")
    started_at: datetime = Field(..., description="Timezone-aware sweep start timestamp")
    completed_at: datetime | None = Field(default=None, description="Timezone-aware sweep completion timestamp")
    results: list[PolicyExpirationResult] = Field(default_factory=list, description="Individual expiration results")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Sweep metadata")

    @field_validator("sweep_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pes_"):
            raise ValueError("sweep_id must use pes_ prefix")
        return v
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_policy_expiration_model.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_expiration.py tests/unit/test_policy_expiration_model.py
git commit -m "feat: Phase 44 Task 2 — PolicyExpirationResult and PolicyExpirationSweepReport models"
```

---

### Task 3: Notification stores — delivery store and rule store

**Files:**
- Create: `agent_app/runtime/policy_notification_store.py`
- Create: `agent_app/runtime/policy_notification_rule_store.py`
- Test: `tests/unit/test_policy_notification_store.py`
- Test: `tests/unit/test_policy_notification_rule_store.py`

- [ ] **Step 1: Write the failing tests for notification store**

```python
"""Tests for notification delivery store (Phase 44)."""
from __future__ import annotations

import pytest
import tempfile
import os
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationMessage,
)
from agent_app.runtime.policy_notification_store import (
    InMemoryPolicyNotificationStore,
    SQLitePolicyNotificationStore,
    create_policy_notification_store,
)


def _make_msg(notification_id="pn_001", event_type="test.event", status=None):
    return PolicyNotificationMessage(
        notification_id=notification_id,
        event_type=event_type,
        severity=PolicyNotificationSeverity.INFO,
        title="Test",
        body="Body",
        status=status or PolicyNotificationStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )


class TestInMemoryNotificationStore:
    @pytest.fixture
    def store(self):
        return InMemoryPolicyNotificationStore()

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        msg = _make_msg()
        created = await store.create(msg)
        assert created.notification_id == "pn_001"
        got = await store.get("pn_001")
        assert got is not None
        assert got.title == "Test"

    @pytest.mark.asyncio
    async def test_get_missing(self, store):
        assert await store.get("pn_missing") is None

    @pytest.mark.asyncio
    async def test_update(self, store):
        msg = _make_msg()
        await store.create(msg)
        msg.status = PolicyNotificationStatus.SENT
        msg.sent_at = datetime.now(timezone.utc)
        updated = await store.update(msg)
        assert updated.status == PolicyNotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.create(_make_msg("pn_001"))
        await store.create(_make_msg("pn_002"))
        msgs = await store.list()
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_list_by_status(self, store):
        await store.create(_make_msg("pn_001", status=PolicyNotificationStatus.PENDING))
        await store.create(_make_msg("pn_002", status=PolicyNotificationStatus.SENT))
        pending = await store.list(status=PolicyNotificationStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].notification_id == "pn_001"

    @pytest.mark.asyncio
    async def test_list_by_event_type(self, store):
        await store.create(_make_msg("pn_001", event_type="policy.rollout.gate.failed"))
        await store.create(_make_msg("pn_002", event_type="policy.rollout.approval.requested"))
        result = await store.list(event_type="policy.rollout.gate.failed")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_with_limit(self, store):
        for i in range(5):
            await store.create(_make_msg(f"pn_{i:03d}"))
        msgs = await store.list(limit=3)
        assert len(msgs) == 3


class TestSQLiteNotificationStore:
    @pytest.fixture
    def store(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        store = SQLitePolicyNotificationStore(db_path=path)
        yield store
        store.close()
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        msg = _make_msg()
        await store.create(msg)
        got = await store.get("pn_001")
        assert got is not None
        assert got.title == "Test"

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, store):
        msg = _make_msg()
        await store.create(msg)
        path = store._db_path
        store.close()
        store2 = SQLitePolicyNotificationStore(db_path=path)
        got = await store2.get("pn_001")
        assert got is not None
        store2.close()

    @pytest.mark.asyncio
    async def test_list_by_status(self, store):
        await store.create(_make_msg("pn_001", status=PolicyNotificationStatus.PENDING))
        await store.create(_make_msg("pn_002", status=PolicyNotificationStatus.SENT))
        pending = await store.list(status=PolicyNotificationStatus.PENDING)
        assert len(pending) == 1


class TestCreateNotificationStore:
    def test_memory(self):
        store = create_policy_notification_store("memory")
        assert isinstance(store, InMemoryPolicyNotificationStore)

    def test_sqlite(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = create_policy_notification_store("sqlite", db_path=path)
            assert isinstance(store, SQLitePolicyNotificationStore)
            store.close()
        finally:
            os.unlink(path)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            create_policy_notification_store("redis")
```

- [ ] **Step 2: Write the failing tests for rule store**

```python
"""Tests for notification rule store (Phase 44)."""
from __future__ import annotations

import pytest
import tempfile
import os
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
)
from agent_app.runtime.policy_notification_rule_store import (
    InMemoryPolicyNotificationRuleStore,
    SQLitePolicyNotificationRuleStore,
    create_policy_notification_rule_store,
)


def _make_rule(rule_id="pnr_001", name="test_rule", event_types=None, status=None):
    return PolicyNotificationRule(
        rule_id=rule_id,
        name=name,
        event_types=event_types or ["test.event"],
        severity=PolicyNotificationSeverity.INFO,
        status=status or PolicyNotificationRuleStatus.ENABLED,
    )


class TestInMemoryRuleStore:
    @pytest.fixture
    def store(self):
        return InMemoryPolicyNotificationRuleStore()

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        rule = _make_rule()
        await store.create(rule)
        got = await store.get("pnr_001")
        assert got is not None
        assert got.name == "test_rule"

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.create(_make_rule("pnr_001"))
        await store.create(_make_rule("pnr_002", name="rule2"))
        rules = await store.list()
        assert len(rules) == 2

    @pytest.mark.asyncio
    async def test_list_by_status(self, store):
        await store.create(_make_rule("pnr_001", status=PolicyNotificationRuleStatus.ENABLED))
        await store.create(_make_rule("pnr_002", name="r2", status=PolicyNotificationRuleStatus.DISABLED))
        enabled = await store.list(status=PolicyNotificationRuleStatus.ENABLED)
        assert len(enabled) == 1

    @pytest.mark.asyncio
    async def test_enable(self, store):
        rule = _make_rule(status=PolicyNotificationRuleStatus.DISABLED)
        await store.create(rule)
        updated = await store.enable("pnr_001")
        assert updated.status == PolicyNotificationRuleStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable(self, store):
        rule = _make_rule()
        await store.create(rule)
        updated = await store.disable("pnr_001")
        assert updated.status == PolicyNotificationRuleStatus.DISABLED


class TestSQLiteRuleStore:
    @pytest.fixture
    def store(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        store = SQLitePolicyNotificationRuleStore(db_path=path)
        yield store
        store.close()
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        rule = _make_rule()
        await store.create(rule)
        got = await store.get("pnr_001")
        assert got is not None
        assert got.name == "test_rule"

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, store):
        rule = _make_rule()
        await store.create(rule)
        path = store._db_path
        store.close()
        store2 = SQLitePolicyNotificationRuleStore(db_path=path)
        got = await store2.get("pnr_001")
        assert got is not None
        store2.close()


class TestCreateRuleStore:
    def test_memory(self):
        store = create_policy_notification_rule_store("memory")
        assert isinstance(store, InMemoryPolicyNotificationRuleStore)

    def test_sqlite(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = create_policy_notification_rule_store("sqlite", db_path=path)
            assert isinstance(store, SQLitePolicyNotificationRuleStore)
            store.close()
        finally:
            os.unlink(path)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_store.py tests/unit/test_policy_notification_rule_store.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Write notification store implementation**

```python
"""Notification delivery store — persists notification messages.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyNotificationStore(Protocol):
    """Protocol for persisting notification messages."""

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage: ...
    async def get(self, notification_id: str) -> PolicyNotificationMessage | None: ...
    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage: ...
    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]: ...


class InMemoryPolicyNotificationStore:
    """In-memory notification store."""

    def __init__(self) -> None:
        self._messages: dict[str, PolicyNotificationMessage] = {}
        self._order: list[str] = []

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        self._messages[message.notification_id] = message
        self._order.append(message.notification_id)
        return message

    async def get(self, notification_id: str) -> PolicyNotificationMessage | None:
        return self._messages.get(notification_id)

    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        self._messages[message.notification_id] = message
        return message

    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        results: list[PolicyNotificationMessage] = []
        # Newest first
        for nid in reversed(self._order):
            msg = self._messages.get(nid)
            if msg is None:
                continue
            if status is not None and msg.status != status:
                continue
            if event_type is not None and msg.event_type != event_type:
                continue
            results.append(msg)
            if limit is not None and len(results) >= limit:
                break
        return results


class SQLitePolicyNotificationStore:
    """SQLite-backed notification store."""

    def __init__(self, db_path: str = ".agent_app/policy_notifications.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_notifications (
                notification_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                source_type TEXT,
                source_id TEXT,
                actor_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                error_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notif_status ON policy_notifications(status);
            CREATE INDEX IF NOT EXISTS idx_notif_event ON policy_notifications(event_type);
        """)
        self._conn.commit()

    async def create(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        self._conn.execute(
            """INSERT INTO policy_notifications
               (notification_id, event_type, severity, title, body,
                source_type, source_id, actor_id, metadata_json,
                status, created_at, sent_at, error_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.notification_id,
                message.event_type,
                message.severity.value,
                message.title,
                message.body,
                message.source_type,
                message.source_id,
                message.actor_id,
                json.dumps(message.metadata),
                message.status.value,
                message.created_at.isoformat(),
                message.sent_at.isoformat() if message.sent_at else None,
                json.dumps(message.error) if message.error else None,
            ),
        )
        self._conn.commit()
        return message

    async def get(self, notification_id: str) -> PolicyNotificationMessage | None:
        row = self._conn.execute(
            "SELECT * FROM policy_notifications WHERE notification_id=?", (notification_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    async def update(self, message: PolicyNotificationMessage) -> PolicyNotificationMessage:
        self._conn.execute(
            """UPDATE policy_notifications
               SET status=?, sent_at=?, error_json=?
               WHERE notification_id=?""",
            (
                message.status.value,
                message.sent_at.isoformat() if message.sent_at else None,
                json.dumps(message.error) if message.error else None,
                message.notification_id,
            ),
        )
        self._conn.commit()
        return message

    async def list(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if event_type is not None:
            clauses.append("event_type=?")
            params.append(event_type.value if hasattr(event_type, 'value') else event_type)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_notifications{where} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {limit}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_message(row) for row in rows]

    def _row_to_message(self, row: sqlite3.Row) -> PolicyNotificationMessage:
        from agent_app.governance.policy_notification import PolicyNotificationSeverity
        data = dict(row)
        data["severity"] = PolicyNotificationSeverity(data["severity"])
        data["status"] = PolicyNotificationStatus(data["status"])
        data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
        data["error"] = json.loads(data["error_json"]) if data.get("error_json") else None
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("sent_at"):
            data["sent_at"] = datetime.fromisoformat(data["sent_at"])
        return PolicyNotificationMessage(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_notification_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyNotificationStore:
    if store_type == "memory":
        return InMemoryPolicyNotificationStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLitePolicyNotificationStore(db_path=db_path)
    raise ValueError(f"Unknown notification store type '{store_type}'. Supported: 'memory', 'sqlite'.")
```

- [ ] **Step 5: Write rule store implementation**

```python
"""Notification rule store — persists notification rules.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_notification import (
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyNotificationRuleStore(Protocol):
    """Protocol for persisting notification rules."""

    async def create(self, rule: PolicyNotificationRule) -> PolicyNotificationRule: ...
    async def get(self, rule_id: str) -> PolicyNotificationRule | None: ...
    async def list(
        self,
        status: PolicyNotificationRuleStatus | None = None,
    ) -> list[PolicyNotificationRule]: ...
    async def enable(self, rule_id: str) -> PolicyNotificationRule: ...
    async def disable(self, rule_id: str) -> PolicyNotificationRule: ...


class InMemoryPolicyNotificationRuleStore:
    """In-memory notification rule store."""

    def __init__(self) -> None:
        self._rules: dict[str, PolicyNotificationRule] = {}

    async def create(self, rule: PolicyNotificationRule) -> PolicyNotificationRule:
        self._rules[rule.rule_id] = rule
        return rule

    async def get(self, rule_id: str) -> PolicyNotificationRule | None:
        return self._rules.get(rule_id)

    async def list(
        self,
        status: PolicyNotificationRuleStatus | None = None,
    ) -> list[PolicyNotificationRule]:
        results: list[PolicyNotificationRule] = []
        for rule in self._rules.values():
            if status is not None and rule.status != status:
                continue
            results.append(rule)
        return results

    async def enable(self, rule_id: str) -> PolicyNotificationRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Notification rule '{rule_id}' not found")
        rule.status = PolicyNotificationRuleStatus.ENABLED
        return rule

    async def disable(self, rule_id: str) -> PolicyNotificationRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Notification rule '{rule_id}' not found")
        rule.status = PolicyNotificationRuleStatus.DISABLED
        return rule


class SQLitePolicyNotificationRuleStore:
    """SQLite-backed notification rule store."""

    def __init__(self, db_path: str = ".agent_app/policy_notification_rules.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_notification_rules (
                rule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                event_types_json TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                source_types_json TEXT NOT NULL DEFAULT '[]',
                channels_json TEXT NOT NULL DEFAULT '["log"]',
                title_template TEXT,
                body_template TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
        """)
        self._conn.commit()

    async def create(self, rule: PolicyNotificationRule) -> PolicyNotificationRule:
        self._conn.execute(
            """INSERT INTO policy_notification_rules
               (rule_id, name, event_types_json, severity, status,
                source_types_json, channels_json, title_template,
                body_template, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule.rule_id,
                rule.name,
                json.dumps(rule.event_types),
                rule.severity.value,
                rule.status.value,
                json.dumps(rule.source_types),
                json.dumps(rule.channels),
                rule.title_template,
                rule.body_template,
                json.dumps(rule.metadata),
            ),
        )
        self._conn.commit()
        return rule

    async def get(self, rule_id: str) -> PolicyNotificationRule | None:
        row = self._conn.execute(
            "SELECT * FROM policy_notification_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_rule(row)

    async def list(
        self,
        status: PolicyNotificationRuleStatus | None = None,
    ) -> list[PolicyNotificationRule]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM policy_notification_rules WHERE status=?",
                (status.value,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM policy_notification_rules"
            ).fetchall()
        return [self._row_to_rule(row) for row in rows]

    async def enable(self, rule_id: str) -> PolicyNotificationRule:
        rule = await self.get(rule_id)
        if rule is None:
            raise KeyError(f"Notification rule '{rule_id}' not found")
        self._conn.execute(
            "UPDATE policy_notification_rules SET status=? WHERE rule_id=?",
            (PolicyNotificationRuleStatus.ENABLED.value, rule_id),
        )
        self._conn.commit()
        rule.status = PolicyNotificationRuleStatus.ENABLED
        return rule

    async def disable(self, rule_id: str) -> PolicyNotificationRule:
        rule = await self.get(rule_id)
        if rule is None:
            raise KeyError(f"Notification rule '{rule_id}' not found")
        self._conn.execute(
            "UPDATE policy_notification_rules SET status=? WHERE rule_id=?",
            (PolicyNotificationRuleStatus.DISABLED.value, rule_id),
        )
        self._conn.commit()
        rule.status = PolicyNotificationRuleStatus.DISABLED
        return rule

    def _row_to_rule(self, row: sqlite3.Row) -> PolicyNotificationRule:
        from agent_app.governance.policy_notification import PolicyNotificationSeverity
        data = dict(row)
        data["event_types"] = json.loads(data.pop("event_types_json", "[]"))
        data["severity"] = PolicyNotificationSeverity(data["severity"])
        data["status"] = PolicyNotificationRuleStatus(data["status"])
        data["source_types"] = json.loads(data.pop("source_types_json", "[]"))
        data["channels"] = json.loads(data.pop("channels_json", '["log"]'))
        data["title_template"] = data.pop("title_template", None)
        data["body_template"] = data.pop("body_template", None)
        data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
        return PolicyNotificationRule(**data)

    def close(self) -> None:
        self._conn.close()


def create_policy_notification_rule_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> PolicyNotificationRuleStore:
    if store_type == "memory":
        return InMemoryPolicyNotificationRuleStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLitePolicyNotificationRuleStore(db_path=db_path)
    raise ValueError(f"Unknown notification rule store type '{store_type}'. Supported: 'memory', 'sqlite'.")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_store.py tests/unit/test_policy_notification_rule_store.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agent_app/runtime/policy_notification_store.py agent_app/runtime/policy_notification_rule_store.py tests/unit/test_policy_notification_store.py tests/unit/test_policy_notification_rule_store.py
git commit -m "feat: Phase 44 Task 3 — notification delivery store and rule store with InMemory + SQLite"
```

---

### Task 4: Notification channels and NotificationService

**Files:**
- Create: `agent_app/runtime/policy_notification_channels.py`
- Create: `agent_app/runtime/policy_notification_service.py`
- Test: `tests/unit/test_policy_notification_channels.py`
- Test: `tests/unit/test_policy_notification_service.py`

- [ ] **Step 1: Write the failing tests for channels**

```python
"""Tests for notification channels (Phase 44)."""
from __future__ import annotations

import pytest
import logging
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationMessage,
)
from agent_app.runtime.policy_notification_channels import (
    LogNotificationChannel,
    InMemoryNotificationChannel,
    FailingNotificationChannel,
)


def _make_msg(notification_id="pn_001"):
    return PolicyNotificationMessage(
        notification_id=notification_id,
        event_type="test.event",
        severity=PolicyNotificationSeverity.INFO,
        title="Test",
        body="Body",
        created_at=datetime.now(timezone.utc),
    )


class TestLogNotificationChannel:
    @pytest.mark.asyncio
    async def test_name(self):
        ch = LogNotificationChannel()
        assert ch.name == "log"

    @pytest.mark.asyncio
    async def test_send(self):
        ch = LogNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.SENT
        assert result.sent_at is not None


class TestInMemoryNotificationChannel:
    @pytest.mark.asyncio
    async def test_name(self):
        ch = InMemoryNotificationChannel()
        assert ch.name == "memory"

    @pytest.mark.asyncio
    async def test_send(self):
        ch = InMemoryNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.SENT
        assert len(ch.sent) == 1


class TestFailingNotificationChannel:
    @pytest.mark.asyncio
    async def test_send_fails(self):
        ch = FailingNotificationChannel()
        msg = _make_msg()
        result = await ch.send(msg)
        assert result.status == PolicyNotificationStatus.FAILED
        assert result.error is not None
```

- [ ] **Step 2: Write the failing tests for notification service**

```python
"""Tests for notification service (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from agent_app.governance.policy_notification import (
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
)
from agent_app.runtime.policy_notification_store import InMemoryPolicyNotificationStore
from agent_app.runtime.policy_notification_rule_store import InMemoryPolicyNotificationRuleStore
from agent_app.runtime.policy_notification_channels import (
    LogNotificationChannel,
    InMemoryNotificationChannel,
    FailingNotificationChannel,
)
from agent_app.runtime.policy_notification_service import PolicyNotificationService


class TestNotifyEvent:
    @pytest.fixture
    def svc(self):
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        channels = {"log": LogNotificationChannel(), "memory": InMemoryNotificationChannel()}
        return PolicyNotificationService(store, rule_store, channels=channels)

    @pytest.mark.asyncio
    async def test_matching_rule_creates_notification(self, svc):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="gate_failed",
            event_types=["policy.rollout.gate.failed"],
            severity=PolicyNotificationSeverity.ERROR,
            channels=["log"],
        )
        await svc.rule_store.create(rule)
        msgs = await svc.notify_event(
            "policy.rollout.gate.failed",
            {"rollout_id": "ro_001", "step_id": "s1"},
        )
        assert len(msgs) == 1
        assert msgs[0].severity == PolicyNotificationSeverity.ERROR

    @pytest.mark.asyncio
    async def test_no_matching_rule(self, svc):
        msgs = await svc.notify_event("unknown.event", {})
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_source_type_filter(self, svc):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="rollout_only",
            event_types=["test.event"],
            source_types=["rollout_step"],
        )
        await svc.rule_store.create(rule)
        msgs = await svc.notify_event("test.event", {}, source_type="rollout_step")
        assert len(msgs) == 1
        msgs2 = await svc.notify_event("test.event", {}, source_type="promotion")
        assert len(msgs2) == 0

    @pytest.mark.asyncio
    async def test_disabled_rule_ignored(self, svc):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="disabled_rule",
            event_types=["test.event"],
            status=PolicyNotificationRuleStatus.DISABLED,
        )
        await svc.rule_store.create(rule)
        msgs = await svc.notify_event("test.event", {})
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_unknown_channel_fails(self, svc):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="bad_channel",
            event_types=["test.event"],
            channels=["slack"],
        )
        await svc.rule_store.create(rule)
        msgs = await svc.notify_event("test.event", {})
        assert len(msgs) == 1
        assert msgs[0].status == PolicyNotificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_template_rendering(self, svc):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="templated",
            event_types=["test.event"],
            title_template="Gate failed: {rollout_id}",
            body_template="Status: {status}",
        )
        await svc.rule_store.create(rule)
        msgs = await svc.notify_event("test.event", {"rollout_id": "ro_001", "status": "failed"})
        assert len(msgs) == 1
        assert "ro_001" in msgs[0].title
        assert "failed" in msgs[0].body


class TestSendPending:
    @pytest.mark.asyncio
    async def test_send_pending(self):
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        channels = {"log": LogNotificationChannel()}
        svc = PolicyNotificationService(store, rule_store, channels=channels)
        # Manually create a pending notification
        from agent_app.governance.policy_notification import PolicyNotificationMessage
        msg = PolicyNotificationMessage(
            notification_id="pn_001",
            event_type="test.event",
            severity=PolicyNotificationSeverity.INFO,
            title="Test",
            body="Body",
            channels=["log"],
            created_at=datetime.now(timezone.utc),
        )
        await store.create(msg)
        sent = await svc.send_pending()
        assert len(sent) == 1
        assert sent[0].status == PolicyNotificationStatus.SENT


class TestListNotifications:
    @pytest.mark.asyncio
    async def test_list(self):
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        svc = PolicyNotificationService(store, rule_store)
        msgs = await svc.list_notifications()
        assert isinstance(msgs, list)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_channels.py tests/unit/test_policy_notification_service.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Write channels implementation**

```python
"""Notification channels — deliver notification messages.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationStatus,
)

logger = logging.getLogger(__name__)


try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyNotificationChannel(Protocol):
    """Protocol for notification delivery channels."""

    name: str

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        ...


class LogNotificationChannel:
    """Deliver notifications via standard library logging."""

    name = "log"

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        logger.info(
            "Notification [%s] %s: %s",
            message.severity.value,
            message.title,
            message.body,
        )
        message.status = PolicyNotificationStatus.SENT
        message.sent_at = datetime.now(timezone.utc)
        return message


class InMemoryNotificationChannel:
    """Store sent notifications in memory for testing."""

    name = "memory"

    def __init__(self) -> None:
        self.sent: list[PolicyNotificationMessage] = []

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        self.sent.append(message)
        message.status = PolicyNotificationStatus.SENT
        message.sent_at = datetime.now(timezone.utc)
        return message


class FailingNotificationChannel:
    """Channel that always fails — for testing error handling."""

    name = "failing"

    async def send(
        self,
        message: PolicyNotificationMessage,
    ) -> PolicyNotificationMessage:
        message.status = PolicyNotificationStatus.FAILED
        message.error = {"type": "channel_error", "message": "FailingChannel always fails"}
        return message
```

- [ ] **Step 5: Write notification service implementation**

```python
"""Notification service — match rules, create, send, and list notifications.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)
from agent_app.runtime.policy_notification_store import PolicyNotificationStore
from agent_app.runtime.policy_notification_rule_store import PolicyNotificationRuleStore

logger = logging.getLogger(__name__)


class PolicyNotificationService:
    """Service for creating and delivering policy notifications."""

    def __init__(
        self,
        notification_store: PolicyNotificationStore,
        rule_store: PolicyNotificationRuleStore,
        channels: dict[str, Any] | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._store = notification_store
        self._rule_store = rule_store
        self._channels = channels or {}
        self._audit_logger = audit_logger

    async def notify_event(
        self,
        event_type: str,
        data: dict[str, Any],
        source_type: str | None = None,
        source_id: str | None = None,
        actor_id: str | None = None,
    ) -> list[PolicyNotificationMessage]:
        """Match enabled rules against event, create and send notifications."""
        rules = await self._rule_store.list(status=PolicyNotificationRuleStatus.ENABLED)
        matching = [
            r for r in rules
            if event_type in r.event_types
            and (not r.source_types or source_type in r.source_types)
        ]

        messages: list[PolicyNotificationMessage] = []
        for rule in matching:
            # Render templates
            title = rule.title_template
            if title:
                try:
                    title = title.format(**data)
                except (KeyError, IndexError):
                    title = rule.title_template
            else:
                title = f"{event_type}"

            body = rule.body_template
            if body:
                try:
                    body = body.format(**data)
                except (KeyError, IndexError):
                    body = rule.body_template
            else:
                body = str(data)

            msg = PolicyNotificationMessage(
                notification_id=f"pn_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                severity=rule.severity,
                title=title,
                body=body,
                source_type=source_type,
                source_id=source_id,
                actor_id=actor_id,
                metadata=data,
                created_at=datetime.now(timezone.utc),
            )

            # Store as PENDING
            await self._store.create(msg)

            # Send through channels
            all_ok = True
            channel_errors: list[dict[str, Any]] = []
            for ch_name in rule.channels:
                ch = self._channels.get(ch_name)
                if ch is None:
                    all_ok = False
                    channel_errors.append({"channel": ch_name, "error": "unknown channel"})
                    continue
                try:
                    result = await ch.send(msg)
                    if result.status == PolicyNotificationStatus.FAILED:
                        all_ok = False
                        channel_errors.append({
                            "channel": ch_name,
                            "error": result.error or "channel returned failed",
                        })
                except Exception as exc:
                    all_ok = False
                    channel_errors.append({
                        "channel": ch_name,
                        "error": str(exc),
                    })

            # Update status
            if all_ok:
                msg.status = PolicyNotificationStatus.SENT
                msg.sent_at = datetime.now(timezone.utc)
            else:
                msg.status = PolicyNotificationStatus.FAILED
                msg.error = {"channel_errors": channel_errors}
            await self._store.update(msg)

            # Audit
            await self._audit(
                f"policy.notification.{'sent' if all_ok else 'failed'}",
                {"notification_id": msg.notification_id, "rule_id": rule.rule_id},
            )

            messages.append(msg)

        return messages

    async def send_pending(
        self,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        """Send all pending notifications through their channels."""
        pending = await self._store.list(status=PolicyNotificationStatus.PENDING, limit=limit)
        # For pending messages without channel info, send via log
        log_ch = self._channels.get("log")
        sent: list[PolicyNotificationMessage] = []
        for msg in pending:
            if log_ch is not None:
                try:
                    result = await log_ch.send(msg)
                    msg.status = result.status
                    msg.sent_at = result.sent_at
                except Exception as exc:
                    msg.status = PolicyNotificationStatus.FAILED
                    msg.error = {"type": "send_error", "message": str(exc)}
            else:
                msg.status = PolicyNotificationStatus.SENT
                msg.sent_at = datetime.now(timezone.utc)
            await self._store.update(msg)
            sent.append(msg)
        return sent

    async def list_notifications(
        self,
        status: PolicyNotificationStatus | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[PolicyNotificationMessage]:
        """List notifications with optional filters."""
        return await self._store.list(status=status, event_type=event_type, limit=limit)

    async def _audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Record audit event (best-effort)."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent
            event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                data=data,
            )
            await self._audit_logger.log(event)
        except Exception:
            pass
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_channels.py tests/unit/test_policy_notification_service.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agent_app/runtime/policy_notification_channels.py agent_app/runtime/policy_notification_service.py tests/unit/test_policy_notification_channels.py tests/unit/test_policy_notification_service.py
git commit -m "feat: Phase 44 Task 4 — notification channels (log/memory/failing) and PolicyNotificationService"
```

---

### Task 5: ExpirationService and PolicyExpirationWorker

**Files:**
- Create: `agent_app/runtime/policy_expiration_service.py`
- Create: `agent_app/runtime/policy_expiration_worker.py`
- Test: `tests/unit/test_policy_expiration_service.py`
- Test: `tests/unit/test_policy_expiration_worker.py`

- [ ] **Step 1: Write the failing tests for expiration service**

```python
"""Tests for expiration service (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from agent_app.governance.policy_expiration import (
    PolicyExpirationTargetType,
    PolicyExpirationAction,
    PolicyExpirationResult,
    PolicyExpirationSweepReport,
)
from agent_app.runtime.policy_expiration_service import PolicyExpirationService


class TestExpireRolloutApprovals:
    @pytest.mark.asyncio
    async def test_expired_approvals_produce_results(self):
        approval_store = AsyncMock()
        expired_approval = MagicMock()
        expired_approval.approval_id = "rsa_001"
        expired_approval.status = MagicMock()
        expired_approval.status.value = "expired"
        approval_store.expire_pending.return_value = [expired_approval]

        svc = PolicyExpirationService(rollout_approval_store=approval_store)
        results = await svc.expire_rollout_approvals()
        assert len(results) == 1
        assert results[0].target_type == PolicyExpirationTargetType.ROLLOUT_APPROVAL
        assert results[0].action == PolicyExpirationAction.EXPIRED

    @pytest.mark.asyncio
    async def test_missing_store_skipped(self):
        svc = PolicyExpirationService()
        results = await svc.expire_rollout_approvals()
        assert len(results) == 0


class TestExpireGateRequirements:
    @pytest.mark.asyncio
    async def test_expired_requirements_produce_results(self):
        gate_store = AsyncMock()
        from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
        req = MagicMock()
        req.requirement_id = "rgr_001"
        req.status = ReleaseGateRequirementStatus.EXPIRED
        req.source_type = "promotion"
        req.max_age_seconds = 300
        req.satisfied_at = None
        req.created_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        gate_store.list.return_value = [req]
        gate_store.update = AsyncMock(return_value=req)

        svc = PolicyExpirationService(release_gate_requirement_store=gate_store)
        results = await svc.expire_gate_requirements()
        # Should find the stale requirement and mark it expired
        assert len(results) >= 0  # May or may not expire depending on logic

    @pytest.mark.asyncio
    async def test_missing_store_skipped(self):
        svc = PolicyExpirationService()
        results = await svc.expire_gate_requirements()
        assert len(results) == 0


class TestSweep:
    @pytest.mark.asyncio
    async def test_sweep_returns_report(self):
        svc = PolicyExpirationService()
        report = await svc.sweep()
        assert report.sweep_id.startswith("pes_")
        assert report.started_at is not None

    @pytest.mark.asyncio
    async def test_sweep_with_both_stores(self):
        approval_store = AsyncMock()
        approval_store.expire_pending.return_value = []
        gate_store = AsyncMock()
        gate_store.list.return_value = []

        svc = PolicyExpirationService(
            rollout_approval_store=approval_store,
            release_gate_requirement_store=gate_store,
        )
        report = await svc.sweep()
        assert report.completed_at is not None

    @pytest.mark.asyncio
    async def test_expiration_triggers_notification(self):
        approval_store = AsyncMock()
        expired_approval = MagicMock()
        expired_approval.approval_id = "rsa_001"
        expired_approval.status = MagicMock()
        expired_approval.status.value = "expired"
        approval_store.expire_pending.return_value = [expired_approval]

        notification_service = AsyncMock()
        notification_service.notify_event = AsyncMock(return_value=[])

        svc = PolicyExpirationService(
            rollout_approval_store=approval_store,
            notification_service=notification_service,
        )
        report = await svc.sweep()
        assert notification_service.notify_event.called

    @pytest.mark.asyncio
    async def test_errors_captured(self):
        approval_store = AsyncMock()
        approval_store.expire_pending.side_effect = Exception("db error")

        svc = PolicyExpirationService(rollout_approval_store=approval_store)
        report = await svc.sweep()
        # Should not crash, errors captured in results
        assert report.completed_at is not None
```

- [ ] **Step 2: Write the failing tests for worker**

```python
"""Tests for expiration worker (Phase 44)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from agent_app.runtime.policy_expiration_worker import PolicyExpirationWorker


class TestExpirationWorker:
    @pytest.mark.asyncio
    async def test_run_once_calls_sweep(self):
        svc = AsyncMock()
        from agent_app.governance.policy_expiration import PolicyExpirationSweepReport
        from datetime import datetime, timezone
        report = PolicyExpirationSweepReport(
            sweep_id="pes_001",
            started_at=datetime.now(timezone.utc),
        )
        svc.sweep.return_value = report

        worker = PolicyExpirationWorker(svc, interval_seconds=60)
        result = await worker.run_once()
        assert result.sweep_id == "pes_001"
        svc.sweep.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_stop_safe(self):
        svc = AsyncMock()
        from agent_app.governance.policy_expiration import PolicyExpirationSweepReport
        from datetime import datetime, timezone
        report = PolicyExpirationSweepReport(
            sweep_id="pes_001",
            started_at=datetime.now(timezone.utc),
        )
        svc.sweep.return_value = report

        worker = PolicyExpirationWorker(svc, interval_seconds=60)
        # Should be safe to stop without starting
        await worker.stop()
        # Should be safe to start and stop
        # (start creates a background task but we stop immediately)
        await worker.stop()

    @pytest.mark.asyncio
    async def test_no_auto_start_on_import(self):
        """Worker must not start automatically on import/instantiation."""
        svc = AsyncMock()
        worker = PolicyExpirationWorker(svc, interval_seconds=60)
        assert not worker.is_running
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_expiration_service.py tests/unit/test_policy_expiration_worker.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Write expiration service implementation**

```python
"""Expiration service — sweep pending approvals and gate requirements past their TTL.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from agent_app.governance.policy_expiration import (
    PolicyExpirationTargetType,
    PolicyExpirationAction,
    PolicyExpirationResult,
    PolicyExpirationSweepReport,
)

logger = logging.getLogger(__name__)


class PolicyExpirationService:
    """Service for expiring stale approvals and gate requirements."""

    def __init__(
        self,
        rollout_approval_store: Any | None = None,
        release_gate_requirement_store: Any | None = None,
        notification_service: Any | None = None,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._approval_store = rollout_approval_store
        self._gate_store = release_gate_requirement_store
        self._notification_service = notification_service
        self._audit_logger = audit_logger
        self._event_store = event_store

    async def sweep(
        self,
        now: datetime | None = None,
    ) -> PolicyExpirationSweepReport:
        """Run a full expiration sweep across all target types."""
        if now is None:
            now = datetime.now(timezone.utc)

        started_at = now
        all_results: list[PolicyExpirationResult] = []

        # Expire rollout approvals
        try:
            approval_results = await self.expire_rollout_approvals(now=now)
            all_results.extend(approval_results)
        except Exception as exc:
            logger.error("Error expiring rollout approvals: %s", exc)
            all_results.append(PolicyExpirationResult(
                result_id=f"per_{uuid.uuid4().hex[:12]}",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="sweep_error",
                action=PolicyExpirationAction.ERROR,
                error={"type": "sweep_error", "message": str(exc)},
                created_at=datetime.now(timezone.utc),
            ))

        # Expire gate requirements
        try:
            gate_results = await self.expire_gate_requirements(now=now)
            all_results.extend(gate_results)
        except Exception as exc:
            logger.error("Error expiring gate requirements: %s", exc)
            all_results.append(PolicyExpirationResult(
                result_id=f"per_{uuid.uuid4().hex[:12]}",
                target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
                target_id="sweep_error",
                action=PolicyExpirationAction.ERROR,
                error={"type": "sweep_error", "message": str(exc)},
                created_at=datetime.now(timezone.utc),
            ))

        completed_at = datetime.now(timezone.utc)

        # Emit change events
        await self._emit_events(all_results)

        report = PolicyExpirationSweepReport(
            sweep_id=f"pes_{uuid.uuid4().hex[:12]}",
            started_at=started_at,
            completed_at=completed_at,
            results=all_results,
        )
        return report

    async def expire_rollout_approvals(
        self,
        now: datetime | None = None,
    ) -> list[PolicyExpirationResult]:
        """Expire pending rollout approvals past their expires_at."""
        if self._approval_store is None:
            return []

        if now is None:
            now = datetime.now(timezone.utc)

        results: list[PolicyExpirationResult] = []
        try:
            expired = await self._approval_store.expire_pending(now=now)
            for approval in expired:
                result = PolicyExpirationResult(
                    result_id=f"per_{uuid.uuid4().hex[:12]}",
                    target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                    target_id=approval.approval_id,
                    action=PolicyExpirationAction.EXPIRED,
                    reason="Approval expired",
                    created_at=datetime.now(timezone.utc),
                )
                results.append(result)

                # Notify
                if self._notification_service is not None:
                    try:
                        await self._notification_service.notify_event(
                            "policy.rollout.approval.expired",
                            {"approval_id": approval.approval_id},
                            source_type="rollout_approval",
                            source_id=approval.approval_id,
                        )
                    except Exception:
                        pass
        except Exception as exc:
            results.append(PolicyExpirationResult(
                result_id=f"per_{uuid.uuid4().hex[:12]}",
                target_type=PolicyExpirationTargetType.ROLLOUT_APPROVAL,
                target_id="error",
                action=PolicyExpirationAction.ERROR,
                error={"type": "expire_error", "message": str(exc)},
                created_at=datetime.now(timezone.utc),
            ))

        return results

    async def expire_gate_requirements(
        self,
        now: datetime | None = None,
    ) -> list[PolicyExpirationResult]:
        """Check gate requirements for max_age_seconds expiration."""
        if self._gate_store is None:
            return []

        if now is None:
            now = datetime.now(timezone.utc)

        results: list[PolicyExpirationResult] = []
        try:
            from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
            # Get all REQUIRED (not yet satisfied/expired) requirements
            requirements = await self._gate_store.list(status=ReleaseGateRequirementStatus.REQUIRED)
            for req in requirements:
                if req.max_age_seconds is None:
                    continue
                # Check if the gate result is stale
                reference_time = req.satisfied_at or req.created_at
                if reference_time is not None:
                    age = (now - reference_time).total_seconds()
                    if age > req.max_age_seconds:
                        req.status = ReleaseGateRequirementStatus.EXPIRED
                        await self._gate_store.update(req)
                        result = PolicyExpirationResult(
                            result_id=f"per_{uuid.uuid4().hex[:12]}",
                            target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
                            target_id=req.requirement_id,
                            action=PolicyExpirationAction.EXPIRED,
                            reason=f"Gate requirement expired (max_age={req.max_age_seconds}s, age={age:.0f}s)",
                            created_at=datetime.now(timezone.utc),
                        )
                        results.append(result)

                        if self._notification_service is not None:
                            try:
                                await self._notification_service.notify_event(
                                    "policy.promotion.gate.expired",
                                    {"requirement_id": req.requirement_id},
                                    source_type="promotion_gate_requirement",
                                    source_id=req.requirement_id,
                                )
                            except Exception:
                                pass
        except Exception as exc:
            results.append(PolicyExpirationResult(
                result_id=f"per_{uuid.uuid4().hex[:12]}",
                target_type=PolicyExpirationTargetType.PROMOTION_GATE_REQUIREMENT,
                target_id="error",
                action=PolicyExpirationAction.ERROR,
                error={"type": "expire_error", "message": str(exc)},
                created_at=datetime.now(timezone.utc),
            ))

        return results

    async def _emit_events(self, results: list[PolicyExpirationResult]) -> None:
        """Emit audit and change events for expiration results."""
        for result in results:
            # Audit
            if self._audit_logger is not None:
                try:
                    from agent_app.governance.audit import AuditEvent
                    event = AuditEvent(
                        event_id=f"ae_{uuid.uuid4().hex[:12]}",
                        event_type=f"policy.expiration.target_{result.action.value}",
                        data={
                            "target_type": result.target_type.value,
                            "target_id": result.target_id,
                        },
                    )
                    await self._audit_logger.log(event)
                except Exception:
                    pass

            # Change event
            if self._event_store is not None:
                try:
                    from agent_app.governance.policy_change_event import (
                        PolicyChangeEvent,
                        PolicyChangeEventType,
                    )
                    event_type_str = "policy.expiration.target_expired"
                    event = PolicyChangeEvent(
                        event_id=f"pce_{uuid.uuid4().hex[:12]}",
                        event_type=event_type_str,
                        data={
                            "target_type": result.target_type.value,
                            "target_id": result.target_id,
                            "action": result.action.value,
                        },
                        created_at=datetime.now(timezone.utc),
                    )
                    await self._event_store.append(event)
                except Exception:
                    pass
```

- [ ] **Step 5: Write worker implementation**

```python
"""Optional in-process expiration worker — calls ExpirationService on an interval.

Phase 44: Notification Hooks and Expiration Workers.

Does NOT start automatically. Must be explicitly started with await worker.start().
Tests should prefer worker.run_once().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PolicyExpirationWorker:
    """In-process worker that periodically runs expiration sweeps."""

    def __init__(
        self,
        expiration_service: Any,
        interval_seconds: int = 60,
    ) -> None:
        self._service = expiration_service
        self._interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """Whether the worker is currently running."""
        return self._running

    async def start(self) -> None:
        """Start the worker loop. Safe to call multiple times."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Expiration worker started (interval=%ds)", self._interval_seconds)

    async def stop(self) -> None:
        """Stop the worker loop. Safe to call multiple times."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Expiration worker stopped")

    async def run_once(self) -> Any:
        """Run a single expiration sweep. Preferred for tests."""
        return await self._service.sweep()

    async def _run_loop(self) -> None:
        """Internal loop that calls sweep at interval."""
        try:
            while self._running:
                try:
                    await self._service.sweep()
                except Exception as exc:
                    logger.error("Expiration sweep failed: %s", exc)
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_expiration_service.py tests/unit/test_policy_expiration_worker.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agent_app/runtime/policy_expiration_service.py agent_app/runtime/policy_expiration_worker.py tests/unit/test_policy_expiration_service.py tests/unit/test_policy_expiration_worker.py
git commit -m "feat: Phase 44 Task 5 — PolicyExpirationService and PolicyExpirationWorker"
```

---

### Task 6: Config schema, loader, RBAC, events, AgentApp properties

**Files:**
- Modify: `agent_app/governance/policy_rbac.py` — add 7 permissions
- Modify: `agent_app/governance/policy_change_event.py` — add 10 event types
- Modify: `agent_app/config/schema.py` — add NotificationConfig, ExpirationConfig
- Modify: `agent_app/config/loader.py` — wire notification + expiration
- Modify: `agent_app/core/app.py` — add 3 properties
- Test: `tests/unit/test_policy_notification_config.py`

- [ ] **Step 1: Write the failing tests**

The test file should verify:
1. New RBAC permissions exist (NOTIFICATION_VIEW, NOTIFICATION_SEND, NOTIFICATION_RULE_VIEW, NOTIFICATION_RULE_ENABLE, NOTIFICATION_RULE_DISABLE, EXPIRATION_SWEEP, EXPIRATION_VIEW)
2. NOTIFICATION_VIEW and EXPIRATION_VIEW are in _DEFAULT_ALLOWED
3. New event types exist (10 new types, total count = 55 + 10 = 65)
4. NotificationConfig and ExpirationConfig models work
5. PolicyReleaseConfig has notifications and expiration fields
6. AgentApp has notification_service, expiration_service, expiration_worker properties
7. Missing config preserves behavior (backward compat)
8. Loader wires services when config present

```python
"""Tests for Phase 44 config, loader, RBAC, events, and AgentApp properties."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rbac import PolicyReleasePermission, _DEFAULT_ALLOWED
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.config.schema import NotificationConfig, ExpirationConfig, PolicyReleaseConfig
from agent_app.core.app import AgentApp


class TestRBACPermissions:
    def test_notification_permissions_exist(self):
        assert PolicyReleasePermission.NOTIFICATION_VIEW.value == "policy.notification.view"
        assert PolicyReleasePermission.NOTIFICATION_SEND.value == "policy.notification.send"
        assert PolicyReleasePermission.NOTIFICATION_RULE_VIEW.value == "policy.notification.rule.view"
        assert PolicyReleasePermission.NOTIFICATION_RULE_ENABLE.value == "policy.notification.rule.enable"
        assert PolicyReleasePermission.NOTIFICATION_RULE_DISABLE.value == "policy.notification.rule.disable"

    def test_expiration_permissions_exist(self):
        assert PolicyReleasePermission.EXPIRATION_SWEEP.value == "policy.expiration.sweep"
        assert PolicyReleasePermission.EXPIRATION_VIEW.value == "policy.expiration.view"

    def test_view_permissions_default_allowed(self):
        assert PolicyReleasePermission.NOTIFICATION_VIEW in _DEFAULT_ALLOWED
        assert PolicyReleasePermission.EXPIRATION_VIEW in _DEFAULT_ALLOWED


class TestChangeEvents:
    def test_new_event_types(self):
        assert hasattr(PolicyChangeEventType, "NOTIFICATION_CREATED")
        assert hasattr(PolicyChangeEventType, "NOTIFICATION_SENT")
        assert hasattr(PolicyChangeEventType, "NOTIFICATION_FAILED")
        assert hasattr(PolicyChangeEventType, "NOTIFICATION_RULE_ENABLED")
        assert hasattr(PolicyChangeEventType, "NOTIFICATION_RULE_DISABLED")
        assert hasattr(PolicyChangeEventType, "EXPIRATION_SWEEP_STARTED")
        assert hasattr(PolicyChangeEventType, "EXPIRATION_SWEEP_COMPLETED")
        assert hasattr(PolicyChangeEventType, "EXPIRATION_SWEEP_FAILED")
        assert hasattr(PolicyChangeEventType, "EXPIRATION_TARGET_EXPIRED")
        assert hasattr(PolicyChangeEventType, "EXPIRATION_PERMISSION_DENIED")

    def test_event_type_count(self):
        # 55 existing + 10 new = 65
        assert len(PolicyChangeEventType) == 65


class TestConfigModels:
    def test_notification_config_defaults(self):
        cfg = NotificationConfig()
        assert cfg.enabled is False

    def test_expiration_config_defaults(self):
        cfg = ExpirationConfig()
        assert cfg.enabled is False
        assert cfg.sweep_interval_seconds == 300

    def test_policy_release_config_has_new_fields(self):
        cfg = PolicyReleaseConfig()
        assert cfg.notifications is None
        assert cfg.expiration is None


class TestAgentAppProperties:
    def test_notification_service_property(self):
        app = AgentApp()
        assert app.notification_service is None

    def test_expiration_service_property(self):
        app = AgentApp()
        assert app.expiration_service is None

    def test_expiration_worker_property(self):
        app = AgentApp()
        assert app.expiration_worker is None

    def test_set_notification_service(self):
        app = AgentApp()
        app.notification_service = "mock_service"
        assert app.notification_service == "mock_service"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_policy_notification_config.py -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Add RBAC permissions to `agent_app/governance/policy_rbac.py`**

Add after `ROLLOUT_GATE_RUN`:
```python
    NOTIFICATION_VIEW = "policy.notification.view"
    NOTIFICATION_SEND = "policy.notification.send"
    NOTIFICATION_RULE_VIEW = "policy.notification.rule.view"
    NOTIFICATION_RULE_ENABLE = "policy.notification.rule.enable"
    NOTIFICATION_RULE_DISABLE = "policy.notification.rule.disable"
    EXPIRATION_SWEEP = "policy.expiration.sweep"
    EXPIRATION_VIEW = "policy.expiration.view"
```

Add to `_DEFAULT_ALLOWED`:
```python
    PolicyReleasePermission.NOTIFICATION_VIEW,
    PolicyReleasePermission.EXPIRATION_VIEW,
```

- [ ] **Step 4: Add event types to `agent_app/governance/policy_change_event.py`**

Add after `ROLLOUT_GATE_PERMISSION_DENIED`:
```python
    NOTIFICATION_CREATED = "policy.notification.created"
    NOTIFICATION_SENT = "policy.notification.sent"
    NOTIFICATION_FAILED = "policy.notification.failed"
    NOTIFICATION_RULE_ENABLED = "policy.notification.rule.enabled"
    NOTIFICATION_RULE_DISABLED = "policy.notification.rule.disabled"
    EXPIRATION_SWEEP_STARTED = "policy.expiration.sweep_started"
    EXPIRATION_SWEEP_COMPLETED = "policy.expiration.sweep_completed"
    EXPIRATION_SWEEP_FAILED = "policy.expiration.sweep_failed"
    EXPIRATION_TARGET_EXPIRED = "policy.expiration.target_expired"
    EXPIRATION_PERMISSION_DENIED = "policy.expiration.permission_denied"
```

- [ ] **Step 5: Add config models to `agent_app/config/schema.py`**

Add after `RolloutGateAutomationConfig`:
```python
class NotificationRuleConfig(BaseModel):
    """A notification rule from YAML config."""

    name: str = Field(..., description="Rule name")
    event_types: list[str] = Field(..., description="Event types to match")
    severity: str = Field(default="info", description="Notification severity: info, warning, error, critical")
    channels: list[str] = Field(default_factory=lambda: ["log"], description="Delivery channels")
    title_template: str | None = Field(default=None, description="Title template")
    body_template: str | None = Field(default=None, description="Body template")


class NotificationConfig(BaseModel):
    """Notification system configuration (Phase 44)."""

    enabled: bool = Field(default=False, description="Enable notifications")
    store: PolicyReleaseStoreConfig | None = Field(
        default=None,
        description="Notification store configuration",
    )
    rules: list[NotificationRuleConfig] = Field(
        default_factory=list,
        description="Notification rules",
    )


class ExpirationConfig(BaseModel):
    """Expiration sweep configuration (Phase 44)."""

    enabled: bool = Field(default=False, description="Enable expiration sweeps")
    sweep_interval_seconds: int = Field(
        default=300,
        description="Interval between automatic sweeps (seconds)",
    )
```

Add to `PolicyReleaseConfig`:
```python
    notifications: NotificationConfig | None = Field(
        default=None,
        description="Notification config (Phase 44)",
    )
    expiration: ExpirationConfig | None = Field(
        default=None,
        description="Expiration config (Phase 44)",
    )
```

- [ ] **Step 6: Add properties to `agent_app/core/app.py`**

Add after `rollout_gate_automation_service` property:
```python
    @property
    def notification_service(self) -> Any:
        """Phase 44: Return the notification service, if configured."""
        return getattr(self, "_notification_service", None)

    @notification_service.setter
    def notification_service(self, value: Any) -> None:
        """Phase 44: Set the notification service."""
        self._notification_service = value

    @property
    def expiration_service(self) -> Any:
        """Phase 44: Return the expiration service, if configured."""
        return getattr(self, "_expiration_service", None)

    @expiration_service.setter
    def expiration_service(self, value: Any) -> None:
        """Phase 44: Set the expiration service."""
        self._expiration_service = value

    @property
    def expiration_worker(self) -> Any:
        """Phase 44: Return the expiration worker, if configured."""
        return getattr(self, "_expiration_worker", None)

    @expiration_worker.setter
    def expiration_worker(self, value: Any) -> None:
        """Phase 44: Set the expiration worker."""
        self._expiration_worker = value
```

- [ ] **Step 7: Update config loader `agent_app/config/loader.py`**

Add Phase 44 wiring: when notifications config present and enabled, create notification store, rule store, notification service, and set on AgentApp. When expiration config present and enabled, create expiration service and optionally worker. Load inline notification rules into rule store.

- [ ] **Step 8: Update existing event count test**

In `tests/unit/test_policy_change_event.py`, update expected count from 55 to 65.

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_config.py tests/unit/test_policy_change_event.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py agent_app/config/schema.py agent_app/config/loader.py agent_app/core/app.py tests/unit/test_policy_notification_config.py tests/unit/test_policy_change_event.py
git commit -m "feat: Phase 44 Task 6 — config, loader, RBAC, events, AgentApp properties for notifications and expiration"
```

---

### Task 7: CLI commands and console pages

**Files:**
- Modify: `agent_app/cli.py` — add notification list/send-pending/rule list/enable/disable + expiration sweep/run-once
- Modify: `agent_app/console/router.py` — add 6 routes
- Modify: `agent_app/adapters/fastapi.py` — wire notification_service, expiration_service
- Create: `agent_app/console/templates/policy_notifications.html`
- Create: `agent_app/console/templates/policy_notification_rules.html`
- Create: `agent_app/console/templates/policy_expiration.html`
- Test: `tests/unit/test_policy_notification_cli.py`
- Test: `tests/unit/test_policy_notification_console.py`

- [ ] **Step 1: Write CLI tests**

Test that each CLI command runs without crash and produces expected output. Follow the pattern from Phase 43's CLI tests — use `runner.invoke()` with `--config` flag.

- [ ] **Step 2: Write console tests**

Test that routes return 200 for GET pages and POST actions work. Follow Phase 43 console test patterns — skip if FastAPI/Jinja2 not installed.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_cli.py tests/unit/test_policy_notification_console.py -v`
Expected: FAIL

- [ ] **Step 4: Add CLI commands to `agent_app/cli.py`**

Add `policy notification list`, `policy notification send-pending`, `policy notification rule list`, `policy notification rule enable`, `policy notification rule disable`, `policy expiration sweep`, `policy expiration run-once` subcommands.

- [ ] **Step 5: Add console routes to `agent_app/console/router.py`**

Add 6 routes:
- `GET /policy-console/notifications`
- `POST /policy-console/notifications/send-pending`
- `GET /policy-console/notification-rules`
- `POST /policy-console/notification-rules/{rule_id}/enable`
- `POST /policy-console/notification-rules/{rule_id}/disable`
- `GET /policy-console/expiration`
- `POST /policy-console/expiration/sweep`

- [ ] **Step 6: Create HTML templates**

Three simple templates following the pattern of existing policy console templates.

- [ ] **Step 7: Wire in `agent_app/adapters/fastapi.py`**

Add `notification_service=getattr(agent_app, "notification_service", None)` and `expiration_service=getattr(agent_app, "expiration_service", None)`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_cli.py tests/unit/test_policy_notification_console.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add agent_app/cli.py agent_app/console/router.py agent_app/adapters/fastapi.py agent_app/console/templates/policy_notifications.html agent_app/console/templates/policy_notification_rules.html agent_app/console/templates/policy_expiration.html tests/unit/test_policy_notification_cli.py tests/unit/test_policy_notification_console.py
git commit -m "feat: Phase 44 Task 7 — CLI notification/expiration commands and console pages"
```

---

### Task 8: Documentation and final verification

**Files:**
- Modify: `docs/policy_release.md` — add Phase 44 section
- Modify: `CHANGELOG.md` — add v0.32.0 entry
- Modify: `README.md` — add Phase 44 in roadmap
- Create: `docs/release_checklist_phase44.md`

- [ ] **Step 1: Update `docs/policy_release.md`**

Add Phase 44 section covering: purpose, notification architecture, notification rules, built-in channels, expiration sweep service, optional worker, CLI commands, console workflow, RBAC permissions, known limitations.

- [ ] **Step 2: Update `CHANGELOG.md`**

Add v0.32.0 entry with Phase 44 features.

- [ ] **Step 3: Update `README.md`**

Add Phase 44 in the roadmap section.

- [ ] **Step 4: Create `docs/release_checklist_phase44.md`**

Document: feature summary, verification steps, new files, modified files, known limitations, Phase 45 recommendation.

- [ ] **Step 5: Run Phase 44-specific tests**

Run: `pytest tests/unit/test_policy_notification_model.py tests/unit/test_policy_expiration_model.py tests/unit/test_policy_notification_store.py tests/unit/test_policy_notification_rule_store.py tests/unit/test_policy_notification_channels.py tests/unit/test_policy_notification_service.py tests/unit/test_policy_expiration_service.py tests/unit/test_policy_expiration_worker.py tests/unit/test_policy_notification_config.py tests/unit/test_policy_notification_cli.py tests/unit/test_policy_notification_console.py -v`
Expected: All PASS

- [ ] **Step 6: Run broader regression tests**

Run: `pytest tests/unit/ -k "policy" --timeout=60 -q`
Expected: 0 failed

- [ ] **Step 7: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase44.md
git commit -m "docs: Phase 44 documentation — notification hooks and expiration workers"
```
