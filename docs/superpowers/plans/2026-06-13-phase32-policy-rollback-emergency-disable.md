# Phase 32: Policy Rollback, Emergency Disable, and Activation Safety Controls

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make policy runtime activation safer for production by adding rollback, emergency disable/enable, and resolver safety checks.

**Architecture:** Add PolicyEnvironmentState model and store for per-environment enable/disable tracking. Extend PolicyActivation with rollback metadata. Extend PolicyActivationStore with previous-activation lookup. Wire environment store into ActivePolicyResolver to block resolution when disabled. Expose rollback/disable/enable through CLI, console, and service APIs with RBAC and audit.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, Click, FastAPI/Jinja2

---

## File Structure

| File | Purpose |
|------|---------|
| `agent_app/governance/policy_environment.py` | PolicyEnvironmentStatus enum, PolicyEnvironmentState model |
| `agent_app/runtime/policy_environment_store.py` | Protocol + InMemory + SQLite + factory |
| `agent_app/governance/policy_activation.py` | Extended with rollback fields |
| `agent_app/runtime/policy_activation_store.py` | Extended with get_previous_activation, rollback_to_activation |
| `agent_app/governance/policy_rbac.py` | Add ENVIRONMENT_DISABLE, ENVIRONMENT_ENABLE, ENVIRONMENT_VIEW permissions |
| `agent_app/runtime/policy_resolver.py` | Inject environment store, block disabled environments |
| `agent_app/runtime/policy_release.py` | Add rollback_environment, disable_policy_environment, enable_policy_environment |
| `agent_app/config/schema.py` | Add environments store config |
| `agent_app/config/loader.py` | Wire environment store |
| `agent_app/cli.py` | Add environment disable/enable/list, activation rollback commands |
| `agent_app/console/router.py` | Add environment detail, disable/enable POST, rollback POST routes |
| `agent_app/console/templates/policy_environment_detail.html` | New template |
| `agent_app/adapters/fastapi.py` | Wire environment store into console |
| `tests/unit/test_policy_environment.py` | Model tests |
| `tests/unit/test_policy_environment_store.py` | Store tests |
| `tests/unit/test_policy_activation_rollback.py` | Rollback tests |
| `tests/unit/test_policy_resolver_safety.py` | Resolver safety tests |
| `tests/unit/test_policy_release_phase32.py` | Service rollback/disable/enable tests |
| `tests/unit/test_policy_release_config_phase32.py` | Config schema tests |
| `tests/unit/test_policy_release_cli_phase32.py` | CLI tests |
| `tests/unit/test_policy_release_console_phase32.py` | Console tests |

---

### Task 1: PolicyEnvironment model

**Files:**
- Create: `agent_app/governance/policy_environment.py`
- Test: `tests/unit/test_policy_environment.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for PolicyEnvironmentState model (Phase 32)."""
import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_environment import (
    PolicyEnvironmentStatus,
    PolicyEnvironmentState,
)


class TestPolicyEnvironmentStatus:
    def test_enabled_value(self):
        assert PolicyEnvironmentStatus.ENABLED == "enabled"

    def test_disabled_value(self):
        assert PolicyEnvironmentStatus.DISABLED == "disabled"

    def test_all_statuses(self):
        values = {s.value for s in PolicyEnvironmentStatus}
        assert values == {"enabled", "disabled"}


class TestPolicyEnvironmentState:
    def test_default_enabled(self):
        state = PolicyEnvironmentState(environment="prod")
        assert state.status == PolicyEnvironmentStatus.ENABLED
        assert state.disabled_reason is None
        assert state.disabled_by is None
        assert state.disabled_at is None
        assert state.enabled_by is None
        assert state.enabled_at is None

    def test_disabled_state(self):
        now = datetime.now(timezone.utc)
        state = PolicyEnvironmentState(
            environment="prod",
            status=PolicyEnvironmentStatus.DISABLED,
            disabled_reason="Emergency",
            disabled_by="admin",
            disabled_at=now,
        )
        assert state.status == PolicyEnvironmentStatus.DISABLED
        assert state.disabled_reason == "Emergency"
        assert state.disabled_by == "admin"
        assert state.disabled_at is not None

    def test_requires_environment(self):
        with pytest.raises(Exception):
            PolicyEnvironmentState()

    def test_updated_at_timezone_aware(self):
        state = PolicyEnvironmentState(environment="staging")
        assert state.updated_at.tzinfo is not None
```

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv/bin/python -m pytest tests/unit/test_policy_environment.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write implementation**

