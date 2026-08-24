# 项目当前状态与后续边界（2026-08-24）

- 审计时间：2026-08-24 CST
- 审计范围：当前源码、最近提交、README、架构设计文档、测试与示例
- 文档性质：实现状态快照，不替代 `AGENTS.md` 或架构设计文档

## 总体结论

项目已经从单 Agent Runtime 原型推进为一个覆盖 P0–P7 多个阶段的本地 Runtime foundation：

```text
P0–P3   核心 Agent 语义
P3.5    Runtime API / agentd / CLI / 持久化
P3.6–7  Operations / Evaluation / Replay
P5      只读 TUI / Web / Session Explorer
P6      本地队列、Worker、租约、锁和 Runtime 执行适配
P7      Domain Package / Dataset / Profile / Ecosystem Registry 元数据
```

当前最准确的定位是：

> 具备完整单 Agent 执行语义、本地产品化接口、可恢复 Runtime 和本地分布式/生态基础原语的工程平台。

它仍然不是生产级集群控制面，也不是已经完成跨节点高可用、真实 Kubernetes 运维、网络 Worker
编排或自动安装外部依赖的完整平台。

## 已落地的核心能力

### 1. Agent Runtime 语义

Runtime 控制以下闭环：

```text
Goal → Task → Context → Decision → Capability Resolution → Policy
     → Tool → Observation → Evidence → World Model
     → Task Expansion → Evaluation → Continue / Recover / Finish
```

已经具备：

- typed Goal、Task、Decision、Action、Observation、Evidence、Evaluation 合约；
- 模型不拥有状态、权限、重试、工具执行或完成信号；
- Capability 与 Tool 分离；Mutation 默认拒绝；确认路径由 Runtime 控制；
- SessionSnapshot、Task Graph、Evidence replay、World replay；
- bounded iteration、分类 Recovery、unknown execution；
- action idempotency key、parameters hash、attempt；
- side-effect resource lock、resource conflict 和 resource version metadata。

主要实现入口：

- [AgentRuntime](../../src/universal_agent/runtime/agent.py)
- [ActionExecutor](../../src/universal_agent/runtime/actions.py)
- [ObservationProcessor](../../src/universal_agent/runtime/processing.py)
- [Domain Runtime](../../src/universal_agent/domain/runtime.py)
- [Session rebuild](../../src/universal_agent/runtime/session.py)

### 2. Multi-Domain 与完成判定

上一轮审计中发现的两个问题已经修复：

- Domain Loader 现在拒绝空 evaluator；
- `finish()` 现在同时要求 EvaluationStatus completed、Task completed 和
  `goal_completed=True`；
- Observation processing 会依据执行 Action 所属的 Domain 选择 evaluator；
- 已增加双 Domain evaluator routing 示例和集成覆盖；
- Snapshot 会保存完整 Domain composition，并在恢复时校验；
- Profile 选择现在只接受绑定到当前 RuntimeService composition 的 Profile。

仍需注意：Evidence extractor、World updater、Task expander 目前仍是 composition-wide 合并，
尚未像 evaluator 一样具备完整的 owner/routing 语义。

### 3. P3.5–P3.7 本地产品化

已经提供：

- Runtime API、RuntimeService、agentd route adapter、标准库 HTTP bridge；
- CLI、pause/resume/cancel、cursor event reads、有限批次 SSE 输出；
- memory/file/SQLite Session/Event Store；
- metrics、Prometheus、cost、logs、traces、OTLP-shaped export、audit、doctor；
- doctor 的 State/Event consistency 检查，可发现 orphan event 和终态 Session 缺失终态 Event；
- Evaluation Harness、suite config、quality gate、report persistence、execution replay；
- deterministic runtime mode、golden replay、TUI、Web Console、Session Explorer。

这些接口已经形成稳定的本地应用边界，但不是生产服务的完整实现。

### 4. P6 本地分布式 Runtime 闭环

当前不再只是 queue view，已经具备本地执行适配：

- InMemory、File、SQLite WorkQueue；
- InMemory、File、SQLite Worker Registry；
- InMemory、File、SQLite leased lock；
- capability-aware worker leasing；
- async handler 运行期间自动 heartbeat queue lease 和 worker lease；
- session、goal、task、confirmed pending action 的调度；
- waiting Session、current Task、confirmed pending Action 的 Worker resume；
- scheduled Goal 的 Worker 执行；
- Runtime-owned pending Action sweep；
- session-scoped distributed execution lock；
- worker run-once 和 bounded worker-run；
- distributed snapshot、health、expiry、cancel，以及 agentd/CLI 入口。

因此当前已形成：

```text
RuntimeService
  → WorkScheduler
  → File/SQLite Queue
  → WorkQueueWorker
  → RuntimeAPI run/resume
  → Session/Event Store
```

但这是本地协调闭环，不是跨节点分布式系统。

### 5. P7 Ecosystem Registry

已经具备：

- Domain Package metadata registry、scaffold 和 dependency verification；
- Evaluation Dataset catalog 和 suite reference verification；
- Profile Catalog；
- Ecosystem Catalog、registry manifest、file-backed registry store；
- domain package install plan；
- full ecosystem install plan/result；
- CLI/示例中的 registry export、query、install、verify。

当前工作树还包含一组未提交的 Profile Catalog verification 改动：它们增加了 Profile
配置文件存在性/identity 检查和 `agent profile verify` 入口。由于这些改动尚未进入 HEAD，
本报告将其作为 working-tree 增量记录，不把它们视为已提交版本的稳定契约。

