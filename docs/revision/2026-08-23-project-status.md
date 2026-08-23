# 项目当前状态与后续边界

- 记录时间：2026-08-23 CST
- 依据：当前仓库源码、README、架构设计文档、测试与示例
- 目的：记录实现成熟度与未完成边界，不替代 `AGENTS.md` 或架构设计文档

## 一句话定位

本项目已经是一个具备完整单 Agent 语义循环的 Runtime foundation，而不是简单的
`Prompt → LLM → Tool` 包装器。它目前适合本地嵌入、架构验证、Domain 开发和回归评估；
尚不应被描述为已经具备生产级分布式执行、真实 Kubernetes 运维或完整生态安装能力的平台。

## 已落地能力

### P0–P3：运行时语义

核心执行路径已经由 Runtime 控制：

```text
Goal
 → Task
 → Context
 → Decision
 → Capability Resolution
 → Policy
 → Tool
 → Observation
 → Evidence
 → World Model
 → Task Expansion
 → Evaluation
 → Continue / Recover / Finish
```

当前已经具备：

- typed Goal、Task、Decision、Action、Observation、Evidence 和 Evaluation 合约；
- Runtime-owned state 与 bounded iteration/recovery；
- Capability 与 Tool 分离，以及 Allow/Confirm/Deny 策略边界；
- SessionSnapshot、Task Graph、Evidence replay、World replay，以及 Session version/CAS；
- advisory Memory，不把 Memory 当作 Evidence 或完成信号；
- Domain Manifest、Domain Composition、Kubernetes fake-backed remediation；
- Domain Loader 空 evaluator 拒绝、按 action Domain 的 evaluator routing、
  `goal_completed` 显式完成门禁，以及损坏 SessionSnapshot 拒绝/归档失败状态；
- idempotency key、参数 hash、uncertain execution 和 side-effect resource lock。

相关实现入口：

- [AgentRuntime](../../src/universal_agent/runtime/agent.py)
- [ActionExecutor](../../src/universal_agent/runtime/actions.py)
- [ObservationProcessor](../../src/universal_agent/runtime/processing.py)
- [Domain Runtime / Composition](../../src/universal_agent/domain/runtime.py)
- [Session rebuild](../../src/universal_agent/runtime/session.py)

### P3.5–P3.7：本地产品化与评估

已经提供：

- Runtime API、RuntimeService、agentd route adapter 和标准库 HTTP bridge；
- CLI、pause/resume/cancel、cursor event reads 和有限批次 SSE 格式输出；
- memory/file/SQLite session 与 event store，且 session snapshot save 具备 version/CAS；
- metrics、Prometheus、cost、logs、traces、OTLP-shaped output、audit、doctor，
  以及 Doctor state/event consistency 检查；
- Evaluation Harness、suite file、quality gate、report recording、execution replay；
- deterministic runtime mode、golden replay、TUI、Web Console、Session Explorer。

这些接口主要是稳定的应用边界和本地验证面，尚不等于生产服务实现。

### P6：分布式运行时基础原语

当前已有本地内存、本地文件与 SQLite 队列/锁/Worker Registry 适配实现：

- WorkScheduler、InMemory/File/SQLite WorkQueue、WorkerLease、heartbeat、retry、expiry，
  以及 FileWorkQueue 版本/重复 work item 损坏文件拒绝和跨进程文件锁；
- RuntimeConfig.distributed_queue 与 RuntimeHost 组装 memory/file/SQLite work queue；
- RuntimeConfig.distributed_locks 与 RuntimeHost 组装 memory/file/SQLite leased lock registry；
- RuntimeConfig.distributed_workers 与 RuntimeHost 组装 memory/file/SQLite worker registry；
- capability-aware Worker leasing，以及长异步 handler 的 queue/worker lease heartbeat；
- RuntimeService 本地 queue → worker → RuntimeAPI 闭环，用于已存在且无需确认的 waiting session
  resume、当前 Task resume、已确认 pending Action resume、Runtime-owned pending Action sweep，
  以及新 Goal 的 scheduled execution；
- RuntimeHost file-backed coordination 闭环，覆盖 file-backed session/event store、queue、lock
  和 worker registry 跨 host rebuild 后继续执行 scheduled session；
- RuntimeHost SQLite-backed queue 闭环，覆盖 distributed_queue SQLite adapter 跨 host rebuild
  后保留 scheduled session；
- RuntimeHost SQLite-backed worker registry 闭环，覆盖 distributed_workers SQLite adapter 跨 host rebuild
  后保留 worker heartbeat/能力状态；
- session-scoped execution lock，Worker 在 resume waiting session、current Task 或 confirmed pending Action
  前会获取 `session/<session_id>` 锁，冲突时回到 retry 队列，成功或失败后释放锁；
- Worker Registry 以及 online/draining/offline/lost 状态和 file-backed worker 持久化/重载；
- leased distributed lock 以及 file-backed lock 持久化/重载；
- Runtime Snapshot、Health Report、Coordinator；
- agentd/CLI 的 distributed snapshot、health、schedule-session、schedule-task、schedule-action、schedule-pending-actions、schedule-goal、worker、lock、expire 视图，
  以及 CLI worker-run-once / bounded worker-run 本地执行入口。

