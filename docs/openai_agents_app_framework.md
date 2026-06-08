# 基于 OpenAI Agents SDK 的大模型应用封装框架可实施方案书

> 方案版本：v0.1  
> 适用阶段：从 0 到 1 搭建企业级 Agent 应用开发框架  
> 基础依赖：OpenAI Agents SDK for Python / openai-agents-python  
> 目标读者：框架负责人、AI 应用架构师、后端工程师、平台工程师、Agent 应用开发者

---

## 1. 项目背景

随着大模型应用从“单轮对话”逐步演进到“可调用工具、可执行任务、可协作、可审计、可部署”的 Agent 应用，单纯使用模型 API 已经无法满足复杂业务开发需求。OpenAI Agents SDK 已经提供了较轻量且生产可用的底层能力，包括 Agent、Runner、工具调用、多 Agent 协作、handoff、sessions、guardrails、streaming、tracing、MCP、人审等能力。

但在真实业务团队中，直接使用底层 SDK 仍然会遇到以下问题：

1. 不同业务团队重复编写 Agent、工具注册、运行入口、鉴权、日志、审批、会话管理代码。
2. 缺少统一的 Agent 应用结构和开发规范。
3. 工具调用缺少风险分级、权限校验、审计和人审流程。
4. Prompt、Agent 配置、工具、工作流缺乏版本管理。
5. 难以在开发、测试、灰度、生产之间保持一致的运行行为。
6. 缺少统一的成本、延迟、成功率、工具调用、审批、失败回放等观测能力。
7. 复杂业务场景需要同时支持“模型自主决策”和“确定性业务流程”，但现有项目容易把所有逻辑都塞给 Agent。

因此，本方案建议在 OpenAI Agents SDK 之上构建一层面向业务应用开发的封装框架。该框架不是替代 Agents SDK，而是围绕其原生能力提供应用级工程化封装，使团队能够更快、更安全、更可控地构建大模型应用。

---

## 2. 项目定位

本项目定位为：

> 一个基于 OpenAI Agents SDK 的大模型应用开发框架，提供声明式 Agent 配置、工具注册与治理、工作流编排、会话与上下文管理、安全防护、人审、可观测性、评估、部署适配等能力，同时保留对底层 SDK 的扩展访问能力。

框架应重点解决“如何稳定开发和上线 Agent 应用”，而不是重新实现底层 Agent 执行循环。

---

## 3. 建设目标

### 3.1 总体目标

构建一套可复用、可扩展、可治理、可部署的大模型应用封装框架，使开发者可以用较少代码完成从 Agent 定义、工具接入、工作流编排到上线观测的完整流程。

### 3.2 具体目标

| 目标 | 说明 |
|---|---|
| 降低开发门槛 | 通过 AgentSpec、ToolRegistry、Workflow 等抽象减少重复代码 |
| 保持扩展性 | 不屏蔽 OpenAI Agents SDK 原生能力，保留 escape hatch |
| 支持多场景 | 支持客服、知识库问答、数据分析、自动报告、业务流程自动化等场景 |
| 工具治理 | 支持工具命名空间、权限、风险等级、审批、超时、重试、审计 |
| 状态管理 | 支持 session、上下文、短期记忆、长期记忆、业务状态 |
| 多 Agent 编排 | 支持 handoff、agents-as-tools、确定性 DAG 工作流 |
| 生产可观测 | 支持 tracing、日志、指标、成本、回放、错误诊断 |
| 质量评估 | 支持离线 eval、回归测试、工具调用断言、输出质量评估 |
| 易部署 | 支持 FastAPI、Worker、CLI、队列任务、容器化部署 |

---

## 4. 设计原则

### 4.1 薄封装原则

框架应尽量做“应用级封装”，而不是重新设计一套与 OpenAI Agents SDK 冲突的 Agent 系统。底层仍应使用 SDK 的 Agent、Runner、Tools、Sessions、Guardrails、Tracing 等能力。

### 4.2 Python-first 原则

复杂业务逻辑应优先允许通过 Python 表达，而不是强制 YAML / JSON 化。配置文件适合描述稳定结构，Python 代码适合表达复杂逻辑。

### 4.3 声明式 + 代码式并存

对普通业务开发者，提供声明式配置：

```yaml
agents:
  support:
    instructions: ./prompts/support.md
    tools:
      - order.query
      - ticket.create
```

对高级开发者，允许使用 Python 直接构建：

```python
support_agent = agent(
    name="support",
    instructions="你是客服助手",
    tools=["order.query", "ticket.create"],
)
```

### 4.4 可逃逸原则

任何高级场景都应允许开发者获取底层原生 SDK 对象：

```python
native_agent = app.get_native_agent("support")
```

框架不能成为能力天花板。

### 4.5 治理内建原则

工具调用、审批、权限、审计、风险分级、成本统计不应作为后期补丁，而应从框架第一版开始纳入设计。

### 4.6 确定性优先原则