```python
"""Policy environment state -- tracks enabled/disabled status for policy environments."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyEnvironmentStatus(StrEnum):
    """Status of a policy environment."""
    ENABLED = "enabled"
    DISABLED = "disabled"


class PolicyEnvironmentState(BaseModel):
    """Tracks the enabled/disabled state of a policy environment."""
    environment: str = Field(..., description="Environment name")
    status: PolicyEnvironmentStatus = Field(
        default=PolicyEnvironmentStatus.ENABLED,
        description="Current environment status",
    )
    disabled_reason: str | None = Field(default=None, description="Why the environment was disabled")
    disabled_by: str | None = Field(default=None, description="Who disabled the environment")
    disabled_at: datetime | None = Field(default=None, description="When the environment was disabled")
    enabled_by: str | None = Field(default=None, description="Who last enabled the environment")
    enabled_at: datetime | None = Field(default=None, description="When the environment was last enabled")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp",
    )
```

- [ ] **Step 4: Run tests to verify GREEN**

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_environment.py tests/unit/test_policy_environment.py
git commit -m "feat: Phase 32 Task 1 -- PolicyEnvironmentState model"
```

---

### Task 2: PolicyEnvironmentStore

**Files:**
- Create: `agent_app/runtime/policy_environment_store.py`
- Test: `tests/unit/test_policy_environment_store.py`

- [ ] **Step 1: Write failing tests** (9 tests: 4 InMemory + 4 SQLite + 1 factory)

```python
"""Tests for PolicyEnvironmentStore (Phase 32)."""
import pytest
from agent_app.governance.policy_environment import PolicyEnvironmentStatus
from agent_app.runtime.policy_environment_store import (
    InMemoryPolicyEnvironmentStore,
    SQLitePolicyEnvironmentStore,
    create_policy_environment_store,
)


class TestInMemoryPolicyEnvironmentStore:
    @pytest.mark.asyncio
    async def test_get_default_enabled(self):
        store = InMemoryPolicyEnvironmentStore()
        state = await store.get("prod")
        assert state.environment == "prod"
        assert state.status == PolicyEnvironmentStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable(self):
        store = InMemoryPolicyEnvironmentStore()
        state = await store.disable("prod", disabled_by="admin", reason="Emergency")
        assert state.status == PolicyEnvironmentStatus.DISABLED
        assert state.disabled_reason == "Emergency"
        assert state.disabled_by == "admin"

    @pytest.mark.asyncio
    async def test_enable(self):
        store = InMemoryPolicyEnvironmentStore()
        await store.disable("prod", disabled_by="admin", reason="Emergency")
        state = await store.enable("prod", enabled_by="admin2")
        assert state.status == PolicyEnvironmentStatus.ENABLED
        assert state.enabled_by == "admin2"

    @pytest.mark.asyncio
    async def test_list_states(self):
        store = InMemoryPolicyEnvironmentStore()
        await store.disable("prod", disabled_by="admin", reason="Emergency")
        states = await store.list()
        assert len(states) == 1
        assert states[0].environment == "prod"


