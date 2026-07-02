# Book Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `examples/book_publisher/` — a runnable example that generates audience-tailored book descriptions (children/adult/student/teacher, YAML-extensible) via a parallel DAG of mock-LLM agent nodes, then pushes each variant to a set of mock publishing platforms (wechat_mp/zhihu/juejin/csdn, YAML-extensible) through the framework's real governance pipeline — auto-publishing low-risk platforms and pausing high-risk ones for human approval.

**Architecture:** Two-stage pipeline. Stage 1 is a `Workflow.dag()` of tool-free agent nodes (one per persona, parallel), executed via `app.run(workflow="book_generation", ...)`, backed by a deterministic `MockPersonaBackend` that renders persona-flavored text from `AgentSpec.metadata` — no network calls. Stage 2 is a plain async loop (`book_publisher/pipeline.py`) that drives a directly-constructed, real `ToolExecutor` per persona×platform publish call; this is a deliberate correction from the original design (see "Why not DAG tool nodes" below) because the framework's DAG tool-node path cannot have its approvals resolved by the app's public `approve()` API.

**Tech Stack:** Python 3.10+, pydantic v2, pyyaml, pytest (`asyncio_mode = "auto"`, no `@pytest.mark.asyncio` needed), the existing `agent_app` framework (`AgentApp`, `Workflow`, `ToolExecutor`, `InMemoryApprovalStore`/`InMemoryAuditLogger`/`InMemoryTraceCollector`). Run tests with `.venv/bin/python -m pytest`.

---

## Why not DAG tool nodes for publishing

Confirmed by reading the framework source before writing this plan:

- `DagExecutor._execute_tool_node` (`agent_app/workflows/dag.py`) lazily builds its **own** `ToolExecutor` wired to a throwaway `_NoOpApprovalStore()` — never the `approval_store` passed into `AgentApp`. `_NoOpApprovalStore.get()` unconditionally raises `KeyError`, so an approval created this way can never be looked up again.
- `WorkflowExecutor._run_dag` (`agent_app/runtime/workflow_executor.py`) never populates `AppRunResult.interruptions` for DAG runs.
- `ToolExecutor.execute()`'s `approved_tool_call` marker (the only apparent "retry after approval" mechanism) is validated by `_is_tool_call_approval_marker_valid`, which requires a private sentinel object (`_NATIVE_HITL_APPROVAL_TOKEN`) reserved for the OpenAI-native-SDK HITL integration. External code cannot construct a valid marker.

Net effect: a DAG `tool` node with `requires_approval=True` creates an approval that `app.approve()` can never resolve. Per this example's non-goal of zero `agent_app/` core changes, the fix is architectural, not a patch: the DAG is used only for the tool-free content-generation stage; publishing drives a directly-constructed real `ToolExecutor` from plain orchestration code. See `docs/superpowers/specs/2026-07-01-book-publisher-design.md` (as corrected) for the full spec.

---

## File Structure

```
examples/book_publisher/
├── README.md
├── agentapp.yaml
├── main.py
├── data/
│   └── sample_book.yaml
├── personas/
│   ├── children.yaml
│   ├── adult.yaml
│   ├── student.yaml
│   └── teacher.yaml
├── platforms/
│   ├── wechat_mp.yaml
│   ├── zhihu.yaml
│   ├── juejin.yaml
│   └── csdn.yaml
├── prompts/
│   └── book_writer.md
├── evals/
│   └── book_publisher.yaml
└── book_publisher/
    ├── __init__.py
    ├── models.py
    ├── personas.py
    ├── platforms.py
    ├── mock_backend.py
    ├── publishers/
    │   ├── __init__.py
    │   ├── base.py
    │   └── mock.py
    ├── tools.py
    ├── pipeline.py
    └── build_app.py

tests/unit/
├── test_book_publisher_models.py
├── test_book_publisher_personas.py
├── test_book_publisher_platforms.py
├── test_book_publisher_mock_backend.py
├── test_book_publisher_publishers.py
├── test_book_publisher_tools.py
├── test_book_publisher_build_app.py
└── test_book_publisher_pipeline.py
```

Every test file needs `examples/book_publisher` on `sys.path` (it is not an installed package). Each test file inserts it explicitly at the top — the same pattern `main.py` uses — rather than adding a repo-wide `pythonpath` config, so this example's test wiring stays self-contained:

```python
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))
```

(`parents[2]` from `tests/unit/test_x.py` is the repo root: `parents[0]`=`tests/unit`, `parents[1]`=`tests`, `parents[2]`=repo root.)

---

### Task 1: Data models + sample book

**Files:**
- Create: `examples/book_publisher/book_publisher/__init__.py`
- Create: `examples/book_publisher/book_publisher/models.py`
- Create: `examples/book_publisher/data/sample_book.yaml`
- Test: `tests/unit/test_book_publisher_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_models.py
"""Tests for book_publisher.models — data models for the example."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.models import (
    BookInput,
    GeneratedContent,
    PersonaSpec,
    PlatformSpec,
    PublishingReport,
    PublishReceipt,
)


def test_book_input_from_yaml_loads_sample_book():
    book = BookInput.from_yaml(_EXAMPLE_DIR / "data" / "sample_book.yaml")
    assert book.title
    assert book.summary
    assert len(book.key_points) >= 1


def test_book_input_to_prompt_text_includes_title_and_summary():
    book = BookInput(
        title="Deep Echo",
        summary="A crew finds an ancient signal beneath the sea.",
        key_points=["found footage", "twist ending"],
        tags=["scifi", "mystery"],
    )
    text = book.to_prompt_text()
    assert "Deep Echo" in text
    assert "A crew finds an ancient signal beneath the sea." in text
    assert "found footage" in text
    assert "scifi" in text


def test_persona_spec_defaults_target_platforms_to_none():
    persona = PersonaSpec(
        name="adult",
        display_name="Adult",
        tone="measured",
        reading_level="high school+",
        max_length=800,
    )
    assert persona.target_platforms is None
    assert persona.extra_instructions == ""


def test_platform_spec_defaults():
    platform = PlatformSpec(name="csdn", display_name="CSDN")
    assert platform.risk_level == "medium"
    assert platform.requires_approval is False
    assert platform.format == "plain"


def test_publishing_report_summary_lists_receipts():
    book = BookInput(title="Deep Echo", summary="...", key_points=[], tags=[])
    report = PublishingReport(
        book=book,
        generated=[
            GeneratedContent(
                persona="adult", book_title="Deep Echo", text="...", run_id="r1", status="completed"
            )
        ],
        receipts=[
            PublishReceipt(platform="csdn", persona="adult", status="published"),
            PublishReceipt(platform="wechat_mp", persona="adult", status="approval_required", approval_id="apv_1"),
        ],
    )
    text = report.summary()
    assert "Deep Echo" in text
    assert "csdn" in text
    assert "approval_required" in text
    assert "apv_1" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_models.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher'`)

- [ ] **Step 3: Write the models**

```python
# examples/book_publisher/book_publisher/__init__.py
"""Book publisher example — multi-persona, multi-platform content pipeline."""
```