对关键业务流程，不应完全依赖模型自由决策。退款、删除、发邮件、下单、数据库写入等高风险路径应通过确定性流程、审批和权限控制约束。

---

## 5. 非目标

第一阶段不建议做以下事情：

1. 不重新实现 OpenAI Agents SDK 的 agent loop。
2. 不自研模型调用层，除非后续有多模型路由需求。
3. 不第一版就构建完整可视化控制台。
4. 不把所有工作流都做成复杂低代码平台。
5. 不追求支持所有可能的 Agent 形态，而是先验证 2 到 3 个高价值业务模板。
6. 不把 session、memory、RAG、业务状态混成一个概念。

---

## 6. 总体架构

建议采用分层架构：

```text
┌──────────────────────────────────────────────┐
│ Application Layer                            │
│ Web UI / API / Worker / CLI / Chat Interface │
├──────────────────────────────────────────────┤
│ Agent App Framework                          │
│ AppRunner / Workflow / Registry / Policy     │
├──────────────────────────────────────────────┤
│ Agent Composition Layer                      │
│ AgentSpec / ToolSpec / Handoff / Skill       │
├──────────────────────────────────────────────┤
│ Runtime Layer                                │
│ Session / Context / Memory / State / Stream  │
├──────────────────────────────────────────────┤
│ Governance Layer                             │
│ Guardrails / HITL / Permission / Audit       │
├──────────────────────────────────────────────┤
│ Observability Layer                          │
│ Trace / Metrics / Cost / Eval / Replay       │
├──────────────────────────────────────────────┤
│ Integration Layer                            │
│ Function Tool / MCP / RAG / DB / HTTP / MQ   │
├──────────────────────────────────────────────┤
│ OpenAI Agents SDK                            │
│ Agent / Runner / Tools / Sessions / Tracing  │
└──────────────────────────────────────────────┘
```

---

## 7. 核心模块设计

## 7.1 AgentSpec：Agent 声明与编译模块

### 7.1.1 职责

AgentSpec 用于描述一个 Agent 的核心属性，并将其编译为 OpenAI Agents SDK 的原生 Agent 对象。

### 7.1.2 核心字段

```python
from pydantic import BaseModel
from typing import Any, Optional

class AgentSpec(BaseModel):
    name: str
    description: Optional[str] = None
    model: Optional[str] = None
    instructions: str | dict
    tools: list[str] = []
    handoffs: list[str] = []
    guardrails: list[str] = []
    output_schema: Optional[type] = None
    model_settings: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    raw_agent_kwargs: dict[str, Any] = {}
```

### 7.1.3 设计要点

1. `instructions` 支持字符串、模板路径、动态函数。
2. `tools` 通过 ToolRegistry 解析。
3. `handoffs` 通过 AgentRegistry 解析。
4. `guardrails` 通过 PolicyRegistry 解析。
5. `raw_agent_kwargs` 用于传递底层 SDK 未被框架显式封装的新参数。
6. 支持 Agent 版本，例如 `support:v1`、`support:v2`。

### 7.1.4 示例

```python
support_agent = AgentSpec(
    name="support",
    description="客服助手",
    model="gpt-5.1",
    instructions="./prompts/support.md",
    tools=["order.query", "ticket.create", "refund.request"],
    guardrails=["pii_input_check", "safe_output_check"],
    metadata={
        "owner": "customer-success",
        "risk_level": "medium",
    },
)
```

---

## 7.2 ToolRegistry：工具注册与治理模块

### 7.2.1 职责

ToolRegistry 负责统一管理所有可被 Agent 调用的工具，包括 Python function tool、MCP 工具、托管工具、RAG 工具、HTTP API 工具、本地业务工具等。

### 7.2.2 工具元数据

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    namespace: str
    risk_level: str = "low"  # low / medium / high / critical
    requires_approval: bool = False
    permissions: list[str] = []
    timeout_seconds: int = 30
    retry: dict = {}
    audit_enabled: bool = True
    tags: list[str] = []
```

### 7.2.3 工具注册示例

```python
@tool(
    name="order.query",
    description="查询订单详情",
    namespace="order",
    risk_level="low",
    permissions=["order:read"],
)
async def query_order(order_id: str) -> dict:
    ...
```

```python
@tool(
    name="order.refund",
    description="发起订单退款",
    namespace="order",
    risk_level="high",
    requires_approval=True,
    permissions=["order:refund"],
)
async def refund_order(order_id: str, amount: float, reason: str) -> dict:
    ...
```

### 7.2.4 工具调用前置流程

工具真正执行前应经过以下流程：

```text
Agent 触发工具调用
  ↓
参数 schema 校验
  ↓
权限校验
  ↓
风险等级判断
  ↓
是否需要审批
  ↓
限流 / 熔断 / 超时设置
  ↓
执行工具
  ↓
记录审计日志
  ↓
