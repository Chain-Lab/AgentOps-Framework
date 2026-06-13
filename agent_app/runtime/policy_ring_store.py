"""Release ring store -- persists release ring state across environments."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_ring import ReleaseRing, ReleaseRingStatus

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class ReleaseRingStore(Protocol):
    """Protocol for persisting release ring states."""
    async def create(self, ring: ReleaseRing) -> ReleaseRing: ...
    async def get(self, ring_id: str) -> ReleaseRing | None: ...
    async def get_by_name(self, environment: str, name: str) -> ReleaseRing | None: ...
    async def list(self, environment: str | None = None) -> list[ReleaseRing]: ...
    async def set_default(self, environment: str, ring_name: str) -> ReleaseRing: ...
    async def disable(self, environment: str, ring_name: str) -> ReleaseRing: ...
    async def enable(self, environment: str, ring_name: str) -> ReleaseRing: ...


class InMemoryReleaseRingStore:
    """In-memory release ring store."""
    def __init__(self) -> None:
        self._rings: dict[str, ReleaseRing] = {}

    async def create(self, ring: ReleaseRing) -> ReleaseRing:
        self._rings[ring.ring_id] = ring
        return ring

    async def get(self, ring_id: str) -> ReleaseRing | None:
        return self._rings.get(ring_id)

    async def get_by_name(self, environment: str, name: str) -> ReleaseRing | None:
        for ring in self._rings.values():
            if ring.environment == environment and ring.name == name:
                return ring
        return None

    async def list(self, environment: str | None = None) -> list[ReleaseRing]:
        rings = list(self._rings.values())
        if environment is not None:
            rings = [r for r in rings if r.environment == environment]
        return rings

    async def set_default(self, environment: str, ring_name: str) -> ReleaseRing:
        # Clear previous default for this environment
        for ring in self._rings.values():
            if ring.environment == environment and ring.is_default:
                self._rings[ring.ring_id] = ring.model_copy(update={"is_default": False, "updated_at": datetime.now(timezone.utc)})
        # Set new default
        target = await self.get_by_name(environment, ring_name)
        if target is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        updated = target.model_copy(update={"is_default": True, "updated_at": datetime.now(timezone.utc)})
        self._rings[updated.ring_id] = updated
        return updated

    async def disable(self, environment: str, ring_name: str) -> ReleaseRing:
        target = await self.get_by_name(environment, ring_name)
        if target is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        updated = target.model_copy(update={"status": ReleaseRingStatus.DISABLED, "updated_at": datetime.now(timezone.utc)})
        self._rings[updated.ring_id] = updated
        return updated

    async def enable(self, environment: str, ring_name: str) -> ReleaseRing:
        target = await self.get_by_name(environment, ring_name)
        if target is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        updated = target.model_copy(update={"status": ReleaseRingStatus.ENABLED, "updated_at": datetime.now(timezone.utc)})
        self._rings[updated.ring_id] = updated
        return updated


class SQLiteReleaseRingStore:
    """SQLite-backed release ring store."""
    def __init__(self, db_path: str = ".agent_app/policy_release_rings.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_release_rings (
                ring_id TEXT PRIMARY KEY,
                environment TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(environment, name)
            );
        """)
        self._conn.commit()

    async def create(self, ring: ReleaseRing) -> ReleaseRing:
        self._conn.execute(
            """INSERT OR REPLACE INTO policy_release_rings
               (ring_id, environment, name, description, status, is_default, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ring.ring_id, ring.environment, ring.name, ring.description,
             ring.status.value, int(ring.is_default),
             ring.created_at.isoformat(), ring.updated_at.isoformat()),
        )
        self._conn.commit()
        return ring

    async def get(self, ring_id: str) -> ReleaseRing | None:
        row = self._conn.execute(
            "SELECT * FROM policy_release_rings WHERE ring_id=?", (ring_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ring(row)

    async def get_by_name(self, environment: str, name: str) -> ReleaseRing | None:
        row = self._conn.execute(
            "SELECT * FROM policy_release_rings WHERE environment=? AND name=?",
            (environment, name),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ring(row)

    async def list(self, environment: str | None = None) -> list[ReleaseRing]:
        if environment is not None:
            rows = self._conn.execute(
                "SELECT * FROM policy_release_rings WHERE environment=? ORDER BY name",
                (environment,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM policy_release_rings ORDER BY environment, name"
            ).fetchall()
        return [self._row_to_ring(row) for row in rows]

    async def set_default(self, environment: str, ring_name: str) -> ReleaseRing:
        # Clear previous default for this environment
        self._conn.execute(
            "UPDATE policy_release_rings SET is_default=0, updated_at=? WHERE environment=? AND is_default=1",
            (datetime.now(timezone.utc).isoformat(), environment),
        )
        # Set new default
        now = datetime.now(timezone.utc)
        self._conn.execute(
            "UPDATE policy_release_rings SET is_default=1, updated_at=? WHERE environment=? AND name=?",
            (now.isoformat(), environment, ring_name),
        )
        self._conn.commit()
        result = await self.get_by_name(environment, ring_name)
        if result is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        return result

    async def disable(self, environment: str, ring_name: str) -> ReleaseRing:
        now = datetime.now(timezone.utc)
        self._conn.execute(
            "UPDATE policy_release_rings SET status=?, updated_at=? WHERE environment=? AND name=?",
            (ReleaseRingStatus.DISABLED.value, now.isoformat(), environment, ring_name),
        )
        self._conn.commit()
        result = await self.get_by_name(environment, ring_name)
        if result is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        return result

    async def enable(self, environment: str, ring_name: str) -> ReleaseRing:
        now = datetime.now(timezone.utc)
        self._conn.execute(
            "UPDATE policy_release_rings SET status=?, updated_at=? WHERE environment=? AND name=?",
            (ReleaseRingStatus.ENABLED.value, now.isoformat(), environment, ring_name),
        )
        self._conn.commit()
        result = await self.get_by_name(environment, ring_name)
        if result is None:
            raise ValueError(f"Ring '{ring_name}' not found in environment '{environment}'")
        return result

    def _row_to_ring(self, row: sqlite3.Row) -> ReleaseRing:
        data = dict(row)
        data["status"] = ReleaseRingStatus(data["status"])
        data["is_default"] = bool(data["is_default"])
        for ts_field in ("created_at", "updated_at"):
            data[ts_field] = datetime.fromisoformat(data[ts_field])
        return ReleaseRing(**data)

    def close(self) -> None:
        self._conn.close()


def create_release_ring_store(store_type: str = "memory", db_path: str | None = None) -> ReleaseRingStore:
    if store_type == "memory":
        return InMemoryReleaseRingStore()
    if store_type == "sqlite":
        return SQLiteReleaseRingStore(db_path=db_path or ".agent_app/policy_release_rings.db")
    raise ValueError(f"Unknown ring store type '{store_type}'. Supported: 'memory', 'sqlite'.")
