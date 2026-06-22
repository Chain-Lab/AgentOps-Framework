# Phase 52 Release Checklist

## Models
- [ ] FederationNotificationDeliveryEventType enum (12 event types: created, queued, rendered, suppressed, send_attempted, sent, failed, retry_scheduled, dlq_created, dlq_replayed, webhook_signature_failed, template_failed)
- [ ] ChannelHealthStatus enum (HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN)
- [ ] NotificationDeliveryEvent model with ndev_ prefix
- [ ] NotificationMetricWindow model
- [ ] ChannelHealthSnapshot model
- [ ] NotificationChannelSlaOverride model
- [ ] NotificationSlaPolicy model
- [ ] NotificationSlaViolation model with nsv_ prefix
- [ ] NotificationAlertRule model with nar_ prefix
- [ ] NotificationAlertEvent model with nal_ prefix

## Observability Store
- [ ] NotificationObservabilityStore Protocol
- [ ] InMemoryNotificationObservabilityStore
- [ ] SQLiteNotificationObservabilityStore with notification_delivery_events table
- [ ] Factory function create_notification_observability_store
- [ ] aggregate_metrics() with filtering by federation_id and channel
- [ ] Sensitive data redaction in recorded events (38-key sensitive key set)

## SLA Service
- [ ] NotificationSlaService with evaluate() method
- [ ] Per-channel SLA override resolution
- [ ] Severity determination (warning vs critical based on 2x threshold)
- [ ] SLA violation recording with PolicyChangeEvent + FederationHistoryEvent

## Alert Store
- [ ] NotificationAlertStore Protocol
- [ ] InMemoryNotificationAlertStore
- [ ] SQLiteNotificationAlertStore
- [ ] Factory function create_notification_alert_store
- [ ] Cooldown-aware alert firing (prevents duplicate alerts within window)
- [ ] Alert lifecycle: open -> acknowledged -> resolved

## Report Export
- [ ] export_notification_events_json
- [ ] export_notification_events_csv
- [ ] export_notification_metrics_json
- [ ] export_notification_metrics_csv
- [ ] export_notification_alerts_json
- [ ] export_notification_alerts_csv
- [ ] Sensitive data redaction in all exports

## Notification Service Integration
- [ ] _record_delivery_event() called throughout dispatch lifecycle
- [ ] Best-effort recording (never breaks notification flow)
- [ ] record_sla_violation() audit hooks
- [ ] record_alert_created() audit hooks

## Config
- [ ] RolloutFederationNotificationObservabilityConfig
- [ ] RolloutFederationNotificationSlaConfig
- [ ] RolloutFederationNotificationSlaChannelOverrideConfig
- [ ] RolloutFederationNotificationAlertConfig
- [ ] RolloutFederationNotificationAlertRuleConfig
- [ ] Loader wiring for all observability config sections

## RBAC
- [ ] OBSERVABILITY_VIEW permission (policy.observability.view, default-allowed)
- [ ] OBSERVABILITY_EXPORT permission (policy.observability.export, default-allowed)
- [ ] FEDERATION_NOTIFICATION_LIST permission (policy.federation.notification.list, default-allowed)

## Change Events
- [ ] FEDERATION_NOTIFICATION_SLA_VIOLATION_DETECTED
- [ ] FEDERATION_NOTIFICATION_ALERT_CREATED
- [ ] FEDERATION_NOTIFICATION_ALERT_ACKNOWLEDGED
- [ ] FEDERATION_NOTIFICATION_ALERT_RESOLVED
- [ ] FEDERATION_NOTIFICATION_REPORT_EXPORTED

## Federation History Events
- [ ] No new FederationHistoryEventType values (reuses existing events via integration hooks)

## CLI Commands
- [ ] agentapp policy federation notification events list
- [ ] agentapp policy federation notification metrics
- [ ] agentapp policy federation notification health
- [ ] agentapp policy federation notification sla check
- [ ] agentapp policy federation notification alerts list
- [ ] agentapp policy federation notification alerts ack
- [ ] agentapp policy federation notification alerts resolve
- [ ] agentapp policy federation notification report export

## Console Pages
- [ ] GET /federation/notifications/observability (dashboard)
- [ ] GET /federation/notifications/events (delivery events list)
- [ ] GET /federation/notifications/metrics (metrics detail)
- [ ] GET /federation/notifications/health (channel health)
- [ ] GET /federation/notifications/sla (SLA violations)
- [ ] GET /federation/notifications/alerts (alert list)
- [ ] GET /federation/notifications/alerts/{alert_id} (alert detail)
- [ ] POST /federation/notifications/alerts/{alert_id}/acknowledge
- [ ] POST /federation/notifications/alerts/{alert_id}/resolve

## Export
- [ ] export_federation_notification_events_json
- [ ] export_federation_notification_events_csv
- [ ] export_federation_notification_metrics_json
- [ ] export_federation_notification_metrics_csv
- [ ] export_federation_notification_alerts_json
- [ ] export_federation_notification_alerts_csv
- [ ] No sensitive data (API keys, tokens, secrets, signatures) in exports

## Observability
- [ ] SLA violations recorded as policy change events + history events
- [ ] Alert creation recorded as policy change event + history event
- [ ] Alert acknowledge/resolve recorded as policy change events

## Documentation
- [ ] Phase 52 section in docs/policy_release.md
- [ ] CHANGELOG.md v0.40.0 entry
- [ ] README.md Phase 52 roadmap entry
- [ ] Release checklist

## Enum Count Verification
- [ ] PolicyChangeEventType: 118 → 123
- [ ] FederationHistoryEventType: 36 (unchanged)
- [ ] PolicyReleasePermission: 88 (unchanged)
- [ ] FederationNotificationStatus: 9 (unchanged)
