# Phase 65: Gap Closure — Webhook Signing Fix, Rate Limiter Persistence, Doc Backfill, OpenTelemetry Integration

**Goal:** Fix three verified production bugs (silently-broken webhook signature wiring, dead nonce-replay config, dead promotion-approval config), add a persistent backend for approval rate limiting, backfill missing release-checklist docs, and integrate OpenTelemetry as a first-class tracing exporter.

**Architecture:** Six independently-shippable components, each following the codebase's existing conventions (lazy SQLite store construction, `TraceCollector`/`ApprovalRateLimiter` Protocol implementations, `except Exception: pass`-free error surfacing at config-build time). No new top-level subsystems — all six slot into existing extension points (`config/loader.py`, `PolicyReleaseService`, `TracingConfig.type`).

**Tech Stack:** stdlib `sqlite3` (WAL mode, matching `ControlPlaneStore` style), `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http` (new optional dependency), pytest with `skipif` gating for otel-dependent tests.

---

## Background: How These Gaps Were Found

A full-codebase sweep (grep for TODO/NotImplementedError, CHANGELOG "known limitations" sections, README roadmap audit, config schema vs. loader cross-reference) surfaced 8 candidate gaps. Each was independently verified by reading the actual code (not trusting the sweep's inference). Two candidates were **false positives** and are explicitly excluded from this phase:

- **CompensationStateStore SQLite persistence** — already fully implemented. `DagExecutor._init_compensation_store()` (agent_app/workflows/dag.py:1585) already calls `create_compensation_state_store(store_type=cfg.store, db_path=cfg.path)`, and `DagCompensationConfig` is already threaded from `loader.py:373` → `AgentApp` → `DagExecutor`. No action needed.
- **CanaryEvalRunner** — already fully implemented at `agent_app/evals/canary.py` (100 lines, `CanaryEvalResult` + `CanaryEvalRunner.run_for_activation()`). `from agent_app.evals.canary import CanaryEvalRunner` succeeds at runtime; the CLI's `ImportError` fallback branch is defensive code that never triggers. No action needed.

The five components below are the *verified* remaining gaps, confirmed by reading the actual source (not the sweep's summary).

---

## Component 1: Webhook Signature Service Wiring Fix

**Current broken state** (`agent_app/config/loader.py:1288-1305`, inside the federation notification config block, itself inside a bare `try/except Exception: pass` at line 1311-1312):

```python
signing_cfg = getattr(notif_cfg, "webhook_signing", None)
if signing_cfg is not None and signing_cfg.enabled:
    from agent_app.runtime.policy_rollout_federation_notification_webhook_signature import FederationWebhookSignatureService
    from agent_app.runtime.policy_rollout_federation_notification_nonce_store import create_federation_webhook_nonce_store
    ...
    signature_service = FederationWebhookSignatureService(
        algorithm=signing_cfg.algorithm,
        signature_version=signing_cfg.signature_version,
        active_key_id=signing_cfg.active_key_id,
        keys=signing_cfg.keys,
        timestamp_tolerance_seconds=signing_cfg.timestamp_tolerance_seconds,
        nonce_store=nonce_store,
        nonce_ttl_seconds=signing_cfg.nonce_ttl_seconds,
    )
```

Two independent breaks:
1. **Wrong import paths.** The actual files are `agent_app/runtime/policy_rollout_federation_webhook_signature.py` and `agent_app/runtime/policy_rollout_federation_webhook_nonce_store.py` — no `notification_` infix. Both imports raise `ModuleNotFoundError`.
2. **Invalid constructor kwargs.** `FederationWebhookSignatureService.__init__` (policy_rollout_federation_webhook_signature.py:27) only accepts `active_key_id`, `keys`, `signature_version`, `timestamp_tolerance_seconds` — no `algorithm`, `nonce_store`, or `nonce_ttl_seconds` parameters exist. Even with fixed imports, this call raises `TypeError`.

Both errors are swallowed by the bare `except Exception: pass`, so `webhook_signing.enabled: true` has silently done nothing since Phase 51 introduced it.

**Fix:**
- Correct both import paths to the real module names.
- Correct the constructor call to pass only `active_key_id`, `keys`, `signature_version`, `timestamp_tolerance_seconds`.
- `nonce_store` is not a constructor argument on the signature service — it's a per-call argument to `verify()` (confirmed: `verify()` signature at policy_rollout_federation_webhook_signature.py:90-143 accepts `nonce_store` as an optional parameter). Keep the existing `create_federation_webhook_nonce_store(...)` call and continue storing the result on `app._federation_webhook_nonce_store` (this part of the existing code is already correct) — it is not passed into the signature service constructor.
- Replace the bare `except Exception: pass` around *this specific block* with a narrower catch that logs a warning via the standard `logging` module (`logger.warning("Failed to initialize webhook signature service: %s", exc)`) rather than silently discarding — matching the "best-effort, never crash the daemon" convention used elsewhere (e.g. Phase 63's control plane store init) while still surfacing the failure.

## Component 2: Nonce Replay Protection Enforcement

**Current state:** `RolloutFederationWebhookSigningConfig.nonce_replay_protection` (schema.py:661, default `True`) is never read anywhere in the codebase. The only call site of `verify()` — the CLI `webhook verify` command (cli.py:9863) — does not pass `nonce_store`, so nonce-reuse detection never executes regardless of the config value.

**Fix:**
- In the CLI verify command handler, read `app.federation_webhook_nonce_store` alongside the existing `app.federation_webhook_signature_service` lookup.
- Read the signing config's `nonce_replay_protection` flag (accessible via the app or by re-reading config — reuse whatever the loader already exposes; if not currently exposed, add `app._federation_webhook_nonce_replay_protection: bool` alongside the other two attributes set in Component 1's fixed loader block).
- Pass `nonce_store=nonce_store if nonce_replay_protection else None` into `verify()`.
- This makes the config field functional: `True` (default) enables reuse detection when a nonce store is available; `False` explicitly opts out.

## Component 3: ApprovalRateLimiter SQLite Backend

**Current state:** `agent_app/runtime/approval_rate_limit.py` defines only `InMemoryApprovalRateLimiter`, using an in-process dict keyed by `tenant|user|tool` and `time.monotonic()` timestamps. State is lost on restart and never shared across multiple instances — a real gap for a framework whose Phase 55-64 theme is explicitly "production hardening" and "multi-instance readiness."

`GovernanceConfig.rate_limit: RateLimitConfig | None` (schema.py:206-210) currently has only `max_requests` and `window_seconds` — no backend selector. `loader.py:218-222` unconditionally constructs `InMemoryApprovalRateLimiter`.

**New class — `SQLiteApprovalRateLimiter`** in `agent_app/runtime/approval_rate_limit.py`, following the `ControlPlaneStore` style (agent_app/runtime/policy_rollout_federation_notification_control_plane.py):
- Constructor: `__init__(self, max_requests: int = 10, window_seconds: int = 60, db_path: str = ".agent_app/approval_rate_limit.db", audit_logger: AuditLogger | None = None)`.
- Opens `sqlite3.connect(db_path, check_same_thread=False)`, `row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`.
- Schema: `CREATE TABLE IF NOT EXISTS approval_rate_limit_hits (key TEXT NOT NULL, hit_time REAL NOT NULL)` + `CREATE INDEX IF NOT EXISTS idx_arl_key_time ON approval_rate_limit_hits(key, hit_time)`.
- `check_allowed()`: same `(tenant_id, user_id, tool_name)` → key derivation as the in-memory version (reuse the same `_key()` logic, extracted to a shared module-level function `_rate_limit_key()` to avoid duplication). Uses `time.time()` (wall-clock, not `time.monotonic()`, since state must be comparable across process restarts and instances) for the stored timestamp. Steps: `DELETE FROM approval_rate_limit_hits WHERE key=? AND hit_time<?` (purge expired) → `SELECT COUNT(*) FROM approval_rate_limit_hits WHERE key=?` → if `>= max_requests`, log rate-limited event and return `False`; else `INSERT` a new row with current time and return `True`. Wrap the purge+count+insert in a single `sqlite3` transaction (`with self._conn:`) to avoid races between concurrent callers on the same connection.
- Provide `close()` method matching other SQLite stores' convention.

**Config extension:** Add `backend: str = "memory"` and `db_path: str | None = None` fields to `RateLimitConfig` (schema.py:206), with a `field_validator` restricting `backend` to `{"memory", "sqlite"}` (matching the validation style already used elsewhere in schema.py, e.g. `PolicyEngineConfig._validate_default_action`).

**Loader change** (loader.py:218-222): branch on `rl_cfg.backend` — `"sqlite"` constructs `SQLiteApprovalRateLimiter(max_requests=..., window_seconds=..., db_path=rl_cfg.db_path or ".agent_app/approval_rate_limit.db")`, `"memory"` (default) keeps the existing `InMemoryApprovalRateLimiter` construction unchanged.

## Component 4: Missing Release Checklist Docs (Corrected Scope)

**Scope correction:** The original gap sweep flagged `docs/release_checklist_phase{56,58,59,60,61,63}.md` as missing (6 files). Cross-referencing `CHANGELOG.md`'s actual version headers shows **Phase 56 and Phase 58 have no standalone CHANGELOG entry** — their work appears to have been folded into Phase 57 (`v0.43.0`) and Phase 59 (`v0.44.0`) respectively during development. Writing checklists for phases with no corresponding shipped-version record would fabricate history not supported by the codebase's own documentation. This component is therefore scoped to the **four phases with a verified standalone CHANGELOG entry**: Phase 59, 60, 61, 63.

**Format:** Match the existing template (seen in `docs/release_checklist_phase41.md` and similar): `# Release Checklist — Phase N`, `**Version:**`, `**Phase:** <title>`, `**Date:**`, then `## Implementation Checklist` / `## Test Coverage` / `## Acceptance Criteria`, each a checkbox list.

**Content source:** Each checklist's content is derived directly from that phase's own CHANGELOG `### Added` / `### Changed` bullets (already read in full for all four phases during design) — no invented content. Test counts come from the CHANGELOG's own stated numbers (e.g. Phase 61: "59 new Phase 61 unit tests across 7 test files"; Phase 63: "79 new Phase 63 unit tests across 5 test files"; Phase 59: 18 new PolicyChangeEventType values + multi-instance services; Phase 60: "15 new unit tests... test_phase60_daemon_closed_loop.py").

## Component 5: OpenTelemetry Integration

**Current state:** `agent_app/observability/otel.py` (119 lines) defines `OpenTelemetryNotInstalledError` and `OpenTelemetryTraceExporter` — a standalone class using an `InMemorySpanExporter` for test introspection only. It implements no protocol used elsewhere, is never imported by `config/loader.py` or `AgentApp`, and has zero test coverage. The existing Phase 12 tracing system (`TracingConfig.type: noop|memory|jsonl`, wired at loader.py:350-361 into `NoOpTraceCollector`/`InMemoryTraceCollector`/`JSONLTraceCollector`, all implementing the `TraceCollector` Protocol at `agent_app/observability/collector.py:11`) is what's actually used end-to-end.

**Design decision — dual-write, protocol-conformant collector, not a replacement:** `FastAPI`'s trace endpoints (`agent_app/adapters/fastapi.py:509-536`) and the CLI's `trace list`/`trace show` commands (cli.py:2329-2390) both call `collector.list_traces()` / `collector.get_events()` for read-back. Since OTLP export is fire-and-forget (data lands in an external system like Jaeger/Tempo, not readable back locally), a pure OTLP-only collector would silently break these existing features whenever `tracing.type: otel` is selected. The new `OtelTraceCollector` therefore:
1. Implements the full `TraceCollector` Protocol (`record`, `get_events`, `list_traces`).
2. Internally maintains a bounded in-memory buffer for read-back, reusing `InMemoryTraceCollector`'s existing retention logic (`max_traces`, `max_events_per_trace`) via composition (holds an `InMemoryTraceCollector` instance internally rather than duplicating its eviction code).
3. On every `record(event)`, also converts the `RunEvent` into an OTel span and exports it via the configured SDK exporter.

**Span mapping** (`RunEvent` → OTel span, in a new `_run_event_to_span()` helper):
- Span name: `event.event_type` (e.g. `"tool.completed"`).
- OTel trace ID: deterministically derived from `event.trace_id` (e.g. `int(hashlib.md5(event.trace_id.encode()).hexdigest()[:32], 16)`, truncated/masked to fit OTel's 128-bit trace ID space) so all events sharing a `trace_id` land in the same OTel trace.
- Span ID: deterministically derived from `event.event_id` the same way (64-bit).
- Start/end time: `event.timestamp` for start; `event.timestamp + duration_ms` for end when `duration_ms` is present, else a zero-duration span.
- Attributes: `run_id`, `tenant_id`, `user_id`, `workflow_name`, `agent_name`, `tool_name`, `approval_id`, `status` (all non-None fields), plus `error` and `data` flattened with a `data.` prefix (matching how `RunEvent.data` is already a free-form dict).

**Config extension** — new fields on `TracingConfig` (schema.py:226-234):
- `type` gains a fourth valid value: `"otel"`.
- `otel_service_name: str = "agent-app"`.
- `otel_exporter: str = "console"` — `"console"` (OTel SDK's built-in `ConsoleSpanExporter`, zero extra dependencies beyond `opentelemetry-sdk`) or `"otlp"` (requires the new optional dependency).
- `otel_otlp_endpoint: str | None = None` — required when `otel_exporter == "otlp"`.

**New optional dependency** — add `opentelemetry-exporter-otlp-proto-http` to the `otel` extra in `pyproject.toml` (HTTP variant chosen over gRPC to avoid the extra `grpcio` system dependency).

**Loader wiring** (loader.py, alongside the existing `type == "jsonl"` branch): add `type == "otel"` branch that lazily imports `agent_app.observability.otel.OtelTraceCollector`, catching `OpenTelemetryNotInstalledError` and re-raising it as a clear `build_app()`-time `RuntimeError` (not swallowed) so misconfiguration is visible immediately rather than failing silently deep in a run.

**Tests** — new `tests/unit/test_observability_otel.py`:
- A `_has_otel()` helper (try/except import, matching the existing `_has_fastapi()` pattern in `test_fastapi_adapter.py`) gates OTel-SDK-dependent tests with `@pytest.mark.skipif`.
- Non-skipped test: `build_app()` with `tracing.type: otel` and otel packages absent raises a clear `RuntimeError` (not a bare import crash).
- Skipped-when-no-otel tests: span mapping correctness (trace ID determinism, attribute mapping), dual-write behavior (`get_events()`/`list_traces()` still work after `record()`), console exporter smoke test.

---

## Testing Strategy (all components)

- TDD throughout: failing test → minimal implementation → passing test, per component, per this codebase's established convention.
- Component 1 & 2: new test in `tests/unit/test_webhook_signing_rotation_phase57.py` (existing file, extend it) exercising `build_app()` end-to-end with `webhook_signing.enabled: true` — this test would have caught the original bug (no such end-to-end test currently exists, confirmed during research).
- Component 3: new `tests/unit/test_approval_rate_limit_sqlite.py` mirroring the existing `InMemoryApprovalRateLimiter` test structure (find and match existing test file for the in-memory version), plus a loader-wiring test.
- Component 4: no code tests — a lightweight `tests/unit/test_release_checklist_docs_exist.py` (or extend the Phase 64-style doc-existence pattern) asserting the four files exist and contain the required section headers, matching the Phase 64 convention of validating deployment artifacts structurally.
- Component 5: as detailed above.
- Component 6: new test asserting `execute_promotion()` with `require_promotion_approval=False` on a non-approved request succeeds (previously raised `ValueError`); with `True` (default), existing tests continue to pass unchanged, proving no regression.
- Full regression: run entire `tests/unit` suite before and after, confirm 0 new failures (current baseline: 5477 passed).

## Component 6: `require_promotion_approval` Wiring

**Current state:** `PolicyReleaseConfig.require_promotion_approval` (schema.py:1131, default `True`) is never referenced by `PolicyReleaseService.execute_promotion()` (agent_app/runtime/policy_release.py:574-624), which unconditionally requires `request.status == PromotionRequestStatus.APPROVED` (line 620) regardless of the config value. Failure direction is fail-safe (approval is always enforced, never bypassable), so this is a configurability gap, not a security hole.

**Fix:**
- Thread `require_promotion_approval` from `PolicyReleaseConfig` into `PolicyReleaseService.__init__` (confirm exact constructor signature during implementation — it may already receive the full config or a subset; add the field if not already present).
- In `execute_promotion()`, change the status check: when `self._require_promotion_approval` is `True` (default), keep the existing `request.status != APPROVED` → raise behavior unchanged. When `False`, skip the approval-status check entirely (promotion can execute directly from e.g. `PENDING` status) — but still enforce the existing gate-check and permission-check logic below it unchanged.

## Out of Scope (explicitly deferred, not part of Phase 65)

- OTLP gRPC exporter variant (HTTP-only for this phase).
- Distributed nonce-store sharing across instances beyond what `create_federation_webhook_nonce_store`'s existing SQLite backend already provides.
- Any of the large architectural items surfaced but not chosen (DAG-on-OpenAI-backend, plugin system, native backend resume) — each needs its own brainstorm/plan cycle per this project's established phase convention.
