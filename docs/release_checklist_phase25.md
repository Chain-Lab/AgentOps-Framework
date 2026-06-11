# Phase 25: Policy Decision Store & Ops Reporting v1 — Release Checklist

## Implementation

- [x] `PolicyDecisionStore` Protocol (`agent_app/governance/policy_decision_store.py`)
- [x] `InMemoryPolicyDecisionStore` — list-based with filter support, sorted newest-first
- [x] `SQLitePolicyDecisionStore` — persistent SQLite store with 5 indexes
- [x] `PolicyDecisionTrace` model — added `tool_name` field
- [x] `PolicyReport` model — aggregated statistics
- [x] `PolicyReportingService` — generate_report, export_jsonl, export_csv
- [x] Config schema: `PolicyDecisionStoreConfig` in `GovernanceConfig.policy_decisions`
- [x] Config loader: builds store from config (memory/sqlite)
- [x] ToolExecutor wiring: records PolicyDecisionTrace after every policy evaluation
- [x] AppRunner wiring: passes policy_decision_store to ToolExecutor
- [x] AgentApp wiring: passes policy_decision_store through _ensure_runner()
- [x] Enhanced `/policy-decisions` FastAPI endpoint with full filters + pagination
- [x] New `/policy-decisions/{decision_id}` FastAPI endpoint
- [x] New `/policy-report` FastAPI endpoint
- [x] CLI: `policy decisions`, `policy report`, `policy export` commands
- [x] customer_support example: SQLite policy decision store configured
- [x] `docs/policy_reporting.md` documentation

## Tests

- [x] 24 tests for PolicyDecisionStore (InMemory + SQLite)
- [x] 8 tests for PolicyReportingService (report, export JSONL, export CSV)
- [x] 187 total policy-related tests passing
- [ ] Full test suite verification (1814 tests)

## Architecture Boundaries

- [x] Core modules have no FastAPI dependency
- [x] Core modules have no openai-agents dependency
- [x] SQLite via stdlib `sqlite3` only (no ORM)

## Documentation

- [x] `docs/policy_reporting.md`
- [x] `CHANGELOG.md` Phase 25 section
- [x] `README.md` feature list updated