```python
# examples/book_publisher/book_publisher/models.py
"""Pydantic data models for the book publisher example."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class BookInput(BaseModel):
    """Structured book brief — no file parsing, just title/summary/points/tags."""

    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BookInput":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def to_prompt_text(self) -> str:
        lines = [f"Title: {self.title}", f"Summary: {self.summary}"]
        if self.key_points:
            lines.append("Key points:")
            lines.extend(f"- {point}" for point in self.key_points)
        if self.tags:
            lines.append(f"Tags: {', '.join(self.tags)}")
        return "\n".join(lines)


class PersonaSpec(BaseModel):
    """One target audience, loaded from personas/*.yaml."""

    name: str
    display_name: str
    tone: str
    reading_level: str
    max_length: int
    extra_instructions: str = ""
    target_platforms: list[str] | None = None


class PlatformSpec(BaseModel):
    """One downstream publishing platform, loaded from platforms/*.yaml."""

    name: str
    display_name: str
    max_length: int | None = None
    format: str = "plain"
    hashtag_style: str = ""
    risk_level: str = "medium"
    requires_approval: bool = False


class GeneratedContent(BaseModel):
    """One persona's generated description for the book."""

    persona: str
    book_title: str
    text: str
    run_id: str
    status: str
    tags: list[str] = Field(default_factory=list)


class PublishReceipt(BaseModel):
    """Result of attempting to publish one persona's content to one platform."""

    platform: str
    persona: str
    status: str  # "published" | "approval_required" | "failed"
    approval_id: str | None = None
    published_at: str | None = None
    formatted_preview: str | None = None


class PublishingReport(BaseModel):
    """Full run summary: the book, what was generated, and every publish receipt."""

    book: BookInput
    generated: list[GeneratedContent] = Field(default_factory=list)
    receipts: list[PublishReceipt] = Field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Book: {self.book.title}", f"Generated variants: {len(self.generated)}"]
        for r in self.receipts:
            line = f"  [{r.platform}] persona={r.persona} status={r.status}"
            if r.approval_id:
                line += f" approval_id={r.approval_id}"
            lines.append(line)
        return "\n".join(lines)
```

```yaml
# examples/book_publisher/data/sample_book.yaml
title: "Deep Echo"
summary: >
  A team of young marine biologists surveying the South Pacific seabed
  discovers an ancient structure emitting a rhythmic signal, and slowly
  uncovers a civilization's secret spanning a thousand years.
key_points:
  - "Hard sci-fi setting blended with mystery-investigation pacing"
  - "A cross-disciplinary team solves the puzzle together"
  - "The ending raises questions about humanity's relationship with the ocean"
tags:
  - "scifi"
  - "mystery"
  - "ocean"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_models.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/__init__.py \
        examples/book_publisher/book_publisher/models.py \
        examples/book_publisher/data/sample_book.yaml \
        tests/unit/test_book_publisher_models.py
git commit -m "feat: add book_publisher data models and sample book"
```

---

### Task 2: Persona registry + persona YAMLs

**Files:**
- Create: `examples/book_publisher/book_publisher/personas.py`
- Create: `examples/book_publisher/personas/children.yaml`
- Create: `examples/book_publisher/personas/adult.yaml`
- Create: `examples/book_publisher/personas/student.yaml`
- Create: `examples/book_publisher/personas/teacher.yaml`
- Test: `tests/unit/test_book_publisher_personas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_personas.py
"""Tests for book_publisher.personas — YAML-driven persona registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.personas import PersonaRegistry


def test_loads_all_default_personas():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    names = {p.name for p in registry.all()}
    assert names == {"children", "adult", "student", "teacher"}
    assert len(registry) == 4


def test_children_persona_restricts_target_platforms():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    children = registry.get("children")
    assert children.target_platforms == ["csdn"]


def test_adult_persona_targets_all_platforms_by_default():
    registry = PersonaRegistry.load(_EXAMPLE_DIR / "personas")
    adult = registry.get("adult")
    assert adult.target_platforms is None


def test_adding_a_new_persona_needs_zero_code_changes(tmp_path):
    (tmp_path / "librarian.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "librarian",
                "display_name": "Librarian",
                "tone": "curatorial, precise",
                "reading_level": "professional",
                "max_length": 400,
            }
        ),
        encoding="utf-8",
    )
    registry = PersonaRegistry.load(tmp_path)
    assert len(registry) == 1
    assert registry.get("librarian").tone == "curatorial, precise"


def test_duplicate_persona_name_raises(tmp_path):
    for fname in ("a.yaml", "b.yaml"):
        (tmp_path / fname).write_text(
            yaml.safe_dump(
                {
                    "name": "dup",
                    "display_name": "Dup",
                    "tone": "x",
                    "reading_level": "x",
                    "max_length": 100,
                }
            ),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate persona"):
        PersonaRegistry.load(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_personas.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.personas'`)

- [ ] **Step 3: Write the persona registry and YAML fixtures**

```python
# examples/book_publisher/book_publisher/personas.py
"""PersonaRegistry — loads PersonaSpec objects from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from book_publisher.models import PersonaSpec


class PersonaRegistry:
    """Directory-scanned registry of audience personas.

    Adding a new audience is adding one YAML file to the directory passed
    to :meth:`load` — no code changes required.
    """

    def __init__(self) -> None:
        self._personas: dict[str, PersonaSpec] = {}

    @classmethod
    def load(cls, dir_path: str | Path) -> "PersonaRegistry":
        registry = cls()
        for path in sorted(Path(dir_path).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            persona = PersonaSpec.model_validate(data)
            if persona.name in registry._personas:
                raise ValueError(
                    f"Duplicate persona name '{persona.name}' found in {path}"
                )
            registry._personas[persona.name] = persona
        return registry

    def all(self) -> list[PersonaSpec]:
        return list(self._personas.values())

    def get(self, name: str) -> PersonaSpec:
        return self._personas[name]

    def __len__(self) -> int:
        return len(self._personas)
```

```yaml
# examples/book_publisher/personas/children.yaml
name: children
display_name: "Children"
tone: "simple, playful, lots of metaphors and questions"
reading_level: "early elementary"
max_length: 300
extra_instructions: "avoid any violent or scary descriptions; use encouraging language"
target_platforms: [csdn]
```

```yaml
# examples/book_publisher/personas/adult.yaml
name: adult
display_name: "Adult"
tone: "measured, restrained, balances literary voice with information density"
reading_level: "high school and above"
max_length: 800
extra_instructions: "some suspense is fine, but do not spoil the ending"
```

```yaml
# examples/book_publisher/personas/student.yaml
name: student
display_name: "Student"
tone: "energetic, relatable, emphasizes growth and exploration"
reading_level: "middle to high school"
max_length: 500
extra_instructions: "highlight teamwork and cross-disciplinary themes"
```

```yaml
# examples/book_publisher/personas/teacher.yaml
name: teacher
display_name: "Teacher"
tone: "professional, instruction-oriented, surfaces classroom discussion angles"
reading_level: "educator reference"
max_length: 600
extra_instructions: "distill 2-3 discussion questions suitable for classroom use"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_personas.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/personas.py \
        examples/book_publisher/personas/ \
        tests/unit/test_book_publisher_personas.py
git commit -m "feat: add book_publisher persona registry and default personas"
```