这些安装操作的语义是“校验并注册本地 metadata”，不会：

- import Domain entrypoint；
- 激活 DomainRuntime；
- 创建 RuntimeHost；
- 运行评估场景；
- 下载外部依赖；
- 验证签名或供应链完整性。

## 当前仍存在的主要边界

### 高优先级：State/Event 仍非原子提交

Session Snapshot 与 Runtime Event 由不同 Store 分别写入。SQLite 虽然提供 CAS 和独立事务，
但没有跨 Session/Event 的统一 transaction 或 outbox。进程在“保存状态”和“写事件”之间崩溃时，
可能产生 projection gap；Doctor 可以发现部分断裂，但不能自动修复。

建议下一步定义一种明确策略：

```text
同一事务提交
或
Transactional outbox → event publisher → projection verification
```

### 高优先级：P6 仍是本地协调，不是 HA

File adapter 依赖本机 advisory file lock，SQLite adapter 依赖本机数据库事务。它们适合：

- 单机多进程实验；
- 本地 RuntimeHost 重启恢复；
- 队列/租约语义测试。

它们不提供：

- 网络 Worker；
- 共识协议；
- 跨节点租约时钟协调；
- 高可用 leader election；
- 跨节点队列/锁/worker registry 一致性。

### 高优先级：真实外部执行尚未接入

- Kubernetes 仍使用注入的 fake backend；
- Model 层提供 Protocol 和 Scripted adapter，但没有绑定具体模型 SDK；
- Tool 参数目前主要是 required key 校验，没有完整 schema、权限 token 和资源版本策略；
- P7 metadata registry 不执行 entrypoint，不负责环境安装。

### 中高优先级：World Model 仍以 Fact Projection 为主

`WorldEntity` 和 `WorldRelation` 类型已定义，但默认 `InMemoryWorldModel` 的主要落地仍是
subject/claim/value fact。跨 Domain entity identity、relation merge、冲突解决和图查询尚未完成。

### 中高优先级：配置、Secrets 与 agentd 安全

Runtime environment 是通用 JSON，并可通过 config/API/Web Console 暴露。当前尚未有：

- secrets 与普通配置分离；
- 字段级敏感信息策略；
- agentd authentication/authorization；
- tenant isolation；
- TLS、审计不可篡改存储和限流。

接入真实凭据前必须先完成这些边界。

### 中优先级：事件流和取消

- `/events/stream` 目前是有限批次 SSE 格式，不是长期连接 push；
- cancellation 主要改变 Runtime 状态；in-flight tool cancellation 仍依赖 Tool/adapter 合约；
- 跨进程重复 Worker 执行虽然有 CAS、session lock、idempotency 和 lease，但还需要压力测试；
- resource version 当前主要作为 metadata 传递，尚未统一成为 backend 的乐观并发检查。

## 验证结果

本次在当前仓库执行：

```text
pytest --disable-warnings -r a   446 passed, 5 skipped
ruff format --check              失败：3 个文件需要格式化
ruff check                      失败：working-tree CLI import 未排序
mypy strict                     passed，214 source files
```

5 个跳过测试都与执行环境禁止本地 socket bind 有关。pytest 仍报告较多 Python 3.14 /
pytest-asyncio event loop 弃用警告；这些警告目前不影响测试结果，但应在依赖升级前处理。

需要格式化的文件：

- [cli.py](../../src/universal_agent/cli.py)
- [ecosystem/catalog.py](../../src/universal_agent/ecosystem/catalog.py)
- [evaluation/dataset.py](../../src/universal_agent/evaluation/dataset.py)

当前工作树额外的 Ruff import 问题位于 [cli.py](../../src/universal_agent/cli.py) 的 Profile import block。

## 建议的下一步

### P0：恢复质量门禁

1. 修复上述 3 个 Ruff format 文件；
2. 为 File/SQLite queue、lock、worker registry 增加跨进程竞争与崩溃恢复测试；
3. 为 State/Event consistency 增加故障注入和自动恢复验证；
4. 对 `EcosystemRegistry` 增加路径逃逸、manifest tampering、checksum/signature policy 测试。

### P1：完善运行时一致性

1. 设计 State/Event 原子提交或 outbox；
2. 将 resource version 从 metadata 升级为可验证的 optimistic concurrency contract；
3. 为 extractor/updater/expander 增加 Domain owner/routing；
4. 补充 pause/resume/cancel、unknown execution、重复提交和多 Worker 压力测试。

### P2：生产适配

1. 接入真实 Model Provider 和 Kubernetes API adapter；
2. 增加 agentd authentication、authorization、tenant isolation 和 secrets provider；
3. 将 Queue/Worker Registry/Lock 抽象到真正的网络/持久化后端；
4. 提供长连接 Event Stream 和正式 OpenTelemetry exporter。

Multi-Agent、复杂 Web 交互和更大范围的分布式部署仍应排在这些基础可靠性工作之后。

## 权威文档关系

- 工程约束：[AGENTS.md](../../AGENTS.md)
- 架构目标：[universal-agent-runtime-domain-runtime-design.md](../../universal-agent-runtime-domain-runtime-design.md)
- 操作说明：[README.md](../../README.md)
- 上一版状态快照：[2026-08-23-project-status.md](2026-08-23-project-status.md)
- 本文：2026-08-24 的最新实现审计快照