class TestSQLitePolicyEnvironmentStore:
    @pytest.mark.asyncio
    async def test_get_default_enabled(self, tmp_path):
        store = SQLitePolicyEnvironmentStore(db_path=str(tmp_path / "env.db"))
        state = await store.get("prod")
        assert state.status == PolicyEnvironmentStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable_persists(self, tmp_path):
        db = str(tmp_path / "env.db")
        store = SQLitePolicyEnvironmentStore(db_path=db)
        await store.disable("prod", disabled_by="admin", reason="Emergency")
        store2 = SQLitePolicyEnvironmentStore(db_path=db)
        state = await store2.get("prod")
        assert state.status == PolicyEnvironmentStatus.DISABLED

    @pytest.mark.asyncio
    async def test_enable_persists(self, tmp_path):
        db = str(tmp_path / "env.db")
        store = SQLitePolicyEnvironmentStore(db_path=db)
        await store.disable("prod", disabled_by="admin", reason="Emergency")
        await store.enable("prod", enabled_by="admin2")
        store2 = SQLitePolicyEnvironmentStore(db_path=db)
        state = await store2.get("prod")
        assert state.status == PolicyEnvironmentStatus.ENABLED

    @pytest.mark.asyncio
    async def test_list_states(self, tmp_path):
        store = SQLitePolicyEnvironmentStore(db_path=str(tmp_path / "env.db"))
        await store.disable("prod", disabled_by="admin", reason="Emergency")
        states = await store.list()
        assert len(states) >= 1


class TestCreatePolicyEnvironmentStore:
    def test_memory(self):
        store = create_policy_environment_store("memory")
        assert isinstance(store, InMemoryPolicyEnvironmentStore)

    def test_sqlite(self, tmp_path):
        store = create_policy_environment_store("sqlite", db_path=str(tmp_path / "env.db"))
        assert isinstance(store, SQLitePolicyEnvironmentStore)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            create_policy_environment_store("redis")
```

- [ ] **Step 2: Run tests — RED**

- [ ] **Step 3: Implement InMemoryPolicyEnvironmentStore, SQLitePolicyEnvironmentStore, create_policy_environment_store()**

Protocol:
```python
class PolicyEnvironmentStore(Protocol):
    async def get(self, environment: str) -> PolicyEnvironmentState: ...
    async def disable(self, environment: str, disabled_by: str, reason: str) -> PolicyEnvironmentState: ...
    async def enable(self, environment: str, enabled_by: str, reason: str | None = None) -> PolicyEnvironmentState: ...
    async def list(self) -> list[PolicyEnvironmentState]: ...