返回工具结果
```

### 7.2.5 工具风险分级

| 风险等级 | 示例 | 策略 |
|---|---|---|
| low | 查询订单、查询知识库、读取公开数据 | 允许直接调用，记录日志 |
| medium | 创建工单、生成草稿、修改非关键字段 | 需要权限，可配置审批 |
| high | 退款、发送邮件、修改订单、写数据库 | 默认需要审批 |
| critical | 删除数据、支付、合同提交、批量变更 | 必须审批，可要求二次确认 |

---

## 7.3 Workflow：工作流编排模块

### 7.3.1 职责

Workflow 用于描述一个大模型应用的执行流程，支持多种编排方式。

### 7.3.2 三类编排模式

#### 模式一：单 Agent 模式

适合简单问答、单一任务助手。

```python
workflow = Workflow.single(agent="support")
```

#### 模式二：Handoff 路由模式

适合客服分诊、意图分流、专家 Agent 接管。

```python
workflow = Workflow.handoff(
    entry="triage",
    agents=["billing", "technical_support", "refund"],
)
```

#### 模式三：Orchestrator 模式

适合研究、报告、分析类任务，由主 Agent 调用其他 Agent 作为工具。

```python
workflow = Workflow.orchestrator(
    manager="research_manager",
    agents_as_tools=["web_researcher", "data_analyst", "writer", "reviewer"],
)
```

#### 模式四：确定性 DAG 模式

适合有强业务约束的流程。

```python
workflow = dag(
    step("classify_intent"),
    branch({
        "refund": step("check_policy") >> step("request_approval") >> step("refund"),
        "faq": step("retrieve_answer") >> step("respond"),
        "ticket": step("create_ticket") >> step("respond"),
    }),
)
```

### 7.3.3 推荐策略

1. 能用确定性流程表达的关键业务路径，不交给模型自由决定。
2. 用户意图识别、文本生成、信息总结、工具选择可由模型完成。
3. 涉及副作用的操作必须进入权限和审批流程。
4. 工作流层不应过厚，复杂流程可集成 Temporal、Prefect、Dagster、Celery 等系统。

---

## 7.4 AppRunner：统一运行入口

### 7.4.1 职责

AppRunner 是业务应用调用框架的统一入口，负责组装 Agent、Workflow、Session、Context、Policy、Tracing，并调用底层 Runner 执行。

### 7.4.2 标准接口

```python
result = await app.run(
    workflow="customer_support",
    input="我想申请退款",
    user_id="u_123",
    tenant_id="t_001",
    session_id="conv_456",
    stream=False,
    metadata={
        "channel": "web",
        "request_id": "req_789",
    },
)
```

### 7.4.3 运行流程

```text
接收请求
  ↓
解析 workflow / agent
  ↓
加载用户、租户、权限、运行配置
  ↓
加载 session
  ↓
构造 RunContext
  ↓
执行 input guardrails
  ↓
调用 OpenAI Agents SDK Runner
  ↓
处理工具调用、handoff、streaming、approval
  ↓
执行 output guardrails
  ↓
记录 trace、metrics、audit
  ↓
返回统一 RunResult
```

### 7.4.4 统一返回结构

```python
class AppRunResult(BaseModel):
    run_id: str
    status: str  # completed / interrupted / failed
    final_output: str | dict | None
    interruptions: list[dict] = []
    tool_calls: list[dict] = []
    handoffs: list[dict] = []
    usage: dict = {}
    cost: dict = {}
    latency_ms: int
    trace_id: str | None = None
    error: dict | None = None
```

---

## 7.5 Context、Session 与 Memory 模块

### 7.5.1 概念区分

| 概念 | 生命周期 | 用途 |
|---|---|---|
| Context | 单次运行 | 用户身份、权限、租户、请求元数据 |
| Session | 多轮会话 | 保存对话历史，支持上下文连续 |
| Memory | 长期记忆 | 保存用户偏好、业务事实、长期状态 |
| State | 工作流状态 | 保存审批、暂停、恢复、步骤状态 |

### 7.5.2 RunContext

```python
class RunContext(BaseModel):
    run_id: str
    user_id: str
    tenant_id: str
    roles: list[str] = []
    permissions: list[str] = []
    session_id: str | None = None
    request_id: str | None = None
    channel: str | None = None
    metadata: dict = {}
```

### 7.5.3 SessionManager

第一版支持：

1. SQLiteSession：本地开发和 demo。
2. RedisSession：生产环境低成本方案。
3. SQLAlchemySession：企业场景可审计存储。
4. CustomSession：外部系统接入。

### 7.5.4 MemoryManager

建议第二阶段再实现长期记忆，避免第一版复杂化。长期记忆应支持：

1. 用户偏好记忆。
2. 企业知识记忆。
3. 业务实体状态。
4. 可删除、可过期、可追溯。
5. 与 RAG 检索分离。

---

## 7.6 Governance：安全治理模块

### 7.6.1 Guardrails

框架应封装常见 input / output guardrails：

| Guardrail | 类型 | 说明 |
|---|---|---|
| pii_input_check | input | 检测用户输入中的敏感信息 |
| jailbreak_check | input | 检测越权、提示注入、恶意请求 |
| tool_policy_check | input/tool | 判断请求是否允许调用某类工具 |
| safe_output_check | output | 检查输出是否包含不应泄露的信息 |
| citation_check | output | 检查知识库问答是否包含来源 |
| structured_output_check | output | 校验结构化输出是否符合 schema |

### 7.6.2 Permission

工具权限建议采用 RBAC + ABAC 结合：

```python
class PermissionPolicy(BaseModel):
    subject: str       # user / agent / tenant
    action: str        # order:refund
    resource: str      # order_id / tenant_id
    conditions: dict   # amount < 500, owner == user
