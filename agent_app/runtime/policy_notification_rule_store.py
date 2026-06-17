"""Policy notification rule store -- persists PolicyNotificationRule instances with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_notification import (
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class PolicyNotificationRuleStore(Protocol):
    """Protocol for persisting policy notification rules."""

    async def create(self, rule: PolicyNotificationRule) -> PolicyNotificationRule: ...
    async def get(self, rule_id: str) -> PolicyNotificationRule | None: ...
    async def list(
        self,
        status: PolicyNotificationRuleStatus | None = None,
    ) -> list[PolicyNotificationRule]: ...
    async def enable(self, rule_id: str) -> PolicyNotificationRule: ...
    async def disable(self, rule_id: str) -> PolicyNotificationRule: ...


class InMemoryPolicyNotificationRuleStore:
    """In-memory policy notification rule store."""

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
            raise KeyError(f"Policy notification rule '{rule_id}' not found")
        rule.status = PolicyNotificationRuleStatus.ENABLED
        return rule

    async def disable(self, rule_id: str) -> PolicyNotificationRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Policy notification rule '{rule_id}' not found")
        rule.status = PolicyNotificationRuleStatus.DISABLED
        return rule


class SQLitePolicyNotificationRuleStore:
    """SQLite-backed policy notification rule store."""

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
                source_types_json TEXT,
                channels_json TEXT,
                title_template TEXT,
                body_template TEXT,
                metadata_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pnr_status ON policy_notification_rules(status);
        """)
        self._conn.commit()

    async def create(self, rule: PolicyNotificationRule) -> PolicyNotificationRule:
        event_types_json = json.dumps(rule.event_types)
        source_types_json = json.dumps(rule.source_types)
        channels_json = json.dumps(rule.channels)
        metadata_json = json.dumps(rule.metadata)
        self._conn.execute(
            """INSERT INTO policy_notification_rules
               (rule_id, name, event_types_json, severity, status,
                source_types_json, channels_json, title_template,
                body_template, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule.rule_id,
                rule.name,
                event_types_json,
                rule.severity.value,
                rule.status.value,
                source_types_json,
                channels_json,
                rule.title_template,
                rule.body_template,
                metadata_json,
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
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_notification_rules{where} ORDER BY rule_id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_rule(row) for row in rows]

    async def enable(self, rule_id: str) -> PolicyNotificationRule:
        row = self._conn.execute(
            "SELECT * FROM policy_notification_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Policy notification rule '{rule_id}' not found")
        self._conn.execute(
            "UPDATE policy_notification_rules SET status=? WHERE rule_id=?",
            (PolicyNotificationRuleStatus.ENABLED.value, rule_id),
        )
        self._conn.commit()
        rule = self._row_to_rule(row)
        rule.status = PolicyNotificationRuleStatus.ENABLED
        return rule

    async def disable(self, rule_id: str) -> PolicyNotificationRule:
        row = self._conn.execute(
            "SELECT * FROM policy_notification_rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Policy notification rule '{rule_id}' not found")
        self._conn.execute(
            "UPDATE policy_notification_rules SET status=? WHERE rule_id=?",
            (PolicyNotificationRuleStatus.DISABLED.value, rule_id),
        )
        self._conn.commit()
        rule = self._row_to_rule(row)
        rule.status = PolicyNotificationRuleStatus.DISABLED
        return rule

    def _row_to_rule(self, row: sqlite3.Row) -> PolicyNotificationRule:
        data = dict(row)
        data["severity"] = PolicyNotificationSeverity(data["severity"])
        data["status"] = PolicyNotificationRuleStatus(data["status"])
        # Parse event_types_json
        event_types_json = data.pop("event_types_json", None)
        if event_types_json:
            data["event_types"] = json.loads(event_types_json)
        else:
            data.pop("event_types", None)
        # Parse source_types_json
        source_types_json = data.pop("source_types_json", None)
        if source_types_json:
            data["source_types"] = json.loads(source_types_json)
        else:
            data.pop("source_types", None)
        # Parse channels_json
        channels_json = data.pop("channels_json", None)
        if channels_json:
            data["channels"] = json.loads(channels_json)
        else:
            data.pop("channels", None)
        # Parse metadata_json
        metadata_json = data.pop("metadata_json", None)
        if metadata_json:
            data["metadata"] = json.loads(metadata_json)
        else:
            data.pop("metadata", None)
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
    raise ValueError(f"Unknown policy notification rule store type '{store_type}'. Supported: 'memory', 'sqlite'.")
