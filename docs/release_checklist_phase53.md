# Phase 53 Release Checklist

## Models
- [ ] NotificationAlertDeliveryTarget model with ndt_ prefix
- [ ] NotificationAlertDeliveryAttempt model with nda_ prefix
- [ ] AlertDeliveryRetryPolicy model
- [ ] NotificationRollup model with nru_ prefix
- [ ] RollupGranularity enum (HOURLY, DAILY)

## Alert Delivery Store
- [ ] NotificationAlertDeliveryStore Protocol
- [ ] InMemoryNotificationAlertDeliveryStore
- [ ] SQLiteNotificationAlertDeliveryStore with alert_delivery_targets and alert_delivery_attempts tables
- [ ] Factory function create_alert_delivery_store

## Alert Delivery Service
- [ ] NotificationAlertDeliveryService with create_target, deliver_alert, list_targets, list_attempts
- [ ] Target matching by federation_id, channel, and severity filter
- [ ] Dry-run mode: records attempts without HTTP calls
- [ ] Retry policy support (max_attempts, backoff_seconds, retryable_status_codes)

## Alert Delivery Adapters
- [ ] MemoryAlertDeliveryAdapter (live in-process)
- [ ] WebhookAlertDeliveryAdapter (dry-run only, respects _SENSITIVE_KEYS redaction)
- [ ] ConsoleAlertDeliveryAdapter (live stdout output)

## Prometheus Export
- [ ] NotificationPrometheusExporter with HELP/TYPE comments
- [ ] Label escaping per Prometheus spec
- [ ] No secrets in metric labels or values
- [ ] export_notification_prometheus() function
- [ ] _SENSITIVE_KEYS redaction applied before export

## JSONL Export
- [ ] NotificationJsonlExporter
- [ ] export_notification_jsonl() function
- [ ] Export types: events, alerts, delivery attempts
- [ ] _SENSITIVE_KEYS redaction applied before serialization

## Retention Service
- [ ] NotificationRetentionService with cleanup()
- [ ] Per-type retention days: events, alerts, attempts, targets
- [ ] Archive-before-purge: moves expired records to archive before deletion
- [ ] Dry-run mode: reports what would be purged without deleting
- [ ] _SENSITIVE_KEYS: no keys/signatures in retention operations or logs

## Rollup Service
- [ ] NotificationRollupService with build() and list()
- [ ] Granularity: HOURLY and DAILY
- [ ] Upsert semantics: re-running replaces existing aggregated data
- [ ] Dimensions: federation_id, channel, event_type, status

## Config
- [ ] RolloutFederationNotificationAlertDeliveryConfig
- [ ] RolloutFederationNotificationPrometheusExportConfig
- [ ] RolloutFederationNotificationJsonlExportConfig
- [ ] RolloutFederationNotificationRetentionConfig
- [ ] RolloutFederationNotificationRollupConfig
- [ ] Loader wiring for all Phase 53 config sections

## RBAC
- [ ] ALERT_DELIVERY_VIEW permission (policy.federation.notification.alert_delivery.view, default-allowed)
- [ ] ALERT_DELIVERY_MANAGE permission (policy.federation.notification.alert_delivery.manage)
- [ ] PROMETHEUS_EXPORT permission (policy.federation.notification.prometheus.export, default-allowed)
- [ ] JSONL_EXPORT permission (policy.federation.notification.jsonl.export, default-allowed)
- [ ] RETENTION_MANAGE permission (policy.federation.notification.retention.manage)
- [ ] ROLLUP_BUILD permission (policy.federation.notification.rollup.build)

## Change Events
- [ ] FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_CREATED
- [ ] FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_UPDATED
- [ ] FEDERATION_NOTIFICATION_ALERT_DELIVERY_TARGET_DISABLED
- [ ] FEDERATION_NOTIFICATION_ALERT_DELIVERY_ATTEMPT_RECORDED
- [ ] FEDERATION_NOTIFICATION_ALERT_DELIVERY_DLQ_CREATED
- [ ] FEDERATION_NOTIFICATION_PROMETHEUS_EXPORTED
- [ ] FEDERATION_NOTIFICATION_JSONL_EXPORTED
- [ ] FEDERATION_NOTIFICATION_RETENTION_CLEANUP_RAN
- [ ] FEDERATION_NOTIFICATION_ROLLUP_BUILT

## Federation History Events
- [ ] NOTIFICATION_ALERT_DELIVERY_TARGET_CREATED
- [ ] NOTIFICATION_ALERT_DELIVERY_TARGET_UPDATED
- [ ] NOTIFICATION_ALERT_DELIVERY_TARGET_DISABLED
- [ ] NOTIFICATION_ALERT_DELIVERY_ATTEMPT_RECORDED
- [ ] NOTIFICATION_ALERT_DELIVERY_DLQ_CREATED
- [ ] NOTIFICATION_PROMETHEUS_METRICS_EXPORTED
- [ ] NOTIFICATION_JSONL_EXPORTED
- [ ] NOTIFICATION_RETENTION_CLEANUP_RAN
- [ ] NOTIFICATION_ROLLUP_BUILT

## CLI Commands
- [ ] agentapp policy federation notification alert deliver <alert_id>
- [ ] agentapp policy federation notification alert targets list
- [ ] agentapp policy federation notification alert attempts list
- [ ] agentapp policy federation notification prometheus export
- [ ] agentapp policy federation notification jsonl export <type>
- [ ] agentapp policy federation notification retention cleanup
- [ ] agentapp policy federation notification rollup build
- [ ] agentapp policy federation notification rollup list

## Console Pages
- [ ] GET /federation/notifications/alert-delivery (dashboard)
- [ ] GET /federation/notifications/alert-delivery/targets (target management)
- [ ] GET /federation/notifications/alert-delivery/attempts (attempt history)
- [ ] GET /federation/notifications/prometheus (metrics display)
- [ ] GET /federation/notifications/jsonl (export interface)
- [ ] GET /federation/notifications/retention (policy config + dry-run)
- [ ] GET /federation/notifications/rollup (rollup dashboard)

## Export
- [ ] Prometheus text format with HELP/TYPE comments
- [ ] JSONL structured export for events, alerts, attempts
- [ ] No sensitive data (API keys, tokens, secrets, signatures) in any export

## Security
- [ ] _SENSITIVE_KEYS applied to all exports (Prometheus, JSONL, CSV)
- [ ] Webhook adapter dry-run only (no real HTTP calls)
- [ ] No keys, signatures, or sensitive request headers in logs or exports

## Notification Service Integration
- [ ] _record_change_event() for all Phase 53 lifecycle events
- [ ] _record_history() for all Phase 53 history events

## Documentation
- [ ] Phase 53 section in docs/policy_release.md
- [ ] CHANGELOG.md v0.41.0 entry
- [ ] README.md Phase 53 roadmap entry
- [ ] Release checklist

## Enum Count Verification
- [ ] PolicyChangeEventType: 124 → 133
- [ ] FederationHistoryEventType: 42 → 51
- [ ] PolicyReleasePermission: 94 (unchanged)
