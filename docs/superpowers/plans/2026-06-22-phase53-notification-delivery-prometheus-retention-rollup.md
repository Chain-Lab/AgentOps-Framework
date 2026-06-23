# Phase 53: Federation Notification External Alert Delivery, Prometheus Export, Retention & Rollup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Externalize Phase 52 observability into alert delivery, Prometheus metrics, JSONL export, retention/archive/purge, and metrics rollup.

**Architecture:** New domain models + stores for alert delivery, Prometheus text export, JSONL structured export, retention service, hourly/daily rollup. Config extensions, CLI commands, console pages, audit/history wiring. All external adapters support memory/dry-run mode.

**Tech Stack:** Python, Pydantic, SQLite, Jinja2 (console), asyncio

---

## Task 1: Alert Delivery Models

**Files:**
- Create: `agent_app/governance/policy_rollout_federation_notification_alert_delivery.py`
- Test: `tests/unit/test_policy_notification_alert_delivery_models.py`

- [ ] **Step 1: Write failing tests**

```python
def test_alert_delivery_channel_type_enum():
    assert AlertDeliveryChannelType.WEBHOOK == "webhook"
    assert AlertDeliveryChannelType.EMAIL == "email"

def test_alert_delivery_status_enum():
    assert AlertDeliveryStatus.PENDING == "pending"

def test_alert_delivery_target_defaults():
    t = AlertDeliveryTarget(target_id="t1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    assert t.enabled is True
    assert t.severity_filter == []
    assert t.endpoint is None

def test_alert_delivery_target_with_filters():
    t = AlertDeliveryTarget(
        target_id="t1", name="Ops", channel_type=AlertDeliveryChannelType.WEBHOOK,
        endpoint="https://example.invalid/alerts",
        severity_filter=["critical"], channel_filter=["webhook"],
    )
    assert t.severity_filter == ["critical"]

def test_alert_delivery_attempt_defaults():
    now = datetime.now(timezone.utc)
    a = AlertDeliveryAttempt(attempt_id="nda_1", alert_id="nae_1", target_id="t1",
        channel_type=AlertDeliveryChannelType.WEBHOOK, status=AlertDeliveryStatus.PENDING, created_at=now)
    assert a.attempt == 1
    assert a.error_code is None

def test_alert_delivery_retry_policy_defaults():
    p = AlertDeliveryRetryPolicy()
    assert p.max_attempts == 3
    assert p.base_delay_seconds == 60

def test_sensitive_fields_sanitized_in_target():
    t = AlertDeliveryTarget(target_id="t1", name="Ops", channel_type=AlertDeliveryChannelType.WEBHOOK,
        headers={"authorization": "Bearer secret"}, metadata={"api_key": "xyz"})
    assert t.headers["authorization"] == "[REDACTED]"
    assert t.metadata["api_key"] == "[REDACTED]"

def test_sensitive_fields_sanitized_in_attempt():
    now = datetime.now(timezone.utc)
    a = AlertDeliveryAttempt(attempt_id="nda_1", alert_id="nae_1", target_id="t1",
        channel_type=AlertDeliveryChannelType.WEBHOOK, status=AlertDeliveryStatus.FAILED,
        error_message="auth failed: token=abc123", payload_preview={"key": "secret_value"},
        created_at=now)
    assert "abc123" not in a.error_message
    assert a.payload_preview.get("key") == "[REDACTED]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/governance/policy_rollout_federation_notification_alert_delivery.py` with:
- `AlertDeliveryChannelType(StrEnum)` — MEMORY, WEBHOOK, EMAIL, SLACK, CONSOLE
- `AlertDeliveryStatus(StrEnum)` — PENDING, DELIVERED, FAILED, RETRY_SCHEDULED, DLQ, SUPPRESSED
- `AlertDeliveryTarget(BaseModel)` — target_id, name, channel_type, enabled, severity_filter, channel_filter, federation_filter, endpoint, headers, metadata
- `AlertDeliveryAttempt(BaseModel)` — attempt_id, alert_id, target_id, channel_type, status, attempt, next_retry_at, error_code, error_message, payload_preview, created_at, delivered_at
- `AlertDeliveryRetryPolicy(BaseModel)` — max_attempts, base_delay_seconds, max_delay_seconds
- `_SENSITIVE_KEYS` set (reuse from observability module)
- `_sanitize_headers()`, `_sanitize_metadata()`, `_sanitize_payload_preview()` helpers
- `@field_validator` for `attempt_id` (`nda_`), `target_id` (`ndt_`)
- `@model_validator(mode="after")` sanitization for all three models
- `_redact_sensitive_values()` function (can import from observability module)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v`
Expected: PASS (8+ tests)

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_rollout_federation_notification_alert_delivery.py tests/unit/test_policy_notification_alert_delivery_models.py
git commit -m "feat: Phase 53 Task 1 — Alert delivery domain models"
```

