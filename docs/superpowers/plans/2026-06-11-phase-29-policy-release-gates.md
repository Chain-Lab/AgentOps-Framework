# Phase 29: Policy Release Gates & Versioned Policy Bundles v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build policy release safety gates with versioned policy bundles, gate evaluation, and promote/rollback lifecycle.

**Architecture:** New modules follow Phase 28 store patterns (Protocol + InMemory + SQLite). PolicyReleaseService orchestrates bundle creation, replay-based gate evaluation, promote, and rollback. CLI and console read-only pages for visibility.

**Tech Stack:** Pydantic models, stdlib sqlite3, argparse CLI, FastAPI/Jinja2 console (read-only)

---

## File Structure

```
agent_app/governance/policy_bundle.py       # PolicyBundle, PolicyBundleStatus, stores
agent_app/governance/policy_gate.py         # PolicyGateRule/Result/Status, PolicyGateEvaluator
agent_app/runtime/policy_gate_store.py      # PolicyGateStore protocol + InMemory + SQLite
agent_app/runtime/policy_release.py         # PolicyReleaseService
agent_app/config/schema.py                  # Extend GovernanceConfig with policy_release
agent_app/config/loader.py                  # Wire policy_release into build_app
agent_app/cli.py                            # New bundle/gate subcommands
agent_app/console/router.py                 # New bundle/gate read-only routes
agent_app/console/templates/bundles.html    # New
agent_app/console/templates/bundle_detail.html # New
agent_app/console/templates/gates.html      # New
agent_app/console/templates/gate_detail.html # New
tests/unit/test_policy_bundle_store.py      # New
tests/unit/test_policy_gate.py              # New
tests/unit/test_policy_gate_store.py        # New
tests/unit/test_policy_release.py           # New
tests/unit/test_policy_release_cli.py       # New
tests/unit/test_policy_release_console.py   # New
docs/policy_release.md                      # New
CHANGELOG.md                                # Update
README.md                                   # Update
docs/release_checklist_phase29.md           # New
```

---

### Task 1: Policy Bundle models + InMemory store

**Files:**
- Create: `agent_app/governance/policy_bundle.py`
- Create: `tests/unit/test_policy_bundle_store.py`

- [ ] **Step 1: Write the failing test** (see full test code in plan)
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_bundle_store.py -v
  ```
- [ ] **Step 3: Write minimal implementation** — PolicyBundleStatus enum, PolicyBundle model, compute_config_hash(), InMemoryPolicyBundleStore
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_bundle_store.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/governance/policy_bundle.py tests/unit/test_policy_bundle_store.py
  git commit -m "feat: Phase 29 Task 1 — PolicyBundle model and InMemory store"
  ```

---

### Task 2: SQLitePolicyBundleStore

**Files:**
- Modify: `agent_app/governance/policy_bundle.py`
- Test: `tests/unit/test_policy_bundle_store.py`

- [ ] **Step 1: Append SQLite tests** — test_persists_across_instances, test_list_sorted_desc, test_activate_archives_previous, test_get_active
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_bundle_store.py::TestSQLitePolicyBundleStore -v
  ```
- [ ] **Step 3: Implement SQLitePolicyBundleStore** — _init_db with policy_bundles table, create/get/list/get_active/activate/archive, _row_to_bundle, close()
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_bundle_store.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/governance/policy_bundle.py tests/unit/test_policy_bundle_store.py
  git commit -m "feat: Phase 29 Task 2 — SQLitePolicyBundleStore"
  ```

---

### Task 3: Policy Gate models and evaluator

**Files:**
- Create: `agent_app/governance/policy_gate.py`
- Create: `tests/unit/test_policy_gate.py`

- [ ] **Step 1: Write the failing test** — PolicyGateStatus, PolicyGateRule, PolicyGateResult models; PolicyGateEvaluator with passed/failed/warning cases
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_gate.py -v
  ```
- [ ] **Step 3: Implement** — enums, BaseModel classes, PolicyGateEvaluator.evaluate() with per-rule evaluation
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_gate.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/governance/policy_gate.py tests/unit/test_policy_gate.py
  git commit -m "feat: Phase 29 Task 3 — PolicyGate models and evaluator"
  ```

---

### Task 4: PolicyGateStore (InMemory + SQLite)

**Files:**
- Create: `agent_app/runtime/policy_gate_store.py`
- Test: `tests/unit/test_policy_gate_store.py`

