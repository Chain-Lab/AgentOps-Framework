"""Policy bundle — versioned policy configuration bundles for release management.

Phase 29: versioned policy bundles with lifecycle management (draft → active → archived).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PolicyBundleStatus(str, Enum):
    """Lifecycle status of a policy bundle."""
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    ROLLED_BACK = "rolled_back"


class PolicyBundle(BaseModel):
    """A versioned policy configuration bundle.

    Attributes:
        bundle_id: Unique identifier (pb_ prefix).
        name: Human-readable name.
        version: Semantic version string.
        status: Current lifecycle status.
        config_path: Path to the config file this bundle was created from.
        config_hash: SHA-256 hash of the policy-relevant config content.
        policy_rules_hash: Hash of just the policy rules (if available).
        description: Optional description of changes.
        created_by: Identity of who created the bundle.
        created_at: When the bundle was created.
        activated_at: When the bundle was activated (if applicable).
        archived_at: When the bundle was archived (if applicable).
        metadata: Arbitrary metadata (rule counts, summaries, etc.).
    """

    bundle_id: str = Field(..., description="Unique bundle identifier (pb_ prefix)")
    name: str = Field(..., description="Human-readable bundle name")
    version: str = Field(..., description="Semantic version string")
    status: str = Field(default=PolicyBundleStatus.DRAFT, description="Lifecycle status")
    config_path: str | None = Field(default=None, description="Source config path")
    config_hash: str = Field(..., description="SHA-256 hash of policy config")
    policy_rules_hash: str | None = Field(default=None, description="Hash of policy rules")
    description: str | None = Field(default=None, description="Change description")
    created_by: str | None = Field(default=None, description="Creator identity")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    activated_at: datetime | None = Field(default=None, description="Activation timestamp")
    archived_at: datetime | None = Field(default=None, description="Archive timestamp")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary metadata"
    )


# ---------------------------------------------------------------------------
# Config hash helper
# ---------------------------------------------------------------------------

def compute_config_hash(content: str) -> str:
    """Compute a stable SHA-256 hash for config content.

    Args:
        content: The policy-relevant config content (e.g., YAML rules section).

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    canonical = json.dumps(
        {"content": content},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Bundle store protocol
# ---------------------------------------------------------------------------

class PolicyBundleStore(Protocol):
    """Protocol for persisting policy bundles."""

    async def create(self, bundle: PolicyBundle) -> PolicyBundle:
        """Create a new bundle. Overwrites if bundle_id exists."""
        ...

    async def get(self, bundle_id: str) -> PolicyBundle | None:
        """Retrieve a bundle by ID. Returns None if not found."""
        ...

    async def list(self, limit: int = 50) -> list[PolicyBundle]:
        """List bundles sorted by created_at descending."""
        ...

    async def get_active(self) -> PolicyBundle | None:
        """Get the currently active bundle. Returns None if none."""
        ...

    async def activate(self, bundle_id: str) -> PolicyBundle:
        """Activate a bundle, archiving any previously active bundle."""
        ...

    async def archive(self, bundle_id: str) -> PolicyBundle:
        """Archive a bundle."""
        ...


# ---------------------------------------------------------------------------
# InMemoryPolicyBundleStore
# ---------------------------------------------------------------------------

class InMemoryPolicyBundleStore:
    """In-memory policy bundle store for testing and development."""

    def __init__(self) -> None:
        self._bundles: dict[str, PolicyBundle] = {}
        self._order: list[str] = []

    async def create(self, bundle: PolicyBundle) -> PolicyBundle:
        """Create a new bundle. Overwrites if bundle_id exists."""
        if bundle.bundle_id not in self._bundles:
            self._order.append(bundle.bundle_id)
        self._bundles[bundle.bundle_id] = bundle
        return bundle

    async def get(self, bundle_id: str) -> PolicyBundle | None:
        """Retrieve a bundle by ID."""
        return self._bundles.get(bundle_id)

    async def list(self, limit: int = 50) -> list[PolicyBundle]:
        """List bundles sorted by created_at descending."""
        ids = list(reversed(self._order[-limit:]))
        return [self._bundles[bid] for bid in ids if bid in self._bundles]

    async def get_active(self) -> PolicyBundle | None:
        """Get the currently active bundle."""
        for bid in reversed(self._order):
            b = self._bundles.get(bid)
            if b and b.status == PolicyBundleStatus.ACTIVE:
                return b
        return None

    async def activate(self, bundle_id: str) -> PolicyBundle:
        """Activate a bundle, archiving any previously active bundle."""
        if bundle_id not in self._bundles:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        # Archive any currently active bundle
        for bid in self._order:
            b = self._bundles.get(bid)
            if b and b.status == PolicyBundleStatus.ACTIVE:
                b.status = PolicyBundleStatus.ARCHIVED
                b.archived_at = datetime.now(timezone.utc)
                self._bundles[bid] = b

        # Activate the target bundle
        bundle = self._bundles[bundle_id]
        bundle.status = PolicyBundleStatus.ACTIVE
        bundle.activated_at = datetime.now(timezone.utc)
        self._bundles[bundle_id] = bundle
        return bundle

    async def archive(self, bundle_id: str) -> PolicyBundle:
        """Archive a bundle."""
        if bundle_id not in self._bundles:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )
        bundle = self._bundles[bundle_id]
        bundle.status = PolicyBundleStatus.ARCHIVED
        bundle.archived_at = datetime.now(timezone.utc)
        self._bundles[bundle_id] = bundle
        return bundle


