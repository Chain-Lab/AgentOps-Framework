# Phase 49 Release Checklist

## Version: v0.37.0

### New Features
- [ ] Federation notification models
- [ ] Federation notification store (InMemory + SQLite)
- [ ] Federation notification adapters (noop, console, fake, webhook)
- [ ] Federation notification service (enqueue + dispatch)
- [ ] Federation approval escalation worker
- [ ] Distributed lock (InMemory + SQLite)
- [ ] CLI notification and worker commands
- [ ] Console notification and escalation pages
- [ ] Approval service notification integration
- [ ] Observability notification summary

### Breaking Changes
- None (backward compatible)

### Event Count Changes
- PolicyChangeEventType: 94 → 100
- FederationHistoryEventType: 28 → 30
- PolicyReleasePermission: 76 → 79

### Test Verification
- [ ] Full test suite passes with 0 failures
- [ ] All Phase 48 approval tests pass
- [ ] All Phase 47 observability tests pass
- [ ] Import boundaries clean
- [ ] Optional dependency behavior preserved

### Documentation
- [ ] docs/policy_release.md updated
- [ ] CHANGELOG.md updated
- [ ] README.md updated
- [ ] Release checklist created
