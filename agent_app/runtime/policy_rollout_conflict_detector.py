"""Federation conflict detector -- checks federated rollout plans for conflicts before activation.

Phase 46 Task 3: Detects duplicate targets, missing/disabled targets, active federation
collisions, and environment/ring conflicts with existing active rollouts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedTargetStatus,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)

if TYPE_CHECKING:
    from agent_app.runtime.policy_rollout_federation_store import (
        FederatedRolloutPlanStore,
        FederatedRolloutTargetStore,
    )
    from agent_app.runtime.policy_rollout_store import RolloutPlanStore

# Severity sort order: ERROR (0) before WARNING (1).
_SEVERITY_ORDER: dict[RolloutConflictSeverity, int] = {
    RolloutConflictSeverity.ERROR: 0,
    RolloutConflictSeverity.WARNING: 1,
}


class RolloutConflictDetector:
    """Detects conflicts in a federated rollout plan against existing state.

    The detector is *read-only*: it never mutates the target store, federation
    store, rollout store, or the plan being inspected.
    """

    def __init__(
        self,
        target_store: FederatedRolloutTargetStore,
        federation_store: FederatedRolloutPlanStore,
        rollout_store: RolloutPlanStore | None = None,
    ) -> None:
        self._target_store = target_store
        self._federation_store = federation_store
        self._rollout_store = rollout_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect_conflicts(
        self,
        federation_plan: FederatedRolloutPlan,
    ) -> list[RolloutConflict]:
        """Return all conflicts found for *federation_plan*, deterministically ordered."""
        effective_ids = self._effective_target_ids(federation_plan)
        conflicts: list[RolloutConflict] = []

        # 1. Duplicate targets (from raw effective_ids, including duplicates)
        duplicate_conflicts = self._detect_duplicates(effective_ids)
        conflicts.extend(duplicate_conflicts)
        duplicate_ids = {c.target_id for c in duplicate_conflicts}

        # Compute unique target IDs for remaining checks.
        # When ALL target_ids reference a single unique target that is duplicated,
        # DUPLICATE supersedes MISSING/DISABLED (the plan is entirely about one
        # misconfigured target).  When there are multiple unique targets, check
        # all of them independently.
        unique_targets = set(effective_ids)
        if len(unique_targets) == 1 and duplicate_ids:
            remaining_ids: list[str] = []
        else:
            remaining_ids = list(dict.fromkeys(effective_ids))

        # 2. Missing / 3. Disabled targets
        conflicts.extend(await self._detect_missing_and_disabled(remaining_ids))

        # 4. Active federation same target
        conflicts.extend(
            await self._detect_active_federation_conflicts(
                remaining_ids, federation_plan.federation_id
            )
        )

        # 5-6. Environment/ring + bundle conflicts with active rollouts
        if self._rollout_store is not None:
            conflicts.extend(
                await self._detect_rollout_conflicts(remaining_ids, federation_plan.bundle_id)
            )

        # Deterministic sort: severity (ERROR before WARNING), then
        # conflict_type.value, target_id, existing_rollout_id, existing_federation_id.
        conflicts.sort(
            key=lambda c: (
                _SEVERITY_ORDER[c.severity],
                c.conflict_type.value,
                c.target_id or "",
                c.existing_rollout_id or "",
                c.existing_federation_id or "",
            )
        )
        return conflicts

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_target_ids(plan: FederatedRolloutPlan) -> list[str]:
        """Return plan.target_ids if non-empty, else union of wave target_ids."""
        if plan.target_ids:
            return list(plan.target_ids)
        ids: list[str] = []
        for wave in plan.waves:
            ids.extend(wave.target_ids)
        return ids

    @staticmethod
    def _detect_duplicates(target_ids: list[str]) -> list[RolloutConflict]:
        """Detect duplicate target IDs, sorted by target_id."""
        seen: set[str] = set()
        dupes: set[str] = set()
        for tid in target_ids:
            if tid in seen:
                dupes.add(tid)
            seen.add(tid)
        conflicts: list[RolloutConflict] = []
        for tid in sorted(dupes):
            conflicts.append(
                RolloutConflict(
                    conflict_id=f"frc_duplicate_target_{tid}",
                    conflict_type=RolloutConflictType.DUPLICATE_TARGET,
                    severity=RolloutConflictSeverity.ERROR,
                    target_id=tid,
                    message=f"Duplicate target_id '{tid}' in federation plan",
                )
            )
        return conflicts

    async def _detect_missing_and_disabled(
        self, target_ids: list[str]
    ) -> list[RolloutConflict]:
        """Detect targets that do not exist or are disabled, sorted by target_id."""
        conflicts: list[RolloutConflict] = []
        for tid in sorted(target_ids):
            target = await self._target_store.get(tid)
            if target is None:
                conflicts.append(
                    RolloutConflict(
                        conflict_id=f"frc_missing_target_{tid}",
                        conflict_type=RolloutConflictType.MISSING_TARGET,
                        severity=RolloutConflictSeverity.ERROR,
                        target_id=tid,
                        message=f"Target '{tid}' not found",
                    )
                )
            elif target.status == FederatedTargetStatus.DISABLED:
                conflicts.append(
                    RolloutConflict(
                        conflict_id=f"frc_disabled_target_{tid}",
                        conflict_type=RolloutConflictType.DISABLED_TARGET,
                        severity=RolloutConflictSeverity.ERROR,
                        target_id=tid,
                        message=f"Target '{tid}' is disabled",
                    )
                )
        return conflicts

    async def _detect_active_federation_conflicts(
        self,
        target_ids: list[str],
        current_federation_id: str,
    ) -> list[RolloutConflict]:
        """Detect targets already claimed by another ACTIVE federation.

        Sorted by (existing_federation_id, target_id).
        """
        active_plans = await self._federation_store.list(
            status=FederatedRolloutPlanStatus.ACTIVE
        )
        # Map target_id -> list of existing federation_ids
        target_to_federations: dict[str, list[str]] = {}
        for plan in active_plans:
            if plan.federation_id == current_federation_id:
                continue
            plan_target_ids = self._effective_target_ids(plan)
            for tid in plan_target_ids:
                if tid in set(target_ids):
                    target_to_federations.setdefault(tid, []).append(plan.federation_id)

        entries: list[tuple[str, str]] = []
        for tid, fed_ids in target_to_federations.items():
            for fid in fed_ids:
                entries.append((tid, fid))
        entries.sort(key=lambda e: (e[1], e[0]))

        conflicts: list[RolloutConflict] = []
        for tid, fid in entries:
            conflicts.append(
                RolloutConflict(
                    conflict_id=f"frc_target_already_active_{tid}_{fid}",
                    conflict_type=RolloutConflictType.TARGET_ALREADY_ACTIVE,
                    severity=RolloutConflictSeverity.ERROR,
                    target_id=tid,
                    existing_federation_id=fid,
                    message=f"Target '{tid}' is already active in federation '{fid}'",
                )
            )
        return conflicts

    async def _detect_rollout_conflicts(
        self,
        target_ids: list[str],
        bundle_id: str,
    ) -> list[RolloutConflict]:
        """Detect environment/ring and bundle conflicts with active rollouts.

        Sorted by (rollout_id, target_id).
        """
        active_rollouts = await self._rollout_store.list(  # type: ignore[union-attr]
            status=None,
        )
        # Only consider active rollouts
        from agent_app.governance.policy_rollout import RolloutPlanStatus

        active_rollouts = [
            r for r in active_rollouts if r.status == RolloutPlanStatus.ACTIVE
        ]

        # Build lookup: (environment, ring_name) -> list[rollout]
        env_ring_to_rollouts: dict[tuple[str, str | None], list] = {}
        for rollout in active_rollouts:
            for step in rollout.steps:
                key = (step.environment, step.ring_name)
                env_ring_to_rollouts.setdefault(key, []).append(rollout)

        # For each target, check if any active rollout covers the same env/ring
        entries: list[tuple[str, str, object]] = []  # (target_id, rollout_id, rollout)
        for tid in target_ids:
            target = await self._target_store.get(tid)
            if target is None:
                # Already reported as missing; skip
                continue
            key = (target.environment, target.ring_name)
            for rollout in env_ring_to_rollouts.get(key, []):
                entries.append((tid, rollout.rollout_id, rollout))

        # Deduplicate (target_id, rollout_id) pairs
        seen: set[tuple[str, str]] = set()
        unique_entries: list[tuple[str, str, object]] = []
        for tid, rid, rollout in entries:
            if (tid, rid) not in seen:
                seen.add((tid, rid))
                unique_entries.append((tid, rid, rollout))
        unique_entries.sort(key=lambda e: (e[1], e[0]))

        conflicts: list[RolloutConflict] = []
        for tid, rid, rollout in unique_entries:
            # Environment/ring conflict (ERROR)
            conflicts.append(
                RolloutConflict(
                    conflict_id=f"frc_environment_ring_conflict_{tid}_{rid}",
                    conflict_type=RolloutConflictType.ENVIRONMENT_RING_CONFLICT,
                    severity=RolloutConflictSeverity.ERROR,
                    target_id=tid,
                    existing_rollout_id=rid,
                    message=(
                        f"Target '{tid}' environment/ring overlaps with active "
                        f"rollout '{rid}'"
                    ),
                )
            )
            # Bundle conflict (WARNING) -- only when bundles differ
            if rollout.bundle_id != bundle_id:  # type: ignore[union-attr]
                conflicts.append(
                    RolloutConflict(
                        conflict_id=f"frc_bundle_conflict_{tid}_{rid}",
                        conflict_type=RolloutConflictType.BUNDLE_CONFLICT,
                        severity=RolloutConflictSeverity.WARNING,
                        target_id=tid,
                        existing_rollout_id=rid,
                        message=(
                            f"Target '{tid}' active rollout '{rid}' uses a "
                            f"different bundle ('{rollout.bundle_id}' vs '{bundle_id}')"  # type: ignore[union-attr]
                        ),
                    )
                )
        return conflicts