---

## Task 2: Alert Delivery Store

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_store.py`
- Test: `tests/unit/test_policy_notification_alert_delivery_store.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_create_target():
    store = InMemoryAlertDeliveryStore()
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    result = await store.create_target(t)
    assert result.target_id == "ndt_1"

@pytest.mark.asyncio
async def test_duplicate_target_raises():
    store = InMemoryAlertDeliveryStore()
    t1 = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    t2 = AlertDeliveryTarget(target_id="ndt_1", name="Ops2", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t1)
    with pytest.raises(ValueError, match="already exists"):
        await store.create_target(t2)

@pytest.mark.asyncio
async def test_get_target():
    store = InMemoryAlertDeliveryStore()
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    result = await store.get_target("ndt_1")
    assert result.name == "Ops"

@pytest.mark.asyncio
async def test_list_targets_enabled_filter():
    store = InMemoryAlertDeliveryStore()
    await store.create_target(AlertDeliveryTarget(target_id="ndt_1", name="A", channel_type=AlertDeliveryChannelType.CONSOLE, enabled=True))
    await store.create_target(AlertDeliveryTarget(target_id="ndt_2", name="B", channel_type=AlertDeliveryChannelType.CONSOLE, enabled=False))
    enabled = await store.list_targets(enabled=True)
    assert len(enabled) == 1
    assert enabled[0].target_id == "ndt_1"

@pytest.mark.asyncio
async def test_update_target():
    store = InMemoryAlertDeliveryStore()
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    t.name = "Updated"
    result = await store.update_target(t)
    assert result.name == "Updated"

@pytest.mark.asyncio
async def test_delete_target():
    store = InMemoryAlertDeliveryStore()
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    await store.delete_target("ndt_1")
    assert await store.get_target("ndt_1") is None

@pytest.mark.asyncio
async def test_record_and_list_attempts():
    store = InMemoryAlertDeliveryStore()
    now = datetime.now(timezone.utc)
    a = AlertDeliveryAttempt(attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
        channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.DELIVERED, created_at=now)
    result = await store.record_attempt(a)
    assert result.attempt_id == "nda_1"
    attempts = await store.list_attempts(alert_id="nae_1")
    assert len(attempts) == 1

@pytest.mark.asyncio
async def test_list_attempts_pagination():
    store = InMemoryAlertDeliveryStore()
    now = datetime.now(timezone.utc)
    for i in range(5):
        a = AlertDeliveryAttempt(attempt_id=f"nda_{i}", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.DELIVERED, created_at=now)
        await store.record_attempt(a)
    page = await store.list_attempts(limit=2, offset=2)
    assert len(page) == 2

@pytest.mark.asyncio
async def test_sqlite_persists():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        store = SQLiteAlertDeliveryStore(path)
        t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
        await store.create_target(t)
        store2 = SQLiteAlertDeliveryStore(path)
        result = await store2.get_target("ndt_1")
        assert result.name == "Ops"
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_store.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_store.py` with:
- `AlertDeliveryStore` Protocol with all CRUD methods
- `InMemoryAlertDeliveryStore` — dict-backed, duplicate detection, pagination
- `SQLiteAlertDeliveryStore` — connection-owned, CREATE TABLE + 2 indexes, row factory, close()
- `create_alert_delivery_store()` factory

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_alert_delivery_store.py tests/unit/test_policy_notification_alert_delivery_store.py
git commit -m "feat: Phase 53 Task 2 — Alert delivery store"
```

---

## Task 3: Alert Delivery Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_service.py`
- Test: extend `tests/unit/test_policy_notification_alert_delivery_models.py` or create separate

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_deliver_matching_target():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter()
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter})
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="warning",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc))
    attempts = await service.deliver_alert(alert)
    assert len(attempts) == 1
    assert attempts[0].status == AlertDeliveryStatus.DELIVERED