```

### 7.6.3 Human-in-the-loop

高风险工具调用应暂停运行，进入审批状态：

```text
Agent 想调用 refund_order
  ↓
框架判断 requires_approval=True
  ↓
生成 approval_request
  ↓
运行中断，返回 interrupted
  ↓
审批人批准 / 拒绝
  ↓
恢复 RunState
  ↓
继续运行或返回拒绝说明
```

### 7.6.4 ApprovalRequest

```python
class ApprovalRequest(BaseModel):
    approval_id: str
    run_id: str
    agent_name: str
    tool_name: str
    arguments: dict
    risk_level: str
    requested_by: str
    tenant_id: str
    status: str  # pending / approved / rejected / expired
    reason: str | None = None
```

---

## 7.7 Observability：可观测与审计模块

### 7.7.1 必须记录的运行数据

| 数据 | 说明 |
|---|---|
| run_id | 每次运行唯一 ID |
| trace_id | 底层 trace ID |
| user_id / tenant_id | 用户与租户 |
| workflow / agent | 使用的流程和 Agent |
| input / output 摘要 | 用于排查，敏感信息需脱敏 |
| tool_calls | 工具调用名称、参数摘要、耗时、状态 |
| handoffs | Agent 转移链路 |
| approval | 审批状态 |
| usage | token、模型、成本 |
| latency | 总耗时和分阶段耗时 |
| error | 错误类型、堆栈、恢复建议 |

### 7.7.2 指标体系

| 指标 | 说明 |
|---|---|
| Run 成功率 | completed / total |
| 平均延迟 | P50 / P95 / P99 |
| 工具调用成功率 | tool_success / tool_total |
| 审批触发率 | approvals / runs |
| 平均成本 | cost / run |
| Handoff 次数 | 多 Agent 路由复杂度 |
| Guardrail 触发率 | 安全风险趋势 |
| 用户满意度 | 显式反馈或业务指标 |

### 7.7.3 Replay

每次失败应支持回放：

```python
await app.replay(run_id="run_123", mode="dry_run")
```

回放时可选择：

1. 使用原始模型输出。
2. 重新调用模型。
3. mock 工具。
4. 只重放工作流步骤。
5. 对比不同 Agent 版本结果。

---

## 7.8 Eval：评估与回归测试模块

### 7.8.1 Eval 文件格式

```yaml
name: customer_support_eval
agent: support
cases:
  - id: refund_case_001
    input: "我想退掉订单 123"
    expected:
      should_call_tools:
        - order.query
        - refund.request
      should_require_approval: true
      output_contains:
        - "退款"
        - "审批"
```

### 7.8.2 评估维度

| 维度 | 说明 |
|---|---|
| 意图识别 | 是否正确识别任务 |
| 工具选择 | 是否调用正确工具 |
| 参数质量 | 工具参数是否完整、准确 |
| 安全合规 | 是否触发必要 guardrail 或审批 |
| 输出质量 | 语言、完整度、结构化格式 |
| 成本延迟 | 是否在预算内 |
| 回归稳定性 | 新版本是否破坏旧案例 |

### 7.8.3 CI 集成

```bash
agentapp eval run ./evals/customer_support.yaml
agentapp eval compare support:v1 support:v2
```

---

## 7.9 Integration：外部系统集成模块

第一阶段建议支持：

1. HTTP API 工具。
2. Python function tool。
3. 数据库只读查询工具。
4. RAG 检索工具。
5. MCP 服务接入。
6. FastAPI 运行适配器。
7. Celery / RQ 异步任务适配器。

第二阶段支持：

1. Kafka / RabbitMQ 事件驱动。
2. Langfuse / Datadog / OpenTelemetry。
3. 多向量库适配。
4. 企业 SSO / IAM。
5. 可视化审批台。

---

## 8. 推荐项目结构

```text
agent_app/
  core/
    agent_spec.py
    tool_spec.py
    workflow.py
    app_runner.py
    context.py
    result.py

  registry/
    agent_registry.py
    tool_registry.py
    workflow_registry.py
    policy_registry.py

  runtime/
    session_manager.py
    memory_manager.py
    state_store.py
    streaming.py
    run_config.py

  governance/
    guardrails.py
    approval.py
    permission.py
    audit.py
    risk.py

  integrations/
    function_tool.py
    mcp.py
    rag.py
    database.py
    http.py
    vector_store.py

  observability/
    tracing.py
    metrics.py
    cost.py
    evals.py
    replay.py

  adapters/
    openai_agents.py
    fastapi.py
    celery.py
    cli.py

  templates/
    customer_support/
    research_assistant/
    data_analyst/

  examples/
    customer_support/
    research_assistant/
    data_analyst/

  tests/
    unit/
    integration/
    evals/
