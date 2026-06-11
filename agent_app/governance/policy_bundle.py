"""Policy bundle — versioned policy configuration bundles for release management.

Phase 29: versioned policy bundles with lifecycle management (draft → active → archived).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
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
