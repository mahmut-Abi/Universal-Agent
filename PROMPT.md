# Codex 开发 Prompt

你现在是这个项目的主程/架构负责人。

项目目标：

> 构建一个 Universal Agent Runtime + Pluggable Domain Runtime。

我已经将完整设计文档放在项目目录：

`universal-agent-runtime-domain-runtime-design.md`

同时项目根目录存在：

`AGENTS.md`

## 你的第一原则

先阅读：

1. `AGENTS.md`
2. `universal-agent-runtime-domain-runtime-design.md`
3. 当前仓库所有已有源码、README、配置和测试

不要直接开始写代码。

你需要先理解当前仓库实际状态，再决定下一步。

---

## 核心架构必须保持

```text
Goal
 ↓
State
 ↓
World Model
 ↓
Current Task
 ↓
Context Compiler
 ↓
Decision
 ↓
Policy Check
 ↓
Action
 ↓
Observation
 ↓
Evidence
 ↓
State / World Model Update
 ↓
Evaluation
 ↓
Continue / Recover / Ask User / Finish
```

核心原则：

- LLM 不拥有 State
- LLM 不拥有 Control Flow
- LLM 不拥有 Policy
- Tool != Capability
- Tool Success != Task Success
- World Model != Knowledge Base
- Domain != Prompt
- Domain 不允许通过修改 Kernel 的 if/elif 来实现
- 不允许依赖静态 Mega Planner
- Task 必须允许动态扩展
- Agent 必须通过 Observation / Evidence / Evaluator 判断任务是否完成

---

# 第一阶段：只做架构审计

完成以下工作：

1. 查看仓库目录结构。
2. 找到现有 Agent Runtime、Model、Tool、State、Prompt、Memory、Workflow 等实现。
3. 判断当前代码与设计文档的对应关系。
4. 找出已经存在的可复用模块。
5. 找出明显违反架构原则的地方。
6. 判断当前项目最适合从 P0 的哪个位置开始。
7. 检查测试现状。

不要在这一阶段进行大规模重构。

先输出一份简短的：

```text
Architecture Audit

Current Architecture:
...

Reusable Components:
...

Missing Components:
...

Architectural Risks:
...

Recommended P0:
...

Implementation Order:
...
```

然后再开始开发。

---

# 第二阶段：实现 P0

目标：

实现一个最小但完整的 Agent Loop：

```text
Goal
 ↓
Task
 ↓
Decision
 ↓
Tool
 ↓
Observation
 ↓
State
 ↓
Decision
```

至少具备：

- Goal
- Task
- State
- Decision
- Model Adapter
- Tool Registry
- Tool Runtime
- Observation
- Agent Loop
- Session / Execution ID
- 基础 Context

不要在 P0 加入复杂 Memory、Multi-Agent、Marketplace、Graph DB 等东西。

---

# 第三阶段：实现 P1

加入：

- Domain Manifest
- Domain Runtime
- Capability
- Policy Engine
- Evaluator
- Context Compiler

最终必须做到：

```text
Universal Runtime
        +
Kubernetes Domain
```

而不修改 Universal Kernel 的领域逻辑。

---

# 第四阶段：Kubernetes Domain

使用 Kubernetes 作为第一个完整 Domain。

至少提供：

```text
Ontology:
    Cluster
    Node
    Namespace
    Pod
    Deployment
    Service

Capabilities:
    inspect_cluster
    inspect_workload
    inspect_pod
    inspect_logs
    inspect_events
    deploy_workload
    rollout_restart

Tools:
    kubectl
```

至少完成一个真实场景：

> 找出一个 Deployment / Pod 不健康的原因，并在安全范围内进行修复，然后验证修复结果。

必须形成：

```text
Observation
+
Evidence
+
Decision
+
Action
+
Verification
```

---

# 开发要求

## 1. 小步提交式开发

每完成一个逻辑阶段：

- 运行测试
- 检查类型
- 检查 lint
- 检查 import
- 检查架构边界

不要一次写几千行代码然后最后才测试。

## 2. 优先复用