```

---

## 9. 配置文件设计

### 9.1 agentapp.yaml

```yaml
app:
  name: agent-app-framework
  environment: dev

models:
  default: gpt-5.1

agents:
  triage:
    description: 客服分诊 Agent
    model: gpt-5.1
    instructions: ./prompts/triage.md
    handoffs:
      - billing
      - refund
      - technical_support

  refund:
    description: 退款处理 Agent
    model: gpt-5.1
    instructions: ./prompts/refund.md
    tools:
      - order.query
      - refund.request
    guardrails:
      - pii_input_check
      - refund_policy_check

tools:
  order.query:
    type: function
    risk_level: low
    permissions:
      - order:read

  refund.request:
    type: function
    risk_level: high
    requires_approval: true
    permissions:
      - refund:create

workflows:
  customer_support:
    type: handoff
    entry: triage

runtime:
  session:
    type: sqlite
    path: ./data/sessions.db

observability:
  tracing:
    enabled: true
  audit:
    enabled: true
```

---

## 10. 开发者使用示例

### 10.1 定义工具

```python
from agent_app import tool

@tool(
    name="order.query",
    description="查询订单信息",
    risk_level="low",
    permissions=["order:read"],
)
async def query_order(order_id: str) -> dict:
    return {
        "order_id": order_id,
        "status": "paid",
        "amount": 199.0,
    }


@tool(
    name="refund.request",
    description="创建退款申请",
    risk_level="high",
    requires_approval=True,
    permissions=["refund:create"],
)
async def request_refund(order_id: str, amount: float, reason: str) -> dict:
    return {
        "refund_id": "rf_123",
        "order_id": order_id,
        "amount": amount,
        "status": "created",
    }
```

### 10.2 启动应用

```python
from agent_app import AgentApp

app = AgentApp.from_config("./agentapp.yaml")

result = await app.run(
    workflow="customer_support",
    input="我想退掉订单 123",
    user_id="u_001",
    tenant_id="t_001",
    session_id="conv_001",
)

print(result.final_output)
```

### 10.3 处理审批

```python
result = await app.run(
    workflow="customer_support",
    input="请帮我退款 199 元",
    user_id="u_001",
    tenant_id="t_001",
    session_id="conv_001",
)

if result.status == "interrupted":
    approval = result.interruptions[0]
    await app.approve(
        run_id=result.run_id,
        approval_id=approval["approval_id"],
        approved_by="manager_001",
    )

    resumed = await app.resume(run_id=result.run_id)
```

---

## 11. API 服务设计

### 11.1 REST API

```http
POST /runs
GET /runs/{run_id}
POST /runs/{run_id}/approve
POST /runs/{run_id}/reject
POST /runs/{run_id}/resume
GET /runs/{run_id}/events
GET /agents
GET /tools
GET /workflows
```

### 11.2 创建运行

```json
{
  "workflow": "customer_support",
  "input": "我想申请退款",
  "user_id": "u_001",
  "tenant_id": "t_001",
  "session_id": "conv_001",
  "stream": false
}
```

### 11.3 运行返回

```json
{
  "run_id": "run_abc",
  "status": "interrupted",
  "final_output": null,
  "interruptions": [
    {
      "approval_id": "apv_001",
      "tool_name": "refund.request",
      "arguments": {
        "order_id": "123",
        "amount": 199
      },
      "risk_level": "high"
    }
  ]
}
```

---

## 12. Streaming 设计

### 12.1 WebSocket 事件类型

```json
{"type": "run.started", "run_id": "run_123"}
{"type": "text.delta", "delta": "您好"}
{"type": "tool.started", "tool_name": "order.query"}
{"type": "tool.completed", "tool_name": "order.query"}
{"type": "approval.required", "approval_id": "apv_001"}
{"type": "run.completed", "final_output": "..."}
```

### 12.2 中断与恢复

当出现审批中断：

```text
stream 结束
  ↓
前端展示审批卡片
  ↓
审批通过
  ↓
调用 resume
  ↓
