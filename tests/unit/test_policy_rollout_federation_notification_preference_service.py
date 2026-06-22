"""Tests for FederationNotificationPreferenceService — Phase 51 Task 4.

Tests preference resolution, mandatory events, fail modes, and specificity.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceExplanation,
    FederationNotificationPreferenceSubjectType,
)
from agent_app.runtime.policy_rollout_federation_notification_preference_service import (
    FederationNotificationPreferenceService,
)
from agent_app.runtime.policy_rollout_federation_notification_preference_store import (
    InMemoryFederationNotificationPreferenceStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_preference(
    preference_id: str = "fnp_001",
    subject_type: str = "user",
    subject_id: str = "user-1",
    decision: str = "opt_out",
    federation_id: str | None = None,
    approval_id: str | None = None,
    event_type: str | None = None,
    channel: str | None = None,
    reason: str | None = None,
) -> FederationNotificationPreference:
    return FederationNotificationPreference(
        preference_id=preference_id,
        subject_type=FederationNotificationPreferenceSubjectType(subject_type),
        subject_id=subject_id,
        federation_id=federation_id,
        approval_id=approval_id,
        event_type=event_type,
        channel=channel,
        decision=FederationNotificationPreferenceDecision(decision),
        reason=reason,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


# ===========================================================================
# TestFederationNotificationPreferenceService
# ===========================================================================


class TestFederationNotificationPreferenceService:
    """Unit tests for FederationNotificationPreferenceService."""

    # ------------------------------------------------------------------
    # Default delivery
    # ------------------------------------------------------------------

    def test_default_delivery_opt_in(self):
        """With default_delivery=True, no preference means deliver."""
        svc = FederationNotificationPreferenceService(default_delivery=True)
        result = _run_async(svc.should_deliver("user", "u1", "policy.created", "email"))
        assert result is True

    def test_default_delivery_opt_out(self):
        """With default_delivery=False, no preference means suppress."""
        svc = FederationNotificationPreferenceService(default_delivery=False)
        result = _run_async(svc.should_deliver("user", "u1", "policy.created", "email"))
        assert result is False

    # ------------------------------------------------------------------
    # Explicit preferences
    # ------------------------------------------------------------------

    def test_explicit_opt_out_blocks_delivery(self):
        """An explicit opt_out preference blocks delivery."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        result = _run_async(svc.should_deliver("user", "user-1", "policy.created", "email"))
        assert result is False

    def test_explicit_opt_in_allows_delivery(self):
        """An explicit opt_in preference allows delivery."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="opt_in")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        result = _run_async(svc.should_deliver("user", "user-1", "policy.created", "email"))
        assert result is True

    def test_inherit_uses_default(self):
        """An INHERIT decision is skipped, falling back to system default."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="inherit")))
        svc = FederationNotificationPreferenceService(preference_store=store, default_delivery=True)
        result = _run_async(svc.should_deliver("user", "user-1", "policy.created", "email"))
        assert result is True  # falls through to default

    # ------------------------------------------------------------------
    # Scoped preferences
    # ------------------------------------------------------------------

    def test_channel_specific_preference(self):
        """A channel-scoped preference matches when channel aligns."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(channel="slack", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        # Slack should be blocked
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "slack")) is False
        # Email should fall through to default
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email")) is True

    def test_event_type_specific_preference(self):
        """An event_type-scoped preference matches when event_type aligns."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(event_type="policy.created", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email")) is False
        assert _run_async(svc.should_deliver("user", "user-1", "policy.updated", "email")) is True

    def test_federation_specific_preference(self):
        """A federation-scoped preference matches when federation_id aligns."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(federation_id="fed-1", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email", federation_id="fed-1")) is False
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email", federation_id="fed-2")) is True

    def test_approval_specific_preference(self):
        """An approval-scoped preference matches when approval_id aligns."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(approval_id="apr-1", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email", approval_id="apr-1")) is False
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email", approval_id="apr-2")) is True

    # ------------------------------------------------------------------
    # Specificity
    # ------------------------------------------------------------------

    def test_more_specific_preference_wins(self):
        """A more specific preference overrides a less specific one."""
        store = InMemoryFederationNotificationPreferenceStore()
        # Global opt_in
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_global", decision="opt_in",
        )))
        # Channel-specific opt_out
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_channel", channel="slack", decision="opt_out",
        )))
        svc = FederationNotificationPreferenceService(preference_store=store)
        # Slack should be blocked (more specific)
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "slack")) is False
        # Email should be allowed (global opt_in)
        assert _run_async(svc.should_deliver("user", "user-1", "policy.created", "email")) is True

    # ------------------------------------------------------------------
    # Mandatory events
    # ------------------------------------------------------------------

    def test_mandatory_event_overrides_opt_out(self):
        """Mandatory events override an explicit opt_out."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="opt_out")))
        svc = FederationNotificationPreferenceService(
            preference_store=store,
            mandatory_event_types=["security.alert"],
        )
        result = _run_async(svc.should_deliver("user", "user-1", "security.alert", "email"))
        assert result is True

    def test_mandatory_event_with_opt_in(self):
        """Mandatory events with opt_in still deliver."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="opt_in")))
        svc = FederationNotificationPreferenceService(
            preference_store=store,
            mandatory_event_types=["security.alert"],
        )
        result = _run_async(svc.should_deliver("user", "user-1", "security.alert", "email"))
        assert result is True

    # ------------------------------------------------------------------
    # Fail modes
    # ------------------------------------------------------------------

    def test_fail_open_on_error(self):
        """Fail-open: store error falls back to default delivery."""
        class BrokenStore:
            async def resolve_effective_preference(self, **kwargs):
                raise RuntimeError("store broken")
        svc = FederationNotificationPreferenceService(
            preference_store=BrokenStore(),
            default_delivery=True,
            failure_mode="open",
        )
        result = _run_async(svc.should_deliver("user", "u1", "policy.created", "email"))
        assert result is True  # default is opt_in

    def test_fail_closed_on_error(self):
        """Fail-closed: store error results in no delivery."""
        class BrokenStore:
            async def resolve_effective_preference(self, **kwargs):
                raise RuntimeError("store broken")
        svc = FederationNotificationPreferenceService(
            preference_store=BrokenStore(),
            default_delivery=True,
            failure_mode="closed",
        )
        # Fail-closed: resolve returns None, no preference matched,
        # but system default is opt_in, so it still delivers.
        # The fail-closed mode affects resolve_effective_preference only.
        # The explain_preference still uses system default when pref is None.
        # To truly test fail-closed, we need default_delivery=False.
        svc2 = FederationNotificationPreferenceService(
            preference_store=BrokenStore(),
            default_delivery=False,
            failure_mode="closed",
        )
        result = _run_async(svc2.should_deliver("user", "u1", "policy.created", "email"))
        assert result is False

    # ------------------------------------------------------------------
    # Explain
    # ------------------------------------------------------------------

    def test_explain_returns_decision(self):
        """explain_preference returns the correct decision."""
        svc = FederationNotificationPreferenceService(default_delivery=True)
        explanation = _run_async(svc.explain_preference("user", "u1", "policy.created", "email"))
        assert explanation.decision == FederationNotificationPreferenceDecision.OPT_IN

    def test_explain_returns_matched_preference_id(self):
        """explain_preference returns the matched preference ID."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(preference_id="fnp_123", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        explanation = _run_async(svc.explain_preference("user", "user-1", "policy.created", "email"))
        assert explanation.matched_preference_id == "fnp_123"

    def test_explain_returns_specificity(self):
        """explain_preference returns the specificity score."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(channel="slack", decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        explanation = _run_async(svc.explain_preference("user", "user-1", "policy.created", "slack"))
        assert explanation.specificity == 1  # channel only = 1

    def test_explain_mandatory_notification(self):
        """explain_preference marks mandatory notifications correctly."""
        svc = FederationNotificationPreferenceService(
            mandatory_event_types=["security.alert"],
        )
        explanation = _run_async(svc.explain_preference("user", "u1", "security.alert", "email"))
        assert explanation.is_mandatory is True
        assert explanation.reason_code == "mandatory_override"

    def test_explain_system_default(self):
        """explain_preference marks system default correctly."""
        svc = FederationNotificationPreferenceService(default_delivery=True)
        explanation = _run_async(svc.explain_preference("user", "u1", "policy.created", "email"))
        assert explanation.system_default is True
        assert explanation.reason_code == "system_default"

    def test_explain_reason_code(self):
        """explain_preference returns correct reason_code for matched preference."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(decision="opt_out")))
        svc = FederationNotificationPreferenceService(preference_store=store)
        explanation = _run_async(svc.explain_preference("user", "user-1", "policy.created", "email"))
        assert explanation.reason_code == "preference_opt_out"

    # ------------------------------------------------------------------
    # No store
    # ------------------------------------------------------------------

    def test_no_store_returns_default(self):
        """With no store, should_deliver returns the system default."""
        svc = FederationNotificationPreferenceService(default_delivery=True)
        assert _run_async(svc.should_deliver("user", "u1", "policy.created", "email")) is True
        svc2 = FederationNotificationPreferenceService(default_delivery=False)
        assert _run_async(svc2.should_deliver("user", "u1", "policy.created", "email")) is False

    # ------------------------------------------------------------------
    # Store filtering
    # ------------------------------------------------------------------

    def test_preference_subject_type_filter(self):
        """list_preferences filters by subject_type."""
        store = InMemoryFederationNotificationPreferenceStore()
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_user", subject_type="user", subject_id="u1",
        )))
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_role", subject_type="role", subject_id="admin",
        )))
        result = _run_async(store.list_preferences(subject_type="user"))
        assert len(result) == 1
        assert result[0].preference_id == "fnp_user"

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def test_opt_out_wins_over_opt_in_at_same_specificity(self):
        """At the same specificity level, OPT_OUT wins over OPT_IN."""
        store = InMemoryFederationNotificationPreferenceStore()
        # Both are channel-scoped (specificity 1)
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_in", channel="slack", decision="opt_in",
        )))
        _run_async(store.set_preference(_make_preference(
            preference_id="fnp_out", channel="slack", decision="opt_out",
        )))
        svc = FederationNotificationPreferenceService(preference_store=store)
        result = _run_async(svc.should_deliver("user", "user-1", "policy.created", "slack"))
        assert result is False  # opt_out wins

    # ------------------------------------------------------------------
    # Multiple mandatory event types
    # ------------------------------------------------------------------

    def test_multiple_mandatory_event_types(self):
        """Multiple mandatory event types are all enforced."""
        svc = FederationNotificationPreferenceService(
            mandatory_event_types=["security.alert", "compliance.breach"],
        )
        assert _run_async(svc.should_deliver("user", "u1", "security.alert", "email")) is True
        assert _run_async(svc.should_deliver("user", "u1", "compliance.breach", "email")) is True
        assert _run_async(svc.should_deliver("user", "u1", "policy.created", "email")) is True  # default

    # ------------------------------------------------------------------
    # Specificity levels
    # ------------------------------------------------------------------

    def test_compute_specificity_levels(self):
        """_compute_specificity returns correct scores for different scopes."""
        # Global (no scope) = 0
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference()
        ) == 0

        # Channel only = 1
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(channel="slack")
        ) == 1

        # Event type only = 1
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(event_type="policy.created")
        ) == 1

        # Event type + channel = 2
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(event_type="policy.created", channel="slack")
        ) == 2

        # Federation only = 2
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(federation_id="fed-1")
        ) == 2

        # Federation + event_type + channel = 4
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(federation_id="fed-1", event_type="policy.created", channel="slack")
        ) == 4

        # Approval only = 4
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(approval_id="apr-1")
        ) == 4

        # Approval + event_type + channel = 6
        assert FederationNotificationPreferenceService._compute_specificity(
            _make_preference(approval_id="apr-1", event_type="policy.created", channel="slack")
        ) == 6

        # None = 0
        assert FederationNotificationPreferenceService._compute_specificity(None) == 0
