"""Runtime policy store -- persists RuntimePolicyRule instances.

Phase 38: InMemory + SQLite implementations with Protocol.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.governance.policy_rollout_approval import RolloutApprovalPolicy
from agent_app.governance.runtime_policy import (
    RuntimePolicyEffect,
    RuntimePolicyRule,
    RuntimePolicyRuleStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class RuntimePolicyStore(Protocol):
    """Protocol for persisting runtime policy rules."""

    async def create(self, rule: RuntimePolicyRule) -> RuntimePolicyRule: ...
    async def get(self, rule_id: str) -> RuntimePolicyRule | None: ...
    async def list(
        self,
        action_type: PolicyActionType | None = None,
        status: RuntimePolicyRuleStatus | None = None,
    ) -> list[RuntimePolicyRule]: ...
    async def enable(self, rule_id: str) -> RuntimePolicyRule: ...
    async def disable(self, rule_id: str) -> RuntimePolicyRule: ...


class InMemoryRuntimePolicyStore:
    """In-memory runtime policy rule store."""

    def __init__(self) -> None:
        self._rules: dict[str, RuntimePolicyRule] = {}

    async def create(self, rule: RuntimePolicyRule) -> RuntimePolicyRule:
        if rule.rule_id in self._rules:
            raise ValueError(f"Rule '{rule.rule_id}' already exists")
        self._rules[rule.rule_id] = rule.model_copy()
        return rule

    async def get(self, rule_id: str) -> RuntimePolicyRule | None:
        rule = self._rules.get(rule_id)
        return rule.model_copy() if rule is not None else None

    async def list(
        self,
        action_type: PolicyActionType | None = None,
        status: RuntimePolicyRuleStatus | None = None,
    ) -> list[RuntimePolicyRule]:
        results: list[RuntimePolicyRule] = []
        for rule in self._rules.values():
            if action_type is not None and rule.action_type != action_type:
                continue
            if status is not None and rule.status != status:
                continue
            results.append(rule.model_copy())
        return results

    async def enable(self, rule_id: str) -> RuntimePolicyRule:
        if rule_id not in self._rules:
            raise KeyError(f"Rule '{rule_id}' not found")
        rule = self._rules[rule_id]
        updated = rule.model_copy(update={"status": RuntimePolicyRuleStatus.ENABLED})
        self._rules[rule_id] = updated
        return updated

    async def disable(self, rule_id: str) -> RuntimePolicyRule:
        if rule_id not in self._rules:
            raise KeyError(f"Rule '{rule_id}' not found")
        rule = self._rules[rule_id]
        updated = rule.model_copy(update={"status": RuntimePolicyRuleStatus.DISABLED})
        self._rules[rule_id] = updated
        return updated


class SQLiteRuntimePolicyStore:
    """SQLite-backed runtime policy rule store."""

    def __init__(self, db_path: str = ".agent_app/runtime_policy_rules.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_policy_rules (
                rule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                action_type TEXT NOT NULL,
                effect TEXT NOT NULL,
                status TEXT NOT NULL,
                tool_name TEXT,
                risk_level TEXT,
                required_permissions_json TEXT NOT NULL,
                required_roles_json TEXT NOT NULL,
                approval_policy_json TEXT,
                reason TEXT,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rpr_status ON runtime_policy_rules(status);
            CREATE INDEX IF NOT EXISTS idx_rpr_action_type ON runtime_policy_rules(action_type);
        """)
        self._conn.commit()

    async def create(self, rule: RuntimePolicyRule) -> RuntimePolicyRule:
        try:
            self._conn.execute(
                """INSERT INTO runtime_policy_rules
                   (rule_id, name, action_type, effect, status, tool_name, risk_level,
                    required_permissions_json, required_roles_json, approval_policy_json,
                    reason, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.rule_id,
                    rule.name,
                    rule.action_type.value,
                    rule.effect.value,
                    rule.status.value,
                    rule.tool_name,
                    rule.risk_level,
                    json.dumps(rule.required_permissions),
                    json.dumps(rule.required_roles),
                    json.dumps(rule.approval_policy.model_dump(mode="json")) if rule.approval_policy else None,
                    rule.reason,
                    json.dumps(rule.metadata),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Rule '{rule.rule_id}' already exists")
        return rule

    async def get(self, rule_id: str) -> RuntimePolicyRule | None:
        row = self._conn.execute(
            "SELECT * FROM runtime_policy_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_rule(row)

    async def list(
        self,
        action_type: PolicyActionType | None = None,
        status: RuntimePolicyRuleStatus | None = None,
    ) -> list[RuntimePolicyRule]:
        clauses: list[str] = []
        params: list[object] = []
        if action_type is not None:
            clauses.append("action_type=?")
            params.append(action_type.value)
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM runtime_policy_rules{where} ORDER BY rule_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_rule(row) for row in rows]

    async def enable(self, rule_id: str) -> RuntimePolicyRule:
        cursor = self._conn.execute(
            "UPDATE runtime_policy_rules SET status=? WHERE rule_id=?",
            (RuntimePolicyRuleStatus.ENABLED.value, rule_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Rule '{rule_id}' not found")
        return await self.get(rule_id)  # type: ignore[return-value]

    async def disable(self, rule_id: str) -> RuntimePolicyRule:
        cursor = self._conn.execute(
            "UPDATE runtime_policy_rules SET status=? WHERE rule_id=?",
            (RuntimePolicyRuleStatus.DISABLED.value, rule_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Rule '{rule_id}' not found")
        return await self.get(rule_id)  # type: ignore[return-value]

    def _row_to_rule(self, row: sqlite3.Row) -> RuntimePolicyRule:
        data = dict(row)
        data["action_type"] = PolicyActionType(data["action_type"])
        data["effect"] = RuntimePolicyEffect(data["effect"])
        data["status"] = RuntimePolicyRuleStatus(data["status"])
        data["required_permissions"] = json.loads(data.pop("required_permissions_json"))
        data["required_roles"] = json.loads(data.pop("required_roles_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        approval_json = data.pop("approval_policy_json")
        if approval_json is not None:
            data["approval_policy"] = RolloutApprovalPolicy(**json.loads(approval_json))
        else:
            data["approval_policy"] = None
        return RuntimePolicyRule(**data)

    def close(self) -> None:
        self._conn.close()


def create_runtime_policy_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RuntimePolicyStore:
    if store_type == "memory":
        return InMemoryRuntimePolicyStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLiteRuntimePolicyStore(db_path=db_path)
    raise ValueError(
        f"Unknown runtime policy store type '{store_type}'. Supported: 'memory', 'sqlite'."
    )