---

### Task 3: Platform registry + platform YAMLs

**Files:**
- Create: `examples/book_publisher/book_publisher/platforms.py`
- Create: `examples/book_publisher/platforms/wechat_mp.yaml`
- Create: `examples/book_publisher/platforms/zhihu.yaml`
- Create: `examples/book_publisher/platforms/juejin.yaml`
- Create: `examples/book_publisher/platforms/csdn.yaml`
- Test: `tests/unit/test_book_publisher_platforms.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_platforms.py
"""Tests for book_publisher.platforms — YAML-driven platform registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.platforms import PlatformRegistry


def test_loads_all_default_platforms():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    names = {p.name for p in registry.all()}
    assert names == {"wechat_mp", "zhihu", "juejin", "csdn"}
    assert len(registry) == 4


def test_wechat_mp_is_high_risk_and_requires_approval():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    wechat = registry.get("wechat_mp")
    assert wechat.risk_level == "high"
    assert wechat.requires_approval is True


def test_csdn_is_low_risk_auto_publish():
    registry = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    csdn = registry.get("csdn")
    assert csdn.risk_level == "low"
    assert csdn.requires_approval is False


def test_adding_a_new_platform_needs_zero_code_changes(tmp_path):
    (tmp_path / "toutiao.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "toutiao",
                "display_name": "Toutiao",
                "max_length": 2000,
                "risk_level": "medium",
            }
        ),
        encoding="utf-8",
    )
    registry = PlatformRegistry.load(tmp_path)
    assert len(registry) == 1
    assert registry.get("toutiao").max_length == 2000


def test_duplicate_platform_name_raises(tmp_path):
    for fname in ("a.yaml", "b.yaml"):
        (tmp_path / fname).write_text(
            yaml.safe_dump({"name": "dup", "display_name": "Dup"}),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate platform"):
        PlatformRegistry.load(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_platforms.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.platforms'`)

- [ ] **Step 3: Write the platform registry and YAML fixtures**

```python
# examples/book_publisher/book_publisher/platforms.py
"""PlatformRegistry — loads PlatformSpec objects from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from book_publisher.models import PlatformSpec


class PlatformRegistry:
    """Directory-scanned registry of downstream publishing platforms.

    Adding a new platform is adding one YAML file to the directory passed
    to :meth:`load` — it auto-registers as a governed publish tool once
    ``build_publish_tools`` runs over the registry (see tools.py).
    """

    def __init__(self) -> None:
        self._platforms: dict[str, PlatformSpec] = {}

    @classmethod
    def load(cls, dir_path: str | Path) -> "PlatformRegistry":
        registry = cls()
        for path in sorted(Path(dir_path).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            platform = PlatformSpec.model_validate(data)
            if platform.name in registry._platforms:
                raise ValueError(
                    f"Duplicate platform name '{platform.name}' found in {path}"
                )
            registry._platforms[platform.name] = platform
        return registry

    def all(self) -> list[PlatformSpec]:
        return list(self._platforms.values())

    def get(self, name: str) -> PlatformSpec:
        return self._platforms[name]

    def __len__(self) -> int:
        return len(self._platforms)
```

```yaml
# examples/book_publisher/platforms/wechat_mp.yaml
name: wechat_mp
display_name: "WeChat Official Account"
max_length: 3000
format: markdown
hashtag_style: "#{tag}"
risk_level: high
requires_approval: true
```

```yaml
# examples/book_publisher/platforms/zhihu.yaml
name: zhihu
display_name: "Zhihu"
max_length: 5000
format: markdown
hashtag_style: "#{tag}#"
risk_level: medium
requires_approval: false
```

```yaml
# examples/book_publisher/platforms/juejin.yaml
name: juejin
display_name: "Juejin"
max_length: 5000
format: markdown
hashtag_style: "#{tag}"
risk_level: medium
requires_approval: true
```

```yaml
# examples/book_publisher/platforms/csdn.yaml
name: csdn
display_name: "CSDN"
max_length: 4000
format: markdown
hashtag_style: "#{tag}"
risk_level: low
requires_approval: false
```

Note: `wechat_mp` gates on `risk_level: high` alone; `juejin` gates via the explicit `requires_approval: true` flag despite only `medium` risk — together they demonstrate both paths through `agent_app.governance.risk.requires_tool_approval` (`risk_level in {"high","critical"}` OR `requires_approval`). `zhihu` and `csdn` both auto-publish.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_platforms.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/platforms.py \
        examples/book_publisher/platforms/ \
        tests/unit/test_book_publisher_platforms.py
git commit -m "feat: add book_publisher platform registry and default platforms"
```

---

### Task 4: Mock LLM backend + prompt template

**Files:**
- Create: `examples/book_publisher/book_publisher/mock_backend.py`
- Create: `examples/book_publisher/prompts/book_writer.md`
- Test: `tests/unit/test_book_publisher_mock_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_mock_backend.py
"""Tests for book_publisher.mock_backend.MockPersonaBackend."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.runtime.backends import AgentBackend

from book_publisher.mock_backend import MockPersonaBackend


def _agent_spec(name: str, **metadata) -> AgentSpec:
    return AgentSpec(
        name=name,
        instructions="write a book description",
        metadata={
            "tone": "playful",
            "reading_level": "early elementary",
            "max_length": 60,
            "extra_instructions": "",
            **metadata,
        },
    )


def test_mock_backend_satisfies_agent_backend_protocol():
    backend = MockPersonaBackend()
    assert isinstance(backend, AgentBackend)


async def test_run_produces_deterministic_output():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__children")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result1 = await backend.run(spec, "Title: Deep Echo\nSummary: ...", context)
    result2 = await backend.run(spec, "Title: Deep Echo\nSummary: ...", context)

    assert result1.status == "completed"
    assert result1.final_output == result2.final_output


async def test_run_output_differs_by_persona_traits():
    backend = MockPersonaBackend()
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
    input_text = "Title: Deep Echo\nSummary: A crew finds an ancient signal."

    children_spec = _agent_spec("book_writer__children", tone="playful", max_length=60)
    adult_spec = _agent_spec("book_writer__adult", tone="measured", max_length=200)

    children_result = await backend.run(children_spec, input_text, context)
    adult_result = await backend.run(adult_spec, input_text, context)

    assert children_result.final_output != adult_result.final_output
    assert len(children_result.final_output) <= 60
    assert len(adult_result.final_output) <= 200


async def test_run_respects_max_length_truncation():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__children", max_length=20)
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result = await backend.run(spec, "Title: Deep Echo\nSummary: " + ("x" * 500), context)
    assert len(result.final_output) <= 20


async def test_stream_yields_run_started_and_completed_events():
    from agent_app.runtime.streaming import StreamEventType

    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__adult")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    events = [event async for event in backend.stream(spec, "Title: Deep Echo", context)]
    assert events[0].type == StreamEventType.RUN_STARTED
    assert events[-1].type == StreamEventType.RUN_COMPLETED


async def test_resume_returns_completed_result():
    backend = MockPersonaBackend()
    spec = _agent_spec("book_writer__adult")
    context = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

    result = await backend.resume(spec, context)
    assert result.status == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_mock_backend.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.mock_backend'`)

- [ ] **Step 3: Write the mock backend and prompt template**

```python
# examples/book_publisher/book_publisher/mock_backend.py
"""Deterministic mock LLM backend — no network I/O, fully offline.