# ---------------------------------------------------------------------------
# SQLitePolicyBundleStore (Phase 29)
# ---------------------------------------------------------------------------

class SQLitePolicyBundleStore:
    """SQLite-backed policy bundle store.

    Persists bundles to a SQLite database file. Survives process restarts
    and can be shared across instances.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ".agent_app/policy_bundles.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_bundles (
                bundle_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                config_path TEXT,
                config_hash TEXT NOT NULL,
                policy_rules_hash TEXT,
                description TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                archived_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_bundles_status
                ON policy_bundles(status);
            CREATE INDEX IF NOT EXISTS idx_bundles_created
                ON policy_bundles(created_at);
        """)
        self._conn.commit()

    async def create(self, bundle: PolicyBundle) -> PolicyBundle:
        """Create a new bundle (INSERT OR REPLACE)."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO policy_bundles
                (bundle_id, name, version, status, config_path, config_hash,
                 policy_rules_hash, description, created_by, created_at,
                 activated_at, archived_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.bundle_id,
                bundle.name,
                bundle.version,
                bundle.status,
                bundle.config_path,
                bundle.config_hash,
                bundle.policy_rules_hash,
                bundle.description,
                bundle.created_by,
                bundle.created_at.isoformat(),
                bundle.activated_at.isoformat() if bundle.activated_at else None,
                bundle.archived_at.isoformat() if bundle.archived_at else None,
                json.dumps(bundle.metadata),
            ),
        )
        self._conn.commit()
        return bundle

    async def get(self, bundle_id: str) -> PolicyBundle | None:
        """Retrieve a bundle by ID."""
        row = self._conn.execute(
            "SELECT * FROM policy_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_bundle(row)

    async def list(self, limit: int = 50) -> list[PolicyBundle]:
        """List bundles sorted by created_at descending."""
        rows = self._conn.execute(
            "SELECT * FROM policy_bundles ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_bundle(row) for row in rows]

    async def get_active(self) -> PolicyBundle | None:
        """Get the currently active bundle."""
        row = self._conn.execute(
            "SELECT * FROM policy_bundles WHERE status = ? ORDER BY created_at DESC LIMIT 1",
            (PolicyBundleStatus.ACTIVE,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_bundle(row)

    async def activate(self, bundle_id: str) -> PolicyBundle:
        """Activate a bundle, archiving any previously active bundle."""
        # Verify bundle exists
        row = self._conn.execute(
            "SELECT bundle_id FROM policy_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        now = datetime.now(timezone.utc).isoformat()

        # Archive any currently active bundle
        self._conn.execute(
            "UPDATE policy_bundles SET status = ?, archived_at = ? WHERE status = ?",
            (PolicyBundleStatus.ARCHIVED, now, PolicyBundleStatus.ACTIVE),
        )

        # Activate the target bundle
        self._conn.execute(
            "UPDATE policy_bundles SET status = ?, activated_at = ? WHERE bundle_id = ?",
            (PolicyBundleStatus.ACTIVE, now, bundle_id),
        )
        self._conn.commit()

        # Return the updated bundle
        updated = self._conn.execute(
            "SELECT * FROM policy_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        return self._row_to_bundle(updated)

    async def archive(self, bundle_id: str) -> PolicyBundle:
        """Archive a bundle."""
        row = self._conn.execute(
            "SELECT bundle_id FROM policy_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Bundle '{bundle_id}' not found in policy bundle store."
            )

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE policy_bundles SET status = ?, archived_at = ? WHERE bundle_id = ?",
            (PolicyBundleStatus.ARCHIVED, now, bundle_id),
        )
        self._conn.commit()

        updated = self._conn.execute(
            "SELECT * FROM policy_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        return self._row_to_bundle(updated)

    def _row_to_bundle(self, row: sqlite3.Row) -> PolicyBundle:
        """Convert a database row to PolicyBundle."""
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
        for ts_field in ("created_at", "activated_at", "archived_at"):
            val = data.get(ts_field)
            data[ts_field] = datetime.fromisoformat(val) if val else None
        return PolicyBundle(**data)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