重新建立 stream
```

---

## 13. MVP 实施范围

### 13.1 MVP 必须包含

| 模块 | MVP 内容 |
|---|---|
| AgentSpec | 支持 Python 和 YAML 定义 |
| ToolRegistry | 支持 function tool 注册 |
| AppRunner | 支持单 Agent 和基础 workflow 运行 |
| Session | 支持 SQLiteSession |
| Guardrails | 支持基础 input/output guardrails |
| HITL | 支持高风险工具审批暂停与恢复 |
| Observability | 支持 run log、tool log、trace_id |
| Eval | 支持 YAML eval case 和基础断言 |
| Example | 完整客服 Agent 示例 |

### 13.2 MVP 暂不包含

1. 完整可视化控制台。
2. 多租户复杂权限后台。
3. 长期 memory。
4. 大规模工具搜索平台。
5. 多模型智能路由。
6. 完整低代码工作流编辑器。

---

## 14. 里程碑计划

### 第 1 阶段：框架骨架与基础运行

周期：1 周

交付物：

1. 项目基础结构。
2. AgentSpec。
3. ToolRegistry。
4. OpenAI Agents SDK adapter。
5. AppRunner 最小实现。
6. 单 Agent 示例。

验收标准：

1. 可以注册工具。
2. 可以定义 Agent。
3. 可以运行一次 Agent。
4. 可以返回统一 AppRunResult。

---

### 第 2 阶段：会话、配置与流式输出

周期：1 周

交付物：

1. `agentapp.yaml` 配置加载。
2. SQLite session 支持。
3. Prompt 文件加载。
4. Streaming 事件封装。
5. FastAPI adapter 初版。

验收标准：

1. 支持多轮对话。
2. 支持配置文件启动应用。
3. 支持 WebSocket 输出文本 delta 和工具事件。

---

### 第 3 阶段：工具治理与审批

周期：1 周

交付物：

1. 工具风险等级。
2. 权限校验。
3. 审批中断。
4. approve / reject / resume API。
5. audit log。

验收标准：

1. 高风险工具默认暂停。
2. 审批通过后可恢复运行。
3. 拒绝后 Agent 能给出合理解释。
4. 工具调用有完整审计记录。

---

### 第 4 阶段：工作流与多 Agent

周期：1 周

交付物：

1. Handoff workflow。
2. Agents-as-tools workflow。
3. 基础 DAG workflow。
4. 客服场景完整 demo。

验收标准：

1. triage Agent 能转交到 refund / billing / tech Agent。
2. manager Agent 能调用 specialist Agent。
3. 固定流程能控制高风险路径。

---

### 第 5 阶段：观测、评估与发布准备

周期：1 至 2 周

交付物：

1. Metrics。
2. Cost tracking。
3. Eval runner。
4. Replay。
5. 文档和模板。
6. Docker / Helm / 部署示例。

验收标准：

1. 可查看 run 成功率、延迟、工具调用、成本。
2. eval case 可在 CI 中运行。
3. 失败 run 可 replay。
4. 客服示例可一键启动。

---

## 15. 技术选型建议

| 领域 | 推荐 |
|---|---|
| 底层 Agent SDK | OpenAI Agents SDK |
| Web 服务 | FastAPI |
| 配置 | YAML + Pydantic |
| 数据库 | PostgreSQL |
| 本地开发 session | SQLite |
| 缓存 / 短期状态 | Redis |
| 异步任务 | Celery / RQ / Dramatiq |
| 日志 | structlog / logging |
| 指标 | Prometheus / OpenTelemetry |
| Trace | SDK tracing + OpenTelemetry adapter |
| 向量检索 | OpenAI File Search / pgvector / Milvus / Qdrant |
| 测试 | pytest |
| 类型检查 | mypy / pyright |
| 包管理 | uv / poetry |
| 部署 | Docker / Kubernetes |

---

## 16. 数据库表设计草案

### 16.1 runs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | run_id |
| workflow | string | 工作流 |
| agent | string | 入口 Agent |
| user_id | string | 用户 |
| tenant_id | string | 租户 |
| session_id | string | 会话 |
| status | string | 状态 |
| input_summary | text | 输入摘要 |
| output_summary | text | 输出摘要 |
| trace_id | string | trace |
| created_at | datetime | 创建时间 |
| completed_at | datetime | 完成时间 |

### 16.2 tool_calls

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 工具调用 ID |
| run_id | string | 关联 run |
| agent | string | Agent 名称 |
| tool_name | string | 工具名称 |
| arguments_summary | json | 参数摘要 |
| status | string | 状态 |
| latency_ms | int | 耗时 |
| error | json | 错误 |

### 16.3 approvals

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | approval_id |
| run_id | string | 关联 run |
| tool_call_id | string | 工具调用 |
| risk_level | string | 风险等级 |
| status | string | pending / approved / rejected |
| requested_by | string | 请求人 |
| approved_by | string | 审批人 |
| reason | text | 原因 |
| created_at | datetime | 创建时间 |
| resolved_at | datetime | 处理时间 |

### 16.4 eval_results

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | eval result ID |
| eval_name | string | eval 名称 |
| agent_version | string | Agent 版本 |
| case_id | string | case ID |
| passed | bool | 是否通过 |
| score | float | 分数 |
| details | json | 详情 |

---

## 17. 安全与合规设计

### 17.1 敏感信息处理

1. 输入输出日志默认脱敏。
2. 工具参数中涉及手机号、邮箱、证件号、地址、银行卡等字段应脱敏存储。
3. 管理员可配置字段级脱敏规则。
4. trace 与 audit 权限分离。
5. 支持按租户隔离数据。

### 17.2 工具副作用控制

1. 写操作工具必须显式声明。
2. 高风险写操作默认审批。
3. 工具执行前必须鉴权。
4. 工具执行后必须审计。
5. 支持 dry-run 模式。
6. 支持幂等 key。

### 17.3 Prompt 注入防护

1. RAG 检索内容与系统指令分离。
2. 外部内容不允许覆盖系统策略。
3. 工具调用前检查用户意图和权限。
4. 对来自网页、文档、邮件的内容增加提示注入检测。
5. 输出前检查是否泄露系统提示词、工具 schema、密钥等信息。

---

## 18. 测试策略

### 18.1 单元测试

覆盖：

1. AgentSpec 编译。
2. ToolRegistry 注册和解析。
3. 权限策略。
4. 审批策略。
5. 配置加载。
6. RunResult 转换。

### 18.2 集成测试

覆盖：

1. Agent 调用工具。
2. 多轮 session。
3. streaming。
4. handoff。
5. approval resume。
6. guardrail tripwire。

### 18.3 Eval 测试

覆盖：

1. 工具调用是否符合预期。
2. 输出结构是否符合 schema。
3. 高风险操作是否触发审批。
4. 多 Agent 路由是否正确。
5. 新版本 Agent 是否回归。

### 18.4 压测

关注：

1. 并发 run 数。
2. 平均延迟。
3. 工具调用延迟。
4. session 存储瓶颈。
5. streaming 连接数。
6. 数据库写入压力。

---

## 19. 示例业务场景：客服 Agent

### 19.1 场景说明

用户通过网页客服入口咨询订单、退款、发票、物流、售后等问题。系统先由 triage Agent 判断问题类型，再转交到对应专家 Agent。

### 19.2 Agent 划分

| Agent | 职责 |
|---|---|
| triage | 判断意图并路由 |
| order_agent | 查询订单、物流、支付状态 |
| refund_agent | 判断退款政策，发起退款申请 |
| billing_agent | 发票、支付、账单问题 |
| ticket_agent | 创建人工工单 |

### 19.3 工具列表

| 工具 | 风险 | 是否审批 |
|---|---|---|
| order.query | low | 否 |
| logistics.query | low | 否 |
| invoice.query | low | 否 |
| ticket.create | medium | 否或按规则 |
| refund.request | high | 是 |
| order.cancel | high | 是 |

### 19.4 端到端流程

```text
用户：我想退款
  ↓
