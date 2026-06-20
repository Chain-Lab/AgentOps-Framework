# Phase 50 Release Checklist

## Models
- [ ] FederationNotificationDLQStatus enum (4 values)
- [ ] FederationNotificationDLQReason enum (5 values)
- [ ] FederationNotificationDeadLetter model with fdlq_ prefix
- [ ] FederationNotificationRetryPolicy model
- [ ] FederationScheduledWorkerStatus enum (4 values)
- [ ] FederationScheduledWorkerState model
- [ ] DEAD_LETTERED added to FederationNotificationStatus

## DLQ Store
- [ ] FederationNotificationDLQStore Protocol
- [ ] InMemoryFederationNotificationDLQStore
- [ ] SQLiteFederationNotificationDLQStore
- [ ] Factory function

## Notification Service Integration
- [ ] Retry policy applied in dispatch_pending
- [ ] Per-channel retry policy override
- [ ] DLQ entry created on max retries exceeded
- [ ] send_to_dlq=False skips DLQ
- [ ] Change events and history recorded for DLQ

## Scheduled Worker
- [ ] FederationScheduledWorker with start/stop/status/tick
- [ ] asyncio task-based loop
- [ ] Distributed lock acquisition
- [ ] Graceful shutdown via asyncio.Event

## Config
- [ ] RolloutFederationDLQConfig
- [ ] RolloutFederationRetryPolicyConfig
- [ ] RolloutFederationChannelRetryConfig
- [ ] RolloutFederationScheduledWorkerConfig
- [ ] Loader wiring

## RBAC
- [ ] FEDERATION_DLQ_LIST permission (default-allowed)
- [ ] FEDERATION_DLQ_MANAGE permission
- [ ] FEDERATION_WORKER_MANAGE permission

## Events
- [ ] 6 new PolicyChangeEventType values
- [ ] 3 new FederationHistoryEventType values

## CLI
- [ ] dlq list/show/retry/purge/export commands
- [ ] worker status/start commands

## Console
- [ ] DLQ list page
- [ ] DLQ detail page
- [ ] Worker status page

## Export
- [ ] export_federation_dlq_summary_json
- [ ] export_federation_dlq_summary_csv

## Observability
- [ ] get_dlq_summary()
- [ ] get_worker_summary()

## Tests
- [ ] DLQ model tests
- [ ] DLQ store tests
- [ ] Retry policy tests
- [ ] DLQ service integration tests
- [ ] Scheduled worker tests
- [ ] CLI tests
- [ ] Console tests
- [ ] Export tests
- [ ] All Phase 49 tests still pass

## Documentation
- [ ] docs/policy_release.md updated
- [ ] CHANGELOG.md updated
- [ ] README.md updated
- [ ] release_checklist_phase50.md created
