# Phase 65: Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken webhook signature service wiring (silently failing since Phase 51), make the `nonce_replay_protection` and `require_promotion_approval` config fields actually control behavior, add a SQLite backend for approval rate limiting, backfill 4 missing release-checklist docs, and integrate OpenTelemetry as a fourth `TracingConfig.type` option.

**Architecture:** Each task is a self-contained fix or addition to an existing extension point (`agent_app/config/loader.py` branches, `PolicyReleaseService` constructor, `agent_app/runtime/approval_rate_limit.py`, `agent_app/observability/otel.py`). No new subsystems. TDD throughout — every behavior change has a test that fails first.

**Tech Stack:** stdlib `sqlite3` (WAL mode), `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http` (new optional dependency), pytest with `pytest.mark.asyncio` and `pytest.mark.skipif`.

**Full spec:** `docs/superpowers/specs/2026-07-01-phase65-gap-closure-design.md`

---

## Important context for every task

This repo has **two independent webhook-signing subsystems** that share confusingly similar names:

- **System A** — `agent_app/runtime/policy_rollout_federation_webhook_signature.py`, class `FederationWebhookSignatureService`. Constructor: `(*, active_key_id="default", keys=None, signature_version="v1", timestamp_tolerance_seconds=300)`. This is the system already used by `agent_app/runtime/policy_rollout_federation_notification_service.py` (`self._webhook_signature_service.sign(...)`), by `AgentApp.federation_webhook_signature_service` (a property at `agent_app/core/app.py:332-333`), and by the CLI `webhook verify` command (`agent_app/cli.py:9839-9878`). **This is the system Task 1 wires up.**
- **System B** — `agent_app/runtime/policy_rollout_federation_notification_webhook_signing.py`, function-based (`sign_payload`, `verify_signed_payload`, `WebhookSigningSecretProvider`). Tested independently in `tests/unit/test_webhook_signing_rotation_phase57.py`. Not touched by this plan — it is a separate, already-functioning utility module, just not wired into `build_app()`. Do not confuse its file path with System A's.

The config class `RolloutFederationWebhookSigningConfig` (`agent_app/config/schema.py:644-663`) has fields `enabled`, `algorithm`, `signature_version`, `active_key_id`, `keys`, `timestamp_tolerance_seconds`, `nonce_ttl_seconds`, `nonce_store_backend`, `nonce_store_path`, `secret_env_prefix`, `nonce_replay_protection`. Only `active_key_id`, `keys`, `signature_version`, `timestamp_tolerance_seconds` map onto System A's constructor — `algorithm` and `secret_env_prefix` are not consumed anywhere in this plan (pre-existing unused fields, out of scope: `algorithm` is presently always SHA-256 regardless of value, and `secret_env_prefix` belongs conceptually to System B's not-yet-wired env-provider pattern). Do not try to make these two fields do something new — that would be scope creep beyond "fix what's broken."

---

## Task 1: Fix webhook signature service loader wiring

**Files:**
- Modify: `agent_app/config/loader.py:1288-1305`
- Test: `tests/unit/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/unit/test_config_loader.py` (append at the end of the file, matching the existing `import` style at the top of that file — it already imports `build_app` and uses `tmp_path`/YAML-writing fixtures; check the top of the file for the exact pattern before adding, and reuse it):

```python
def test_build_app_wires_webhook_signature_service(tmp_path):
    """Regression test: webhook_signing.enabled=true must actually construct
    a working FederationWebhookSignatureService, not silently no-op.

    Prior to the Phase 65 fix, this raised ModuleNotFoundError (wrong import
    path) then TypeError (invalid constructor kwargs), both swallowed by a
    bare `except Exception: pass` in loader.py.
    """
    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
governance:
  policy_release:
    rollout_federation:
      enabled: true
      notifications:
        enabled: true
        webhook_signing:
          enabled: true
          active_key_id: test-key
          keys:
            test-key: test-secret-value
          timestamp_tolerance_seconds: 300
""")
    app = build_app(str(config_path))
    service = app.federation_webhook_signature_service
    assert service is not None
    headers = service.sign("test-body")
    assert headers["X-AgentApp-Signature"].startswith("v1=")
    assert headers["X-AgentApp-Key-ID"] == "test-key"
```

Before adding this test, run `grep -n "rollout_federation" agent_app/config/schema.py | head -5` to confirm the exact nesting path from `PolicyReleaseConfig` down to `webhook_signing` — the YAML above assumes `governance.policy_release.rollout_federation.notifications.webhook_signing`, matching the `notif_cfg = getattr(fed_cfg, "notifications", None)` and `signing_cfg = getattr(notif_cfg, "webhook_signing", None)` lookups already in `loader.py`. If the actual nesting differs, adjust the YAML's indentation to match — do not change the assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py::test_build_app_wires_webhook_signature_service -v`
Expected: FAIL — `app.federation_webhook_signature_service` is `None` (the broken try/except silently swallowed the construction error), so `assert service is not None` fails.

- [ ] **Step 3: Fix the import paths and constructor call**

In `agent_app/config/loader.py`, find this block (currently at lines 1288-1305):

```python
                signing_cfg = getattr(notif_cfg, "webhook_signing", None)
                if signing_cfg is not None and signing_cfg.enabled:
                    from agent_app.runtime.policy_rollout_federation_notification_webhook_signature import FederationWebhookSignatureService
                    from agent_app.runtime.policy_rollout_federation_notification_nonce_store import create_federation_webhook_nonce_store

                    nonce_store = create_federation_webhook_nonce_store(
                        store_type=signing_cfg.nonce_store_backend,
                        db_path=signing_cfg.nonce_store_path,
                    )
                    signature_service = FederationWebhookSignatureService(
                        algorithm=signing_cfg.algorithm,
                        signature_version=signing_cfg.signature_version,
                        active_key_id=signing_cfg.active_key_id,
                        keys=signing_cfg.keys,
                        timestamp_tolerance_seconds=signing_cfg.timestamp_tolerance_seconds,
                        nonce_store=nonce_store,
                        nonce_ttl_seconds=signing_cfg.nonce_ttl_seconds,
                    )
                    app._federation_webhook_signature_service = signature_service
                    app._federation_webhook_nonce_store = nonce_store

                    # Attach signature service to notification service if it exists
                    ns = getattr(app, "_federation_notification_service", None)
                    if ns is not None:
                        ns._signature_service = signature_service
```

Replace it with:

```python
                signing_cfg = getattr(notif_cfg, "webhook_signing", None)
                if signing_cfg is not None and signing_cfg.enabled:
                    from agent_app.runtime.policy_rollout_federation_webhook_signature import FederationWebhookSignatureService
                    from agent_app.runtime.policy_rollout_federation_webhook_nonce_store import create_federation_webhook_nonce_store

                    nonce_store = create_federation_webhook_nonce_store(
                        store_type=signing_cfg.nonce_store_backend,
                        db_path=signing_cfg.nonce_store_path,
                    )
                    signature_service = FederationWebhookSignatureService(
                        active_key_id=signing_cfg.active_key_id,
                        keys=signing_cfg.keys,
                        signature_version=signing_cfg.signature_version,
                        timestamp_tolerance_seconds=signing_cfg.timestamp_tolerance_seconds,
                    )
                    app._federation_webhook_signature_service = signature_service
                    app._federation_webhook_nonce_store = nonce_store
                    app._federation_webhook_nonce_replay_protection = signing_cfg.nonce_replay_protection

                    # Attach signature service to notification service if it exists
                    ns = getattr(app, "_federation_notification_service", None)
                    if ns is not None:
                        ns._signature_service = signature_service
```

Note what changed: both import paths dropped the incorrect `notification_` infix; the constructor call dropped `algorithm=` and `nonce_ttl_seconds=` (not real parameters) and moved `nonce_store=nonce_store` out entirely (not a constructor parameter — `verify()` takes it per-call, wired in Task 2); a new line stores `nonce_replay_protection` on the app for Task 2 to read.

- [ ] **Step 4: Replace the silent exception swallow with a visible warning**

Find the `except Exception: pass` that closes this whole try block (currently at loader.py line ~1311-1312, marked `# noqa: BLE001 — graceful failure`). Locate it with:

```bash
grep -n "noqa: BLE001 — graceful failure" agent_app/config/loader.py
```

This bare except wraps a large block covering webhook signing plus several other federation notification sub-features (preferences, templates, etc. — all in the same try). Do not narrow the whole block (other sub-features may legitimately want best-effort silence). Instead, add a nested `try/except` specifically around the webhook signature construction so a broken signing config surfaces a warning without affecting sibling features:

