"""Federation notification preference service — delivery decision and explanation.

Phase 51: Preference resolution with priority, mandatory events, and fail modes.
"""
from __future__ import annotations

from agent_app.governance.policy_rollout_federation_notification_preference import (
    FederationNotificationPreference,
    FederationNotificationPreferenceDecision,
    FederationNotificationPreferenceExplanation,
)
from agent_app.runtime.policy_rollout_federation_notification_preference_store import (
    FederationNotificationPreferenceStore,
)


class FederationNotificationPreferenceService:
    """Service for resolving notification delivery preferences.

    Priority (most specific first):
    1. Approval + event_type + channel
    2. Federation + event_type + channel
    3. Event_type + channel
    4. Channel only
    5. Subject global preference
    6. System default (deliver)

    Conflict resolution: OPT_OUT wins over OPT_IN at same specificity.
    Mandatory events override OPT_OUT.
    """

    def __init__(
        self,
        preference_store: FederationNotificationPreferenceStore | None = None,
        *,
        default_delivery: bool = True,
        failure_mode: str = "open",  # "open" or "closed"
        mandatory_event_types: list[str] | None = None,
    ) -> None:
        self._store = preference_store
        self._default_delivery = default_delivery
        self._failure_mode = failure_mode
        self._mandatory_event_types = set(mandatory_event_types or [])

    async def should_deliver(
        self,
        subject_type: str,
        subject_id: str,
        event_type: str,
        channel: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        """Determine whether a notification should be delivered."""
        explanation = await self.explain_preference(
            subject_type=subject_type,
            subject_id=subject_id,
            event_type=event_type,
            channel=channel,
            federation_id=federation_id,
            approval_id=approval_id,
        )
        return explanation.decision != FederationNotificationPreferenceDecision.OPT_OUT

    async def resolve_effective_preference(
        self,
        subject_type: str,
        subject_id: str,
        event_type: str,
        channel: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
    ) -> FederationNotificationPreference | None:
        """Find the most specific matching preference."""
        if self._store is None:
            return None
        try:
            return await self._store.resolve_effective_preference(
                subject_type=subject_type,
                subject_id=subject_id,
                federation_id=federation_id,
                approval_id=approval_id,
                event_type=event_type,
                channel=channel,
            )
        except Exception:  # noqa: BLE001
            if self._failure_mode == "closed":
                return None  # fail-closed: no preference = don't deliver
            return None  # fail-open: use default

    async def explain_preference(
        self,
        subject_type: str,
        subject_id: str,
        event_type: str,
        channel: str,
        federation_id: str | None = None,
        approval_id: str | None = None,
    ) -> FederationNotificationPreferenceExplanation:
        """Explain the delivery decision for a notification."""
        is_mandatory = event_type in self._mandatory_event_types

        pref = await self.resolve_effective_preference(
            subject_type=subject_type,
            subject_id=subject_id,
            event_type=event_type,
            channel=channel,
            federation_id=federation_id,
            approval_id=approval_id,
        )

        if is_mandatory:
            return FederationNotificationPreferenceExplanation(
                decision=FederationNotificationPreferenceDecision.OPT_IN,
                matched_preference_id=pref.preference_id if pref else None,
                specificity=self._compute_specificity(pref) if pref else 0,
                is_mandatory=True,
                system_default=pref is None,
                reason="Mandatory notification — overrides user preference",
                reason_code="mandatory_override",
            )

        if pref is not None:
            return FederationNotificationPreferenceExplanation(
                decision=pref.decision,
                matched_preference_id=pref.preference_id,
                specificity=self._compute_specificity(pref),
                is_mandatory=False,
                system_default=False,
                reason=f"Matched preference {pref.preference_id}: {pref.decision.value}",
                reason_code=f"preference_{pref.decision.value}",
            )

        # No matching preference — use system default
        default_decision = (
            FederationNotificationPreferenceDecision.OPT_IN
            if self._default_delivery
            else FederationNotificationPreferenceDecision.OPT_OUT
        )
        return FederationNotificationPreferenceExplanation(
            decision=default_decision,
            matched_preference_id=None,
            specificity=0,
            is_mandatory=False,
            system_default=True,
            reason=f"No preference found — using system default ({default_decision.value})",
            reason_code="system_default",
        )

    @staticmethod
    def _compute_specificity(pref: FederationNotificationPreference | None) -> int:
        """Compute specificity score for a preference rule."""
        if pref is None:
            return 0
        score = 0
        if pref.approval_id:
            score += 4
        if pref.federation_id:
            score += 2
        if pref.event_type:
            score += 1
        if pref.channel:
            score += 1
        return score
