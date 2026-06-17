# Phase 44 Release Checklist: Notification Hooks and Expiration Workers

## Feature Summary

Phase 44 adds framework-level notification hooks and expiration workers, making governance states actionable through rule-based notifications and automated expiration sweeps.

## Verification Steps

- [ ] Phase 44 notification model tests pass
- [ ] Phase 44 expiration model tests pass
- [ ] Phase 44 notification store tests pass
- [ ] Phase 44 rule store tests pass
- [ ] Phase 44 notification channel tests pass
- [ ] Phase 44 notification service tests pass
- [ ] Phase 44 expiration service tests pass
- [ ] Phase 44 expiration worker tests pass
- [ ] Phase 44 config/RBAC/events tests pass
- [ ] Phase 44 CLI tests pass
- [ ] Phase 44 console tests pass
- [ ] Broader policy regression tests pass
- [ ] Phase 42/43 backward compatibility preserved
- [ ] Import boundaries preserved (no circular imports)

## New Files

| File | Purpose |
|------|---------|
| `agent_app/governance/policy_notification.py` | Notification message + rule models |
| `agent_app/governance/policy_expiration.py` | Expiration result + sweep report models |
| `agent_app/runtime/policy_notification_store.py` | Notification delivery store (Protocol + InMemory + SQLite) |
| `agent_app/runtime/policy_notification_rule_store.py` | Notification rule store (Protocol + InMemory + SQLite) |
| `agent_app/runtime/policy_notification_channels.py` | Log + InMemory + Failing notification channels |
| `agent_app/runtime/policy_notification_service.py` | NotificationService: match rules, create/send/list |
| `agent_app/runtime/policy_expiration_service.py` | ExpirationService: sweep approvals + gate requirements |
| `agent_app/runtime/policy_expiration_worker.py` | Optional in-process expiration worker |
| `agent_app/console/templates/policy_notifications.html` | Notification list page |
| `agent_app/console/templates/policy_notification_rules.html` | Notification rule list page |
| `agent_app/console/templates/policy_expiration.html` | Expiration sweep page |

## Modified Files

| File | Changes |
|------|---------|
| `agent_app/governance/policy_rbac.py` | +7 RBAC permissions |
| `agent_app/governance/policy_change_event.py` | +10 event types |
| `agent_app/config/schema.py` | +3 config models, +2 fields on PolicyReleaseConfig |
| `agent_app/config/loader.py` | Wire notification + expiration services |
| `agent_app/core/app.py` | +3 properties (notification_service, expiration_service, expiration_worker) |
| `agent_app/cli.py` | notification/expiration CLI subcommands |
| `agent_app/console/router.py` | 6 notification/expiration routes |
| `agent_app/adapters/fastapi.py` | Wire notification_service, expiration_service |

## Known Limitations

- No Slack/Jira/email integration
- No external webhook delivery
- No distributed queue
- No durable retry backoff
- No production scheduler
- Worker is local-process only
- Notifications depend on rules and event coverage

## Phase 45 Recommendation

**Policy Rollout Analytics & History** — Add rollout history tracking, gate result persistence across rollout lifecycle, analytics/visualization for rollout gate outcomes, and integration with the observability dashboard.