```

InMemory: dict[str, PolicyEnvironmentState] internally. `get()` returns default enabled if not present.

SQLite: table `policy_environment_states`, same pattern as other SQLite stores.

- [ ] **Step 4: Run tests — GREEN**

- [ ] **Step 5: Commit**

---

### Task 3: Extend PolicyActivation with rollback fields + store rollback methods

**Files:**
- Modify: `agent_app/governance/policy_activation.py`
- Modify: `agent_app/runtime/policy_activation_store.py`
- Test: `tests/unit/test_policy_activation_rollback.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for activation rollback (Phase 32)."""
import pytest
from datetime import datetime, timezone
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.runtime.policy_activation_store import InMemoryPolicyActivationStore


class TestPolicyActivationRollbackFields:
    def test_rollback_fields_default_none(self):
        a = PolicyActivation(
            activation_id="pa_001", environment="prod",
            bundle_id="pb_001", config_hash="hash1", activated_by="admin",
        )
        assert a.rollback_of_activation_id is None
        assert a.rollback_target_activation_id is None


class TestInMemoryRollback:
    @pytest.mark.asyncio
    async def test_get_previous_activation(self):
        store = InMemoryPolicyActivationStore()
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        await store.activate(a1)
        await store.activate(a2)
        prev = await store.get_previous_activation("prod")
        assert prev is not None
        assert prev.activation_id == "pa_001"

    @pytest.mark.asyncio
    async def test_get_previous_activation_none(self):
        store = InMemoryPolicyActivationStore()
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await store.activate(a1)
        prev = await store.get_previous_activation("prod")
        assert prev is None

    @pytest.mark.asyncio
    async def test_rollback_creates_new_activation(self):
        store = InMemoryPolicyActivationStore()
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        await store.activate(a1)
        await store.activate(a2)
        result = await store.rollback_to_activation("prod", "pa_001", rolled_back_by="ops")
        assert result.status == PolicyActivationStatus.ACTIVE
        assert result.rollback_of_activation_id == "pa_002"
        assert result.rollback_target_activation_id == "pa_001"
        assert result.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_rollback_supersedes_current(self):
        store = InMemoryPolicyActivationStore()
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        a2 = PolicyActivation(activation_id="pa_002", environment="prod", bundle_id="pb_002", config_hash="h2", activated_by="admin")
        await store.activate(a1)
        await store.activate(a2)
        await store.rollback_to_activation("prod", "pa_001", rolled_back_by="ops")
        current = await store.get_active("prod")
        assert current is not None
        assert current.bundle_id == "pb_001"

    @pytest.mark.asyncio
    async def test_rollback_wrong_environment_fails(self):
        store = InMemoryPolicyActivationStore()
        a1 = PolicyActivation(activation_id="pa_001", environment="prod", bundle_id="pb_001", config_hash="h1", activated_by="admin")
        await store.activate(a1)
        with pytest.raises(ValueError, match="environment"):
            await store.rollback_to_activation("staging", "pa_001", rolled_back_by="ops")

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_activation_fails(self):
        store = InMemoryPolicyActivationStore()
        with pytest.raises(KeyError):
            await store.rollback_to_activation("prod", "pa_999", rolled_back_by="ops")
```

- [ ] **Step 2: Run tests — RED**

- [ ] **Step 3: Add rollback fields to PolicyActivation model**

Add to `agent_app/governance/policy_activation.py`:
```python
    rollback_of_activation_id: str | None = Field(default=None, description="Activation being rolled back")
    rollback_target_activation_id: str | None = Field(default=None, description="Activation being rolled back to")
```

- [ ] **Step 4: Add get_previous_activation and rollback_to_activation to both InMemory and SQLite stores**

`get_previous_activation(environment, before_activation_id=None)` — returns the most recent non-ACTIVE activation for the environment (superseded or rolled_back), excluding the current active one.

`rollback_to_activation(environment, target_activation_id, rolled_back_by, reason=None)` — validates target belongs to same environment, marks current ACTIVE as SUPERSEDED, creates a new ACTIVE activation pointing to the same bundle with rollback metadata.

Also update SQLite schema to include the two new columns.

- [ ] **Step 5: Run tests — GREEN**

- [ ] **Step 6: Commit**

---

### Task 4: Extend RBAC permissions

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Test: append to existing `tests/unit/test_policy_rbac.py`

- [ ] **Step 1: Write failing test**

```python
def test_phase32_permissions_exist():
    from agent_app.governance.policy_rbac import PolicyReleasePermission
    assert PolicyReleasePermission.ENVIRONMENT_DISABLE == "policy.environment.disable"
    assert PolicyReleasePermission.ENVIRONMENT_ENABLE == "policy.environment.enable"
    assert PolicyReleasePermission.ENVIRONMENT_VIEW == "policy.environment.view"
```

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Add three new permission values to PolicyReleasePermission enum**

```python
    ENVIRONMENT_DISABLE = "policy.environment.disable"
    ENVIRONMENT_ENABLE = "policy.environment.enable"
    ENVIRONMENT_VIEW = "policy.environment.view"
```

Update docstring. `ROLLBACK_EXECUTE` already exists for activation rollback.

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 5: ActivePolicyResolver safety checks

**Files:**
- Modify: `agent_app/runtime/policy_resolver.py`
- Test: `tests/unit/test_policy_resolver_safety.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for ActivePolicyResolver safety checks (Phase 32)."""
import pytest
from agent_app.runtime.policy_resolver import ActivePolicyResolver
from agent_app.runtime.policy_environment_store import InMemoryPolicyEnvironmentStore


class _MockBundle:
    def __init__(self, bid, chash):
        self.bundle_id = bid
        self.config_hash = chash


class _MockBundleStore:
    def __init__(self, bundles):
        self._b = {b.bundle_id: b for b in bundles}
    async def get(self, bid):
        return self._b.get(bid)