```python
                signing_cfg = getattr(notif_cfg, "webhook_signing", None)
                if signing_cfg is not None and signing_cfg.enabled:
                    try:
                        from agent_app.runtime.policy_rollout_federation_webhook_signature import FederationWebhookSignatureService
                        from agent_app.runtime.policy_rollout_federation_webhook_nonce_store import create_federation_webhook_nonce_store

                        nonce_store = create_federation_webhook_nonce_store(
                            store_type=signing_cfg.nonce_store_backend,
                            db_path=signing_cfg.nonce_store_path,
                        )
                        signature_service = FederationWebhookSignatureService(
                            active_key_id=signing_cfg.active_key_id,
                            keys=signing_cfg.keys,
                            signature_version=signing_cfg.signature_version,
                            timestamp_tolerance_seconds=signing_cfg.timestamp_tolerance_seconds,
                        )
                        app._federation_webhook_signature_service = signature_service
                        app._federation_webhook_nonce_store = nonce_store
                        app._federation_webhook_nonce_replay_protection = signing_cfg.nonce_replay_protection

                        # Attach signature service to notification service if it exists
                        ns = getattr(app, "_federation_notification_service", None)
                        if ns is not None:
                            ns._signature_service = signature_service
                    except Exception as exc:  # noqa: BLE001 — best-effort, but visible
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to initialize federation webhook signature service: %s", exc
                        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py::test_build_app_wires_webhook_signature_service -v`
Expected: PASS

- [ ] **Step 6: Run the full config loader test file to check for regressions**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py -v`
Expected: all PASS, 0 failures

- [ ] **Step 7: Commit**

```bash
git add agent_app/config/loader.py tests/unit/test_config_loader.py
git commit -m "fix: webhook signature service loader wiring was silently broken

Two independent bugs: wrong import module paths (typo'd 'notification_'
infix that doesn't exist in either target file), and constructor kwargs
(algorithm, nonce_store, nonce_ttl_seconds) that FederationWebhookSignatureService
never accepted. Both errors were swallowed by a bare except, so
webhook_signing.enabled: true has done nothing since Phase 51."
```

---

## Task 2: Wire nonce replay protection into the CLI verify command

**Files:**
- Modify: `agent_app/cli.py:9862-9868`
- Test: `tests/unit/test_policy_rollout_federation_notification_cli.py` (already has `test_webhook_verify_valid`/`test_webhook_verify_invalid`/`test_webhook_verify_no_keys_in_output` at lines 561-660, using a `MagicMock` for `app`/`service` rather than a real `build_app()` — the new test follows the exact same style)

- [ ] **Step 1: Write the failing test**

Add this test to the same test class that contains `test_webhook_verify_valid` (in `tests/unit/test_policy_rollout_federation_notification_cli.py`), right after `test_webhook_verify_no_keys_in_output`:

```python
    def test_webhook_verify_passes_nonce_store_when_replay_protection_enabled(self, capsys) -> None:
        """Regression test: verify() must receive nonce_store when
        nonce_replay_protection is enabled. Before the Task 2 fix,
        nonce_store was never passed regardless of this config value."""
        from agent_app.cli import _cmd_policy_federation_webhook_verify

        sig_result = FederationWebhookSignatureResult(
            valid=True,
            matched_key_id="key_001",
            signature_version="v1",
            timestamp_valid=True,
            nonce_valid=True,
        )
        service = MagicMock()
        service.verify = MagicMock(return_value=sig_result)
        nonce_store = MagicMock()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"test": "payload"}')
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                body_file=body_path,
                signature="v1=abc123",
                timestamp="2026-01-01T00:00:00Z",
                nonce="nonce123",
            )
            app = MagicMock()
            app.federation_webhook_signature_service = service
            app.federation_webhook_nonce_store = nonce_store
            app._federation_webhook_nonce_replay_protection = True
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_webhook_verify(args))
            assert rc == 0
            service.verify.assert_called_once()
            call_kwargs = service.verify.call_args.kwargs
            assert call_kwargs["nonce_store"] is nonce_store
        finally:
            Path(body_path).unlink(missing_ok=True)

    def test_webhook_verify_skips_nonce_store_when_replay_protection_disabled(self, capsys) -> None:
        """When nonce_replay_protection=False, nonce_store must NOT be
        passed (explicit opt-out preserved)."""
        from agent_app.cli import _cmd_policy_federation_webhook_verify

        sig_result = FederationWebhookSignatureResult(
            valid=True,
            matched_key_id="key_001",
            signature_version="v1",
            timestamp_valid=True,
            nonce_valid=None,
        )
        service = MagicMock()
        service.verify = MagicMock(return_value=sig_result)
        nonce_store = MagicMock()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"test": "payload"}')
            body_path = f.name
        try:
            args = argparse.Namespace(
                config="agentapp.yaml",
                body_file=body_path,
                signature="v1=abc123",
                timestamp="2026-01-01T00:00:00Z",
                nonce="nonce123",
            )
            app = MagicMock()
            app.federation_webhook_signature_service = service
            app.federation_webhook_nonce_store = nonce_store
            app._federation_webhook_nonce_replay_protection = False
            with patch("agent_app.config.loader.build_app", return_value=app):
                rc = _run(_cmd_policy_federation_webhook_verify(args))
            assert rc == 0
            service.verify.assert_called_once()
            call_kwargs = service.verify.call_args.kwargs
            assert call_kwargs["nonce_store"] is None
        finally:
            Path(body_path).unlink(missing_ok=True)
```

Both tests reuse `argparse`, `tempfile`, `Path`, `MagicMock`, `patch`, `FederationWebhookSignatureResult`, and `_run` — all already imported at the top of this file (confirmed: `argparse`, `tempfile` at lines 4/7; `Path` at line 9; `MagicMock`/`patch` at line 10; `FederationWebhookSignatureResult` via the `policy_rollout_federation_webhook` import at line 32; `_run` defined at line 38).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_policy_rollout_federation_notification_cli.py -k "nonce_store_when_replay_protection" -v`
Expected: FAIL — `service.verify.call_args.kwargs` has no `nonce_store` key at all (current code calls `verify(body=..., signature=..., timestamp_str=..., nonce=...)` with no `nonce_store` kwarg whatsoever), so `call_kwargs["nonce_store"]` raises `KeyError` on both new tests.

- [ ] **Step 3: Fix the CLI command**

In `agent_app/cli.py`, find (currently lines 9862-9868):

```python
    try:
        result = signature_service.verify(
            body=body,
            signature=args.signature,
            timestamp_str=args.timestamp,
            nonce=args.nonce,
        )
    except Exception as exc:
        print(f"Error verifying signature: {exc}", file=sys.stderr)
        return 1
```

Replace with:

```python
    nonce_store = getattr(app, "federation_webhook_nonce_store", None)
    replay_protection = getattr(app, "_federation_webhook_nonce_replay_protection", True)

    try:
        result = signature_service.verify(
            body=body,
            signature=args.signature,
            timestamp_str=args.timestamp,
            nonce=args.nonce,
            nonce_store=nonce_store if replay_protection else None,
        )
    except Exception as exc:
        print(f"Error verifying signature: {exc}", file=sys.stderr)
        return 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_policy_rollout_federation_notification_cli.py -k "nonce_store_when_replay_protection" -v`
Expected: PASS

- [ ] **Step 5: Run the full file to check for regressions**

Run: `.venv/bin/pytest tests/unit/test_policy_rollout_federation_notification_cli.py -v`
Expected: all PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_rollout_federation_notification_cli.py
git commit -m "fix: wire nonce_replay_protection config into webhook verify CLI

