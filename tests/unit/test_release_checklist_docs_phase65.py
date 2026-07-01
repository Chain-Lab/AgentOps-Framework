"""Phase 65 — verify backfilled release checklist docs exist and are structured correctly."""
from __future__ import annotations

import os

import pytest

DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))

BACKFILLED_PHASES = [59, 60, 61, 63]


@pytest.mark.parametrize("phase", BACKFILLED_PHASES)
def test_checklist_exists(phase: int) -> None:
    path = os.path.join(DOCS_DIR, f"release_checklist_phase{phase}.md")
    assert os.path.isfile(path), f"Missing {path}"


@pytest.mark.parametrize("phase", BACKFILLED_PHASES)
def test_checklist_has_required_sections(phase: int) -> None:
    path = os.path.join(DOCS_DIR, f"release_checklist_phase{phase}.md")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert f"# Release Checklist — Phase {phase}" in content
    assert "## Implementation Checklist" in content
    assert "## Test Coverage" in content
    assert "## Acceptance Criteria" in content
    assert "**Version:**" in content