Implements the framework's AgentBackend Protocol (agent_app/runtime/backends.py)
directly. A real backend (e.g. an LMStudio OpenAI-compatible server) would read
`agent_spec.instructions` as its system prompt instead; this mock reads the same
persona traits from `agent_spec.metadata` so output stays deterministic for
tests and evals. Swapping to a real backend is a one-line change in
build_app.py's `backend=` argument — the rest of the pipeline is backend-agnostic.
"""

from __future__ import annotations

from typing import AsyncGenerator

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.streaming import StreamEvent, StreamEventType


class MockPersonaBackend:
    """Renders persona-flavored book descriptions with zero network calls."""

    def _render(self, agent_spec: AgentSpec, input: str) -> str:
        meta = agent_spec.metadata
        tone = meta.get("tone", "")
        reading_level = meta.get("reading_level", "")
        max_length = meta.get("max_length", 280)
        extra = meta.get("extra_instructions", "")

        book_text = " ".join(input.strip().split())
        rendered = f"[{reading_level} | {tone}] {book_text}"
        if extra:
            rendered = f"{rendered} ({extra})"
        return rendered[:max_length]

    async def run(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AppRunResult:
        text = self._render(agent_spec, input)
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=text,
            latency_ms=0,
        )

    async def stream(
        self,
        agent_spec: AgentSpec,
        input: str,
        context: RunContext,
        tools: list[object] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        text = self._render(agent_spec, input)
        yield StreamEvent(type=StreamEventType.RUN_STARTED, run_id=context.run_id)

        chunk_size = 16
        for i in range(0, len(text), chunk_size):
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                run_id=context.run_id,
                delta=text[i : i + chunk_size],
            )

        yield StreamEvent(
            type=StreamEventType.RUN_COMPLETED,
            run_id=context.run_id,
            data={"final_output": text},
        )

    async def resume(
        self,
        agent_spec: AgentSpec,
        context: RunContext,
        **kwargs: object,
    ) -> AppRunResult:
        return AppRunResult(
            run_id=context.run_id,
            status="completed",
            final_output=(
                f"Run '{context.run_id}' resumed. "
                "(MockPersonaBackend has no interruptible state to resume.)"
            ),
            latency_ms=0,
        )
```

```markdown
<!-- examples/book_publisher/prompts/book_writer.md -->
You are a book marketing copywriter. Write a short promotional description
of the book for the following audience.

Tone: {tone}
Reading level: {reading_level}
Maximum length: {max_length} characters
Extra instructions: {extra_instructions}

Base your description on the book brief provided as input. Keep it faithful
to the book's summary and key points; do not invent plot details that
contradict the brief.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_mock_backend.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/mock_backend.py \
        examples/book_publisher/prompts/book_writer.md \
        tests/unit/test_book_publisher_mock_backend.py
git commit -m "feat: add book_publisher deterministic mock LLM backend"
```

---

### Task 5: Publisher protocol + mock publisher

**Files:**
- Create: `examples/book_publisher/book_publisher/publishers/__init__.py`
- Create: `examples/book_publisher/book_publisher/publishers/base.py`
- Create: `examples/book_publisher/book_publisher/publishers/mock.py`
- Test: `tests/unit/test_book_publisher_publishers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_publishers.py
"""Tests for book_publisher.publishers — Publisher protocol and MockPublisher."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.models import GeneratedContent, PlatformSpec
from book_publisher.publishers.base import Publisher
from book_publisher.publishers.mock import MockPublisher


def test_mock_publisher_satisfies_publisher_protocol():
    assert isinstance(MockPublisher(), Publisher)


async def test_publish_truncates_to_platform_max_length(tmp_path):
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")
    content = GeneratedContent(
        persona="adult", book_title="Deep Echo", text="x" * 100, run_id="r1", status="completed"
    )
    platform = PlatformSpec(name="csdn", display_name="CSDN", max_length=10)

    receipt = await publisher.publish(content=content, platform=platform)

    assert receipt.status == "published"
    assert receipt.platform == "csdn"
    assert receipt.persona == "adult"
    assert len(receipt.formatted_preview.split("\n\n")[0]) == 10
    assert receipt.published_at is not None


async def test_publish_appends_hashtags_from_tags(tmp_path):
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")
    content = GeneratedContent(
        persona="adult",
        book_title="Deep Echo",
        text="A great book.",
        run_id="r1",
        status="completed",
        tags=["scifi", "mystery"],
    )
    platform = PlatformSpec(name="wechat_mp", display_name="WeChat", hashtag_style="#{tag}")

    receipt = await publisher.publish(content=content, platform=platform)

    assert "#scifi" in receipt.formatted_preview
    assert "#mystery" in receipt.formatted_preview


async def test_publish_writes_one_jsonl_record(tmp_path):
    log_path = tmp_path / "log.jsonl"
    publisher = MockPublisher(log_path=log_path)
    content = GeneratedContent(
        persona="adult", book_title="Deep Echo", text="A great book.", run_id="r1", status="completed"
    )
    platform = PlatformSpec(name="csdn", display_name="CSDN")

    await publisher.publish(content=content, platform=platform)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["platform"] == "csdn"
    assert record["persona"] == "adult"
    assert record["book_title"] == "Deep Echo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_publishers.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.publishers'`)

- [ ] **Step 3: Write the Publisher protocol and MockPublisher**

```python
# examples/book_publisher/book_publisher/publishers/__init__.py
"""Publisher protocol and mock implementation."""
```

```python
# examples/book_publisher/book_publisher/publishers/base.py
"""Publisher protocol — pluggable downstream platform adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from book_publisher.models import GeneratedContent, PlatformSpec, PublishReceipt


@runtime_checkable
class Publisher(Protocol):
    """Adapter that pushes generated content to one downstream platform."""

    async def publish(
        self, *, content: GeneratedContent, platform: PlatformSpec
    ) -> PublishReceipt: ...
```

```python
# examples/book_publisher/book_publisher/publishers/mock.py
"""Mock publisher — simulates posting via a local JSONL log, no real API calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from book_publisher.models import GeneratedContent, PlatformSpec, PublishReceipt

_DEFAULT_LOG_PATH = ".agent_app/book_publisher_log.jsonl"


class MockPublisher:
    """Formats content per-platform and appends a JSON record simulating a post."""

    def __init__(self, log_path: str | Path = _DEFAULT_LOG_PATH) -> None:
        self._log_path = Path(log_path)

    async def publish(
        self, *, content: GeneratedContent, platform: PlatformSpec
    ) -> PublishReceipt:
        text = content.text
        if platform.max_length is not None:
            text = text[: platform.max_length]

        preview = text
        if platform.hashtag_style and content.tags:
            hashtags = " ".join(
                platform.hashtag_style.format(tag=tag) for tag in content.tags
            )
            preview = f"{text}\n\n{hashtags}"

        published_at = datetime.now(timezone.utc).isoformat()

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "platform": platform.name,
            "persona": content.persona,
            "book_title": content.book_title,
            "text": preview,
            "published_at": published_at,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return PublishReceipt(
            platform=platform.name,
            persona=content.persona,
            status="published",
            published_at=published_at,
            formatted_preview=preview,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_publishers.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/publishers/ \
        tests/unit/test_book_publisher_publishers.py
git commit -m "feat: add book_publisher Publisher protocol and mock publisher"
```

