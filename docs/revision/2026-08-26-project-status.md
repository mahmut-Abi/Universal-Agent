# 项目当前状态与工程边界（2026-08-26）

- 审计时间：2026-08-26 CST
- 审计范围：当前 HEAD、README、架构设计文档、源码、测试、示例和依赖配置
- 当前 HEAD：`37d9621 Record decision metrics in evaluation replay`
- 文档性质：实现状态与工程判断快照，不替代 `AGENTS.md` 或架构设计文档

## 总体判断

项目已经从单 Agent Runtime 原型扩展为一个覆盖 P0–P7 主要基础能力、并包含可选 P4
Multi-Agent foundation 的本地 Runtime 平台：

```text
P0–P3   单 Agent 语义、Domain、Evidence、World、Recovery、Memory
P3.5    Runtime API、agentd、CLI、Session/Event 持久化
P3.6–7  Operations、Evaluation、Replay、Deterministic Mode
P4      Multi-Agent contract / registry / delegation / merge / conflict foundation
P5      只读 TUI、Web Console、Session Explorer、Evaluation Console
P6      Queue、Worker、Lease、Lock、持久化协调和 Runtime worker 闭环
P7      Domain Package、Dataset、Profile、Ecosystem Registry 和 SDK scaffolding
```

最准确的定位是：

> 一个架构边界清晰、可测试、可恢复、可嵌入的 Universal Agent Runtime foundation；
> 不是已经完成生产级 HA、跨节点分布式调度、供应链安装或企业安全治理的平台。

## 当前实现亮点

### 1. Runtime 控制权保持正确

Runtime 仍然拥有：

- Goal/Task/Session 状态；
- Decision 合约验证；
- Capability 到 Tool 的确定性解析；
- Policy allow/confirm/deny；
- Tool timeout、unknown execution 和 recovery budget；
- Evidence、World Model、Task expansion 和 Evaluator；
- Finish、Pause、Resume、Cancel 与事件提交。

模型只提出结构化 Decision。当前还会把上下文中的可执行 Capability、required arguments、
argument schema、Goal success criteria 和当前 Task criteria 提供给模型，并在 Runtime 内部
再次验证模型输出。非法或不可执行 Decision 会产生 `DecisionRejected`，合法 Decision 会产生
`DecisionValidated`，并进入指标、评估和 Replay。

主要入口：

- [AgentRuntime](../../src/universal_agent/runtime/agent.py)
- [ActionExecutor](../../src/universal_agent/runtime/actions.py)
- [Argument contract](../../src/universal_agent/core/arguments.py)
- [ObservationProcessor](../../src/universal_agent/runtime/processing.py)

### 2. Domain 与 World Model 已明显深化

当前已经支持：

- Domain identity/version 全链路校验；
- evaluator、extractor、updater、expander 按 Action 所属 Domain 路由；
- Action argument provider，可从 World 读取 resource version/current replicas 等 guard；
- Fact、Entity、Relation、Fact History、冲突候选和 neighborhood 查询；
- Evidence provenance 贯穿 World projection。

不过多 Domain 的跨域推理、跨域实体合并、关系冲突解决仍不是完整的知识图谱实现。

### 3. 本地产品化与可靠性基础已形成

已经具备：

- Runtime API、SDK facade、agentd route adapter、标准库 HTTP bridge；
- SQLiteRuntimeStore 的 state/event transaction；
- FileRuntimeStore 的 write-ahead commit journal；
- Session snapshot version/CAS；
- State/Event consistency doctor 与受控 repair；
- bounded event polling、SSE heartbeat、配置脱敏；
- optional bearer auth 和 read-only token scope；
- env/file secret reference resolution、availability report 和 secret scanning。

这些能力使本地重启、坏快照、部分事件缺失和敏感字段泄漏有了检测或恢复路径，但尚没有
企业级身份系统、KMS/Vault、TLS termination、租户隔离或不可篡改审计存储。

### 4. Model 与 Kubernetes 已有真实适配形状

当前提供：

