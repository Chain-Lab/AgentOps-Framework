# Phase 19 Recovery Admin Console Design

Date: 2026-06-09

## Goal

Build a lightweight, server-rendered Recovery Admin Console on top of the existing Phase 18 recovery admin APIs. The console provides operational visibility and carefully gated recovery actions without changing daemon defaults, recovery dry-run defaults, or existing JSON API semantics.

## Non-goals

- No React, Vite, Node, or frontend build pipeline.
- No UI console state stored in a database.
- No changes to `RecoveryDaemon` scheduling or default-off behavior.
- No changes to recovery dry-run defaults.
- No new hard FastAPI dependency.
- No GET route performs recovery or any other mutating operation.
- No live recovery unless explicitly confirmed.

## Architecture

Add a new optional FastAPI UI router module:

```text
agent_app/adapters/recovery_ui.py
```

Public factory:

```python
def create_recovery_ui_router(app: AgentApp, admin_dependency: Any | None = None) -> APIRouter
```

Security is secure-by-default:

- If `admin_dependency` is supplied, every UI route uses it via router-level `Depends(admin_dependency)`.
- If `admin_dependency` is omitted, every UI route returns `403 Forbidden`.
- The UI router does not implicitly reuse the JSON admin router dependency.
- Callers may pass the same dependency to both routers if desired.
- The module imports FastAPI lazily inside the factory, so `import agent_app` remains independent of FastAPI.

Separation of concerns:

- `agent_app/adapters/recovery_admin.py` remains the JSON API router.
- `agent_app/adapters/recovery_ui.py` provides HTML routes only.
- Both call existing `AgentApp` recovery methods instead of duplicating scanner/service logic.

## Route surface

```http
GET  /admin/recovery
GET  /admin/recovery/candidates
GET  /admin/recovery/candidates/{run_id}
GET  /admin/recovery/history
POST /admin/recovery/scan
POST /admin/recovery/candidates/{run_id}/confirm
POST /admin/recovery/candidates/{run_id}/recover
```

## Page behavior

### Dashboard: `GET /admin/recovery`

Displays:

- daemon enabled/disabled
- dry-run default state
- scanner availability
- recovery service availability
- last tick information when available
- links to candidates and history
- safety statements: daemon does not auto-start, dry-run is default, live recovery requires explicit confirmation, recovery is best-effort

No side effects.

### Candidate list: `GET /admin/recovery/candidates`

Calls `app.run_recovery_scan_once()` with default dry-run behavior and renders:

- scanned count
- selected count
- recovered count (expected 0 for dry-run UI scans)
- skipped count
- failed count
- selected run IDs with inspect links
- skipped/failure summaries

No recovery execution.

### Candidate inspect: `GET /admin/recovery/candidates/{run_id}`

Calls `app.inspect_recovery_candidate(run_id)` and renders:

- run ID
- workflow name
- status
- recommendation
- reasons
- lease status
- resumability
- plan summaries when present
- a form for the confirmation step

Missing candidate returns 404 with a clean error page. No recovery execution.

### History timeline: `GET /admin/recovery/history`

Accepts optional `run_id` query parameter.

- With `run_id`: calls `app.get_recovery_history(run_id)` and renders events chronologically.
- Without `run_id`: renders a form/message explaining that current history API is run-scoped.

No side effects.

### Dry-run scan trigger: `POST /admin/recovery/scan`

Always dry-run.

- If a request attempts `dry_run=false`, return 400.
- Otherwise call `app.run_recovery_scan_once(AutoRecoveryPolicy(dry_run=True))`.
- Render the dry-run scan result.

### Manual recovery confirmation flow

Two-step flow:

1. `POST /admin/recovery/candidates/{run_id}/confirm`
   - calls `inspect_recovery_candidate(run_id)` to confirm the candidate exists and render risk/recommendation details
   - generates a confirmation token
   - does not execute recovery

2. `POST /admin/recovery/candidates/{run_id}/recover`
   - requires `confirmation_token`
   - requires `confirm_no_dry_run=true`
   - validates token
   - only then calls `app.recover_run(run_id=run_id, dry_run=False)`

Missing token, invalid token, or missing explicit confirmation returns 400.

## Confirmation token

Use an HMAC token to avoid adding storage:

```text
HMAC(secret, f"recover:{run_id}")
```

- The secret is generated when the router is created.
- Tokens are valid for the lifetime of that router process.
- This is enough for Phase 19 because the token only gates a second POST in the same process and does not attempt durable authorization.
- Authorization remains the responsibility of `admin_dependency`.

## Rendering

Use plain `HTMLResponse` and small helper functions:

- `_html_page(title, body, status_code=200)`
- `_escape(value)`
- `_error_page(title, message, status_code)`
- `_badge(label, ok)`
- rendering functions for status, candidate, scan result, history

No template engine. No CSS framework. Inline minimal styles are acceptable.

All pages include clear safety text for recovery operations.

## Error handling

- 403 handled by FastAPI dependency.
- 404 for missing candidates.
- 400 for invalid live-scan attempt or invalid recovery confirmation.
- 500 pages use generic text and never return tracebacks or raw exception strings.
- Detailed exceptions are logged server-side.

## Testing

Add `tests/unit/test_recovery_ui.py`.

Required coverage:

1. importing `agent_app` does not require FastAPI
2. importing `agent_app.adapters.recovery_ui` does not require FastAPI until factory call
3. UI router without dependency returns 403 for all routes
4. UI router with deny dependency returns 403
5. dashboard renders
6. candidate list renders
7. candidate inspect renders
8. history renders
9. dry-run scan POST calls scan with dry_run=True
10. live scan attempt is rejected
11. confirm page renders token
12. recover without token is rejected
13. recover with invalid token is rejected
14. confirmed recovery calls `app.recover_run(..., dry_run=False)`
15. missing candidate returns clean 404
16. recovery failure renders clean error/no traceback

Regression commands:

```bash
python -m pytest tests/unit/test_cli.py -q
python -m pytest tests/unit/test_recovery_admin.py tests/unit/test_recovery_cli.py tests/unit/test_recovery_daemon.py -q
python -m pytest tests/unit/test_recovery_ui.py -q
python -m pytest -q
```

## Documentation

Update:

- `README.md`
- `CHANGELOG.md`
- `docs/release_checklist_v0.10.md`
- add `docs/recovery_admin_console.md`

Docs must explain:

- how to mount the UI router
- `admin_dependency` is required for access
- daemon remains default-off
- dry-run remains default
- live recovery requires confirmation token and checkbox/field
- recovery is best-effort; leases are not exactly-once
- current limitations
