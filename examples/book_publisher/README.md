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