- provider-agnostic `JsonHttpModelAdapter`；
- `OpenAIResponsesModelAdapter`，使用 Structured Output 请求 Decision JSON；
- 本地 Decision/context/input-contract 校验；
- `KubectlBackend`，通过受控 subprocess 调用 `kubectl`；
- `KubernetesApiBackend`，通过 HTTP API 访问 Kubernetes；
- current replicas/resource version optimistic checks；
- fake/fixture backend 仍作为默认测试路径。

真实适配器已经存在，但没有 live cluster/provider 的 CI 验证，也没有生产级 credential rotation、
network retry policy、rate limiting、sandbox 或供应商 SDK 的完整能力。

### 5. P4 Multi-Agent 是可选执行边界，不是 Domain 路由

当前已有：

- `AgentTaskRequest` / `AgentTaskResult` contract；
- Agent Profile 与 running Instance registry；
- eligibility、read-only、permission、depth、children、duration、cost 限制；
- 单任务和依赖感知 batch delegation；
- child Agent lifecycle 与 usage 汇总；
- structured conflict resolution；
- Evidence-aware result merge；
- merge evaluation 和 payload/replay helpers。

`RuntimeAgentExecutor` 可以把子任务交给另一个 RuntimeAPI，但 Multi-Agent orchestration 仍是
独立的上层 foundation：它没有替代单 Runtime loop，也没有形成网络 Agent registry、跨进程
delegation transport 或完整 parent/child event ledger。

### 6. P6 已有本地 Runtime worker 闭环

当前已从“只读 queue view”推进到：

```text
RuntimeService
  → WorkScheduler
  → InMemory/File/SQLite WorkQueue
  → WorkQueueWorker
  → RuntimeAPI run/resume
  → Session/Event Store
```

支持 session、goal、task、confirmed pending action 调度，capability-aware leasing，queue/worker
heartbeat，session-scoped lock，expiry、retry、cancel、terminal pruning 和 health recommendations。

但 File/SQLite coordination 仍是本机级 primitives，不提供跨节点共识或高可用。

### 7. P7 已从 Catalog 扩展到显式安装/激活分层

当前包含：

- Domain Package metadata registry、dependency/resource verification；
- declarative/base Domain SDK 和 runtime stub scaffold；
- 显式 `load_domain_package_runtime` entrypoint activation；
- Evaluation Dataset 与 suite reference verification；
- Profile Catalog verification；
- Ecosystem Registry manifest/store/query/install plan；
- sha256、path traversal、metadata drift、dependency cycle、trust policy、signature verifier seam。

重要边界仍然保留：metadata install 不会自动 import/activate Domain，不会下载依赖，也不会
替代包管理器或供应链签名系统。

## 依赖策略的客观评价

当前 `pyproject.toml` 的运行时 `dependencies = []`，约 40,415 行源码、111 个 Python 模块、
57 个测试文件和 99 个示例主要依赖 Python 标准库。这个选择对 Kernel 很合理，但对外围基础设施
已经产生明显机会成本。

### 继续保持标准库/手写的部分

以下内容属于项目的核心语义，继续自有实现是正确的：

- Goal/Task/Session 状态机；
- Decision、Policy、Evidence、World、Evaluation、Recovery contract；
- Domain Composition 与 owner routing；
- Runtime Event 语义；
- In-memory reference implementations；
- Resource lock 的上层接口；
- Multi-Agent Task/Result contract。

这些地方引入 LangGraph、Celery、Temporal 或某个 Agent Framework 作为核心，容易让框架反过来
拥有控制流，违背项目的架构目标。

### 已经不适合全部手写的部分

当前自研代码已经覆盖了许多非核心基础设施：

- JSON schema/config/manifest validation；
- HTTP routing、request parsing、SSE 和 server bridge；
- CLI parser/dispatch；
- SQLite/file persistence codec 和迁移前置逻辑；
- Queue/Worker/Lock persistence；
- OTLP-shaped export；
- HTML/TUI rendering；
- registry digest/trust/install validation。

继续全部手写的风险是代码规模持续膨胀、协议兼容性和并发边界需要自行承担、生产成熟度容易
被“测试很多”误判。当前大型文件已经超过 2,000 行：`cli.py`、`agentd/app.py`、`web.py`；
`service/runtime.py`、`ecosystem/catalog.py` 也超过 1,500 行。

### 推荐的折中路线