- [ ] **Step 1: Write the failing test** — create/get/list (all + by bundle_id), SQLite persistence
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_gate_store.py -v
  ```
- [ ] **Step 3: Implement** — PolicyGateStore protocol, InMemoryPolicyGateStore, SQLitePolicyGateStore with policy_gate_results table, create_gate_store() factory
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_gate_store.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/runtime/policy_gate_store.py tests/unit/test_policy_gate_store.py
  git commit -m "feat: Phase 29 Task 4 — PolicyGateStore (InMemory + SQLite)"
  ```

---

### Task 5: PolicyReleaseService

**Files:**
- Create: `agent_app/runtime/policy_release.py`
- Test: `tests/unit/test_policy_release.py`

- [ ] **Step 1: Write the failing test** — create_bundle (hash computation), run_gate (stores result), promote (requires passing gate), promote success, rollback (activates target)
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_release.py -v
  ```
- [ ] **Step 3: Implement** — PolicyReleaseService with create_bundle/run_gate/promote/rollback
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_release.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/runtime/policy_release.py tests/unit/test_policy_release.py
  git commit -m "feat: Phase 29 Task 5 — PolicyReleaseService"
  ```

---

### Task 6: Config schema and loader extensions

**Files:**
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`

- [ ] **Step 1: Write the failing test** — PolicyReleaseConfig defaults, sqlite config, PolicyGateRuleConfig
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_release.py -k "config" -v
  ```
- [ ] **Step 3: Implement** — Add PolicyGateRuleConfig, PolicyReleaseStoreConfig, PolicyReleaseConfig to schema; add policy_release to GovernanceConfig; update _normalize_dicts_to_lists; wire into loader.py build_app()
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_release.py -k "config" -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/config/schema.py agent_app/config/loader.py
  git commit -m "feat: Phase 29 Task 6 — Config schema and loader for policy_release"
  ```

---

### Task 7: CLI commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_release_cli.py`

- [ ] **Step 1: Write the failing test** — bundle create/list/active, gate run/list, promote, rollback
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_release_cli.py -v
  ```
- [ ] **Step 3: Implement** — Add bundle create/list/active/promote/rollback subcommands; add gate run/list subcommands; handler functions
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_release_cli.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/cli.py tests/unit/test_policy_release_cli.py
  git commit -m "feat: Phase 29 Task 7 — CLI bundle and gate commands"
  ```

---

### Task 8: Console read-only pages

**Files:**
- Modify: `agent_app/console/router.py`
- Create: `agent_app/console/templates/bundles.html`
- Create: `agent_app/console/templates/bundle_detail.html`
- Create: `agent_app/console/templates/gates.html`
- Create: `agent_app/console/templates/gate_detail.html`
- Test: `tests/unit/test_policy_release_console.py`

- [ ] **Step 1: Write the failing test** — bundle index, bundle detail, gate index, gate detail render
- [ ] **Step 2: Run test to verify it fails**
  ```bash
  pytest tests/unit/test_policy_release_console.py -v
  ```
- [ ] **Step 3: Implement** — Add routes to router.py; create Jinja2 templates
- [ ] **Step 4: Run test to verify it passes**
  ```bash
  pytest tests/unit/test_policy_release_console.py -v
  ```
- [ ] **Step 5: Commit**
  ```bash
  git add agent_app/console/router.py agent_app/console/templates/bundles.html agent_app/console/templates/bundle_detail.html agent_app/console/templates/gates.html agent_app/console/templates/gate_detail.html tests/unit/test_policy_release_console.py
  git commit -m "feat: Phase 29 Task 8 — Console read-only bundle and gate pages"
  ```

---

### Task 9: Documentation and final verification

- [ ] **Step 1: Write docs/policy_release.md**
- [ ] **Step 2: Update CHANGELOG.md** — Phase 29 section
- [ ] **Step 3: Update README.md** — Roadmap + Policy Release section
- [ ] **Step 4: Create docs/release_checklist_phase29.md**
- [ ] **Step 5: Run full test suite and verify no regressions**
  ```bash
  pytest tests/unit/ -v --tb=short 2>&1 | tail -5
  ```
- [ ] **Step 6: Commit**
  ```bash
  git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase29.md
  git commit -m "feat: Phase 29 — Policy Release Gates & Versioned Policy Bundles v1"
  ```