The only caller of FederationWebhookSignatureService.verify() never passed
nonce_store, so nonce reuse detection never executed regardless of the
nonce_replay_protection config value. Now the CLI verify command reads
the nonce store and the config flag off the app and passes them through."
```

---

## Task 3: Add SQLite backend for ApprovalRateLimiter

**Files:**
- Modify: `agent_app/runtime/approval_rate_limit.py`
- Test: `tests/unit/test_approval_rate_limit.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_approval_rate_limit.py` (which already imports `ApprovalRateLimiter`, `InMemoryApprovalRateLimiter`, `RateLimitConfig` from `agent_app.runtime.approval_rate_limit` — add `SQLiteApprovalRateLimiter` to that import line):

```python
class TestSQLiteApprovalRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit_allows_creation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=3, window_seconds=60, db_path=db_path)
        for i in range(3):
            allowed = await limiter.check_allowed(
                tenant_id="t1", user_id="u1", tool_name="refund.request"
            )
            assert allowed is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_over_limit_blocks_creation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        blocked = await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert blocked is False
        limiter.close()

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        assert await limiter.check_allowed(tenant_id="t2", user_id="u1", tool_name="refund.request") is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_window_expiry_allows_retry(self, tmp_path) -> None:
        import time
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=1, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        time.sleep(1.1)
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_state_persists_across_instances(self, tmp_path) -> None:
        """The whole point of a SQLite backend: state survives a process restart."""
        db_path = str(tmp_path / "rate_limit.db")
        limiter1 = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter1.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        limiter1.close()

        # New instance, same db_path — simulates a restart.
        limiter2 = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        blocked = await limiter2.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert blocked is False
        limiter2.close()

    @pytest.mark.asyncio
    async def test_rate_limit_writes_audit_event(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        logger = InMemoryAuditLogger()
        limiter = SQLiteApprovalRateLimiter(
            max_requests=1, window_seconds=60, db_path=db_path, audit_logger=logger
        )
        await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        events = logger.list_events(event_type="approval.rate_limited")
        assert len(events) == 1
        limiter.close()


class TestCreateApprovalRateLimiter:
    def test_creates_memory_backend(self) -> None:
        limiter = create_approval_rate_limiter(backend="memory", max_requests=5, window_seconds=60)
        assert isinstance(limiter, InMemoryApprovalRateLimiter)

    def test_creates_sqlite_backend(self, tmp_path) -> None:
        db_path = str(tmp_path / "rl.db")
        limiter = create_approval_rate_limiter(
            backend="sqlite", max_requests=5, window_seconds=60, db_path=db_path
        )
        assert isinstance(limiter, SQLiteApprovalRateLimiter)
        limiter.close()

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown rate limiter backend"):
            create_approval_rate_limiter(backend="redis", max_requests=5, window_seconds=60)
```

Update the file's import line at the top from:

```python
from agent_app.runtime.approval_rate_limit import (
    ApprovalRateLimiter,
    InMemoryApprovalRateLimiter,
    RateLimitConfig,
)
```

to:

```python
from agent_app.runtime.approval_rate_limit import (
    ApprovalRateLimiter,
    InMemoryApprovalRateLimiter,
    RateLimitConfig,
    SQLiteApprovalRateLimiter,
    create_approval_rate_limiter,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_approval_rate_limit.py -v`
Expected: FAIL with `ImportError: cannot import name 'SQLiteApprovalRateLimiter'`

- [ ] **Step 3: Implement `SQLiteApprovalRateLimiter` and the factory function**

Append to `agent_app/runtime/approval_rate_limit.py` (after the existing `InMemoryApprovalRateLimiter` class, before the module-level `_make_event_id` function — or after it, either is fine since Python doesn't care about ordering here; place it right before `_make_event_id` to keep the file's existing tail intact):

```python
class SQLiteApprovalRateLimiter(ApprovalRateLimiter):
    """SQLite-backed sliding-window rate limiter for approval creation.

    Unlike InMemoryApprovalRateLimiter, state survives process restarts
    and can be shared across multiple daemon/API instances pointed at the
    same database file.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        db_path: str = ".agent_app/approval_rate_limit.db",
        audit_logger: AuditLogger | None = None,
    ) -> None:
        import sqlite3

        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._audit_logger = audit_logger
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS approval_rate_limit_hits (
                key TEXT NOT NULL,
                hit_time REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_arl_key_time
                ON approval_rate_limit_hits(key, hit_time);
        """)
        self._conn.commit()

    def _key(self, tenant_id: str | None, user_id: str | None, tool_name: str) -> str:
        parts = [str(tenant_id or "_anon"), str(user_id or "_anon"), tool_name]
        return "|".join(parts)

    async def check_allowed(
        self,
        tenant_id: str | None,
        user_id: str | None,
        tool_name: str,
    ) -> bool:
        import time

        key = self._key(tenant_id, user_id, tool_name)
        now = time.time()
        cutoff = now - self._window_seconds

        with self._conn:
            self._conn.execute(
                "DELETE FROM approval_rate_limit_hits WHERE key=? AND hit_time<?",
                (key, cutoff),
            )
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM approval_rate_limit_hits WHERE key=?",
                (key,),
            ).fetchone()
            count = row["cnt"] if row is not None else 0

            if count >= self._max_requests:
                await self._log_rate_limited(tenant_id, user_id, tool_name)
                return False

            self._conn.execute(
                "INSERT INTO approval_rate_limit_hits (key, hit_time) VALUES (?, ?)",
                (key, now),
            )
        return True

    async def _log_rate_limited(
        self,
        tenant_id: str | None,
        user_id: str | None,
        tool_name: str,
    ) -> None:
        if self._audit_logger is None:
            return
        try:
            await self._audit_logger.log(AuditEvent(
                event_id=_make_event_id(),
                run_id=None,
                event_type="approval.rate_limited",
                user_id=user_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                data={
                    "max_requests": self._max_requests,
                    "window_seconds": self._window_seconds,
                },
            ))
        except Exception:
            pass

    def close(self) -> None:
        self._conn.close()