P6 当前遵循设计文档的“先做 local primitives”策略。它已有本地 waiting session resume
闭环、当前 Task resume 闭环、已确认 pending Action resume 闭环、Runtime-owned pending Action sweep、session-scoped execution lock、新 Goal scheduled execution 闭环、file-backed queue/lock/worker adapters，以及 SQLite-backed queue/lock/worker adapters，但还没有跨进程 Worker
编排、网络协议或跨节点一致性。

### P7：生态元数据基础

当前已有 Domain Package、Evaluation Dataset、Profile 和 Ecosystem Catalog 的元数据
发现/校验/脚手架基础。`EcosystemCatalog.verify()` 已能校验 Profile Domain、Evaluation
Dataset Domain 和 Domain Package dependency 引用是否能在本地生态索引中闭合。
`plan_ecosystem_install()` / `install_ecosystem()` 已能把 registry manifest 中引用的 Domain
Package、Evaluation Dataset 和 Profile config 校验后注册到本地 metadata registries。它们不会自动导入
Domain 代码、激活 Runtime、执行评估或安装外部依赖。

## 已知边界与风险

以下项目应视为当前明确的工程边界，而不是隐藏能力：

1. **Multi-Domain 仍是 composition foundation。** Observation processing 已按执行 action 所属
   Domain 选择 evaluator；但 extractor、updater 和 expander 尚未具备完整的 owner/routing 语义。
2. **World Model 主要是事实模型。** `WorldEntity`/`WorldRelation` 类型已存在，但默认内存实现
   仍以 fact projection 为主，跨 Domain 图推理尚未完成。
3. **P6 不是网络分布式系统。** Queue、Lock 和 Worker Registry 已有本地 file-backed 与 SQLite-backed adapter；
   Coordinator 仍是本地 primitive；没有网络协议、真正的 worker 进程编排或跨节点一致性。
4. **SQLite 不是生产持久化层。** 它提供本地 durable adapter，但没有 schema migration、跨 Store
   事务、outbox、租户隔离或高并发写入策略。
5. **Event Stream 仍是有限批次。** `events/stream` 输出 SSE 格式批次，不是长连接 push stream。
6. **Profile 请求选择是单 Runtime 语义。** agentd 现在会拒绝未绑定到当前 RuntimeService
   Domain composition 的 Profile；Profile 仍不会自动切换 Model、Domain、Policy 或路由到另一个 Runtime。
7. **配置展示必须视为敏感边界。** Runtime environment 目前是通用 JSON；接入真实凭据前必须
   增加 secrets 分离、脱敏、认证和授权。
8. **真实外部集成尚未接入。** Kubernetes 使用注入的 fake backend，Model 层只有 Protocol/
   scripted boundary，运行时没有绑定具体模型 SDK。
9. **状态和事件没有跨 Store 原子提交。** Doctor 已能检测 orphan events 与终态 Session
   缺少匹配终态 Event 的断裂；但进程在 Snapshot 保存与 Event 写入之间崩溃时，仍需要后续
   transaction/outbox 策略恢复。
10. **取消与并发仍需强化。** 当前 cancellation 主要改变 Runtime 状态；in-flight tool cancellation、
    Session CAS/version 和跨进程重复 Worker 执行还需要完整测试与实现。

## 验证快照

在本次审计环境中执行的结果：

```text
pytest -q                      428 passed, 5 skipped
ruff format --check           passed, 211 files checked
ruff check                    passed
mypy (strict)                 passed, 211 source files
```

被跳过的测试是本地 socket bind 受到执行环境权限限制。测试运行还报告了较多 Python 3.14 /
pytest-asyncio event loop 弃用警告；这些警告尚未影响当前测试结果，但后续应在依赖升级前处理。

## 推荐推进顺序

### P0：正确性门禁已压实

- 空 evaluator、`goal_completed`、双 Domain evaluator routing、损坏 Snapshot、agentd Profile 单 Runtime 语义均已覆盖。

### P1：完成单 Agent Runtime 的可靠性

- 继续定义 State/Event 原子性策略（transaction、outbox 或恢复流程）；
- 压实 SQLiteWorkQueue / SQLiteDistributedLockRegistry / SQLiteWorkerRegistry 的 lease 竞争、重复执行保护与恢复边界；
- 覆盖 pause/resume/cancel、lease expiry、unknown execution 和重复执行场景。

### P2：再接通 P6 执行闭环

```text
AgentRuntime
  → WorkScheduler
  → Persistent Queue
  → Worker
  → AgentRuntime run/resume/settle
  → Session + Event Store
```

当前已接通本地 memory/file/SQLite queue 上的 waiting session resume、current Task resume、confirmed pending Action resume、Runtime-owned pending Action sweep、session-scoped execution lock 与 scheduled Goal execution；
后续应继续补持久队列 lease 竞争、跨进程重复执行保护和 state/event 原子性恢复。

### P3：生产安全与外部适配

- secrets 分离、agentd 认证、授权和租户隔离；
- 真实 Model Provider 与 Kubernetes API adapter；
- 持久化 Queue/Worker Registry、schema migration 和操作审计；
- 真正的长连接 Event Stream 与 OpenTelemetry exporter。

Multi-Agent、复杂 UI 和更大范围的分布式部署应继续排在上述可靠性工作之后。

## 文档权威关系

- 工程约束：[`AGENTS.md`](../../AGENTS.md)
- 架构目标：[`universal-agent-runtime-domain-runtime-design.md`](../../universal-agent-runtime-domain-runtime-design.md)
- 操作入口和当前实现说明：[`README.md`](../../README.md)
- 本文：当前实现状态、边界和推进顺序的审计快照