@pytest.mark.asyncio
async def test_severity_filter_blocks():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter()
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter})
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE,
        severity_filter=["critical"])
    await store.create_target(t)
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="warning",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc))
    attempts = await service.deliver_alert(alert)
    assert len(attempts) == 0

@pytest.mark.asyncio
async def test_disabled_target_ignored():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter()
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter})
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE, enabled=False)
    await store.create_target(t)
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="warning",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc))
    attempts = await service.deliver_alert(alert)
    assert len(attempts) == 0

@pytest.mark.asyncio
async def test_retryable_failure_schedules_retry():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter(fail_always=True)
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter},
        retry_policy=AlertDeliveryRetryPolicy(max_attempts=3, base_delay_seconds=60))
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="warning",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc))
    attempts = await service.deliver_alert(alert)
    assert len(attempts) == 1
    assert attempts[0].status == AlertDeliveryStatus.RETRY_SCHEDULED

@pytest.mark.asyncio
async def test_dry_run_creates_suppressed():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter()
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter})
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="warning",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Test alert", status="open", created_at=datetime.now(timezone.utc))
    attempts = await service.deliver_alert(alert, dry_run=True)
    assert len(attempts) == 1
    assert attempts[0].status == AlertDeliveryStatus.SUPPRESSED

@pytest.mark.asyncio
async def test_retry_failed_processes_due():
    store = InMemoryAlertDeliveryStore()
    adapter = MemoryAlertDeliveryAdapter()
    service = NotificationAlertDeliveryService(store=store, adapters={"console": adapter},
        retry_policy=AlertDeliveryRetryPolicy(max_attempts=3))
    t = AlertDeliveryTarget(target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)
    await store.create_target(t)
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    a = AlertDeliveryAttempt(attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
        channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.RETRY_SCHEDULED,
        attempt=1, next_retry_at=old, created_at=old)
    await store.record_attempt(a)
    retried = await service.retry_failed(now=now)
    assert len(retried) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v -k "deliver or retry or filter or dry_run"`
Expected: FAIL — import errors

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_service.py` with:
- `AlertDeliveryAdapterResult(BaseModel)` — success, error_code, error_message, response_metadata, retryable
- `AlertDeliveryAdapter` Protocol — deliver(target, alert, payload) -> AlertDeliveryAdapterResult
- `NotificationAlertDeliveryService` class:
  - `__init__(store, adapters, retry_policy)` — constructor injection
  - `deliver_alert(alert, dry_run)` — match targets, filter, call adapter, record attempt
  - `_match_target(target, alert)` — check enabled, severity_filter, channel_filter, federation_filter
  - `_build_payload(alert, target)` — construct sanitized payload dict
  - `retry_failed(now, limit)` — find RETRY_SCHEDULED attempts past next_retry_at, re-deliver

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v -k "deliver or retry or filter or dry_run"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_alert_delivery_service.py tests/unit/test_policy_notification_alert_delivery_models.py
git commit -m "feat: Phase 53 Task 3 — Alert delivery service"
```

---

## Task 4: Alert Delivery Adapters

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_adapters.py`
- Test: add to `tests/unit/test_policy_notification_alert_delivery_models.py`

- [ ] **Step 1: Write failing tests**

```python
def test_memory_adapter_captures_payload():
    adapter = MemoryAlertDeliveryAdapter()
    result = adapter.deliver(target, alert, {"key": "value"})
    assert result.success is True
    assert len(adapter.delivered) == 1

def test_memory_adapter_fail_next():
    adapter = MemoryAlertDeliveryAdapter(fail_next=True)
    result = adapter.deliver(target, alert, {})
    assert result.success is False

def test_memory_adapter_fail_always():
    adapter = MemoryAlertDeliveryAdapter(fail_always=True)
    result = adapter.deliver(target, alert, {})
    assert result.success is False
    assert result.retryable is True

def test_webhook_adapter_dry_run():
    adapter = WebhookAlertDeliveryAdapter(dry_run=True)
    result = adapter.deliver(target, alert, {})
    assert result.success is True

def test_console_adapter_success():
    adapter = ConsoleAlertDeliveryAdapter()
    result = adapter.deliver(target, alert, {})
    assert result.success is True

def test_payload_redacted_in_memory():
    adapter = MemoryAlertDeliveryAdapter()
    payload = {"authorization": "Bearer secret"}
    adapter.deliver(target, alert, payload)
    assert adapter.delivered[0]["authorization"] == "[REDACTED]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v -k "adapter"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_alert_delivery_adapters.py` with:
- `AlertDeliveryAdapterResult(BaseModel)`
- `AlertDeliveryAdapter` Protocol
- `MemoryAlertDeliveryAdapter` — saves payloads to list, supports fail_next/fail_always, sanitizes
- `WebhookAlertDeliveryAdapter` — dry-run only, sanitizes headers/endpoint, no real network
- `ConsoleAlertDeliveryAdapter` — always succeeds, no network

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_alert_delivery_models.py -v -k "adapter"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_alert_delivery_adapters.py tests/unit/test_policy_notification_alert_delivery_models.py
git commit -m "feat: Phase 53 Task 4 — Alert delivery adapters"
```

---

## Task 5: Prometheus Metrics Export

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_prometheus.py`
- Test: `tests/unit/test_policy_notification_prometheus.py`

- [ ] **Step 1: Write failing tests**

```python
def test_empty_metrics_valid():
    result = export_notification_prometheus_metrics([], [], [])
    assert "# HELP" in result
    assert "# TYPE" in result

def test_metric_names_present():
    m = NotificationMetricWindow(window_start=now, window_end=now, channel="webhook",
        federation_id="fed_1", total=100, sent=95, failed=3, dlq=1, success_rate=0.95,
        failure_rate=0.03, dlq_rate=0.01)
    result = export_notification_prometheus_metrics([m], [], [])
    assert "agentapp_notification_total" in result
    assert "agentapp_notification_success_rate" in result

def test_labels_escaped():
    m = NotificationMetricWindow(window_start=now, window_end=now, channel='web,hook"test',
        total=10, sent=10)
    result = export_notification_prometheus_metrics([m], [], [])
    assert "web,hook" not in result  # Should be escaped

def test_no_secrets_in_output():
    m = NotificationMetricWindow(window_start=now, window_end=now, channel="webhook",
        total=10, sent=10, success_rate=1.0)
    result = export_notification_prometheus_metrics([m], [], [])
    assert "secret" not in result.lower()
    assert "token" not in result.lower()
    assert "password" not in result.lower()

def test_open_alerts_exported():
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="critical",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Alert", status="open", created_at=datetime.now(timezone.utc))
    result = export_notification_prometheus_metrics([], [], [alert])
    assert "agentapp_notification_alerts_open" in result
    assert 'severity="critical"' in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_prometheus.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_prometheus.py` with:
- `_escape_label_value()` helper
- `export_notification_prometheus_metrics(metrics, health, alerts)` — generates Prometheus text exposition format
- HELP/TYPE comments, counter/gauge declarations
- Metrics: total, sent, failed, suppressed, dlq, retry_scheduled, success_rate, failure_rate, dlq_rate, avg_latency_ms, p95_latency_ms, alerts_open

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_prometheus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_prometheus.py tests/unit/test_policy_notification_prometheus.py
git commit -m "feat: Phase 53 Task 5 — Prometheus metrics export"
```

---

## Task 6: JSONL Export

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_jsonl_export.py`
- Test: `tests/unit/test_policy_notification_jsonl_export.py`

- [ ] **Step 1: Write failing tests**

```python
def test_events_jsonl():
    e = NotificationDeliveryEvent(event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=datetime.now(timezone.utc))
    result = export_delivery_events_jsonl([e])
    assert '"event_id": "nde_1"' in result or '"event_id":"nde_1"' in result

def test_empty_returns_empty_string():
    assert export_delivery_events_jsonl([]) == ""

def test_sensitive_fields_redacted():
    e = NotificationDeliveryEvent(event_id="nde_1", event_type=NotificationDeliveryEventType.FAILED,
        error_message="auth failed: token=abc", metadata={"api_key": "xyz"},
        created_at=datetime.now(timezone.utc))
    result = export_delivery_events_jsonl([e])
    assert "abc" not in result
    assert "xyz" not in result

def test_alerts_jsonl():
    alert = NotificationAlertEvent(alert_id="nae_1", rule_id="nar_1", name="Test", severity="critical",
        metric="failure_rate", observed_value=0.1, threshold=0.05,
        message="Alert", status="open", created_at=datetime.now(timezone.utc))
    result = export_alert_events_jsonl([alert])
    assert "nae_1" in result

def test_attempts_jsonl():
    a = AlertDeliveryAttempt(attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
        channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.DELIVERED,
        created_at=datetime.now(timezone.utc))
    result = export_delivery_attempts_jsonl([a])
    assert "nda_1" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_jsonl_export.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_jsonl_export.py` with:
- `export_delivery_events_jsonl(events)` — each event as JSON line, sanitized
- `export_alert_events_jsonl(alerts)` — each alert as JSON line, sanitized
- `export_delivery_attempts_jsonl(attempts)` — each attempt as JSON line, sanitized
- Helper to sanitize model before dump

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_jsonl_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_jsonl_export.py tests/unit/test_policy_notification_jsonl_export.py
git commit -m "feat: Phase 53 Task 6 — JSONL structured export"
```

---

## Task 7: Retention Service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_retention.py`
- Test: `tests/unit/test_policy_notification_retention.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_dry_run_does_not_delete():
    store = InMemoryNotificationObservabilityStore()
    # Add events older than retention period
    old = datetime.now(timezone.utc) - timedelta(days=60)
    e = NotificationDeliveryEvent(event_id="nde_old", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=old)
    await store.record_event(e)
    policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
    service = NotificationRetentionService(observability_store=store, policy=policy)
    result = await service.run_cleanup(dry_run=True)
    assert result.dry_run is True
    assert result.events_deleted == 0  # dry run doesn't delete

@pytest.mark.asyncio
async def test_cleanup_deletes_old_events():
    store = InMemoryNotificationObservabilityStore()
    old = datetime.now(timezone.utc) - timedelta(days=60)
    e = NotificationDeliveryEvent(event_id="nde_old", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=old)
    await store.record_event(e)
    recent = NotificationDeliveryEvent(event_id="nde_new", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=datetime.now(timezone.utc))
    await store.record_event(recent)
    policy = NotificationRetentionPolicy(enabled=True, raw_event_retention_days=30)
    service = NotificationRetentionService(observability_store=store, policy=policy)
    result = await service.run_cleanup()
    assert result.events_deleted == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_retention.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_retention.py` with:
- `NotificationRetentionPolicy(BaseModel)` — enabled, retention_days per type, archive settings
- `NotificationRetentionResult(BaseModel)` — dry_run, counts, archive_files
- `NotificationRetentionService` class:
  - `__init__(observability_store, alert_store, delivery_store, policy)` — constructor injection
  - `run_cleanup(now, dry_run)` — delete old events/alerts/attempts, optional archive

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_retention.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_retention.py tests/unit/test_policy_notification_retention.py
git commit -m "feat: Phase 53 Task 7 — Retention service"
```

---

## Task 8: Metrics Rollup

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_notification_rollup.py`
- Test: `tests/unit/test_policy_notification_rollup.py`

- [ ] **Step 1: Write failing tests**

```python
def test_rollup_granularity_enum():
    assert NotificationRollupGranularity.HOURLY == "hourly"

def test_build_hourly_rollup():
    store = InMemoryNotificationObservabilityStore()
    rollup_store = InMemoryNotificationRollupStore()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    e = NotificationDeliveryEvent(event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", federation_id="fed_1", latency_ms=100, created_at=now)
    await store.record_event(e)
    service = NotificationRollupService(store, rollup_store)
    rollups = await service.build_rollups(NotificationRollupGranularity.HOURLY, now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(rollups) == 1
    assert rollups[0].total == 1
    assert rollups[0].sent == 1

def test_upsert_same_window():
    store = InMemoryNotificationObservabilityStore()
    rollup_store = InMemoryNotificationRollupStore()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    e1 = NotificationDeliveryEvent(event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=now)
    e2 = NotificationDeliveryEvent(event_id="nde_2", event_type=NotificationDeliveryEventType.SENT,
        channel="webhook", created_at=now)
    await store.record_event(e1)
    await store.record_event(e2)
    service = NotificationRollupService(store, rollup_store)
    rollups1 = await service.build_rollups(NotificationRollupGranularity.HOURLY, now - timedelta(hours=1), now + timedelta(hours=1))
    assert rollups1[0].total == 2
    # Re-run should upsert, not duplicate
    rollups2 = await service.build_rollups(NotificationRollupGranularity.HOURLY, now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(rollups2) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_rollup.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Create `agent_app/runtime/policy_rollout_federation_notification_rollup.py` with:
- `NotificationRollupGranularity(StrEnum)` — HOURLY, DAILY
- `NotificationMetricsRollup(BaseModel)` — rollup_id, granularity, window_start/end, federation_id, channel, counts, rates, latency, created_at
- `NotificationRollupStore` Protocol — create_rollup, get_rollup, list_rollups, upsert_rollup
- `InMemoryNotificationRollupStore` — dict-backed
- `SQLiteNotificationRollupStore` — connection-owned, CREATE TABLE, indexes
- `create_notification_rollup_store()` factory
- `NotificationRollupService` class:
  - `build_rollups(granularity, since, until, federation_id, channel)` — query events, aggregate into windows

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_rollup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_notification_rollup.py tests/unit/test_policy_notification_rollup.py
git commit -m "feat: Phase 53 Task 8 — Metrics rollup"
```