def create_approval_rate_limiter(
    backend: str = "memory",
    max_requests: int = 10,
    window_seconds: int = 60,
    db_path: str | None = None,
    audit_logger: AuditLogger | None = None,
) -> ApprovalRateLimiter:
    """Factory for creating an ApprovalRateLimiter backend."""
    if backend == "memory":
        return InMemoryApprovalRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            audit_logger=audit_logger,
        )
    if backend == "sqlite":
        return SQLiteApprovalRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            db_path=db_path or ".agent_app/approval_rate_limit.db",
            audit_logger=audit_logger,
        )
    raise ValueError(f"Unknown rate limiter backend '{backend}'. Supported: 'memory', 'sqlite'.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_approval_rate_limit.py -v`
Expected: all PASS (existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/approval_rate_limit.py tests/unit/test_approval_rate_limit.py
git commit -m "feat: add SQLite backend for ApprovalRateLimiter

Previously only an in-memory, process-local implementation existed —
rate limit state was lost on restart and never shared across multiple
instances, contradicting the multi-instance production readiness theme
established in Phase 58/59."
```

---

## Task 4: Wire RateLimitConfig backend selection through config schema and loader

**Files:**
- Modify: `agent_app/config/schema.py:205-210`
- Modify: `agent_app/config/loader.py:218-222`
- Test: `tests/unit/test_config_loader.py`

**Important — `rate_limiter` is not stored anywhere on `AgentApp`.** Tracing the actual data flow: `build_app()` constructs a local `rate_limiter` variable (loader.py:218-222) and passes it into `_create_backend(..., rate_limiter=rate_limiter)` (loader.py:342), which only forwards it into a `ToolExecutor(rate_limiter=rate_limiter)` — and *only* when `runtime_cfg.backend == "openai"` AND both `approval_store`/`audit_logger` are configured (loader.py:1495-1533). For the `dry_run` backend (the default, and the only backend usable without the `openai` extra installed), the rate limiter is silently discarded. There is no existing test anywhere in the repo that exercises this wiring through `build_app()` (`grep -rln "InMemoryApprovalRateLimiter\|RateLimitConfig" tests/unit/*.py` only returns files testing the limiter classes directly, never through the loader) — so there's no established pattern to copy, and requiring the `openai` extra just to unit-test a config-parsing branch would be disproportionate. Instead, test the *local variable construction logic* directly by monkeypatching the collaborator factory function and asserting on how the loader calls it — a standard technique for verifying a caller wires up a collaborator correctly without needing to inspect the collaborator's own internals afterward.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config_loader.py`:

```python
def test_build_app_wires_sqlite_rate_limiter(tmp_path, monkeypatch):
    calls = []

    def fake_create_approval_rate_limiter(**kwargs):
        calls.append(kwargs)
        from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
        return InMemoryApprovalRateLimiter(
            max_requests=kwargs["max_requests"], window_seconds=kwargs["window_seconds"]
        )

    import agent_app.runtime.approval_rate_limit as rl_module
    monkeypatch.setattr(rl_module, "create_approval_rate_limiter", fake_create_approval_rate_limiter)

    config_path = tmp_path / "agentapp.yaml"
    db_path = str(tmp_path / "rl.db")
    config_path.write_text(f"""
app:
  name: test-app
governance:
  rate_limit:
    max_requests: 5
    window_seconds: 60
    backend: sqlite
    db_path: {db_path}
""")
    build_app(str(config_path))

    assert len(calls) == 1
    assert calls[0]["backend"] == "sqlite"
    assert calls[0]["max_requests"] == 5
    assert calls[0]["window_seconds"] == 60
    assert calls[0]["db_path"] == db_path


def test_build_app_wires_memory_rate_limiter_by_default(tmp_path, monkeypatch):
    calls = []

    def fake_create_approval_rate_limiter(**kwargs):
        calls.append(kwargs)
        from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
        return InMemoryApprovalRateLimiter(
            max_requests=kwargs["max_requests"], window_seconds=kwargs["window_seconds"]
        )

    import agent_app.runtime.approval_rate_limit as rl_module
    monkeypatch.setattr(rl_module, "create_approval_rate_limiter", fake_create_approval_rate_limiter)

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
governance:
  rate_limit:
    max_requests: 5
    window_seconds: 60
""")
    build_app(str(config_path))

    assert len(calls) == 1
    assert calls[0]["backend"] == "memory"
```

Note: `loader.py` must do `from agent_app.runtime.approval_rate_limit import create_approval_rate_limiter` (a plain module-level import used as `create_approval_rate_limiter(...)`) for `monkeypatch.setattr(rl_module, "create_approval_rate_limiter", ...)` to actually intercept the call — if Task 4 Step 4 instead does `from agent_app.runtime.approval_rate_limit import create_approval_rate_limiter` *inside* the `if` block as a local import (which is this codebase's dominant style, confirmed throughout `loader.py`), the monkeypatch still works correctly because Python re-resolves the name from the module's namespace at call time on each local import — just make sure the patch targets `agent_app.runtime.approval_rate_limit.create_approval_rate_limiter` (the definition site), not `agent_app.config.loader.create_approval_rate_limiter` (which doesn't exist as a persistent name there).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py::test_build_app_wires_sqlite_rate_limiter -v`
Expected: FAIL — `pydantic.ValidationError` for the unrecognized `backend`/`db_path` fields (since `RateLimitConfig` doesn't have them yet), or if pydantic is configured to ignore unknown fields, FAIL because `calls` stays empty (`create_approval_rate_limiter` doesn't exist yet, so the loader still calls `InMemoryApprovalRateLimiter` directly and the monkeypatch never intercepts anything).

- [ ] **Step 3: Extend `RateLimitConfig` in schema.py**

Find (schema.py:205-210):

```python
class RateLimitConfig(BaseModel):
    """Approval rate limiting configuration (Phase 21)."""

    max_requests: int = Field(default=10, ge=1, description="Max approval requests per window")
    window_seconds: int = Field(default=60, ge=1, description="Rate limit window in seconds")
```

Replace with:

```python
class RateLimitConfig(BaseModel):
    """Approval rate limiting configuration (Phase 21)."""

    max_requests: int = Field(default=10, ge=1, description="Max approval requests per window")
    window_seconds: int = Field(default=60, ge=1, description="Rate limit window in seconds")
    backend: str = Field(default="memory", description="Rate limiter backend: memory | sqlite (Phase 65)")
    db_path: str | None = Field(default=None, description="SQLite db path (Phase 65)")

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, v: str) -> str:
        if v not in ("memory", "sqlite"):
            raise ValueError(f"Invalid backend '{v}'. Must be: memory, sqlite")
        return v
```

- [ ] **Step 4: Branch on backend in loader.py**

Find (loader.py:218-222):

```python
    rate_limiter: Any = None
    if gov and getattr(gov, "rate_limit", None):
        from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
        rl_cfg = gov.rate_limit
        rate_limiter = InMemoryApprovalRateLimiter(
```

Read the next few lines after this (`.venv/bin/python -c "..."` or just open the file around line 218-230) to see the full existing constructor call before editing — replace the whole block with:

```python
    rate_limiter: Any = None
    if gov and getattr(gov, "rate_limit", None):
        from agent_app.runtime.approval_rate_limit import create_approval_rate_limiter
        rl_cfg = gov.rate_limit
        rate_limiter = create_approval_rate_limiter(
            backend=getattr(rl_cfg, "backend", "memory"),
            max_requests=rl_cfg.max_requests,
            window_seconds=rl_cfg.window_seconds,
            db_path=getattr(rl_cfg, "db_path", None),
```

Keep whatever trailing kwargs the original call had (e.g. `audit_logger=...`) — read the original block first with:

```bash
sed -n '218,235p' agent_app/config/loader.py
```

and preserve every kwarg it currently passes beyond `max_requests`/`window_seconds`, just switching the constructor from `InMemoryApprovalRateLimiter(...)` to `create_approval_rate_limiter(backend=..., ...)` with the same remaining kwargs plus `backend` and `db_path` added.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py -k rate_limiter -v`
Expected: PASS

- [ ] **Step 6: Run full config loader test file**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py -v`
Expected: all PASS, 0 failures

- [ ] **Step 7: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py tests/unit/test_config_loader.py
git commit -m "feat: wire rate_limit.backend config through to SQLiteApprovalRateLimiter"
```

---

## Task 5: Wire `require_promotion_approval` into promotion execution

**Files:**
- Modify: `agent_app/runtime/policy_release.py` (constructor around line 52-99, `execute_promotion` around line 620)
- Modify: `agent_app/config/loader.py:544-561`
- Test: `tests/unit/test_policy_release.py` (class `TestPolicyReleaseServiceRBAC`, which already has a `_make_service()` helper at line 360 and an existing `test_execute_pending_fails` test at line 428 that this task's second new test mirrors)

- [ ] **Step 1: Write the failing tests**

Add these two tests to `tests/unit/test_policy_release.py`, inside `class TestPolicyReleaseServiceRBAC` (right after the existing `test_execute_approved_activates_bundle` method, which ends around line 453):

```python
    async def test_execute_pending_succeeds_when_approval_not_required(self):
        service = PolicyReleaseService(
            bundle_store=InMemoryPolicyBundleStore(),
            replay_runner=_make_mock_replay_runner(),
            replay_store=_make_mock_replay_store(),
            gate_evaluator=_make_default_evaluator(),
            gate_store=InMemoryPolicyGateStore(),
            promotion_store=InMemoryPromotionRequestStore(),
            permission_checker=PolicyReleasePermissionChecker(),
            require_promotion_approval=False,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        # req.status is PENDING here — no approve_promotion() call.
        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        assert result.status == PolicyBundleStatus.ACTIVE

    async def test_execute_pending_still_fails_by_default(self):
        # Regression guard: default behavior (require_promotion_approval=True,
        # the implicit default before this test existed) must be unchanged.
        service = self._make_service()
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context(permissions=["policy.promotion.request", "policy.promotion.execute"])
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        with pytest.raises(ValueError, match="must be approved"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
```

The second test is functionally identical to the existing `test_execute_pending_fails` (line 428) — it's a deliberate regression guard confirming Task 5's change doesn't alter default behavior, kept as a separate test so its failure would clearly point at the new code path rather than requiring cross-referencing the pre-existing test.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_policy_release.py -k "test_execute_pending_succeeds_when_approval_not_required" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'require_promotion_approval'`

- [ ] **Step 3: Add the constructor parameter**

In `agent_app/runtime/policy_release.py`, find the `__init__` signature (currently ending around line 74):

```python
        release_gate_automation_service: Any = None,
        require_simulation_gate_for_promotion: bool = False,
        simulation_gate_max_age_seconds: int | None = None,
    ) -> None:
```

Add `require_promotion_approval: bool = True,` right after `require_simulation_gate_for_promotion: bool = False,`:

```python
        release_gate_automation_service: Any = None,
        require_simulation_gate_for_promotion: bool = False,
        require_promotion_approval: bool = True,
        simulation_gate_max_age_seconds: int | None = None,
    ) -> None:
```

Find the corresponding assignment block (currently ending around line 99):

```python
        self._release_gate_automation_service = release_gate_automation_service
        self._require_simulation_gate_for_promotion = require_simulation_gate_for_promotion
        self._simulation_gate_max_age_seconds = simulation_gate_max_age_seconds
```

Add the new assignment:

```python
        self._release_gate_automation_service = release_gate_automation_service
        self._require_simulation_gate_for_promotion = require_simulation_gate_for_promotion
        self._require_promotion_approval = require_promotion_approval
        self._simulation_gate_max_age_seconds = simulation_gate_max_age_seconds
```

- [ ] **Step 4: Gate the status check in `execute_promotion`**

Find (currently at policy_release.py:620-624):

```python
        if request.status != PromotionRequestStatus.APPROVED:
            raise ValueError(
                f"Cannot execute promotion '{promotion_id}': "
                f"request status is '{request.status}', must be approved."
            )
```

Replace with:

```python
        if self._require_promotion_approval and request.status != PromotionRequestStatus.APPROVED:
            raise ValueError(
                f"Cannot execute promotion '{promotion_id}': "
                f"request status is '{request.status}', must be approved."
            )
```

- [ ] **Step 5: Wire the config value through loader.py**

Find (loader.py:544-561):

```python
        release_service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=replay_runner,
            replay_store=None,
            gate_evaluator=gate_evaluator,
            gate_store=gate_store,
            promotion_store=promotion_store,
            allow_gate_bypass=getattr(release_config, "allow_gate_bypass", False),
```

Add `require_promotion_approval=getattr(release_config, "require_promotion_approval", True),` right after the `allow_gate_bypass=...,` line:

```python
        release_service = PolicyReleaseService(
            bundle_store=bundle_store,
            replay_runner=replay_runner,
            replay_store=None,
            gate_evaluator=gate_evaluator,
            gate_store=gate_store,
            promotion_store=promotion_store,
            allow_gate_bypass=getattr(release_config, "allow_gate_bypass", False),
            require_promotion_approval=getattr(release_config, "require_promotion_approval", True),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_policy_release.py -k "execute_promotion or execute_pending" -v`
Expected: all PASS, including the pre-existing `test_execute_pending_fails` and `test_execute_rejected_fails` (proving no regression on the default `True` path)

- [ ] **Step 7: Run the full policy_release test suite**

```bash
.venv/bin/pytest tests/unit/test_policy_release.py tests/unit/test_policy_release_phase34.py tests/unit/test_policy_release_phase31.py tests/unit/test_policy_release_gate_integration.py -v
```
Expected: 0 failures.

- [ ] **Step 8: Commit**

```bash
git add agent_app/runtime/policy_release.py agent_app/config/loader.py tests/unit/test_policy_release.py
git commit -m "fix: require_promotion_approval config now actually gates execute_promotion

Previously execute_promotion() unconditionally required APPROVED status
regardless of this config value (fail-safe but non-functional). Now
require_promotion_approval=false allows executing promotions directly
from PENDING status; the default (true) preserves existing behavior."
```

---

## Task 6: Backfill release checklist doc — Phase 59

**Files:**
- Create: `docs/release_checklist_phase59.md`

- [ ] **Step 1: Write the doc**

```markdown
# Release Checklist — Phase 59

**Version:** v0.44.0
**Phase:** Multi-Instance Production Readiness — DLQ Replay Safety & Alert Delivery Observability
**Date:** 2026-06-23

## Implementation Checklist

- [x] DLQ replay idempotency tracking (prevents duplicate replay attempts, configurable TTL expiry)
- [x] DLQ replay rate limiter (per-target sliding window, configurable max attempts)
- [x] Priority queue dead letter policy (automatic DLQ promotion on max-retry exceedance)
- [x] Enhanced metrics service (unified snapshot of replay, rate limiter, and dead letter metrics)
- [x] Webhook key rotation service (automatic signing key rotation with interval + history)
- [x] Distributed lock service (multi-instance coordination, TTL-based expiry, fencing tokens)
- [x] Console pages: idempotency, rate limiting, dead letter, metrics, key rotation, distributed lock
- [x] CLI commands: idempotency check/prune, rate-limit check/reset, dead-letter evaluate/list, metrics snapshot, key-rotation status/rotate/history
- [x] FastAPI endpoints for DLQ replay idempotency, rate limiting, enhanced metrics, key rotation
- [x] 18 new PolicyChangeEventType values for Phase 59 events
- [x] Config schema extensions for all Phase 59 services (InMemory + SQLite backends)

## Test Coverage

- [x] Notification delivery pipeline integration tests (idempotency, rate limiting, dead letter checks)
- [x] Console router regression tests (Phase 59 routes ordered before catch-all)
- [x] PolicyChangeEventType count verified: 156 → 174

## Acceptance Criteria

- [x] DLQ replay does not duplicate attempts within the idempotency TTL window
- [x] Replay rate limiter blocks excess attempts per target within the sliding window
- [x] Priority queue items exceeding max retries are promoted to DLQ automatically
- [x] Enhanced metrics snapshot aggregates replay/rate-limit/dead-letter counters
- [x] Webhook signing keys rotate automatically at the configured interval, with history retained
- [x] Distributed lock prevents concurrent multi-instance processing via TTL + fencing tokens
- [x] Console pages render for all six Phase 59 service areas
- [x] CLI commands exit 0 on success, non-zero on failure, support --json where applicable
- [x] FastAPI endpoints return correct status codes for all Phase 59 services
- [x] Existing Phase 57/58 behavior remains backward compatible
```

- [ ] **Step 2: Commit**

```bash
git add docs/release_checklist_phase59.md
git commit -m "docs: backfill missing Phase 59 release checklist"
```

---

## Task 7: Backfill release checklist doc — Phase 60

**Files:**
- Create: `docs/release_checklist_phase60.md`

- [ ] **Step 1: Write the doc**

```markdown
# Release Checklist — Phase 60

**Version:** v0.45.0
**Phase:** Alert Delivery Closed Loop + Production Validation
**Date:** 2026-06-25

## Implementation Checklist

- [x] Retry daemon closed-loop integration: claim → rate limit → idempotency → deliver → ack/requeue/dead-letter
- [x] Distributed lock leader election in daemon: single-instance processing with fencing tokens
- [x] Key rotation auto-scheduling in daemon loop (triggers rotation when due during run_once)
- [x] Enhanced metrics recording in daemon (replay, rate limiter, dead letter, distributed lock counters)
- [x] Prometheus metrics endpoint: `/federation/notifications/metrics/prometheus`
- [x] CLI `metrics-prometheus` command (Prometheus text format)
- [x] CLI `daemon run-once` command (single daemon tick with full Phase 59 store wiring)
- [x] Daemon config extensions: distributed_lock, key_rotation, rate_limit, idempotency, dead_letter fields
- [x] 15 new unit tests for daemon closed-loop integration (test_phase60_daemon_closed_loop.py)

## Test Coverage

- [x] 15 daemon closed-loop integration tests
- [x] Daemon health status includes lock owner, key rotation, Phase 59 store availability

## Acceptance Criteria

- [x] `run_once()` executes the full closed-loop chain without manual store wiring
- [x] Daemon correctly acquires/releases distributed lock leadership
- [x] Key rotation triggers automatically when the configured interval elapses
- [x] Prometheus endpoint returns valid text-format metrics
- [x] `daemon run-once` CLI command executes a single tick end-to-end
- [x] `python-multipart` and `fakeredis` dev dependencies fix previously-failing console/Redis tests
- [x] Existing Phase 59 behavior remains backward compatible
```

- [ ] **Step 2: Commit**

```bash
git add docs/release_checklist_phase60.md
git commit -m "docs: backfill missing Phase 60 release checklist"
```

---

## Task 8: Backfill release checklist doc — Phase 61

**Files:**
- Create: `docs/release_checklist_phase61.md`

- [ ] **Step 1: Write the doc**

```markdown
# Release Checklist — Phase 61

**Version:** v0.46.0
**Phase:** Daemon Production Runtime Hardening
**Date:** 2026-06-27

## Implementation Checklist

- [x] Continuous daemon loop: `_loop()` runs indefinitely with leader/standby mode switching
- [x] Daemon start/stop lifecycle: `start()` acquires distributed lock, `stop()` releases + flushes metrics
- [x] Lock renewal in loop: `_should_renew_lock()` + `_renew_distributed_lock()` on interval
- [x] Health status model: `get_health_status()` returns stopped/healthy/degraded/unhealthy
- [x] YAML config support: `AlertDeliveryRetryDaemonConfig` loads from YAML dict pattern
- [x] Prometheus file metrics exporter: `PrometheusFileMetricsExporter` (atomic writes)
- [x] Async dead-letter evaluation: `evaluate_async()` wraps sync `evaluate()` via `asyncio.to_thread()`
- [x] CLI `daemon health` command (JSON optional)
- [x] CLI `daemon validate-config` command
- [x] CLI `metrics-export` command (Prometheus text file export)
- [x] Config defaults: poll_interval_seconds=1.0, idle_sleep_seconds=1.0, error_sleep_seconds=5.0, max_consecutive_errors=10, shutdown_timeout_seconds=10.0
- [x] 59 new Phase 61 unit tests across 7 test files

## Test Coverage

- [x] 59 unit tests across 7 test files covering loop, lifecycle, health, config, metrics export

## Acceptance Criteria

- [x] Daemon runs continuously via `_loop()` without manual re-invocation
- [x] Leader/standby switching works correctly under lock contention
- [x] Lock renewal keeps leadership alive across long-running loops
- [x] Health status accurately reflects stopped/healthy/degraded/unhealthy states
- [x] YAML-driven config loads correctly via `dict[str, Any]` pattern
- [x] Prometheus file exporter writes atomically (no partial-read races)
- [x] `daemon health` and `daemon validate-config` CLI commands return 0 when daemon not configured (informational, non-fatal)
- [x] Existing Phase 60 behavior remains backward compatible
```

- [ ] **Step 2: Commit**

```bash
git add docs/release_checklist_phase61.md
git commit -m "docs: backfill missing Phase 61 release checklist"
```

---

## Task 9: Backfill release checklist doc — Phase 63

**Files:**
- Create: `docs/release_checklist_phase63.md`

- [ ] **Step 1: Write the doc**

```markdown
# Release Checklist — Phase 63

**Version:** v0.48.0
**Phase:** Persistent Approval / Control Plane
**Date:** 2026-06-30

## Implementation Checklist

- [x] Persistent control plane store (`ControlPlaneStore`): SQLite-backed command state machine (PENDING → ACCEPTED → RUNNING → COMPLETED/FAILED/REJECTED/EXPIRED)
- [x] Persistent approval store (`PersistentApprovalStore`): operator approve/reject/expire lifecycle
- [x] Persistent audit store (`PersistentAuditStore`): append-only events, filterable by event_type/command_id/approval_id
- [x] Control HTTP server (`_ControlHTTPServer`): stdlib-based REST API, Bearer token authentication
- [x] Daemon control polling: background asyncio task polls pending commands, executes, writes audit events
- [x] Daemon control commands: pause, resume, drain, shutdown, flush_metrics, release_lock, health_snapshot
- [x] Health status extensions: control_plane_enabled, control_paused, last_control_command_id, last_control_error, pending_control_commands, pending_approvals
- [x] CLI control commands: `daemon control status`, `daemon control commands list/send/get`
- [x] Config extensions: control_plane_enabled, control_plane_db_path, control_command_poll_interval_seconds, control_http_*
- [x] 79 new Phase 63 unit tests across 5 test files

## Test Coverage

- [x] 79 unit tests across 5 test files (control plane store, approval store, audit store, control server, daemon control commands)

## Acceptance Criteria

- [x] Control commands transition through the full state machine correctly
- [x] Approval requests support operator approve/reject/expire lifecycle
- [x] Audit events are append-only and filterable
- [x] Control HTTP server enforces Bearer token authentication
- [x] Daemon correctly executes all 7 control command types
- [x] Health status reflects control plane and pending-command/approval counts
- [x] CLI control commands round-trip correctly against the HTTP API
- [x] Existing Phase 62 behavior remains backward compatible
```

- [ ] **Step 2: Commit**

```bash
git add docs/release_checklist_phase63.md
git commit -m "docs: backfill missing Phase 63 release checklist"
```

---

## Task 10: Test that the 4 backfilled docs exist and have required structure

**Files:**
- Create: `tests/unit/test_release_checklist_docs_phase65.py`

- [ ] **Step 1: Write the test**

```python
"""Phase 65 — verify backfilled release checklist docs exist and are structured correctly."""
from __future__ import annotations

import os

import pytest

DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))

BACKFILLED_PHASES = [59, 60, 61, 63]


@pytest.mark.parametrize("phase", BACKFILLED_PHASES)
def test_checklist_exists(phase: int) -> None:
    path = os.path.join(DOCS_DIR, f"release_checklist_phase{phase}.md")
    assert os.path.isfile(path), f"Missing {path}"


@pytest.mark.parametrize("phase", BACKFILLED_PHASES)
def test_checklist_has_required_sections(phase: int) -> None:
    path = os.path.join(DOCS_DIR, f"release_checklist_phase{phase}.md")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert f"# Release Checklist — Phase {phase}" in content
    assert "## Implementation Checklist" in content
    assert "## Test Coverage" in content
    assert "## Acceptance Criteria" in content
    assert "**Version:**" in content
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_release_checklist_docs_phase65.py -v`
Expected: all PASS (docs were already created in Tasks 6-9)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_release_checklist_docs_phase65.py
git commit -m "test: verify Phase 65 backfilled release checklist docs"
```

---

## Task 11: OpenTelemetry — config schema and dependency

**Files:**
- Modify: `agent_app/config/schema.py:226-234`
- Modify: `pyproject.toml:36-39`

- [ ] **Step 1: Add the OTLP exporter optional dependency**

Find in `pyproject.toml`:

```toml
otel = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
]
```

Replace with:

```toml
otel = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-http>=1.20",
]
```

- [ ] **Step 2: Extend `TracingConfig` in schema.py**

Find (schema.py:226-234):

```python
class TracingConfig(BaseModel):
    """Observability tracing configuration."""

    type: str = Field(default="memory", description="Tracer type: noop | memory | jsonl")
    path: str | None = Field(default=None, description="Path for jsonl tracer")
    include_inputs: bool = Field(default=False, description="Include inputs in events")
    include_outputs: bool = Field(default=False, description="Include outputs in events")
    max_traces: int | None = Field(default=None, description="Max traces to retain in memory")
    max_events_per_trace: int | None = Field(default=None, description="Max events per trace in memory")
```

Replace with:

```python
class TracingConfig(BaseModel):
    """Observability tracing configuration."""

    type: str = Field(default="memory", description="Tracer type: noop | memory | jsonl | otel")
    path: str | None = Field(default=None, description="Path for jsonl tracer")
    include_inputs: bool = Field(default=False, description="Include inputs in events")
    include_outputs: bool = Field(default=False, description="Include outputs in events")
    max_traces: int | None = Field(default=None, description="Max traces to retain in memory")
    max_events_per_trace: int | None = Field(default=None, description="Max events per trace in memory")
    # Phase 65: OpenTelemetry exporter options
    otel_service_name: str = Field(default="agent-app", description="OTel resource service.name")
    otel_exporter: str = Field(default="console", description="OTel span exporter: console | otlp")
    otel_otlp_endpoint: str | None = Field(default=None, description="OTLP HTTP endpoint (required when otel_exporter=otlp)")

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in ("noop", "memory", "jsonl", "otel"):
            raise ValueError(f"Invalid tracer type '{v}'. Must be: noop, memory, jsonl, otel")
        return v

    @field_validator("otel_exporter")
    @classmethod
    def _validate_otel_exporter(cls, v: str) -> str:
        if v not in ("console", "otlp"):
            raise ValueError(f"Invalid otel_exporter '{v}'. Must be: console, otlp")
        return v
```

- [ ] **Step 3: Write a schema-level test**

Add to `tests/unit/test_observability.py`, inside `class TestTracingConfigRetention` (line 1135 — the existing class already has two tests using `from agent_app.config.schema import TracingConfig` as a local import inside each test method; follow that same local-import style rather than adding a module-level import):

```python
    def test_accepts_otel_type(self) -> None:
        from agent_app.config.schema import TracingConfig
        cfg = TracingConfig(type="otel")
        assert cfg.type == "otel"
        assert cfg.otel_service_name == "agent-app"
        assert cfg.otel_exporter == "console"

    def test_rejects_invalid_type(self) -> None:
        from agent_app.config.schema import TracingConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TracingConfig(type="invalid")

    def test_rejects_invalid_otel_exporter(self) -> None:
        from agent_app.config.schema import TracingConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TracingConfig(otel_exporter="invalid")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_observability.py -k TestTracingConfigRetention -v`
Expected: PASS

- [ ] **Step 5: Run full observability test file to check for regressions**

Run: `.venv/bin/pytest tests/unit/test_observability.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add agent_app/config/schema.py pyproject.toml tests/unit/test_observability.py
git commit -m "feat: add otel type + otel_exporter/otel_otlp_endpoint fields to TracingConfig"
```

---

## Task 12: OpenTelemetry — `OtelTraceCollector` implementation

**Files:**
- Modify: `agent_app/observability/otel.py`
- Test: `tests/unit/test_observability_otel.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_observability_otel.py`:

```python
"""Phase 65 — OpenTelemetry trace collector tests."""
from __future__ import annotations

import pytest

from agent_app.observability.events import RunEvent, RunEventType


def _has_otel() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
        return True
    except ImportError:
        return False


class TestOpenTelemetryNotInstalledError:
    def test_error_message_has_install_hint(self):
        from agent_app.observability.otel import OpenTelemetryNotInstalledError
        err = OpenTelemetryNotInstalledError()
        assert "pip install" in str(err)
        assert "otel" in str(err)


@pytest.mark.skipif(not _has_otel(), reason="opentelemetry not installed")
class TestOtelTraceCollector:
    @pytest.mark.asyncio
    async def test_record_and_get_events_roundtrip(self):
        """Protocol conformance: record() then get_events() must return it back,
        proving the dual-write design (OTel export + in-memory read-back buffer)."""
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(service_name="test-service", exporter="console")
        event = RunEvent(
            trace_id="trace-1",
            event_type=RunEventType.RUN_STARTED,
            run_id="run-1",
            status="started",
        )
        await collector.record(event)
        events = await collector.get_events("trace-1")
        assert len(events) == 1
        assert events[0].event_id == event.event_id

    @pytest.mark.asyncio
    async def test_list_traces_returns_recorded_trace_ids(self):
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(service_name="test-service", exporter="console")
        await collector.record(RunEvent(trace_id="trace-a", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="trace-b", event_type=RunEventType.RUN_STARTED))
        traces = await collector.list_traces()
        assert set(traces) == {"trace-a", "trace-b"}

    @pytest.mark.asyncio
    async def test_deterministic_otel_trace_id_for_same_run_event_trace_id(self):
        """Two events sharing the same RunEvent.trace_id must map to the same
        OTel trace ID, so span-correlation in an external backend works."""
        from agent_app.observability.otel import _otel_trace_id_from_string

        tid1 = _otel_trace_id_from_string("trace-xyz")
        tid2 = _otel_trace_id_from_string("trace-xyz")
        tid3 = _otel_trace_id_from_string("trace-different")
        assert tid1 == tid2
        assert tid1 != tid3

    @pytest.mark.asyncio
    async def test_respects_max_traces_retention(self):
        from agent_app.observability.otel import OtelTraceCollector

        collector = OtelTraceCollector(
            service_name="test-service", exporter="console", max_traces=2
        )
        await collector.record(RunEvent(trace_id="t1", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="t2", event_type=RunEventType.RUN_STARTED))
        await collector.record(RunEvent(trace_id="t3", event_type=RunEventType.RUN_STARTED))
        traces = await collector.list_traces(limit=100)
        assert len(traces) == 2


def test_otel_import_failure_raises_clear_error(monkeypatch):
    """When opentelemetry packages are absent, constructing OtelTraceCollector
    must raise OpenTelemetryNotInstalledError, not a bare ImportError."""
    import sys
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    from agent_app.observability.otel import OtelTraceCollector, OpenTelemetryNotInstalledError

    with pytest.raises(OpenTelemetryNotInstalledError):
        OtelTraceCollector(service_name="test-service")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_observability_otel.py -v`
Expected: FAIL — `ImportError: cannot import name 'OtelTraceCollector'` and `cannot import name '_otel_trace_id_from_string'`

- [ ] **Step 3: Implement `OtelTraceCollector` in `agent_app/observability/otel.py`**

First, fix the pre-existing latent bug in this file — `Any` is used in type hints (`self._tracer: Any = None`, `def get_spans(self) -> list[Any]:`) but never imported (only doesn't crash because of `from __future__ import annotations` making all annotations lazy strings). Add the import at the top:

```python
from __future__ import annotations

from typing import Any

from agent_app.observability.events import RunEvent
```

Then append the following to the end of the file (after the existing `OpenTelemetryTraceExporter` class):

```python
def _otel_trace_id_from_string(s: str) -> int:
    """Deterministically derive a 128-bit OTel trace ID from a RunEvent.trace_id string."""
    import hashlib
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(digest, 16)


def _otel_span_id_from_string(s: str) -> int:
    """Deterministically derive a 64-bit OTel span ID from a RunEvent.event_id string."""
    import hashlib
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()[:16]
    return int(digest, 16)


class OtelTraceCollector:
    """TraceCollector Protocol implementation backed by OpenTelemetry.

    Dual-writes every recorded RunEvent:
    1. Converts it to an OTel span and exports via the configured exporter
       (console or OTLP HTTP).
    2. Buffers it in an internal InMemoryTraceCollector so existing
       get_events()/list_traces() callers (FastAPI trace endpoints, CLI
       trace commands) keep working even though OTLP export itself is
       fire-and-forget and not readable back locally.

    Args:
        service_name: OTel resource service.name.
        exporter: "console" (OTel SDK ConsoleSpanExporter, no extra deps
                  beyond opentelemetry-sdk) or "otlp" (requires
                  opentelemetry-exporter-otlp-proto-http and otlp_endpoint).
        otlp_endpoint: Required when exporter="otlp".
        max_traces: Passed through to the internal InMemoryTraceCollector.
        max_events_per_trace: Passed through to the internal InMemoryTraceCollector.

    Raises:
        OpenTelemetryNotInstalledError: If OpenTelemetry packages are missing.
    """

    def __init__(
        self,
        service_name: str = "agent-app",
        exporter: str = "console",
        otlp_endpoint: str | None = None,
        max_traces: int | None = None,
        max_events_per_trace: int | None = None,
    ) -> None:
        from agent_app.observability.collector import InMemoryTraceCollector

        self._service_name = service_name
        self._exporter_type = exporter
        self._otlp_endpoint = otlp_endpoint
        self._buffer = InMemoryTraceCollector(
            max_traces=max_traces, max_events_per_trace=max_events_per_trace
        )
        self._tracer: Any = None
        self._setup_tracer()

    def _setup_tracer(self) -> None:
        try:
            from opentelemetry import trace as _trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
        except ImportError as exc:
            raise OpenTelemetryNotInstalledError() from exc

        resource = Resource.create({"service.name": self._service_name})
        provider = TracerProvider(resource=resource)

        if self._exporter_type == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
            except ImportError as exc:
                raise OpenTelemetryNotInstalledError() from exc
            span_exporter = OTLPSpanExporter(endpoint=self._otlp_endpoint)
        else:
            span_exporter = ConsoleSpanExporter()

        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        _trace.set_tracer_provider(provider)
        self._tracer = _trace.get_tracer("agent_app")

    async def record(self, event: RunEvent) -> None:
        """Record an event: buffer for read-back and export as an OTel span."""
        await self._buffer.record(event)
        self._export_span(event)

    def _export_span(self, event: RunEvent) -> None:
        from opentelemetry import trace as _trace
        from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan

        event_type = str(event.event_type.value if hasattr(event.event_type, "value") else event.event_type)
        trace_id = _otel_trace_id_from_string(event.trace_id)
        span_id = _otel_span_id_from_string(event.event_id)

        parent_context = _trace.set_span_in_context(
            NonRecordingSpan(SpanContext(
                trace_id=trace_id,
                span_id=span_id,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            ))
        )

        with self._tracer.start_as_current_span(event_type, context=parent_context) as span:
            span.set_attribute("agent_app.trace_id", event.trace_id)
            span.set_attribute("agent_app.event_id", event.event_id)
            if event.run_id:
                span.set_attribute("agent_app.run_id", event.run_id)
            if event.user_id:
                span.set_attribute("agent_app.user_id", event.user_id)
            if event.tenant_id:
                span.set_attribute("agent_app.tenant_id", event.tenant_id)
            if event.workflow_name:
                span.set_attribute("agent_app.workflow_name", event.workflow_name)
            if event.agent_name:
                span.set_attribute("agent_app.agent_name", event.agent_name)
            if event.tool_name:
                span.set_attribute("agent_app.tool_name", event.tool_name)
            if event.status:
                span.set_attribute("agent_app.status", event.status)
            if event.error:
                span.set_attribute("agent_app.error_type", event.error.get("type", ""))
                span.record_exception(Exception(event.error.get("message", "")))
            for k, v in event.data.items():
                if isinstance(v, (str, int, float, bool)):
                    span.set_attribute(f"agent_app.data.{k}", v)

    async def get_events(self, trace_id: str) -> list[RunEvent]:
        return await self._buffer.get_events(trace_id)

    async def list_traces(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        return await self._buffer.list_traces(tenant_id=tenant_id, run_id=run_id, limit=limit)
```

- [ ] **Step 4: Install the otel extra locally so tests can run non-skipped**

```bash
.venv/bin/pip install 'opentelemetry-api>=1.20' 'opentelemetry-sdk>=1.20' 'opentelemetry-exporter-otlp-proto-http>=1.20'
```

If this environment has no network access to install packages, leave the otel-dependent tests skipped (the `@pytest.mark.skipif(not _has_otel(), ...)` gate handles this gracefully) and note it in the final report — do not treat a skip as a failure.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_observability_otel.py -v`
Expected: all PASS (or all SKIPPED for the `TestOtelTraceCollector` class if otel packages aren't installed — `test_otel_import_failure_raises_clear_error` and `TestOpenTelemetryNotInstalledError` must PASS regardless, since they don't require otel to be installed).

- [ ] **Step 6: Commit**

```bash
git add agent_app/observability/otel.py tests/unit/test_observability_otel.py
git commit -m "feat: add OtelTraceCollector implementing TraceCollector Protocol

Dual-writes every RunEvent: exports as an OTel span (console or OTLP HTTP)
while buffering in an internal InMemoryTraceCollector so existing
get_events()/list_traces() consumers (FastAPI trace endpoints, CLI trace
commands) continue working when tracing.type=otel is selected."
```

---

## Task 13: OpenTelemetry — wire into config loader

**Files:**
- Modify: `agent_app/config/loader.py:347-361`
- Test: `tests/unit/test_config_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_loader.py`:

```python
def test_build_app_wires_otel_trace_collector(tmp_path):
    from agent_app.observability.otel import OtelTraceCollector

    if not _otel_installed():
        pytest.skip("opentelemetry not installed")

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
observability:
  tracing:
    type: otel
    otel_service_name: test-app
    otel_exporter: console
""")
    app = build_app(str(config_path))
    assert isinstance(app.trace_collector, OtelTraceCollector)


def test_build_app_raises_clear_error_when_otel_type_requested_without_package(tmp_path, monkeypatch):
    """Regression: misconfiguration must surface at build_app() time, not
    fail silently or crash deep inside a run."""
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    config_path = tmp_path / "agentapp.yaml"
    config_path.write_text("""
app:
  name: test-app
observability:
  tracing:
    type: otel
""")
    with pytest.raises(RuntimeError, match="OpenTelemetry"):
        build_app(str(config_path))


def _otel_installed() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
        return True
    except ImportError:
        return False
```

`AgentApp` stores the trace collector as the public attribute `self.trace_collector` (confirmed at `agent_app/core/app.py:114` — `self.trace_collector = trace_collector`; no underscore prefix, unlike most other internal app state).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py -k otel -v`
Expected: FAIL — `tracing.type: otel` currently falls through the existing `if/elif` chain with no matching branch, so `trace_collector` stays `None` and `isinstance(None, OtelTraceCollector)` is `False`.

- [ ] **Step 3: Add the loader branch**

Find (loader.py:347-361):

```python
    # -- Observability: trace collector (Phase 12) --
    trace_collector: Any = None
    obs_cfg = getattr(config, "observability", None)
    if obs_cfg and obs_cfg.tracing and obs_cfg.tracing.type != "noop":
        tracing_type = obs_cfg.tracing.type
        if tracing_type == "memory":
            from agent_app.observability.collector import InMemoryTraceCollector
            trace_collector = InMemoryTraceCollector(
                max_traces=obs_cfg.tracing.max_traces,
                max_events_per_trace=obs_cfg.tracing.max_events_per_trace,
            )
        elif tracing_type == "jsonl":
            from agent_app.observability.exporters import JSONLTraceCollector
            path = obs_cfg.tracing.path or ".agent_app/traces.jsonl"
            trace_collector = JSONLTraceCollector(path=path)
```

Replace with:

```python
    # -- Observability: trace collector (Phase 12, otel added Phase 65) --
    trace_collector: Any = None
    obs_cfg = getattr(config, "observability", None)
    if obs_cfg and obs_cfg.tracing and obs_cfg.tracing.type != "noop":
        tracing_type = obs_cfg.tracing.type
        if tracing_type == "memory":
            from agent_app.observability.collector import InMemoryTraceCollector
            trace_collector = InMemoryTraceCollector(
                max_traces=obs_cfg.tracing.max_traces,
                max_events_per_trace=obs_cfg.tracing.max_events_per_trace,
            )
        elif tracing_type == "jsonl":
            from agent_app.observability.exporters import JSONLTraceCollector
            path = obs_cfg.tracing.path or ".agent_app/traces.jsonl"
            trace_collector = JSONLTraceCollector(path=path)
        elif tracing_type == "otel":
            from agent_app.observability.otel import OtelTraceCollector, OpenTelemetryNotInstalledError
            try:
                trace_collector = OtelTraceCollector(
                    service_name=obs_cfg.tracing.otel_service_name,
                    exporter=obs_cfg.tracing.otel_exporter,
                    otlp_endpoint=obs_cfg.tracing.otel_otlp_endpoint,
                    max_traces=obs_cfg.tracing.max_traces,
                    max_events_per_trace=obs_cfg.tracing.max_events_per_trace,
                )
            except OpenTelemetryNotInstalledError as exc:
                raise RuntimeError(
                    f"tracing.type is 'otel' but OpenTelemetry is not installed: {exc}"
                ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_config_loader.py -k otel -v`
Expected: PASS (or the first test SKIPPED if otel isn't installed in this environment, and the second test — which doesn't require otel to be installed, since it mocks the import failure — must PASS)

- [ ] **Step 5: Run full config loader and full test suite regression check**

```bash
.venv/bin/pytest tests/unit/test_config_loader.py -v
```
Expected: all PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add agent_app/config/loader.py tests/unit/test_config_loader.py
git commit -m "feat: wire tracing.type=otel into build_app() via OtelTraceCollector

Misconfiguration (otel requested but package missing) now raises a clear
RuntimeError at build_app() time instead of failing silently or deep
inside a run."
```

---

## Task 14: Final documentation and version bump

**Files:**
- Modify: `pyproject.toml` (version)
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Bump version**

In `pyproject.toml`, change:

```toml
version = "0.49.0"
```

to:

```toml
version = "0.50.0"
```

- [ ] **Step 2: Add CHANGELOG entry**

At the very top of `CHANGELOG.md` (before the existing `## v0.49.0` line), insert:

```markdown
## v0.50.0 — Phase 65: Gap Closure — Webhook Signing Fix, Rate Limiter Persistence, OpenTelemetry

### Fixed
- Federation webhook signature service loader wiring: two independent bugs (wrong import module paths, invalid constructor kwargs) meant `webhook_signing.enabled: true` silently did nothing since Phase 51 — the failure was swallowed by a bare `except Exception: pass`. Now correctly wires `FederationWebhookSignatureService`.
- `nonce_replay_protection` config field: previously never read anywhere (even the CLI `webhook verify` command never passed `nonce_store` to `verify()`). Now the CLI verify command reads the config flag and nonce store off the app and enforces replay detection accordingly.
- `require_promotion_approval` config field: previously `execute_promotion()` unconditionally required `APPROVED` status regardless of this flag (fail-safe but non-functional). Now `false` allows executing promotions directly from a non-approved status; `true` (default) is unchanged.

### Added
- `SQLiteApprovalRateLimiter`: persistent, cross-instance approval rate limiting backend (WAL-mode SQLite), selectable via new `RateLimitConfig.backend`/`db_path` fields. Previously only an in-process, restart-losing in-memory limiter existed.
- `OtelTraceCollector`: OpenTelemetry integration as a fourth `TracingConfig.type` option (`otel`), dual-writing every `RunEvent` as both an OTel span (console or OTLP HTTP exporter) and to an internal in-memory buffer so existing `get_events()`/`list_traces()` consumers (FastAPI trace endpoints, CLI trace commands) keep working.
- `opentelemetry-exporter-otlp-proto-http` added to the `otel` optional dependency group.
- Backfilled `docs/release_checklist_phase{59,60,61,63}.md` — four phases that shipped without a standalone release checklist document (Phase 56 and 58 were investigated but found to have no standalone CHANGELOG entry of their own; their work was folded into Phase 57 and Phase 59 respectively, so no checklist was fabricated for them).

### Changed
- `PolicyReleaseService.__init__` gained `require_promotion_approval: bool = True` parameter.
- `ApprovalRateLimiter` construction in `config/loader.py` now goes through a `create_approval_rate_limiter()` factory instead of hardcoding `InMemoryApprovalRateLimiter`.
```

- [ ] **Step 3: Add README roadmap entry**

Find the `## Roadmap` section in `README.md` and add, right before the `v0.49.0` line:

```markdown
- **v0.50.0** — Phase 65 gap closure: fixed silently-broken webhook signature service loader wiring (dead since Phase 51), wired `nonce_replay_protection` and `require_promotion_approval` config fields to actually control behavior, added SQLite-backed persistent approval rate limiting, backfilled 4 missing release checklist docs, integrated OpenTelemetry as a fourth tracing exporter type (console/OTLP)
```

- [ ] **Step 4: Run the full test suite**

```bash
.venv/bin/pytest tests/unit -q
```

Expected: 0 failures. Compare the total passed count against the Phase 64 baseline (5477 passed) — it should now be higher by the number of new tests added across Tasks 1-13.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md README.md
git commit -m "docs: v0.50.0 — Phase 65 gap closure release notes"
```

---

## Final Verification Checklist

- [ ] All 14 tasks committed individually (frequent commits, not one giant commit)
- [ ] `pytest tests/unit -q` shows 0 failures
- [ ] `pytest tests/unit/test_config_loader.py -v` specifically passes (covers Tasks 1, 4, 13)
- [ ] Version bumped to 0.50.0 in `pyproject.toml`
- [ ] CHANGELOG.md and README.md both mention v0.50.0
- [ ] No fabricated documentation — Phase 56/58 checklists deliberately NOT created (documented reasoning in CHANGELOG)
