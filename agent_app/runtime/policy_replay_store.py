"""Policy replay result store.

Phase 27: persistence for policy replay results.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent_app.governance.policy_replay import PolicyReplayResult, PolicyReplayStore


class InMemoryPolicyReplayStore:
    """In-memory policy replay store for testing and development.

    Stores replay results in a simple dict, preserving insertion order.
    """

    def __init__(self) -> None:
        self._results: dict[str, PolicyReplayResult] = {}
        self._order: list[str] = []

    async def save(self, result: PolicyReplayResult) -> PolicyReplayResult:
        """Persist a replay result."""
        rid = result.replay.replay_id
        if rid not in self._results:
            self._order.append(rid)
        self._results[rid] = result
        return result

    async def get(self, replay_id: str) -> PolicyReplayResult | None:
        """Retrieve a replay result by ID. Returns None if not found."""
        return self._results.get(replay_id)

    async def list(self, limit: int = 50) -> list[Any]:
        """List recent replay runs (most recent first), returning the run summary."""
        from agent_app.governance.policy_replay import PolicyReplayRun
        ids = list(reversed(self._order[-limit:]))
        runs: list[PolicyReplayRun] = []
        for rid in ids:
            r = self._results.get(rid)
            if r:
                runs.append(r.replay)
        return runs