class _MockActivation:
    def __init__(self, env, bid, chash):
        self.environment = env
        self.bundle_id = bid
        self.config_hash = chash
        self.status = "active"


class _MockActivationStore:
    def __init__(self, acts):
        self._a = {a.environment: a for a in acts}
    async def get_active(self, env):
        return self._a.get(env)


@pytest.fixture
def resolver_with_env_store():
    bundle = _MockBundle("pb_1", "h1")
    activation = _MockActivation("prod", "pb_1", "h1")
    env_store = InMemoryPolicyEnvironmentStore()
    return ActivePolicyResolver(
        bundle_store=_MockBundleStore([bundle]),
        activation_store=_MockActivationStore([activation]),
        environment_store=env_store,
    ), env_store


class TestResolverSafety:
    @pytest.mark.asyncio
    async def test_disabled_environment_returns_none(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        result = await resolver.resolve_active_bundle("prod")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_environment_raises_on_require(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        with pytest.raises(RuntimeError, match="disabled"):
            await resolver.require_active_bundle("prod")

    @pytest.mark.asyncio
    async def test_enabled_environment_resolves(self, resolver_with_env_store):
        resolver, _ = resolver_with_env_store
        result = await resolver.resolve_active_bundle("prod")
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_env_store_still_works(self):
        bundle = _MockBundle("pb_1", "h1")
        activation = _MockActivation("prod", "pb_1", "h1")
        resolver = ActivePolicyResolver(
            bundle_store=_MockBundleStore([bundle]),
            activation_store=_MockActivationStore([activation]),
        )
        result = await resolver.resolve_active_bundle("prod")
        assert result is not None

    @pytest.mark.asyncio
    async def test_cache_cleared_after_disable(self, resolver_with_env_store):
        resolver, env_store = resolver_with_env_store
        # First resolve populates cache
        await resolver.resolve_active_bundle("prod")
        await env_store.disable("prod", disabled_by="admin", reason="Emergency")
        # Cache should be invalidated by resolver checking env state
        result = await resolver.resolve_active_bundle("prod")
        assert result is None
```

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Add `environment_store` parameter to ActivePolicyResolver.__init__**

Update `resolve_active_bundle` to check environment state before resolution. Update `require_active_bundle` to raise RuntimeError with "disabled" message when environment is disabled.

```python
def __init__(self, bundle_store, activation_store, cache_ttl_seconds=0, environment_store=None):
    self._environment_store = environment_store
    ...

async def resolve_active_bundle(self, environment):
    # Check environment state
    if self._environment_store is not None:
        env_state = await self._environment_store.get(environment)
        if env_state.status == PolicyEnvironmentStatus.DISABLED:
            if self._cache_ttl > 0:
                self._cache[environment] = _CacheEntry(None, self._cache_ttl)
            return None
    ... existing logic ...
```

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 6: PolicyReleaseService rollback/disable/enable APIs

**Files:**
- Modify: `agent_app/runtime/policy_release.py`
- Test: `tests/unit/test_policy_release_phase32.py`

- [ ] **Step 1: Write failing tests** (~10 tests)

Test rollback_environment:
- requires ROLLBACK_EXECUTE permission
- rollback to previous activation succeeds
- rollback to explicit target succeeds
- rollback with no previous activation fails
- rollback wrong environment fails
- audit event written

Test disable_policy_environment:
- requires ENVIRONMENT_DISABLE permission
- disable without reason fails
- disable succeeds
- audit event written

Test enable_policy_environment:
- requires ENVIRONMENT_ENABLE permission
- enable succeeds
- audit event written

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Implement three new methods on PolicyReleaseService**

Add `environment_store` param to `__init__`. Implement:
- `rollback_environment(environment, rolled_back_by, context, target_activation_id=None, reason=None)`
- `disable_policy_environment(environment, disabled_by, context, reason)`
- `enable_policy_environment(environment, enabled_by, context, reason=None)`

All with RBAC checks and audit events.

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 7: Config schema and loader

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Test: `tests/unit/test_policy_release_config_phase32.py`

- [ ] **Step 1: Write failing tests**

```python
def test_environments_config():
    from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseStoreConfig
    cfg = PolicyReleaseConfig(
        bundles=PolicyReleaseStoreConfig(type="memory"),
        gates=PolicyReleaseStoreConfig(type="memory"),
        environments=PolicyReleaseStoreConfig(type="memory"),
    )
    assert cfg.environments is not None
    assert cfg.environments.type == "memory"

def test_environments_defaults_none():
    from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseStoreConfig
    cfg = PolicyReleaseConfig(
        bundles=PolicyReleaseStoreConfig(type="memory"),
        gates=PolicyReleaseStoreConfig(type="memory"),
    )
    assert cfg.environments is None

def test_phase32_backward_compat():
    """Phase 31 configs still load."""
    from agent_app.config.schema import PolicyReleaseConfig, PolicyReleaseStoreConfig
    cfg = PolicyReleaseConfig(
        bundles=PolicyReleaseStoreConfig(type="memory"),
        gates=PolicyReleaseStoreConfig(type="memory"),
        activations=PolicyReleaseStoreConfig(type="memory"),
    )
    assert cfg.environments is None
    assert cfg.activations is not None
```

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Add `environments` field to PolicyReleaseConfig, wire in loader**

In schema.py:
```python
    environments: PolicyReleaseStoreConfig | None = Field(default=None, description="Environment state store config (Phase 32)")
```

In loader.py: Create environment store from config, pass to PolicyReleaseService and ActivePolicyResolver.

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 8: CLI commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_release_cli_phase32.py`

- [ ] **Step 1: Write failing tests** (~6 tests)

- environment list
- environment disable success
- environment disable without reason fails
- environment enable success
- activation rollback success
- activation rollback no previous fails

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Add CLI commands**

Under `policy environment`: `list`, `disable`, `enable`
Under `policy activation`: `rollback`

```bash
agentapp policy environment list --config <path>
agentapp policy environment disable --config <path> --environment prod --actor-id admin --reason "Emergency" --permissions policy.environment.disable
agentapp policy environment enable --config <path> --environment prod --actor-id admin --reason "Resolved" --permissions policy.environment.enable
agentapp policy activation rollback --config <path> --environment prod --actor-id admin --reason "Rollback" --permissions policy.rollback.execute [--target-activation-id pa_...]
```

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 9: Console extensions

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/policy_environment_detail.html`
- Modify: `agent_app/adapters/fastapi.py`
- Test: `tests/unit/test_policy_release_console_phase32.py`

- [ ] **Step 1: Write failing tests** (~6 tests)

- environment detail page renders
- disable POST works
- enable POST works
- rollback POST works
- permission error renders cleanly
- missing reason renders error

- [ ] **Step 2: Run — RED**

- [ ] **Step 3: Implement console routes and template**

New routes:
- `GET /environments/{environment}` — detail page with disable/enable/rollback forms
- `POST /environments/{environment}/disable` — disable action
- `POST /environments/{environment}/enable` — enable action
- `POST /activations/{activation_id}/rollback` — rollback action

Wire environment_store through FastAPI adapter.

- [ ] **Step 4: Run — GREEN**

- [ ] **Step 5: Commit**

---

### Task 10: Documentation

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase32.md`

- [ ] Add Phase 32 section to policy_release.md covering:
  - Environment disable/enable lifecycle
  - Activation rollback lifecycle
  - CLI examples
  - Console workflows
  - Resolver behavior when disabled
  - Audit events
  - Known limitations

- [ ] Add Phase 32 entry to CHANGELOG.md

- [ ] Add v0.20 to README roadmap

- [ ] Create release checklist

- [ ] Commit

---

### Task 11: Final regression verification

- [ ] Run full test suite
- [ ] Verify Phase 31 tests still pass
- [ ] Verify import boundaries (no FastAPI/Jinja2 in core modules)
- [ ] Verify backward compatibility (Phase 31 config still loads)
- [ ] Report results
