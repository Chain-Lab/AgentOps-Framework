# v0.7.0 Release Checklist

## Pre-Release Verification

- [ ] `python -m pytest` — all 371 tests pass
- [ ] `pytest -W default` — no project-code warnings (dependency warnings OK)
- [ ] `agentapp eval run examples/customer_support/evals/customer_support.yaml --config examples/customer_support/agentapp.yaml` — 4/4 pass
- [ ] `agentapp eval run examples/research_assistant/evals/research_assistant.yaml --config examples/research_assistant/agentapp.yaml` — 4/4 pass
- [ ] `tests/unit/test_native_hitl.py` — 24 Phase 10 tests pass
- [ ] `tests/unit/test_import_boundaries.py` — core/registry/config do not import openai-agents
- [ ] Optional dependencies verified: `pip install -e ".[openai]"` works

## Documentation

- [ ] `README.md` — OpenAI Native HITL Mode section present
- [ ] `README.md` — Roadmap updated (v0.7 entries)
- [ ] `docs/openai_backend.md` — HITL Modes section (wrapper vs native)
- [ ] `docs/openai_backend.md` — Flow diagrams for both modes
- [ ] `docs/openai_backend.md` — API reference includes `hitl_mode` and `resume()`
- [ ] `docs/openai_backend.md` — Current Limitations updated (Phase 10 capabilities)
- [ ] `docs/run_state.md` — `backend_state` documented
- [ ] `docs/run_state.md` — OpenAI native mode structure documented
- [ ] `docs/run_state.md` — SDK version compatibility noted
- [ ] `examples/openai_basic/README.md` — HITL modes explained
- [ ] `examples/openai_basic/agentapp.native.yaml` — native config present
- [ ] `examples/openai_basic/main.py` — `--config` flag supported
- [ ] `docs/release_checklist_v0.7.md` — this file

## Smoke Tests

- [ ] `tests/smoke/test_openai_native_hitl_smoke.py` — exists, marked `openai_smoke`
- [ ] `pyproject.toml` — `openai_smoke` marker registered
- [ ] Smoke tests skip when `OPENAI_API_KEY` not set
- [ ] Default `pytest` does not run smoke tests
- [ ] Smoke test run documented: `OPENAI_API_KEY=... python -m pytest -m openai_smoke`

## Changelog

- [ ] `CHANGELOG.md` — `## 0.7.0` section with all additions/changes/limitations

## Package Metadata

- [ ] `pyproject.toml` — `version = "0.7.0"`
- [ ] `pyproject.toml` — pytest markers include `openai_smoke`

## Git

- [ ] `git add .`
- [ ] `git commit -m "release: v0.7.0 openai native hitl resume"`
- [ ] `git tag v0.7.0`
