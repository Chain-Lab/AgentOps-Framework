# Phase 26: Policy Console Lite v1 — Release Checklist

## Implementation

- [x] `PolicyConsoleConfig` model in `agent_app/config/schema.py`
- [x] Config loader: reads `policy_console` from YAML, stores on `AgentApp._console_config`
- [x] `agent_app/console/` package: `__init__.py`, `router.py`, templates, static
- [x] 4 HTML pages: Dashboard, Decisions List, Decision Detail, Report
- [x] Jinja2 templates with shared `base.html` layout
- [x] CSS styling (no frontend build step)
- [x] Console router conditionally mounted in `create_fastapi_app()`
- [x] Static file serving for CSS/JS
- [x] Jinja2 optional — graceful fallback when not installed
- [x] `customer_support` example updated with policy_console config

## Tests

- [x] Config tests: default disabled, enabled config, YAML loading (5 tests)
- [x] Router tests: disabled no routes, each page returns 200, 404 handling (6 tests)
- [x] All existing tests pass (verified via full suite)

## Architecture Boundaries

- [x] Core modules have no FastAPI dependency
- [x] Core modules have no jinja2 dependency (console is in separate package)
- [x] Console reuses Phase 25 store/reporting service
- [x] No duplicate query logic in console

## Documentation

- [x] `docs/policy_console.md`
- [x] `docs/release_checklist_phase26.md`
- [ ] CHANGELOG.md update
- [ ] README.md update