---

## Task 9: Config Extensions

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_policy_notification_config.py`:
```python
def test_alert_delivery_config_defaults():
    cfg = RolloutFederationNotificationAlertDeliveryConfig()
    assert cfg.enabled is False
    assert cfg.retry.max_attempts == 3

def test_retention_config_defaults():
    cfg = RolloutFederationNotificationRetentionConfig()
    assert cfg.enabled is True
    assert cfg.raw_event_retention_days == 30

def test_rollup_config_defaults():
    cfg = RolloutFederationNotificationRollupConfig()
    assert cfg.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_config.py -v -k "delivery or retention or rollup"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, add after Phase 52 configs:
- `AlertDeliveryRetryPolicyConfig` — max_attempts, base_delay_seconds, max_delay_seconds
- `RolloutFederationNotificationAlertDeliveryTargetConfig` — target_id, name, channel_type, enabled, severity_filter, channel_filter, federation_filter, endpoint
- `RolloutFederationNotificationAlertDeliveryConfig` — enabled, store, retry, targets
- `RolloutFederationNotificationRetentionConfig` — enabled, retention_days per type, archive settings
- `RolloutFederationNotificationRollupConfig` — enabled, store, granularities
- Add fields to `RolloutFederationNotificationConfig`

In `loader.py`, add Phase 53 wiring block after Phase 52.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_config.py -v -k "delivery or retention or rollup"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/config/schema.py agent_app/config/loader.py tests/unit/test_policy_notification_config.py
git commit -m "feat: Phase 53 Task 9 — Config schema and loader extensions"
```

---

## Task 10: CLI Commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_notification_cli.py`

- [ ] **Step 1: Write failing tests**

Add CLI tests for new commands:
- `agentapp federation notifications alerts deliver --alert-id <id> --dry-run`
- `agentapp federation notifications alerts delivery targets list`
- `agentapp federation notifications prometheus export`
- `agentapp federation notifications jsonl export --type events`
- `agentapp federation notifications retention cleanup --dry-run`
- `agentapp federation notifications rollup build --granularity hourly`

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_cli.py -v -k "deliver or prometheus or jsonl or retention or rollup"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add after Phase 52 commands:
- `alerts deliver` subcommand
- `alerts delivery` subcommands (targets list, attempts list, targets add/disable)
- `prometheus export` subcommand
- `jsonl export` subcommand
- `retention cleanup` subcommand (with --yes requirement)
- `rollup build` and `rollup list` subcommands

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_notification_cli.py
git commit -m "feat: Phase 53 Task 10 — CLI observability commands"
```

---

## Task 11: Console Pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: 6 new templates
- Test: `tests/unit/test_policy_notification_console.py`

- [ ] **Step 1: Write failing tests**

Add console tests for new pages rendering without errors.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_notification_console.py -v -k "delivery or prometheus or retention or rollup"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `router.py`, add routes for:
- `/federation/notifications/alert-delivery` — overview
- `/federation/notifications/alert-delivery/targets` — target list
- `/federation/notifications/alert-delivery/attempts` — attempt list
- `/federation/notifications/prometheus` — Prometheus text preview
- `/federation/notifications/retention` — retention policy + dry-run
- `/federation/notifications/rollups` — rollup list

Create templates:
- `policy_federation_notification_alert_delivery.html`
- `policy_federation_notification_alert_delivery_targets.html`
- `policy_federation_notification_alert_delivery_attempts.html`
- `policy_federation_notification_prometheus.html`
- `policy_federation_notification_retention.html`
- `policy_federation_notification_rollups.html`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_notification_console.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/console/router.py agent_app/console/templates/policy_federation_notification_alert_delivery*.html agent_app/console/templates/policy_federation_notification_prometheus.html agent_app/console/templates/policy_federation_notification_retention.html agent_app/console/templates/policy_federation_notification_rollups.html tests/unit/test_policy_notification_console.py
git commit -m "feat: Phase 53 Task 11 — Console pages for alert delivery, Prometheus, retention, rollups"
```

