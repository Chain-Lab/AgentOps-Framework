# Phase 51 Release Checklist

## Models
- [ ] FederationNotificationTemplate model with fntmpl_ prefix
- [ ] FederationNotificationTemplateFormat enum (json, text, html, markdown)
- [ ] FederationNotificationPreference model with fnpref_ prefix
- [ ] FederationNotificationPreferenceDecision enum (deliver, suppress, inherit)
- [ ] FederationNotificationPreferenceSubjectType enum (approval, federation, global)
- [ ] FederationWebhookRequestSnapshot model with fwrqsn_ prefix
- [ ] FederationWebhookReplayResult model with freplay_ prefix
- [ ] SUPPRESSED, TEMPLATE_FAILED, SIGNATURE_FAILED added to FederationNotificationStatus

## Template Renderer
- [ ] Safe variable substitution with {{variable}} syntax
- [ ] Strict variables mode (raises on missing)
- [ ] Built-in fallback template
- [ ] Template selection priority (federation+event+channel > event+channel > channel > global > builtin)

## Template Store
- [ ] FederationNotificationTemplateStore Protocol
- [ ] InMemoryFederationNotificationTemplateStore
- [ ] SQLiteFederationNotificationTemplateStore
- [ ] Factory function

## Preference Store & Service
- [ ] FederationNotificationPreferenceStore Protocol
- [ ] InMemoryFederationNotificationPreferenceStore
- [ ] SQLiteFederationNotificationPreferenceStore
- [ ] Preference resolution priority (approval+event+channel > federation+event+channel > event+channel > channel > global > system default)
- [ ] Mandatory event types (override opt-out)
- [ ] Preference explanation with specificity and reason codes

## Webhook Signature & Nonce
- [ ] HMAC-SHA256 signature service with key rotation
- [ ] Signing input: {timestamp}.{nonce}.{body}
- [ ] Signature headers: X-AgentApp-Signature, X-AgentApp-Signature-Timestamp, X-AgentApp-Signature-Nonce, X-AgentApp-Signature-Version, X-AgentApp-Delivery-ID
- [ ] Key rotation with active_key_id + verification keys
- [ ] Timestamp tolerance (default 300s)
- [ ] Nonce store for replay protection

## Webhook Replay
- [ ] Original-payload replay from DLQ (distinct from retry)
- [ ] replay-original: uses original body bytes, generates new signature/timestamp/nonce
- [ ] Replay audit trail
- [ ] FEDERATION_WEBHOOK_REPLAY permission required

## Notification Service Integration
- [ ] Template selection integrated into dispatch
- [ ] Preference resolution before delivery
- [ ] Webhook signing on dispatch
- [ ] Snapshot creation for audit
- [ ] DLQ replay-original distinct from retry

## Config
- [ ] FederationNotificationTemplateConfig
- [ ] FederationNotificationPreferenceConfig
- [ ] FederationWebhookSigningConfig
- [ ] FederationWebhookReplayConfig
- [ ] Loader wiring

## RBAC
- [ ] FEDERATION_NOTIFICATION_TEMPLATE_LIST permission (default-allowed)
- [ ] FEDERATION_NOTIFICATION_TEMPLATE_MANAGE permission
- [ ] FEDERATION_NOTIFICATION_PREFERENCE_VIEW permission (default-allowed)
- [ ] FEDERATION_NOTIFICATION_PREFERENCE_MANAGE permission
- [ ] FEDERATION_WEBHOOK_VERIFY permission
- [ ] FEDERATION_WEBHOOK_REPLAY permission

## Change Events
- [ ] 12 new PolicyChangeEventType values for templates, preferences, signing, replay

## Federation History Events
- [ ] 3 new FederationHistoryEventType values

## CLI Commands
- [ ] agentapp policy federation notification template list/show/create/update/disable/render
- [ ] agentapp policy federation notification preference list/set/show/delete/explain
- [ ] agentapp policy federation notification dlq replay-original --dlq-id ... [--dry-run]
- [ ] agentapp policy federation webhook verify --body-file ... --signature ... --timestamp ... --nonce ...

## Console Pages
- [ ] /policy-console/federation/notifications/templates
- [ ] /policy-console/federation/notifications/templates/{template_id}
- [ ] /policy-console/federation/notifications/preferences
- [ ] /policy-console/federation/notifications/preferences/explain
- [ ] Extended DLQ detail with replay info

## Export
- [ ] export_federation_notification_templates_json
- [ ] export_federation_notification_templates_csv
- [ ] export_federation_notification_preferences_json
- [ ] export_federation_notification_preferences_csv
- [ ] export_federation_webhook_replays_json (digest only, no full body)
- [ ] export_federation_webhook_replays_csv (digest only, no full body)
- [ ] No signature keys, auth headers, or full webhook bodies exported

## Observability
- [ ] get_template_summary() in FederationObservabilityService
- [ ] get_preference_summary() in FederationObservabilityService
- [ ] Template and preference summaries in generate_report() metadata

## Documentation
- [ ] Phase 51 section in docs/policy_release.md
- [ ] CHANGELOG.md v0.39.0 entry
- [ ] README.md Phase 51 roadmap entry
- [ ] Release checklist

## Enum Count Verification
- [ ] PolicyChangeEventType: 118
- [ ] FederationHistoryEventType: 36
- [ ] PolicyReleasePermission: 88
- [ ] FederationNotificationStatus: 9