triage 判断为 refund
  ↓
handoff 到 refund_agent
  ↓
refund_agent 调用 order.query
  ↓
判断是否符合退款政策
  ↓
调用 refund.request
  ↓
框架触发审批
  ↓
审批通过
  ↓
恢复运行
  ↓
返回退款申请结果
```

### 19.5 验收标准

1. 能处理至少 20 个典型客服问题。
2. 订单查询成功率达到 95% 以上。
3. 高风险退款场景 100% 触发审批。
4. 用户连续追问时能保持 session 上下文。
5. 每次工具调用都有审计记录。
6. 每个 run 都能定位 trace 和日志。
7. eval 集合通过率达到 90% 以上。

---

## 20. 风险分析与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 封装过厚导致 SDK 新能力难接入 | 高 | 保留 raw kwargs 和 native object |
| 过早做平台化控制台 | 中 | 先做 SDK + API，控制台后置 |
| 工具权限不严导致副作用风险 | 高 | 工具默认声明风险等级，高风险默认审批 |
| Prompt 注入导致越权工具调用 | 高 | 输入检测、工具前鉴权、上下文隔离 |
| eval 不足导致上线不稳定 | 高 | 每个业务模板必须配 eval case |
| 成本不可控 | 中 | token/cost 统计、模型分级、缓存、工具搜索 |
| session 与 memory 混乱 | 中 | 明确分层，MVP 只做 session |
| 多 Agent 路由不可解释 | 中 | 记录 handoff 链路和路由理由 |
| 框架难以被业务团队接受 | 中 | 提供模板、CLI、文档和最小样例 |

---

## 21. 团队分工建议

| 角色 | 职责 |
|---|---|
| 架构负责人 | 框架架构、抽象边界、技术路线 |
| SDK 工程师 | AgentSpec、ToolRegistry、AppRunner、Adapter |
| 后端工程师 | API、数据库、审批、权限、部署 |
| AI 工程师 | Prompt、Agent 设计、eval、guardrails |
| 平台工程师 | tracing、metrics、CI/CD、容器化 |
| 前端工程师 | Demo UI、审批页面、运行结果展示 |
| QA / 测试 | 测试用例、eval、回归、压测 |

---

## 22. 开发规范建议

### 22.1 Agent 命名

使用业务域前缀：

```text
support.triage
support.refund
support.billing
research.manager
data.analyst
```

### 22.2 工具命名

使用命名空间：

```text
order.query
order.cancel
refund.request
ticket.create
crm.search_customer
kb.search
```

### 22.3 Prompt 管理

推荐结构：

```text
prompts/
  support/
    triage.v1.md
    refund.v1.md
    billing.v1.md
