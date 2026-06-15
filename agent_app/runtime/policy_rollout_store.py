"""Rollout plan store -- persists RolloutPlan instances with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class RolloutPlanStore(Protocol):
    """Protocol for persisting rollout plans."""

    async def create(self, plan: RolloutPlan) -> RolloutPlan: ...
    async def get(self, rollout_id: str) -> RolloutPlan | None: ...
    async def update(self, plan: RolloutPlan) -> RolloutPlan: ...
    async def list(
        self,
        status: RolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[RolloutPlan]: ...


class InMemoryRolloutPlanStore:
    """In-memory rollout plan store."""

    def __init__(self) -> None:
        self._plans: dict[str, RolloutPlan] = {}

    async def create(self, plan: RolloutPlan) -> RolloutPlan:
        self._plans[plan.rollout_id] = plan
        return plan

    async def get(self, rollout_id: str) -> RolloutPlan | None:
        return self._plans.get(rollout_id)

    async def update(self, plan: RolloutPlan) -> RolloutPlan:
        self._plans[plan.rollout_id] = plan
        return plan

    async def list(
        self,
        status: RolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[RolloutPlan]:
        results: list[RolloutPlan] = []
        for plan in self._plans.values():
            if status is not None and plan.status != status:
                continue
            if bundle_id is not None and plan.bundle_id != bundle_id:
                continue
            results.append(plan)
        return results


class SQLiteRolloutPlanStore:
    """SQLite-backed rollout plan store."""

    def __init__(self, db_path: str = ".agent_app/policy_rollout_plans.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_rollout_plans (
                rollout_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                status TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rop_status ON policy_rollout_plans(status);
            CREATE INDEX IF NOT EXISTS idx_rop_bundle ON policy_rollout_plans(bundle_id);
        """)
        self._conn.commit()

    async def create(self, plan: RolloutPlan) -> RolloutPlan:
        self._conn.execute(
            """INSERT INTO policy_rollout_plans
               (rollout_id, name, bundle_id, status, steps_json,
                created_by, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.rollout_id,
                plan.name,
                plan.bundle_id,
                plan.status.value,
                json.dumps([s.model_dump(mode="json") for s in plan.steps]),
                plan.created_by,
                plan.reason,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return plan

    async def get(self, rollout_id: str) -> RolloutPlan | None:
        row = self._conn.execute(
            "SELECT * FROM policy_rollout_plans WHERE rollout_id=?", (rollout_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_plan(row)

    async def update(self, plan: RolloutPlan) -> RolloutPlan:
        existing = self._conn.execute(
            "SELECT rollout_id FROM policy_rollout_plans WHERE rollout_id=?",
            (plan.rollout_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"Rollout plan '{plan.rollout_id}' not found")
        self._conn.execute(
            """UPDATE policy_rollout_plans
               SET name=?, bundle_id=?, status=?, steps_json=?,
                   created_by=?, reason=?, created_at=?, updated_at=?
               WHERE rollout_id=?""",
            (
                plan.name,
                plan.bundle_id,
                plan.status.value,
                json.dumps([s.model_dump(mode="json") for s in plan.steps]),
                plan.created_by,
                plan.reason,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
                plan.rollout_id,
            ),
        )
        self._conn.commit()
        return plan

    async def list(
        self,
        status: RolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[RolloutPlan]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if bundle_id is not None:
            clauses.append("bundle_id=?")
            params.append(bundle_id)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_rollout_plans{where} ORDER BY created_at ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def _row_to_plan(self, row: sqlite3.Row) -> RolloutPlan:
        data = dict(row)
        data["status"] = RolloutPlanStatus(data["status"])
        steps_data = json.loads(data.pop("steps_json"))
        data["steps"] = [RolloutStep(**s) for s in steps_data]
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return RolloutPlan(**data)

    def close(self) -> None:
        self._conn.close()


def create_rollout_plan_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RolloutPlanStore:
    if store_type == "memory":
        return InMemoryRolloutPlanStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLiteRolloutPlanStore(db_path=db_path)
    raise ValueError(f"Unknown rollout store type '{store_type}'. Supported: 'memory', 'sqlite'.")
