"""Policy release service — orchestrates bundle creation, gate evaluation, promote, and rollback.

Phase 29: provides the release safety gate workflow.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PolicyReleaseService:
    """Orchestrates the policy release lifecycle.

    Coordinates policy bundle stores, replay runners, gate evaluators,
    and gate stores to provide:
    - Bundle creation with config hashing
    - Gate evaluation against replay results
    - Promote (requires passing gate by default)
    - Rollback to a previous bundle

    Args:
        bundle_store: Store for policy bundles.
        replay_runner: Runner for policy replays.
        replay_store: Optional store for persisting replay results.
        gate_evaluator: Evaluator for release gate rules.
        gate_store: Store for persisting gate results.
    """

    def __init__(
        self,
        bundle_store: Any,
        replay_runner: Any,
        replay_store: Any,
        gate_evaluator: Any,
        gate_store: Any,
    ) -> None:
        self._bundle_store = bundle_store
        self._replay_runner = replay_runner
        self._replay_store = replay_store
        self._gate_evaluator = gate_evaluator
        self._gate_store = gate_store

    async def create_bundle(
        self,
        name: str,
        version: str,
        config_path: str,
        description: str | None = None,
        created_by: str | None = None,
    ) -> Any:
        """Create a new policy bundle.

        Reads the config content, computes a hash, and stores the bundle
        as a DRAFT.

        Args:
            name: Human-readable bundle name.
            version: Semantic version string.
            config_path: Path to the config file.
            description: Optional description of changes.
            created_by: Identity of who created the bundle.

        Returns:
            The created PolicyBundle.
        """
        from agent_app.governance.policy_bundle import (
            PolicyBundle,
            PolicyBundleStatus,
            compute_config_hash,
        )

        # Read config content for hashing
        config_content = ""
        try:
            config_content = Path(config_path).read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            config_content = config_path

        config_hash = compute_config_hash(config_content)

        bundle = PolicyBundle(
            bundle_id=f"pb_{uuid.uuid4().hex[:12]}",
            name=name,
            version=version,
            status=PolicyBundleStatus.DRAFT,
            config_path=config_path,
            config_hash=config_hash,
            description=description,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        return await self._bundle_store.create(bundle)

    async def run_gate(
        self,
        bundle_id: str,
        limit: int | None = None,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        rule_id: str | None = None,
        created_by: str | None = None,
    ) -> Any:
        """Run a release gate evaluation for a bundle.

        Executes a replay with the given filters, evaluates the result
        against gate rules, and stores the gate result.

        Args:
            bundle_id: The bundle to evaluate.
            limit: Max decisions to replay.
            tenant_id: Filter by tenant.
            tool_name: Filter by tool name.
            rule_id: Filter by original rule name.
            created_by: Identity of who triggered the evaluation.

        Returns:
            PolicyGateResult with evaluation outcome.

        Raises:
            KeyError: If bundle_id not found.
        """
        from agent_app.governance.policy_bundle import PolicyBundle

        bundle = await self._bundle_store.get(bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        # Run replay with filters
        replay_result = await self._replay_runner.run_replay(
            limit=limit,
            tenant_id=tenant_id,
            tool_name=tool_name,
            rule_id=rule_id,
        )

        # Persist replay result if store available
        if self._replay_store is not None:
            await self._replay_store.save(replay_result)

        # Evaluate gate
        gate_result = await self._gate_evaluator.evaluate(
            bundle=bundle,
            replay_result=replay_result,
            created_by=created_by,
        )

        # Store gate result
        return await self._gate_store.save(gate_result)

    async def promote(
        self,
        bundle_id: str,
        promoted_by: str | None = None,
        require_passing_gate: bool = True,
    ) -> Any:
        """Promote a bundle to ACTIVE.

        By default, requires that the bundle has at least one latest
        PASSED gate result.

        Args:
            bundle_id: The bundle to promote.
            promoted_by: Identity of who is promoting.
            require_passing_gate: If True, block promotion if latest gate failed.

        Returns:
            The promoted PolicyBundle (status: ACTIVE).

        Raises:
            KeyError: If bundle_id not found.
            ValueError: If latest gate failed and require_passing_gate is True.
        """
        from agent_app.governance.policy_bundle import PolicyBundleStatus

        bundle = await self._bundle_store.get(bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        if require_passing_gate:
            # Check latest gate result for this bundle
            gate_results = await self._gate_store.list(bundle_id=bundle_id, limit=1)
            if gate_results:
                latest = gate_results[0]
                if not latest.passed:
                    raise ValueError(
                        f"Cannot promote bundle '{bundle_id}': "
                        f"latest gate result is {latest.status}. "
                        f"Gate result ID: {latest.gate_result_id}"
                    )

        # Promote: activate this bundle (archives previous active)
        return await self._bundle_store.activate(bundle_id)

    async def rollback(
        self,
        target_bundle_id: str,
        rolled_back_by: str | None = None,
    ) -> Any:
        """Rollback to a previous bundle.

        Re-activates the target bundle, archiving the current ACTIVE bundle.

        Args:
            target_bundle_id: The bundle to roll back to.
            rolled_back_by: Identity of who is rolling back.

        Returns:
            The re-activated PolicyBundle (status: ACTIVE).

        Raises:
            KeyError: If target_bundle_id not found.
        """
        bundle = await self._bundle_store.get(target_bundle_id)
        if bundle is None:
            raise KeyError(
                f"Bundle '{target_bundle_id}' not found in policy bundle store."
            )

        # Rollback = activate the target bundle
        return await self._bundle_store.activate(target_bundle_id)

    @property
    def bundle_store(self) -> Any:
        """Access the underlying bundle store (for console integration)."""
        return self._bundle_store

    @property
    def gate_store(self) -> Any:
        """Access the underlying gate store (for console integration)."""
        return self._gate_store