```

### 22.4 版本管理

```text
agent_name: support.refund
version: v1
prompt_version: refund.v1
toolset_version: support_tools.v1
eval_version: refund_eval.v1
```

### 22.5 变更流程

1. 修改 Prompt / Agent / Tool。
2. 运行本地 eval。
3. 提交 PR。
4. CI 执行 eval regression。
5. 灰度发布。
6. 监控成功率、成本、审批率、用户反馈。
7. 扩大流量或回滚。

---

## 23. CLI 设计草案

```bash
agentapp init customer-support
agentapp dev
agentapp run support "我想退款"
agentapp eval run ./evals/support.yaml
agentapp eval compare support:v1 support:v2
agentapp tools list
agentapp agents list
agentapp workflows list
agentapp replay run_123
```

---

## 24. Roadmap

### v0.1：开发框架 MVP

1. AgentSpec。
2. ToolRegistry。
3. AppRunner。
4. Session。
5. Approval。
6. Audit。
7. Eval。
8. 客服模板。

### v0.2：生产增强

1. FastAPI Server。
2. Redis / PostgreSQL 状态存储。
3. Streaming。
4. Metrics。
5. Replay。
6. MCP 集成。
7. RAG 模块。

### v0.3：平台化

1. AgentOps Console。
2. Prompt 版本管理。
3. 审批中心。
4. 成本分析。
5. 多租户权限。
6. 灰度发布。

### v1.0：企业级 Agent 应用平台

1. 多模型路由。
2. 多工作流模板。
3. 完整可观测系统。
4. 完整 eval 平台。
5. 插件市场 / 工具市场。
6. 权限、审计、合规模块完善。

---

## 25. 最终建议

本项目的关键成功点不在于“封装多少 OpenAI Agents SDK API”，而在于能否建立一套适合业务团队长期使用的 Agent 应用工程体系。

推荐第一版聚焦以下目标：

1. 让开发者 10 分钟内创建一个可运行 Agent。
2. 让业务团队可以用统一方式注册工具。
3. 让高风险工具调用默认受到权限和审批保护。
4. 让每次运行都可追踪、可审计、可回放。
5. 让每个 Agent 都有 eval，避免 Prompt 修改后不可控。
6. 保持底层 SDK 原生能力可访问，避免框架成为瓶颈。

第一版最适合用“客服 Agent”作为样板工程，因为它天然覆盖：

1. 多轮对话。
2. 意图识别。
3. 多 Agent 路由。
4. 工具调用。
5. 高风险审批。
6. 审计日志。
7. eval 回归。
8. 生产观测。

只要客服样例跑通，这套框架的核心价值就能被验证。

---

## 26. 附录：MVP 任务清单

### 基础能力

- [ ] 创建项目骨架
- [ ] 定义 AgentSpec
- [ ] 定义 ToolSpec
- [ ] 实现 ToolRegistry
- [ ] 实现 AgentRegistry
- [ ] 实现 OpenAI Agents SDK adapter
- [ ] 实现 AppRunner
- [ ] 实现 AppRunResult

### 配置能力

- [ ] 支持 YAML 配置
- [ ] 支持 Prompt 文件加载
- [ ] 支持环境变量覆盖
- [ ] 支持 Agent 版本字段

### 运行能力

- [ ] 支持单 Agent 运行
- [ ] 支持 session
- [ ] 支持 streaming
- [ ] 支持 handoff
- [ ] 支持 agents-as-tools
- [ ] 支持基础 DAG

### 治理能力

- [ ] 工具风险等级
- [ ] 工具权限校验
- [ ] 工具超时
- [ ] 工具错误处理
- [ ] 审批中断
- [ ] 审批恢复
- [ ] 审计日志
- [ ] input guardrails
- [ ] output guardrails

### 观测能力

- [ ] run log
- [ ] tool call log
- [ ] trace_id 关联
- [ ] latency 统计
- [ ] usage 统计
- [ ] cost 统计
- [ ] replay 初版

### 测试与评估

- [ ] pytest 单元测试
- [ ] 集成测试
- [ ] eval runner
- [ ] eval case schema
- [ ] CI 集成
- [ ] 客服样例 eval

### 示例与文档

- [ ] customer_support 示例
- [ ] research_assistant 示例
- [ ] README
- [ ] 快速开始文档
- [ ] 工具开发文档
- [ ] Agent 开发文档
- [ ] 部署文档

---

## 27. 附录：建议仓库 README 开头

```markdown
# Agent App Framework

A production-oriented application framework built on top of OpenAI Agents SDK.

It helps teams build agentic applications with:

- Declarative Agent configuration
- Tool registry and governance
- Workflow orchestration
- Sessions and runtime context
- Guardrails and human approval
- Tracing, audit, metrics and evals
- FastAPI and worker deployment adapters

This framework does not replace OpenAI Agents SDK. It provides an application layer for building, operating and governing real-world Agent applications.
```
