"""Tests for federation notification preference domain models."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceExplanation,
    FederationNotificationPreferenceSubjectType,
)


def _ts() -> datetime:
    return datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


# --- FederationNotificationPreferenceDecision enum ---


class TestPreferenceDecisionEnum:
    def test_enum_values(self):
        assert FederationNotificationPreferenceDecision.INHERIT == "inherit"
        assert FederationNotificationPreferenceDecision.OPT_IN == "opt_in"
        assert FederationNotificationPreferenceDecision.OPT_OUT == "opt_out"
        assert len(FederationNotificationPreferenceDecision) == 3


# --- FederationNotificationPreferenceSubjectType enum ---


class TestPreferenceSubjectTypeEnum:
    def test_enum_values(self):
        assert FederationNotificationPreferenceSubjectType.USER == "user"
        assert FederationNotificationPreferenceSubjectType.SERVICE_ACCOUNT == "service_account"
        assert FederationNotificationPreferenceSubjectType.ROLE == "role"
        assert FederationNotificationPreferenceSubjectType.FEDERATION_MEMBER == "federation_member"
        assert len(FederationNotificationPreferenceSubjectType) == 4


# --- FederationNotificationPreference model ---


class TestPreferenceModel:
    def test_preference_model_valid(self):
        p = FederationNotificationPreference(
            preference_id="fnp_user_123",
            subject_type=FederationNotificationPreferenceSubjectType.USER,
            subject_id="user-123",
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
            reason="Too noisy",
            created_at=_ts(),
            updated_at=_ts(),
        )
        assert p.preference_id == "fnp_user_123"
        assert p.subject_type == FederationNotificationPreferenceSubjectType.USER
        assert p.decision == FederationNotificationPreferenceDecision.OPT_OUT
        assert p.reason == "Too noisy"

    def test_preference_id_prefix_valid(self):
        p = FederationNotificationPreference(
            preference_id="fnp_valid",
            subject_type=FederationNotificationPreferenceSubjectType.ROLE,
            subject_id="admin",
            decision=FederationNotificationPreferenceDecision.OPT_IN,
            created_at=_ts(),
            updated_at=_ts(),
        )
        assert p.preference_id == "fnp_valid"

    def test_preference_id_prefix_invalid(self):
        with pytest.raises(ValidationError, match="fnp_"):
            FederationNotificationPreference(
                preference_id="bad_id",
                subject_type=FederationNotificationPreferenceSubjectType.USER,
                subject_id="user-1",
                decision=FederationNotificationPreferenceDecision.INHERIT,
                created_at=_ts(),
                updated_at=_ts(),
            )

    def test_preference_tz_aware_required(self):
        naive = datetime(2026, 6, 20, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationNotificationPreference(
                preference_id="fnp_test",
                subject_type=FederationNotificationPreferenceSubjectType.USER,
                subject_id="user-1",
                decision=FederationNotificationPreferenceDecision.INHERIT,
                created_at=naive,
                updated_at=_ts(),
            )


# --- FederationNotificationPreferenceExplanation model ---


class TestPreferenceExplanation:
    def test_explanation_model(self):
        exp = FederationNotificationPreferenceExplanation(
            decision=FederationNotificationPreferenceDecision.OPT_OUT,
            matched_preference_id="fnp_user_123",
            specificity=3,
            is_mandatory=False,
            system_default=False,
            reason="User opted out of webhook notifications",
            reason_code="USER_OPT_OUT",
        )
        assert exp.decision == FederationNotificationPreferenceDecision.OPT_OUT
        assert exp.matched_preference_id == "fnp_user_123"
        assert exp.specificity == 3
        assert exp.is_mandatory is False
        assert exp.system_default is False
        assert exp.reason_code == "USER_OPT_OUT"

    def test_explanation_defaults(self):
        exp = FederationNotificationPreferenceExplanation(
            decision=FederationNotificationPreferenceDecision.INHERIT,
        )
        assert exp.matched_preference_id is None
        assert exp.specificity == 0
        assert exp.is_mandatory is False
        assert exp.system_default is False
        assert exp.reason is None
        assert exp.reason_code is None
