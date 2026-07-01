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