```text
项目自有 Core/Runtime contract
        ↓
成熟库适配层（可选 extras）
        ↓
生产后端 / 外部系统
```

优先级建议：

1. `packaging`：版本和 compatibility specifier，不要继续手写版本语义；
2. Pydantic v2 或 msgspec：外部 manifest/config/HTTP DTO，转换后仍进入 Core dataclass；
3. SQLAlchemy Core + Alembic：生产数据库 adapter、migration、outbox 和跨数据库支持；
4. 官方 OpenTelemetry SDK/exporter：保留当前纯函数 projection 作为 deterministic test adapter；
5. Starlette/FastAPI + Uvicorn：为现有 AgentdApp 增加可选 ASGI adapter，保留 stdlib bridge；
6. Hypothesis：验证 Queue/Lease/Snapshot/Codec 的状态不变量。

不建议立即把 Core 改成 ORM/Pydantic 对象，也不建议用 Celery/Temporal 直接替换
Runtime-owned Queue/Action semantics。

## 主要剩余风险

1. **跨节点分布式仍未完成。** 当前 P6 适合单机多进程和本地恢复，不是 HA/consensus 系统。
2. **真实外部集成缺少 live CI。** Kubernetes、OpenAI、通用 HTTP 适配器主要通过 fake transport
   和 fixtures 验证。
3. **跨 Store 原子性有明确策略但仍是本地实现。** SQLite transaction 和 file journal 已存在，
   但没有生产 outbox、异步 publisher、schema migration 和多租户事务边界。
4. **Secrets 仍是 env/file provider。** 没有 KMS/Vault、轮换、短期 token、访问审计。
5. **Domain Package entrypoint 是代码执行边界。** 显式 loader 会 import 本地代码，但没有 sandbox、
   签名强制策略或隔离进程。
6. **Multi-Agent parent/child 持久化仍有限。** contract、limits、merge 已有，但没有完整的
   跨 Agent durable event ledger 和网络传输。
7. **默认核心实现仍是内存型。** World、Memory、ResourceVersionRegistry、Agent Registry 等
   多数不是生产持久化后端。
8. **事件流仍非真正 push。** 当前支持 bounded polling/SSE heartbeat，不是 broker-backed long-lived
   stream。

## 今日验证结果

```text
pytest --disable-warnings -r a   683 passed, 10 skipped
ruff check                      passed
ruff format --check             passed (267 files)
mypy strict                     passed (267 source files)
```

10 个跳过测试都因当前执行环境禁止本地 socket bind。测试仍有较多 Python 3.14 /
pytest-asyncio event loop 弃用警告；它们未影响本次结果。

## 推荐下一步

### P0：稳定质量与边界

- 拆分 `cli.py`、`agentd/app.py`、`web.py`、`ecosystem/catalog.py` 等大型模块；
- 为当前自研 schema/codec/queue/lock 增加 property-based invariant tests；
- 增加 live-like HTTP/Kubernetes/OpenAI contract test harness；
- 明确并记录 `RuntimeHost`、Profile、AgentdApp 和 Multi-Agent 的 activation/selection 语义。

### P1：生产可靠性

- 引入 Postgres/SQLAlchemy adapter 和 Alembic migration；
- 设计 State/Event outbox、重试、幂等 publish 和 replay repair；
- 引入正式 OpenTelemetry exporter 与 context propagation；
- 增加认证、授权、租户、KMS/Vault 和审计存储。

### P2：真正的分布式 Runtime

- 将 Queue、Worker Registry、Lock 和 Coordinator 接入网络后端；
- 定义跨节点 lease、clock skew、leader election、worker fencing 和 duplicate execution policy；
- 把 Multi-Agent delegation transport 与 Session/Event ledger 接到持久化 Runtime。

在这些工作之前，不建议继续扩展更多 UI 或增加更复杂的 Agent framework abstraction。

## 文档权威关系

- 工程约束：[AGENTS.md](../../AGENTS.md)
- 架构目标：[universal-agent-runtime-domain-runtime-design.md](../../universal-agent-runtime-domain-runtime-design.md)
- 操作说明：[README.md](../../README.md)
- 上一版状态：[2026-08-24-project-status.md](2026-08-24-project-status.md)
- 本文：2026-08-26 的最新实现审计快照
