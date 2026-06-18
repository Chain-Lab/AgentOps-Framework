"""Federated rollout target and plan stores -- persists federation models with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_rollout import RolloutStep
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutWave,
    FederatedTargetStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class FederatedRolloutTargetStore(Protocol):
    """Protocol for persisting federated rollout targets."""

    async def create(self, target: FederatedRolloutTarget) -> FederatedRolloutTarget: ...
    async def get(self, target_id: str) -> FederatedRolloutTarget | None: ...
    async def list(
        self,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        status: FederatedTargetStatus | None = None,
    ) -> list[FederatedRolloutTarget]: ...
    async def enable(self, target_id: str) -> FederatedRolloutTarget: ...
    async def disable(self, target_id: str) -> FederatedRolloutTarget: ...


@runtime_checkable
class FederatedRolloutPlanStore(Protocol):
    """Protocol for persisting federated rollout plans."""

    async def create(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan: ...
    async def get(self, federation_id: str) -> FederatedRolloutPlan | None: ...
    async def update(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan: ...
    async def list(
        self,
        status: FederatedRolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[FederatedRolloutPlan]: ...


# ---------------------------------------------------------------------------
# In-memory target store
# ---------------------------------------------------------------------------


class InMemoryFederatedRolloutTargetStore:
    """In-memory federated rollout target store."""

    def __init__(self) -> None:
        self._targets: dict[str, FederatedRolloutTarget] = {}

    async def create(self, target: FederatedRolloutTarget) -> FederatedRolloutTarget:
        self._targets[target.target_id] = target
        return target

    async def get(self, target_id: str) -> FederatedRolloutTarget | None:
        return self._targets.get(target_id)

    async def list(
        self,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        status: FederatedTargetStatus | None = None,
    ) -> list[FederatedRolloutTarget]:
        results: list[FederatedRolloutTarget] = []
        for target in self._targets.values():
            if tenant_id is not None and target.tenant_id != tenant_id:
                continue
            if environment is not None and target.environment != environment:
                continue
            if ring_name is not None and target.ring_name != ring_name:
                continue
            if status is not None and target.status != status:
                continue
            results.append(target)
        results.sort(key=lambda t: (t.created_at, t.target_id))
        return results

    async def enable(self, target_id: str) -> FederatedRolloutTarget:
        target = self._targets.get(target_id)
        if target is None:
            raise KeyError(f"Federated target '{target_id}' not found")
        updated = target.model_copy(update={"status": FederatedTargetStatus.ENABLED})
        self._targets[target_id] = updated
        return updated

    async def disable(self, target_id: str) -> FederatedRolloutTarget:
        target = self._targets.get(target_id)
        if target is None:
            raise KeyError(f"Federated target '{target_id}' not found")
        updated = target.model_copy(update={"status": FederatedTargetStatus.DISABLED})
        self._targets[target_id] = updated
        return updated


# ---------------------------------------------------------------------------
# In-memory plan store
# ---------------------------------------------------------------------------


class InMemoryFederatedRolloutPlanStore:
    """In-memory federated rollout plan store."""

    def __init__(self) -> None:
        self._plans: dict[str, FederatedRolloutPlan] = {}

    async def create(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan:
        self._plans[plan.federation_id] = plan
        return plan

    async def get(self, federation_id: str) -> FederatedRolloutPlan | None:
        return self._plans.get(federation_id)

    async def update(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan:
        if plan.federation_id not in self._plans:
            raise KeyError(f"Federated plan '{plan.federation_id}' not found")
        self._plans[plan.federation_id] = plan
        return plan

    async def list(
        self,
        status: FederatedRolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[FederatedRolloutPlan]:
        results: list[FederatedRolloutPlan] = []
        for plan in self._plans.values():
            if status is not None and plan.status != status:
                continue
            if bundle_id is not None and plan.bundle_id != bundle_id:
                continue
            results.append(plan)
        results.sort(key=lambda p: (p.created_at, p.federation_id))
        return results


# ---------------------------------------------------------------------------
# SQLite target store
# ---------------------------------------------------------------------------


class SQLiteFederatedRolloutTargetStore:
    """SQLite-backed federated rollout target store."""

    def __init__(self, db_path: str = ".agent_app/federated_rollout_targets.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federated_rollout_targets (
                target_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tenant_id TEXT,
                environment TEXT NOT NULL,
                ring_name TEXT,
                region TEXT,
                labels_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_frt_tenant ON federated_rollout_targets(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_frt_environment ON federated_rollout_targets(environment);
            CREATE INDEX IF NOT EXISTS idx_frt_ring ON federated_rollout_targets(ring_name);
            CREATE INDEX IF NOT EXISTS idx_frt_status ON federated_rollout_targets(status);
        """)
        self._conn.commit()

    async def create(self, target: FederatedRolloutTarget) -> FederatedRolloutTarget:
        self._conn.execute(
            """INSERT INTO federated_rollout_targets
               (target_id, name, tenant_id, environment, ring_name, region,
                labels_json, status, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target.target_id,
                target.name,
                target.tenant_id,
                target.environment,
                target.ring_name,
                target.region,
                json.dumps(target.labels),
                target.status.value,
                json.dumps(target.metadata),
                target.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return target

    async def get(self, target_id: str) -> FederatedRolloutTarget | None:
        row = self._conn.execute(
            "SELECT * FROM federated_rollout_targets WHERE target_id=?", (target_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_target(row)

    async def list(
        self,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        status: FederatedTargetStatus | None = None,
    ) -> list[FederatedRolloutTarget]:
        clauses: list[str] = []
        params: list[object] = []
        if tenant_id is not None:
            clauses.append("tenant_id=?")
            params.append(tenant_id)
        if environment is not None:
            clauses.append("environment=?")
            params.append(environment)
        if ring_name is not None:
            clauses.append("ring_name=?")
            params.append(ring_name)
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM federated_rollout_targets{where} ORDER BY created_at ASC, target_id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_target(row) for row in rows]

    async def enable(self, target_id: str) -> FederatedRolloutTarget:
        existing = self._conn.execute(
            "SELECT target_id FROM federated_rollout_targets WHERE target_id=?",
            (target_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"Federated target '{target_id}' not found")
        self._conn.execute(
            "UPDATE federated_rollout_targets SET status=? WHERE target_id=?",
            (FederatedTargetStatus.ENABLED.value, target_id),
        )
        self._conn.commit()
        return await self.get(target_id)  # type: ignore[return-value]

    async def disable(self, target_id: str) -> FederatedRolloutTarget:
        existing = self._conn.execute(
            "SELECT target_id FROM federated_rollout_targets WHERE target_id=?",
            (target_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"Federated target '{target_id}' not found")
        self._conn.execute(
            "UPDATE federated_rollout_targets SET status=? WHERE target_id=?",
            (FederatedTargetStatus.DISABLED.value, target_id),
        )
        self._conn.commit()
        return await self.get(target_id)  # type: ignore[return-value]

    def _row_to_target(self, row: sqlite3.Row) -> FederatedRolloutTarget:
        data = dict(row)
        data["labels"] = json.loads(data.pop("labels_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["status"] = FederatedTargetStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return FederatedRolloutTarget(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# SQLite plan store
# ---------------------------------------------------------------------------


class SQLiteFederatedRolloutPlanStore:
    """SQLite-backed federated rollout plan store."""

    def __init__(self, db_path: str = ".agent_app/federated_rollout_plans.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federated_rollout_plans (
                federation_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                target_ids_json TEXT NOT NULL DEFAULT '[]',
                waves_json TEXT NOT NULL DEFAULT '[]',
                executions_json TEXT NOT NULL DEFAULT '[]',
                rollout_template_steps_json TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_frp_status ON federated_rollout_plans(status);
            CREATE INDEX IF NOT EXISTS idx_frp_bundle ON federated_rollout_plans(bundle_id);
        """)
        self._conn.commit()

    async def create(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan:
        self._conn.execute(
            """INSERT INTO federated_rollout_plans
               (federation_id, name, bundle_id, strategy, status,
                target_ids_json, waves_json, executions_json,
                rollout_template_steps_json, created_by, reason,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.federation_id,
                plan.name,
                plan.bundle_id,
                plan.strategy.value,
                plan.status.value,
                json.dumps(plan.target_ids),
                json.dumps([w.model_dump(mode="json") for w in plan.waves]),
                json.dumps([e.model_dump(mode="json") for e in plan.executions]),
                json.dumps([s.model_dump(mode="json") for s in plan.rollout_template_steps]),
                plan.created_by,
                plan.reason,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return plan

    async def get(self, federation_id: str) -> FederatedRolloutPlan | None:
        row = self._conn.execute(
            "SELECT * FROM federated_rollout_plans WHERE federation_id=?", (federation_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_plan(row)

    async def update(self, plan: FederatedRolloutPlan) -> FederatedRolloutPlan:
        existing = self._conn.execute(
            "SELECT federation_id FROM federated_rollout_plans WHERE federation_id=?",
            (plan.federation_id,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"Federated plan '{plan.federation_id}' not found")
        self._conn.execute(
            """UPDATE federated_rollout_plans
               SET name=?, bundle_id=?, strategy=?, status=?,
                   target_ids_json=?, waves_json=?, executions_json=?,
                   rollout_template_steps_json=?, created_by=?, reason=?,
                   created_at=?, updated_at=?
               WHERE federation_id=?""",
            (
                plan.name,
                plan.bundle_id,
                plan.strategy.value,
                plan.status.value,
                json.dumps(plan.target_ids),
                json.dumps([w.model_dump(mode="json") for w in plan.waves]),
                json.dumps([e.model_dump(mode="json") for e in plan.executions]),
                json.dumps([s.model_dump(mode="json") for s in plan.rollout_template_steps]),
                plan.created_by,
                plan.reason,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
                plan.federation_id,
            ),
        )
        self._conn.commit()
        return plan

    async def list(
        self,
        status: FederatedRolloutPlanStatus | None = None,
        bundle_id: str | None = None,
    ) -> list[FederatedRolloutPlan]:
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
        sql = f"SELECT * FROM federated_rollout_plans{where} ORDER BY created_at ASC, federation_id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def _row_to_plan(self, row: sqlite3.Row) -> FederatedRolloutPlan:
        data = dict(row)
        data["status"] = FederatedRolloutPlanStatus(data["status"])
        data["strategy"] = data["strategy"]  # already a string, model handles StrEnum
        data["target_ids"] = json.loads(data.pop("target_ids_json"))
        data["waves"] = [FederatedRolloutWave(**w) for w in json.loads(data.pop("waves_json"))]
        data["executions"] = [
            FederatedRolloutTargetExecution(**e)
            for e in json.loads(data.pop("executions_json"))
        ]
        data["rollout_template_steps"] = [
            RolloutStep(**s)
            for s in json.loads(data.pop("rollout_template_steps_json"))
        ]
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return FederatedRolloutPlan(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def create_federated_rollout_target_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederatedRolloutTargetStore:
    if store_type == "memory":
        return InMemoryFederatedRolloutTargetStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLiteFederatedRolloutTargetStore(db_path=db_path)
    raise ValueError(f"Unknown federated target store type '{store_type}'. Supported: 'memory', 'sqlite'.")


def create_federated_rollout_plan_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> FederatedRolloutPlanStore:
    if store_type == "memory":
        return InMemoryFederatedRolloutPlanStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLiteFederatedRolloutPlanStore(db_path=db_path)
    raise ValueError(f"Unknown federated plan store type '{store_type}'. Supported: 'memory', 'sqlite'.")