如果仓库已有：

- Model abstraction
- Tool abstraction
- Event system
- State store
- Configuration system
- Logging
- Testing utilities

优先扩展，而不是重新造轮子。

## 3. 类型安全

优先使用明确的数据结构：

```text
Goal
Task
Decision
Observation
Evidence
EvaluationResult
PolicyResult
```

避免核心路径大量使用：

```python
dict[str, Any]
```

## 4. 可观察性

所有重要执行阶段必须能够追踪：

```text
session_id
goal_id
task_id
action_id
```

至少能够回答：

```text
Agent 为什么做这个决定？
当时看到了什么？
调用了什么 Tool？
Tool 返回了什么？
为什么认为成功？
为什么继续？
为什么停止？
```

## 5. 安全

任何 mutation tool 都必须经过 Policy Engine。

不要让 LLM 自己决定：

```text
是否允许删除
是否允许修改生产环境
是否需要确认
```

---

# Decision Contract

模型必须返回结构化 Decision。

推荐：

```json
{
  "type": "execute",
  "capability": "inspect_pod",
  "target": "pod/example",
  "arguments": {},
  "reason": "Need pod logs to diagnose restart",
  "expected_observations": [
    "exit_code",
    "container_state",
    "recent_logs"
  ]
}
```

Runtime 必须：

```text
validate
→ policy check
→ capability resolve
→ tool resolve
→ execute
```

不能直接：

```text
LLM → Tool
```

---

# 完成标准

每一个 Agent 行为都要考虑：

### 正常路径

```text
Goal
→ Decision
→ Action
→ Observation
→ Evaluation
→ Complete
```

### Tool 失败

```text
Action
→ Failure
→ Recovery
```

### Policy 拒绝

```text
Decision
→ Policy
→ Deny
```

### 需要用户确认

```text
Decision
→ Policy
→ Confirmation
→ Pause
```

### Action 成功但结果不正确

```text
Action Success
→ Health Check Failed
→ Diagnosis
→ Recovery
```

---

# 不要做的事情

除非我明确要求，否则不要：

- 重写整个项目
- 引入复杂 Multi-Agent
- 创建大量 Subagent
- 创建静态 Mega Planner
- 用 Prompt 模拟 State
- 用 RAG 模拟 World Model
- 让 LLM 绕过 Policy
- 为每个领域创建一个独立 Agent Core
- 为了“看起来高级”而引入 Graph DB / Vector DB
- 添加没有实际使用场景的抽象层
- 在没有测试的情况下进行大规模重构

---

# 最终目标

最终架构应该接近：

```text
                 Universal Agent Kernel
                          |
                 +--------+--------+
                 |                 |
              Runtime         Agent Profile
                 |                 |
       +---------+---------+       |
       |         |         |       |
     State    Decision   Context   |
       |         |         |       |
       +---------+---------+       |
                 |                 |
             World Model           |
                 |                 |
             Domain Runtime <------+
                 |
       +---------+---------+---------+
       |         |         |         |
     Tools   Knowledge  Policies  Evaluators
```

最终目标不是：

> 做一个 Kubernetes Agent。

而是：

> 做一个 Universal Agent Runtime，使 Kubernetes 只是第一个 Domain Package。

以后应该可以做到：

```text
Universal Runtime
+
Kubernetes Domain
=
Kubernetes Agent

Universal Runtime
+
Coding Domain
=
Coding Agent

Universal Runtime
+
Research Domain
=
Research Agent

Universal Runtime
+
Kubernetes
+
Dify
+
Observability
=
AI Infrastructure Operator
```

---

## 现在开始

第一步不要写代码。

先：

1. 阅读 `AGENTS.md`
2. 阅读 `universal-agent-runtime-domain-runtime-design.md`
3. 审计当前仓库
4. 给出 Architecture Audit
5. 确定 P0 实施计划
6. 再开始编码

整个开发过程中，优先保证：

> **架构边界 > 功能数量 > Demo 效果。**

以及：

> **先让 Agent Loop 正确，再让 Agent 变强。**