---

### Task 6: Governed publish tools

**Files:**
- Create: `examples/book_publisher/book_publisher/tools.py`
- Test: `tests/unit/test_book_publisher_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_tools.py
"""Tests for book_publisher.tools.build_publish_tools."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.mock import MockPublisher
from book_publisher.tools import build_publish_tools


def test_builds_one_tool_per_platform(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = build_publish_tools(platforms, publisher)

    names = {spec.name for spec, _fn in tools}
    assert names == {"publish_wechat_mp", "publish_zhihu", "publish_juejin", "publish_csdn"}


def test_tool_spec_risk_level_and_approval_match_platform(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: spec for spec, _fn in build_publish_tools(platforms, publisher)}

    assert tools["publish_wechat_mp"].risk_level == "high"
    assert tools["publish_wechat_mp"].requires_approval is True
    assert tools["publish_csdn"].risk_level == "low"
    assert tools["publish_csdn"].requires_approval is False


async def test_tool_fn_calls_publisher_and_returns_receipt_dict(tmp_path):
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: fn for spec, fn in build_publish_tools(platforms, publisher)}
    fn = tools["publish_csdn"]

    result = await fn(content="A great book.", persona="adult", book_title="Deep Echo", tags=["scifi"])

    assert result["platform"] == "csdn"
    assert result["persona"] == "adult"
    assert result["status"] == "published"


async def test_each_tool_fn_targets_its_own_platform_not_the_last_one(tmp_path):
    """Regression test for the classic late-binding-closure-in-a-loop bug."""
    platforms = PlatformRegistry.load(_EXAMPLE_DIR / "platforms")
    publisher = MockPublisher(log_path=tmp_path / "log.jsonl")

    tools = {spec.name: fn for spec, fn in build_publish_tools(platforms, publisher)}

    csdn_result = await tools["publish_csdn"](content="c", persona="adult", book_title="t")
    zhihu_result = await tools["publish_zhihu"](content="c", persona="adult", book_title="t")

    assert csdn_result["platform"] == "csdn"
    assert zhihu_result["platform"] == "zhihu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_tools.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.tools'`)

- [ ] **Step 3: Write build_publish_tools**

```python
# examples/book_publisher/book_publisher/tools.py
"""Builds one governed ToolSpec + callable pair per registered platform.

Reuses the framework's existing ToolExecutor governance pipeline as-is: risk
level and approval requirement come straight from each platform's YAML, no
new mechanism needed.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_app.core.tool_spec import ToolSpec

from book_publisher.models import GeneratedContent, PlatformSpec
from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.base import Publisher


def build_publish_tools(
    platform_registry: PlatformRegistry,
    publisher: Publisher,
) -> list[tuple[ToolSpec, Callable[..., Any]]]:
    tools: list[tuple[ToolSpec, Callable[..., Any]]] = []

    for platform in platform_registry.all():
        spec = ToolSpec(
            name=f"publish_{platform.name}",
            description=f"Publish content to {platform.display_name}",
            risk_level=platform.risk_level,
            requires_approval=platform.requires_approval,
        )

        async def _fn(
            content: str,
            persona: str,
            book_title: str,
            tags: list[str] | None = None,
            _platform: PlatformSpec = platform,
        ) -> dict:
            generated = GeneratedContent(
                persona=persona,
                book_title=book_title,
                text=content,
                run_id="",
                status="completed",
                tags=tags or [],
            )
            receipt = await publisher.publish(content=generated, platform=_platform)
            return receipt.model_dump()

        tools.append((spec, _fn))

    return tools
```

Note the `_platform: PlatformSpec = platform` default-argument binding inside the loop — without it, every closure would capture the same (final) loop variable, and every tool would silently publish to the last platform in the list. The test above (`test_each_tool_fn_targets_its_own_platform_not_the_last_one`) guards against regressing this.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_tools.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/tools.py \
        tests/unit/test_book_publisher_tools.py
git commit -m "feat: add book_publisher governed publish tools"
```

---

### Task 7: build_app — assemble AgentApp + ToolExecutor

**Files:**
- Create: `examples/book_publisher/book_publisher/build_app.py`
- Test: `tests/unit/test_book_publisher_build_app.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_build_app.py
"""Tests for book_publisher.build_app.build_app."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.build_app import build_app


def test_build_app_registers_one_agent_per_persona(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    for persona in bp_app.personas.all():
        agent_name = f"book_writer__{persona.name}"
        spec = bp_app.app.agent_registry.get(agent_name)
        assert spec.name == agent_name
        assert spec.metadata["tone"] == persona.tone


def test_build_app_registers_one_tool_per_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    for platform in bp_app.platforms.all():
        tool_name = f"publish_{platform.name}"
        spec = bp_app.app.tool_registry.get_spec(tool_name)
        assert spec.risk_level == platform.risk_level
        assert spec.requires_approval == platform.requires_approval


def test_build_app_registers_book_generation_workflow(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")

    wf = bp_app.app.workflow_registry.get("book_generation")
    node_ids = {node["id"] for node in wf.config["dag"]["nodes"]}
    expected = {f"write_{p.name}" for p in bp_app.personas.all()}
    assert node_ids == expected


def test_build_app_tool_executor_shares_the_apps_approval_store(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    assert bp_app.tool_executor.approval_store is bp_app.app.approval_store


def test_build_app_uses_isolated_registries_not_the_global_default(tmp_path):
    """AgentApp() with no registry= kwarg falls back to the process-global
    default ToolRegistry; build_app() must pass its own bundle to avoid
    cross-test / cross-app tool-name collisions."""
    first = build_app(log_path=tmp_path / "log1.jsonl")
    second = build_app(log_path=tmp_path / "log2.jsonl")
    assert first.app.tool_registry is not second.app.tool_registry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_build_app.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.build_app'`)

- [ ] **Step 3: Write build_app.py**

```python
# examples/book_publisher/book_publisher/build_app.py
"""Assembles the book_publisher example's AgentApp and governance components.

Does not go through agent_app.config.loader.build_app() — that loader always
defaults to DryRunBackend with no supported hook to swap it post-construction
without reaching into AgentApp._runner internals. Instead this constructs
AgentApp directly, with every registry and governance store explicit.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_app import AgentApp, AgentSpec, Workflow
from agent_app.governance.approval import InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.permission import DefaultPermissionChecker
from agent_app.observability.collector import InMemoryTraceCollector
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.tool_executor import ToolExecutor

from book_publisher.mock_backend import MockPersonaBackend
from book_publisher.personas import PersonaRegistry
from book_publisher.platforms import PlatformRegistry
from book_publisher.publishers.mock import MockPublisher
from book_publisher.tools import build_publish_tools

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent


class BookPublisherApp:
    """Bundle returned by build_app(): the AgentApp plus everything main.py needs."""

    def __init__(
        self,
        app: AgentApp,
        tool_executor: ToolExecutor,
        personas: PersonaRegistry,
        platforms: PlatformRegistry,
    ) -> None:
        self.app = app
        self.tool_executor = tool_executor
        self.personas = personas
        self.platforms = platforms


def build_app(
    personas_dir: str | Path | None = None,
    platforms_dir: str | Path | None = None,
    prompt_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> BookPublisherApp:
    personas_dir = Path(personas_dir) if personas_dir else _EXAMPLE_DIR / "personas"
    platforms_dir = Path(platforms_dir) if platforms_dir else _EXAMPLE_DIR / "platforms"
    prompt_path = Path(prompt_path) if prompt_path else _EXAMPLE_DIR / "prompts" / "book_writer.md"

    personas = PersonaRegistry.load(personas_dir)
    platforms = PlatformRegistry.load(platforms_dir)

    # Explicit, isolated registries — NOT the process-global default
    # ToolRegistry that AgentApp() falls back to when registry= is omitted.
    registry = SimpleNamespace(
        agent_registry=AgentRegistry(),
        tool_registry=ToolRegistry(),
        workflow_registry=WorkflowRegistry(),
    )

    approval_store = InMemoryApprovalStore()
    audit_logger = InMemoryAuditLogger()
    trace_collector = InMemoryTraceCollector()

    app = AgentApp(
        registry=registry,
        backend=MockPersonaBackend(),
        approval_store=approval_store,
        audit_logger=audit_logger,
        trace_collector=trace_collector,
    )

    template = prompt_path.read_text(encoding="utf-8")
    dag_nodes = []
    for persona in personas.all():
        agent_name = f"book_writer__{persona.name}"
        instructions = template.format(
            tone=persona.tone,
            reading_level=persona.reading_level,
            max_length=persona.max_length,
            extra_instructions=persona.extra_instructions,
        )
        app.register_agent(
            AgentSpec(
                name=agent_name,
                description=f"Writes book descriptions for the {persona.display_name} audience",
                instructions=instructions,
                metadata={
                    "persona_name": persona.name,
                    "tone": persona.tone,
                    "reading_level": persona.reading_level,
                    "max_length": persona.max_length,
                    "extra_instructions": persona.extra_instructions,
                },
            )
        )
        dag_nodes.append({"id": f"write_{persona.name}", "type": "agent", "ref": agent_name})

    wf = Workflow.dag(name="book_generation", nodes=dag_nodes, execution_mode="parallel")
    app.register_workflow(wf)

    publisher = MockPublisher(log_path=log_path) if log_path is not None else MockPublisher()
    for spec, fn in build_publish_tools(platforms, publisher):
        app.register_tool(spec, fn=fn)

    tool_executor = ToolExecutor(
        tool_registry=app.tool_registry,
        approval_store=approval_store,
        permission_checker=DefaultPermissionChecker(),
        audit_logger=audit_logger,
        trace_collector=trace_collector,
    )

    return BookPublisherApp(
        app=app, tool_executor=tool_executor, personas=personas, platforms=platforms
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_build_app.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/build_app.py \
        tests/unit/test_book_publisher_build_app.py
git commit -m "feat: add book_publisher build_app wiring AgentApp + ToolExecutor"
```

---

### Task 8: Pipeline — generation + governed publishing + approval completion

**Files:**
- Create: `examples/book_publisher/book_publisher/pipeline.py`
- Test: `tests/unit/test_book_publisher_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_book_publisher_pipeline.py
"""Integration tests for book_publisher.pipeline — generation and governed publishing."""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "book_publisher"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from book_publisher.build_app import build_app
from book_publisher.models import BookInput
from book_publisher.pipeline import complete_approved_publish, generate_content, publish_all


def _book() -> BookInput:
    return BookInput(
        title="Deep Echo",
        summary="A crew finds an ancient signal beneath the sea.",
        key_points=["found footage", "twist ending"],
        tags=["scifi", "mystery"],
    )


async def test_generate_content_produces_one_variant_per_persona(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()

    generated = await generate_content(bp_app.app, book, bp_app.personas)

    persona_names = {p.name for p in bp_app.personas.all()}
    assert set(generated.keys()) == persona_names
    for content in generated.values():
        assert content.book_title == "Deep Echo"
        assert content.status == "completed"
        assert content.text


async def test_publish_all_auto_publishes_low_risk_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    csdn_receipts = [r for r in report.receipts if r.platform == "csdn"]
    assert csdn_receipts
    assert all(r.status == "published" for r in csdn_receipts)


async def test_publish_all_interrupts_high_risk_platform(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    wechat_receipts = [r for r in report.receipts if r.platform == "wechat_mp"]
    assert wechat_receipts
    assert all(r.status == "approval_required" for r in wechat_receipts)
    assert all(r.approval_id is not None for r in wechat_receipts)


async def test_children_persona_only_publishes_to_csdn(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)

    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    children_platforms = {r.platform for r in report.receipts if r.persona == "children"}
    assert children_platforms == {"csdn"}


async def test_approve_then_complete_approved_publish_marks_it_published(tmp_path):
    bp_app = build_app(log_path=tmp_path / "log.jsonl")
    book = _book()
    generated = await generate_content(bp_app.app, book, bp_app.personas)
    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )

    pending = next(r for r in report.receipts if r.status == "approval_required")

    await bp_app.app.approve(pending.approval_id, approved_by="demo-editor")
    completed = await complete_approved_publish(bp_app.app, book, generated, pending)

    assert completed.status == "published"
    assert completed.platform == pending.platform
    assert completed.persona == pending.persona
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_pipeline.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'book_publisher.pipeline'`)

- [ ] **Step 3: Write pipeline.py**

```python
# examples/book_publisher/book_publisher/pipeline.py
"""Orchestrates content generation and governed publishing.

Content generation runs through the framework's DAG workflow engine
(tool-free agent nodes only). Publishing deliberately does NOT use DAG tool
nodes — see docs/superpowers/specs/2026-07-01-book-publisher-design.md for
why DagExecutor's internal _NoOpApprovalStore makes DAG-tool-node approvals
unresolvable — and instead drives a directly-constructed, real ToolExecutor
(built once in build_app.py, shared with the app's real approval_store).
"""

from __future__ import annotations

import uuid

from agent_app.core.context import RunContext

from book_publisher.models import (
    BookInput,
    GeneratedContent,
    PersonaSpec,
    PlatformSpec,
    PublishingReport,
    PublishReceipt,
)
from book_publisher.personas import PersonaRegistry
from book_publisher.platforms import PlatformRegistry


async def generate_content(
    app, book: BookInput, personas: PersonaRegistry
) -> dict[str, GeneratedContent]:
    """Runs the book_generation DAG and collects one GeneratedContent per persona."""
    result = await app.run(workflow="book_generation", input=book.to_prompt_text())

    generated: dict[str, GeneratedContent] = {}
    for node_result in result.node_results:
        node_id = node_result["node_id"]
        if not node_id.startswith("write_") or node_result["status"] != "completed":
            continue
        persona_name = node_id.removeprefix("write_")
        generated[persona_name] = GeneratedContent(
            persona=persona_name,
            book_title=book.title,
            text=node_result["output"],
            run_id=result.run_id,
            status=node_result["status"],
            tags=book.tags,
        )
    return generated


def _target_platforms(
    persona: PersonaSpec, platforms: PlatformRegistry
) -> list[PlatformSpec]:
    if persona.target_platforms is None:
        return platforms.all()
    return [platforms.get(name) for name in persona.target_platforms]


async def publish_all(
    app,
    tool_executor,
    book: BookInput,
    personas: PersonaRegistry,
    platforms: PlatformRegistry,
    generated: dict[str, GeneratedContent],
) -> PublishingReport:
    """Drives a real, governed ToolExecutor.execute() per persona x platform pair."""
    receipts: list[PublishReceipt] = []

    for persona in personas.all():
        content = generated.get(persona.name)
        if content is None:
            continue
        for platform in _target_platforms(persona, platforms):
            context = RunContext(
                run_id=str(uuid.uuid4()), user_id="demo-editor", tenant_id="default"
            )
            result = await tool_executor.execute(
                tool_name=f"publish_{platform.name}",
                arguments={
                    "content": content.text,
                    "persona": content.persona,
                    "book_title": content.book_title,
                    "tags": content.tags,
                },
                context=context,
            )

            if result.status == "completed":
                receipts.append(PublishReceipt(**result.output))
            elif result.status == "interrupted":
                receipts.append(
                    PublishReceipt(
                        platform=platform.name,
                        persona=persona.name,
                        status="approval_required",
                        approval_id=result.approval_request.approval_id,
                    )
                )
            else:
                receipts.append(
                    PublishReceipt(
                        platform=platform.name,
                        persona=persona.name,
                        status="failed",
                    )
                )

    return PublishingReport(book=book, generated=list(generated.values()), receipts=receipts)


async def complete_approved_publish(
    app,
    book: BookInput,
    generated: dict[str, GeneratedContent],
    receipt: PublishReceipt,
) -> PublishReceipt:
    """Completes a publish call after app.approve() has granted its approval.

    The framework has no public "resume this exact governed tool call" API
    outside of the OpenAI-native-SDK HITL marker path (reserved for that
    integration, not usable here). tool_registry.get_fn() is the legitimate,
    publicly-exposed escape hatch: it returns the exact same callable
    ToolExecutor would have invoked had the approval gate not fired.
    """
    content = generated[receipt.persona]
    fn = app.tool_registry.get_fn(f"publish_{receipt.platform}")
    result_dict = await fn(
        content=content.text,
        persona=content.persona,
        book_title=content.book_title,
        tags=content.tags,
    )
    return PublishReceipt(**result_dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_pipeline.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add examples/book_publisher/book_publisher/pipeline.py \
        tests/unit/test_book_publisher_pipeline.py
git commit -m "feat: add book_publisher pipeline — generation + governed publishing"
```

---

### Task 9: Full test suite check + main.py demo script

**Files:**
- Create: `examples/book_publisher/main.py`

- [ ] **Step 1: Run the full book_publisher test suite together**

Run: `.venv/bin/python -m pytest tests/unit/test_book_publisher_*.py -v`
Expected: PASS (all tests from Tasks 1-8 pass together, no cross-test interference)

- [ ] **Step 2: Write main.py**

```python
# examples/book_publisher/main.py
"""Book publisher example — multi-persona, multi-platform publishing demo.

Generates audience-tailored book descriptions via a parallel DAG of mock-LLM
agent nodes, then publishes each variant to a set of mock platforms through
the framework's real governance pipeline: low-risk platforms auto-publish,
high-risk platforms pause for human approval via app.approve().
"""

import asyncio
import sys
from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_DIR))

from book_publisher.build_app import build_app
from book_publisher.models import BookInput
from book_publisher.pipeline import complete_approved_publish, generate_content, publish_all


async def main() -> None:
    book = BookInput.from_yaml(_EXAMPLES_DIR / "data" / "sample_book.yaml")
    bp_app = build_app()

    print(f"=== Book Publisher: {book.title} ===\n")

    print("-- Generating persona variants --")
    generated = await generate_content(bp_app.app, book, bp_app.personas)
    for persona_name, content in generated.items():
        print(f"[{persona_name}] {content.text}\n")

    print("-- Publishing --")
    report = await publish_all(
        bp_app.app, bp_app.tool_executor, book, bp_app.personas, bp_app.platforms, generated
    )
    print(report.summary())

    pending = [r for r in report.receipts if r.status == "approval_required"]
    if pending:
        print(f"\n-- Approving {len(pending)} pending publish(es) --")
        for receipt in pending:
            print(f"Approving publish to '{receipt.platform}' for persona '{receipt.persona}'...")
            await bp_app.app.approve(receipt.approval_id, approved_by="demo-editor")
            completed = await complete_approved_publish(bp_app.app, book, generated, receipt)
            receipt.status = completed.status
            receipt.published_at = completed.published_at
            receipt.formatted_preview = completed.formatted_preview

    print("\n=== Final report ===")
    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run the demo end-to-end**

Run: `cd "examples/book_publisher" && "../../.venv/bin/python" main.py`
Expected: prints one generated description per persona, then a report where `wechat_mp`/`juejin` receipts start as `approval_required` and end as `published` after the approve step, and `csdn`/`zhihu` receipts are `published` throughout. Exit code 0.

- [ ] **Step 4: Commit**

```bash
git add examples/book_publisher/main.py
git commit -m "feat: add book_publisher main.py end-to-end demo script"
```

---

### Task 10: agentapp.yaml (documentation manifest)

**Files:**
- Create: `examples/book_publisher/agentapp.yaml`

- [ ] **Step 1: Write agentapp.yaml**

```yaml
# examples/book_publisher/agentapp.yaml
#
# This file documents intent (governance/tracing store types) for a human
# reader. book_publisher/build_app.py does NOT call
# agent_app.config.loader.build_app() against this file — that loader always
# defaults to DryRunBackend with no supported hook to swap it post-construction
# without reaching into AgentApp._runner internals. Instead build_app.py
# constructs AgentApp directly with the equivalent wiring shown below.
# Swapping to a real LLM backend (e.g. LMStudio's OpenAI-compatible /v1
# server) later is a one-line change to the `backend=` argument in
# build_app.py — everything else is backend-agnostic.

app:
  name: book-publisher
  environment: dev

governance:
  approvals:
    type: memory
  audit:
    type: memory

observability:
  tracing:
    type: memory
```

- [ ] **Step 2: Commit**

```bash
git add examples/book_publisher/agentapp.yaml
git commit -m "docs: add book_publisher agentapp.yaml intent manifest"
```

---

### Task 11: Evals

**Files:**
- Create: `examples/book_publisher/evals/book_publisher.yaml`

- [ ] **Step 1: Check the existing evals format**

Run: `cat "examples/research_assistant/evals/research_assistant.yaml"`

Read the output to confirm the exact top-level keys (`name`, `cases`, assertion shape) this repo's eval runner expects, and match that shape exactly in Step 2 below — do not guess the schema.

- [ ] **Step 2: Write book_publisher.yaml**

```yaml
# examples/book_publisher/evals/book_publisher.yaml
name: book_publisher
description: >
  Generates persona-tailored book descriptions and publishes them through
  the governed tool pipeline; verifies low-risk auto-publish, high-risk
  approval gating, and per-persona platform targeting.
cases:
  - name: generates_one_variant_per_persona
    input:
      workflow: book_generation
      book: examples/book_publisher/data/sample_book.yaml
    assert:
      - type: node_count
        node_prefix: "write_"
        equals: 4
      - type: all_nodes_completed
        node_prefix: "write_"

  - name: low_risk_platform_auto_publishes
    input:
      platform: csdn
    assert:
      - type: publish_status
        equals: published

  - name: high_risk_platform_requires_approval_then_publishes
    input:
      platform: wechat_mp
    assert:
      - type: publish_status
        equals: approval_required
      - type: after_approve_and_complete
        equals: published

  - name: new_persona_needs_zero_pipeline_code_changes
    input:
      personas_dir: tests/fixtures/book_publisher/extra_persona
    assert:
      - type: node_count
        node_prefix: "write_"
        equals: 1
```

Note: this mirrors the case list from the spec's "Evals" section — full runnable wiring against this repo's eval harness follows whatever assertion vocabulary Step 1 revealed; adjust the `assert` block's `type` keys to match exactly if the harness uses different names than shown here.

- [ ] **Step 3: Commit**

```bash
git add examples/book_publisher/evals/book_publisher.yaml
git commit -m "docs: add book_publisher eval cases"
```

---

### Task 12: README

**Files:**
- Create: `examples/book_publisher/README.md`

- [ ] **Step 1: Write README.md**

```markdown
# Book Publisher

Multi-persona, multi-platform content pipeline built on the Agent App
Framework — inspired by [ArtiPub](https://github.com/artipub/artipub)'s
"one article, many platforms" workflow.

Given a book brief (title/summary/key points/tags), this example:

1. Generates an audience-tailored description for each persona
   (children/adult/student/teacher by default) in parallel, via a DAG
   workflow of tool-free agent nodes.
2. Publishes each variant to a set of mock platforms
   (wechat_mp/zhihu/juejin/csdn by default) through the framework's real
   governance pipeline — low-risk platforms auto-publish, high-risk
   platforms pause for human approval.

## Run it

```bash
cd examples/book_publisher
../../.venv/bin/python main.py
```

## Run the tests

```bash
.venv/bin/python -m pytest tests/unit/test_book_publisher_*.py -v
```

## Add a new persona

Drop a YAML file into `personas/`:

```yaml
name: librarian
display_name: "Librarian"
tone: "curatorial, precise"
reading_level: "professional"
max_length: 400
extra_instructions: "mention comparable titles when relevant"
target_platforms: [csdn, zhihu]   # omit for "all platforms"
```

No code changes needed — `PersonaRegistry.load()` scans the directory.

## Add a new platform

Drop a YAML file into `platforms/`:

```yaml
name: toutiao
display_name: "Toutiao"
max_length: 2000
format: markdown
hashtag_style: "#{tag}"
risk_level: medium
requires_approval: false
```

It auto-registers as a governed tool (`publish_toutiao`) the next time
`build_app()` runs — no code changes needed. `risk_level: high`/`critical`
or `requires_approval: true` gates it behind `app.approve()`.

## Swapping in a real LLM backend

Content generation runs through `MockPersonaBackend`
(`book_publisher/mock_backend.py`) — fully deterministic, no network calls.
To use a real local LLM (e.g. LMStudio's OpenAI-compatible `/v1` server),
replace the `backend=MockPersonaBackend()` argument in
`book_publisher/build_app.py` with `OpenAIAgentsBackend` pointed at the
LMStudio endpoint. The rest of the pipeline (personas, platforms, tools,
publishing) is backend-agnostic — it only depends on the `AgentBackend`
Protocol.

## Why publishing isn't a DAG tool node

An earlier design routed publishing through DAG `tool` nodes
(`type=tool, requires_approval=True`). During implementation-plan
verification we found `DagExecutor._execute_tool_node` always executes
against an internal, throwaway `_NoOpApprovalStore` rather than the app's
real `approval_store` — so an approval created that way can never be
resolved via `app.approve()`. Publishing instead drives a
directly-constructed, real `ToolExecutor` from `book_publisher/pipeline.py`;
the DAG is used only for the parallel, tool-free content-generation stage.
See `docs/superpowers/specs/2026-07-01-book-publisher-design.md` for the
full writeup.
```

- [ ] **Step 2: Commit**

```bash
git add examples/book_publisher/README.md
git commit -m "docs: add book_publisher README"
```

---

## Self-Review

**Spec coverage:**
- Data models (`BookInput`, `PersonaSpec`, `PlatformSpec`, `GeneratedContent`, `PublishReceipt`, `PublishingReport`) → Task 1.
- Persona registry, YAML-declarative, extensible → Task 2.
- Platform registry, YAML-declarative, extensible → Task 3.
- Mock LLM backend, `AgentBackend` Protocol, deterministic → Task 4.
- `Publisher` protocol + mock adapter → Task 5.
- Governed publish tools reusing `ToolExecutor` governance → Task 6.
- `AgentApp` assembly (explicit registries, real governance stores, DAG registration) → Task 7.
- Content-generation DAG + governed publishing + approval-resume flow → Task 8.
- End-to-end demo script → Task 9.
- `agentapp.yaml` intent manifest → Task 10.
- Evals → Task 11.
- README (run instructions, persona/platform extension, LMStudio swap note, DAG-tool-node correction note) → Task 12.
- Corrected architecture (no DAG tool nodes for publishing) is implemented in Tasks 7-8 and documented in the README (Task 12) and inline code comments (`pipeline.py`).

**Placeholder scan:** No TBD/TODO markers; every step has literal, runnable code except Task 11 Step 2, which is explicitly flagged as needing a Step-1 schema check before being trusted verbatim (the repo's eval-file schema wasn't independently verified during planning — this is called out, not hidden).

**Type/signature consistency:** `BookPublisherApp.{app, tool_executor, personas, platforms}` (Task 7) is the shape every later task/task-test accesses (Task 8 test, Task 9 main.py). `GeneratedContent.tags` (added in Task 1) is consumed by `MockPublisher.publish` (Task 5) and populated in `pipeline.generate_content` (Task 8). Tool names (`publish_{platform.name}`) are consistent across Task 6 (`tools.py`), Task 7 (`build_app.py` registration — implicit via `tools.py`), and Task 8 (`pipeline.py` — both `tool_executor.execute(tool_name=...)` and `tool_registry.get_fn(...)`). `PublishReceipt` fields are identical between the model (Task 1), what `tools.py`'s tool function returns via `.model_dump()` (Task 6), and what `pipeline.publish_all`/`complete_approved_publish` reconstruct via `PublishReceipt(**...)` (Task 8).