---

## Task 12: Audit/History/Change Events + Notification Service Integration

**Files:**
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/governance/policy_rollout_federation_history.py`
- Modify: `agent_app/runtime/policy_rollout_federation_notification_service.py`
- Test: extend existing test files

- [ ] **Step 1: Write failing tests**

Add enum count tests for new event types.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_config.py tests/unit/test_policy_rollout_federation_notification_config.py -v`
Expected: FAIL (enum count mismatch)

- [ ] **Step 3: Write minimal implementation**

Add to `PolicyChangeEventType`:
- federation.notification.alert_delivery.target_created
- federation.notification.alert_delivery.target_updated
- federation.notification.alert_delivery.target_disabled
- federation.notification.alert_delivery.attempt_recorded
- federation.notification.alert_delivery.dlq_created
- federation.notification.prometheus.exported
- federation.notification.jsonl.exported
- federation.notification.retention.cleanup_ran
- federation.notification.rollup.built

Add to `FederationHistoryEventType`:
- notification_alert_delivery_target_created
- notification_alert_delivery_target_updated
- notification_alert_delivery_target_disabled
- notification_alert_delivery_attempt_recorded
- notification_alert_delivery_dlq_created
- notification_prometheus_metrics_exported
- notification_jsonl_exported
- notification_retention_cleanup_ran
- notification_rollup_built

Wire into notification service for lifecycle events.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_config.py tests/unit/test_policy_rollout_federation_notification_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_change_event.py agent_app/governance/policy_rollout_federation_history.py agent_app/runtime/policy_rollout_federation_notification_service.py tests/unit/test_policy_rollout_federation_history_config.py tests/unit/test_policy_rollout_federation_notification_config.py
git commit -m "feat: Phase 53 Task 12 — Audit/history/change events wiring"
```

---

## Task 13: Documentation

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `docs/policy_release.md`
- Create: `docs/release_checklist_phase53.md`

- [ ] **Step 1: Update CHANGELOG.md** — add v0.41.0 section
- [ ] **Step 2: Update README.md** — add Phase 53 roadmap entry
- [ ] **Step 3: Update docs/policy_release.md** — add Phase 53 section
- [ ] **Step 4: Create docs/release_checklist_phase53.md**

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md docs/policy_release.md docs/release_checklist_phase53.md
git commit -m "docs: Phase 53 documentation"
```

---

## Task 14: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -x --timeout=120 -q
```

- [ ] **Step 2: Verify 0 failures**

Expected: All tests pass, 0 failures.

- [ ] **Step 3: Check version consistency**

Verify version in pyproject.toml is 0.41.0 or higher.

- [ ] **Step 4: Commit verification**

```bash
git log --oneline -5
```

---

## Implementation Notes

1. **TDD**: Every new module must have tests written first (RED), then implementation (GREEN).
2. **Sensitive data**: All exports, payloads, and console pages must sanitize using `_SENSITIVE_KEYS`.
3. **Best-effort**: Auxiliary operations (recording, archiving) must never break main flows.
4. **Constructor injection**: Services accept optional dependencies with None defaults.
5. **No external services**: All adapters support memory/dry-run mode.
6. **SQLite**: Each store owns its connection, auto-creates tables/indexes.
7. **StrEnum**: All enums use lowercase dot-separated values.
8. **ID prefixes**: `nda_` (attempts), `ndt_` (targets), `nrp_` (retry policy), `nrs_` (retention result), `nru_` (rollup).
