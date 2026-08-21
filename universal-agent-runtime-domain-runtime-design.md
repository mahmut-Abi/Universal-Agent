# Universal Agent Runtime + Pluggable Domain Runtime

## 1. 文档概述

### 1.1 项目目标

构建一个面向通用任务的 Agent Runtime，通过可插拔的 Domain Runtime / Agent Specification，使同一个 Agent Kernel 能够快速获得某个领域的专业能力。

核心思想：

> Agent Kernel 负责“如何工作”，Domain Runtime 负责“在哪个世界里工作”。

目标不是构建大量垂直 Agent，而是构建一个稳定的 Universal Agent Kernel，通过 Domain Package 动态获得 Kubernetes、Coding、Research、Database、Browser、DevOps、AI Operations 等领域能力。

### 1.2 核心公式

```text
Universal Agent
    =
Agent Kernel
+
Domain Runtime
+
Model
+
Tools
+
Knowledge
+
Policy
+
Evaluator
```

进一步抽象：

```text
Harness        = 怎么运行
Decision       = 怎么决策
Domain         = 在什么世界里工作
Model          = 怎么推理
Capability     = 能做什么
Tool           = 怎么执行
Policy         = 什么能做、什么不能做
World Model    = 当前世界是什么状态
Evidence       = 为什么相信当前状态
Evaluator      = 怎么判断任务是否完成
```

---

# 2. 设计原则

## 2.1 Runtime 与 Domain 解耦

Agent Kernel 不应该知道 Kubernetes、GitHub、数据库等具体领域知识。

错误：

```text
if domain == "kubernetes":
    ...
elif domain == "coding":
    ...
```

正确：

```text
Universal Runtime
        |
        +-- Domain Interface
                |
                +-- Kubernetes Domain
                +-- Coding Domain
                +-- Research Domain
```

## 2.2 LLM 不拥有 Runtime State

LLM 可以提出决策，但不能成为事实状态的唯一存储。

```text
LLM
  |
  | Decision
  v
Runtime
  |
  +-- State
  +-- World Model
  +-- Evidence
  +-- Policy
```

## 2.3 LLM 不拥有 Control Flow

模型不能决定：

- 无限循环
- 重试次数
- 超时
- 是否允许危险操作
- 是否需要用户确认
- Recovery 状态转换

这些属于 Runtime。

## 2.4 Tool 不等于 Capability

Tool 是具体实现。

Capability 是 Agent 的抽象能力。

例如：

```text
Capability:
    inspect_pod

Tools:
    kubectl_get_pod
    k8s_api_get_pod
```

Runtime 应该首先进行 Capability Selection，然后再选择 Tool。

## 2.5 Tool Success 不等于 Task Success

```text
Action
  |
  v
Tool Result
  |
  v
Observation
  |
  v
Evidence
  |
  v
Evaluator
  |
  v
Task Result
```

## 2.6 Domain Profile 不应该只是 System Prompt

Domain 必须结构化描述：

```text
Ontology
Capabilities
Tools
Knowledge
Procedures
Policies
Constraints
Evaluators
Prompt Fragments
```

---

# 3. 总体架构

```text
                         User
                           |
                           v
                 +---------------------+
                 |    Agent Gateway    |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | Universal Agent     |
                 | Runtime              |
                 +----------+----------+
                            |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
+--------------+    +---------------+    +---------------+
| Goal Manager |    | Context       |    | Session       |
|              |    | Compiler      |    | Manager       |
+--------------+    +---------------+    +---------------+
       |                    |                    |
       +--------------------+--------------------+
                            |
                            v
                 +---------------------+
                 | Decision Engine     |
                 +----------+----------+
                            |
                 +----------+----------+
                 |                     |
                 v                     v
        +----------------+    +----------------+
        | Policy Engine  |    | Capability     |
        |                |    | Resolver       |
        +-------+--------+    +-------+--------+
                |                     |
                +----------+----------+
                           |
                           v
                 +---------------------+
                 | Action Runtime      |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | Tool / Environment  |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | Observation        |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | Evidence System     |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | World Model / State |
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 | Evaluator           |
                 +----------+----------+
                            |
                            +------> Continue
                            +------> Recover
                            +------> Ask User
                            +------> Finish
```

---

# 4. 核心模块

## 4.1 Agent Kernel

Agent Kernel 是整个系统中最稳定的部分。

建议包含：

```text
agent-kernel/
├── goal/
├── state/
├── world_model/
├── decision/
├── action/
├── observation/
├── evidence/
├── policy/
├── recovery/
├── context/
├── memory/
├── evaluation/
└── session/
```

Kernel 不允许直接依赖具体 Domain。

---

# 5. Goal System

## 5.1 Goal

Goal 表示用户真正想完成的目标。

```yaml
goal:
  id: goal-001
  description: "排查生产环境 Dify API Pod 重启问题"
  success_criteria:
    - pod_running: true
    - restart_count_stable: true
    - root_cause_identified: true
```

Goal 不应该直接变成完整 Task Tree。

## 5.2 Goal Compiler

Goal Compiler 负责：

1. 提取用户意图
2. 提取约束
3. 提取成功标准
4. 识别潜在领域
5. 生成初始 Goal Model

输出：

```text
Goal
Constraints
Success Criteria
Domain Candidates
Risk Level
```

---

# 6. World Model

World Model 是 Domain Agent 的核心。

## 6.1 基础结构

```text
World Model
├── Entities
├── Relations
├── Facts
├── State
├── Artifacts
├── Events
└── Evidence References
```

例如 Kubernetes：

```text
Entity:
    Pod/dify-api-xxx

Attributes:
    namespace = dify
    status = CrashLoopBackOff
    restartCount = 17

Relations:
    Pod -> Deployment
    Pod -> Node
    Pod -> ConfigMap
    Pod -> Secret
```

## 6.2 World Model 不等于数据库

World Model 是 Agent 当前认知中的世界。

底层可以使用：

- PostgreSQL
- SQLite
- Redis
- Graph DB
- Document Store
- Vector DB

但上层必须提供统一接口。

```python
class WorldModel:
    def get_entity(self, entity_id): ...
    def query(self, query): ...
    def update(self, observation): ...
    def relate(self, source, relation, target): ...
```

---

# 7. Domain Runtime

Domain Runtime 是整个项目的可扩展核心。

推荐接口：

```text
DomainRuntime
├── Manifest
├── Ontology
├── Capabilities
├── Tools
├── Knowledge
├── Procedures
├── Policies
├── Evaluators
└── Context Provider
```

## 7.1 Domain Runtime 生命周期

```text
Load
  |
Validate
  |
Register
  |
Activate
  |
Provide Context
  |
Provide Capabilities
  |
Provide Policies
  |
Provide Evaluators
  |
Unload
```

---

# 8. Domain Manifest

建议定义标准 manifest：

```yaml
apiVersion: agent.nantian.dev/v1alpha1
kind: Domain

metadata:
  name: kubernetes
  version: 1.0.0
  description: Kubernetes operations domain

spec:

  ontology:
    resources:
      - Cluster
      - Node
      - Namespace
      - Pod
      - Deployment
      - Service
      - Gateway
      - HTTPRoute

  capabilities:
    - name: inspect_cluster
      risk: low
    - name: inspect_workload
      risk: low
    - name: deploy_workload
      risk: medium
    - name: delete_resource
      risk: high

  tools:
    - kubectl
    - helm
    - prometheus

  knowledge:
    - kubernetes-concepts
    - internal-runbooks

  procedures:
    - crashloopbackoff
    - imagepullbackoff
    - node-notready

  policies:
    - production-safety

  evaluators:
    - workload-health
    - service-connectivity
```

---

# 9. Ontology

Ontology 描述 Domain 中有哪些实体以及它们如何关联。

```yaml
ontology:
  entities:

    Pod:
      attributes:
        - name
        - namespace
        - phase
        - restartCount

    Deployment:
      attributes:
        - name
        - namespace
        - replicas
        - availableReplicas

  relations:

    - source: Pod
      relation: owned_by
      target: ReplicaSet

    - source: ReplicaSet
      relation: controlled_by
      target: Deployment
```

Ontology 的用途：

1. World Model
2. Context Retrieval
3. Capability Resolution
4. Evidence Linking
5. Evaluator
6. Task Expansion

---

# 10. Capability System

Capability 是 Agent 的抽象能力。

```yaml
capabilities:

  - name: inspect_pod
    category: observation
    risk: low

  - name: modify_deployment
    category: mutation
    risk: medium

  - name: delete_pod
    category: mutation
    risk: high
```

Capability 不直接绑定单个 Tool。

例如：

```text
inspect_pod
    |
    +-- kubectl
    +-- Kubernetes API
```

Runtime 可以根据：

- Tool availability
- Tool cost
- Tool reliability
- Tool permissions
- Domain preference

选择具体 Tool。

---

# 11. Tool System

Tool 描述：

```yaml
tool:
  name: kubectl_get_pod

  capability:
    - inspect_pod

  input_schema:
    ...

  side_effect:
    none

  risk:
    low

  timeout:
    10s
```

Tool Runtime 必须统一处理：

```text
Validation
Authorization
Timeout
Retry
Cancellation
Execution
Result Normalization
Audit
```

---

# 12. Policy Engine

Policy 必须独立于 LLM。

例如：

```yaml
policy:
  name: production-safety

rules:

  - action: namespace.delete
    environment: production
    effect: deny

  - action: deployment.delete
    environment: production
    effect: require_confirmation

  - action: deployment.restart
    environment: production
    effect: allow
```

Decision Pipeline：

```text
LLM Decision
      |
      v
Policy Engine
      |
+-----+-----+
|     |     |
Allow Confirm Deny
```

---

# 13. Decision Engine

Decision Engine 是 Agent 的“大脑接口”。

但它不应该直接控制 Runtime。

推荐：

```text
Decision Request
        |
        v
Context Compiler
        |
        v
LLM
        |
        v
Structured Decision
```

Decision Schema：

```json
{
  "decision": "execute",
  "capability": "inspect_pod",
  "target": "pod/dify-api-123",
  "reason": "Pod is restarting and logs are required to identify the failure",
  "expected_observation": [
    "container_exit_code",
    "recent_logs"
  ]
}
```

LLM 只能返回结构化 Decision。

Runtime 决定是否执行。

---

# 14. Decision Pipeline

建议至少包含：

```text
1. Goal Check
2. State Check
3. World Model Check
4. Candidate Capability Generation
5. Context Compilation
6. LLM Decision
7. Policy Validation
8. Action Scheduling
```

详细：

```text
Goal
 ↓
Current Task
 ↓
Relevant World State
 ↓
Relevant Evidence
 ↓
Available Capabilities
 ↓
Candidate Actions
 ↓
LLM
 ↓
Structured Decision
 ↓
Policy
 ↓
Execute
```

---

# 15. Task System

Task 是短生命周期执行单元。

不要建立巨大的静态 Task Tree。

推荐：

```text
Goal
 |
 +-- Current Task
       |
       +-- Decision
       |
       +-- Action
       |
       +-- Observation
       |
       +-- Result
```

Task 状态：

```text
PENDING
RUNNING
WAITING
BLOCKED
FAILED
RECOVERING
COMPLETED
CANCELLED
```

---

# 16. Dynamic Task Expansion

Task 可以根据 World Model 动态产生。

例如：

```text
Goal:
Deploy Dify
```

初始：

```text
Task:
inspect_environment
```

Observation：

```text
PostgreSQL exists
Redis exists
Object Storage missing
```

动态扩展：

```text
resolve_object_storage
deploy_dify
verify_dify
```

因此：

```text
Task Graph = Dynamic
```

而不是：

```text
Task Graph = LLM 一次性生成
```

---

# 17. Observation System

每次 Action 必须产生 Observation。

```python
class Observation:
    id: str
    action_id: str
    timestamp: datetime
    source: str
    status: str
    raw_result: Any
    normalized_result: Any
```

例如：

```json
{
  "source": "kubernetes",
  "resource": "deployment/dify-api",
  "status": "healthy",
  "availableReplicas": 3,
  "desiredReplicas": 3
}
```

---

# 18. Evidence System

Observation 不一定直接成为事实。

需要 Evidence：

```text
Observation
    |
    v
Evidence
    |
    +-- source
    +-- timestamp
    +-- confidence
    +-- subject
    +-- claim
```

例如：

```json
{
  "subject": "pod/dify-api-123",
  "claim": "pod_is_unhealthy",
  "source": "kubernetes",
  "confidence": 0.99
}
```

多个 Evidence 可以共同支持一个结论。

```text
Pod Status
    +
Exit Code
    +
Logs
    +
Events
    ↓
Root Cause
```

---

# 19. Context Compiler

Context Compiler 是连接 Runtime 和 LLM 的关键模块。

绝对不要：

```text
all history
+
all tools
+
all knowledge
+
all logs
→ LLM
```

应该：

```text
Goal
+
Current Task
+
Relevant World Model
+
Relevant Evidence
+
Relevant Memory
+
Candidate Capabilities
+
Relevant Policies
+
Recent Actions
→ LLM
```

Context Compiler 应支持：

```text
Token Budget
Relevance Ranking
Compression
Summarization
Deduplication
Evidence Selection
Tool Selection
```

---

# 20. Memory

建议分成：

```text
Memory
├── Episodic
├── Semantic
├── Procedural
└── Preference
```

### Episodic

发生过什么。

### Semantic

知道什么。

### Procedural

如何做。

### Preference

用户偏好。

Memory 不应自动全部进入 Context。

必须经过：

```text
Memory Retrieval
        |
        v
Relevance Filter
        |
        v
Context Compiler
```

---

# 21. Recovery System

Recovery 不应该完全由 LLM 自己实现。

定义 Recovery Strategy：

```text
Failure
 |
 +-- Retry
 +-- Re-observe
 +-- Diagnose
 +-- Alternative Capability
 +-- Rollback
 +-- Ask User
 +-- Escalate
```

例如：

```yaml
recovery:
  tool_timeout:
    strategy: retry
    max_attempts: 3

  permission_denied:
    strategy: ask_user

  health_check_failed:
    strategy: diagnose

  destructive_action_failed:
    strategy: stop
```

---

# 22. Evaluator

Evaluator 判断：

> 当前 Task / Goal 是否真的完成。

Evaluator 类型：

```text
Task Evaluator
Goal Evaluator
Action Evaluator
Safety Evaluator
Evidence Evaluator
```

例如：

```text
Goal:
Deploy Dify

Evaluator:

API endpoint = HTTP 200
Web endpoint = HTTP 200
Worker = healthy
Database connection = healthy
Redis connection = healthy
```

只有 Evaluator 通过：

```text
Goal = COMPLETED
```

而不是 LLM 说：

```text
应该已经完成了。
```

---

# 23. Agent Profile

在 Domain Runtime 之上，可以提供用户级 Agent Profile。

```yaml
apiVersion: agent.nantian.dev/v1alpha1
kind: AgentProfile

metadata:
  name: production-kubernetes-operator

spec:

  domains:
    - kubernetes
    - observability
    - linux

  model:
    provider: openai
    model: reasoning-model

  capabilities:
    allow:
      - inspect_cluster
      - inspect_workload
      - deploy_workload
      - rollout_restart

    deny:
      - namespace_delete

  policies:
    - production-safety

  knowledge:
    - company-k8s-runbook

  preferences:
    package_manager: helm
    namespace: monitoring

  confirmation:
    destructive_action: required
```

Agent Profile 是：

> Domain + User Preferences + Model Policy + Runtime Policy 的组合。

---

# 24. 多 Domain 与 Domain Composition

多领域协作是基础能力，多 Agent 编排是高级能力。两者必须分开设计。

核心原则：

> Agent 是执行主体，Domain 是能力/知识边界，World Model 是共享现实，Task Contract 是 Agent 协作协议。

一个 Agent 可以同时激活多个 Domain。

```text
Agent
 |
 +-- Kubernetes
 |
 +-- Dify
 |
 +-- PostgreSQL
 |
 +-- GitHub
 |
 +-- Observability
```

这些 Domain 共享同一个：

```text
Goal
State
World Model
Decision Engine
Memory
Session
```

而不是互相创建 Agent。

也就是说，默认架构应该是：

```text
Universal Agent
      |
      v
Domain Composition
      |
      +-- Kubernetes Domain
      +-- Dify Domain
      +-- PostgreSQL Domain
      +-- Observability Domain
      |
      v
Shared World Model
```

Domain Composition 负责：

1. Domain Discovery：根据 Goal、当前 Task、World Model 和 Evidence 判断需要哪些 Domain。
2. Domain Activation：只激活当前任务需要的 Domain，避免把所有能力一次性塞进上下文。
3. Capability Merge：把多个 Domain 的 Capability 合并成统一候选能力集合。
4. Policy Merge：聚合 Runtime Policy、Profile Policy 和 Domain Policy，并按最保守规则处理冲突。
5. Context Contribution：让每个 Active Domain 只贡献与当前任务相关的 ontology、procedure、knowledge、policy 和 evidence 摘要。

最终 Decision Engine 看到的是：

```text
Relevant World Model
+
Relevant Evidence
+
Available Capabilities
+
Applicable Policies
```

而不是：

```text
Kubernetes Agent
+
Observability Agent
+
Database Agent
```

不要把 Domain 问题实现成 Agent Routing 问题。

---

# 25. Cross-Domain World Model

这是系统的高级能力。

例如：

```text
Dify Application
 |
 +-- Kubernetes Deployment
 |
 +-- PostgreSQL Database
 |
 +-- Redis
 |
 +-- GitHub Repository
 |
 +-- Prometheus Metrics
```

Agent 可以跨领域推理：

```text
Dify API Pod
    |
    v
Kubernetes
    |
    v
CPU throttling
    |
    v
Prometheus
    |
    v
High CPU
    |
    v
Application performance
```

这是真正的通用 Agent 能力。

跨领域 World Model 不应该拆成多个互相同步的局部 World Model。

错误：

```text
Kubernetes World Model
Dify World Model
Prometheus World Model
```

正确：

```text
Shared World Model
    |
    +-- Kubernetes Entity
    +-- Dify Entity
    +-- Observability Entity
    +-- Database Entity
```

例如：

```text
Dify API
    |
    +-- runs_on -> Deployment/dify-api
    +-- depends_on -> Redis
    +-- depends_on -> PostgreSQL

Deployment/dify-api
    |
    +-- has_pod -> Pod/dify-api-123

Pod/dify-api-123
    |
    +-- emits -> Log/xxx
    +-- measured_by -> Metric/xxx
```

这样 Agent 才能把：

```text
Pod CrashLoopBackOff
    -> Exit Code 137
    -> Memory metric spike
    -> OOM hypothesis
    -> Increase memory limit
    -> Rollout
    -> Verify
```

表示为同一个现实中的证据链，而不是跨 Agent 的聊天记录。

## 25.1 Optional Multi-Agent Runtime

Multi-Agent 不应该作为核心执行模型，也不应该用来替代 Domain Composition。

判断标准：

> Domain 是知识/能力边界，Agent 是自主执行边界。

如果只是以下差异，不需要多 Agent：

```text
不同知识
不同工具
不同 Ontology
不同 Procedure
不同 Policy
不同 Evaluator
```

这些都属于 Domain Runtime。

只有出现以下差异，才考虑 Multi-Agent：

```text
不同 Goal
不同 State
不同权限
不同生命周期
不同执行环境
不同自主循环
不同隔离要求
```

例如：

```text
Main Agent
    |
    +-- Coding Domain
    +-- Kubernetes Domain
    +-- GitHub Domain
```

适合单 Agent 多 Domain。

而：

```text
Orchestrator
    |
    +-- Coding Agent
    +-- Security Audit Agent
```

只有在 Coding Agent 与 Security Audit Agent 有独立目标、独立上下文、独立权限和独立评估标准时才合理。

未来 Multi-Agent 应作为 Runtime Primitive 预留接口，但不要在早期实现完整编排框架。

Agent 之间通信必须使用结构化 Task Contract，而不是互相传聊天记录：

```json
{
  "task_id": "task-123",
  "goal": "audit deployment security",
  "input": {
    "resource": "deployment/foo"
  },
  "constraints": {
    "read_only": true
  },
  "expected_output": {
    "type": "security_report"
  }
}
```

返回：

```json
{
  "task_id": "task-123",
  "status": "completed",
  "result": {
    "risk_level": "medium",
    "findings": []
  },
  "evidence": [
    "evidence-123",
    "evidence-456"
  ]
}
```

核心要求：

- Agent 协作传递 Task、Result、Evidence，不传自由聊天上下文。
- 被调用 Agent 的完成状态必须由它自己的 Evaluator 产生。
- 调用方只能把返回内容作为 Evidence / Result 输入，不能把被调用 Agent 的 prose 当作事实。
- Policy 必须分别在调用方和被调用方执行，不能由 Orchestrator 的 prompt 替代。
- Multi-Agent 层不得向 Kernel 引入具体 Domain 分支。

---

# 26. Domain Package

建议 Domain 最终可以打包：

```text
kubernetes-domain/
├── manifest.yaml
├── ontology/
│   └── resources.yaml
├── capabilities/
│   └── capabilities.yaml
├── tools/
│   ├── kubectl.yaml
│   └── helm.yaml
├── policies/
│   └── production.yaml
├── procedures/
│   ├── crashloopbackoff.yaml
│   ├── imagepullbackoff.yaml
│   └── node-notready.yaml
├── knowledge/
│   └── index.yaml
├── evaluators/
│   └── workload-health.py
└── prompts/
    └── domain.yaml
```

---

# 27. Domain SDK

开发者不应该直接修改 Kernel。

提供 SDK：

```python
from agent_sdk import Domain

class KubernetesDomain(Domain):

    def manifest(self):
        return ...

    def ontology(self):
        return ...

    def capabilities(self):
        return ...

    def tools(self):
        return ...

    def policies(self):
        return ...

    def evaluators(self):
        return ...
```

安装：

```bash
agent domain install kubernetes-domain
```

激活：

```bash
agent profile create production-k8s \
    --domain kubernetes \
    --domain observability
```

---

# 28. 推荐仓库结构

```text
universal-agent/
│
├── kernel/
│   ├── goal/
│   ├── state/
│   ├── world_model/
│   ├── decision/
│   ├── action/
│   ├── observation/
│   ├── evidence/
│   ├── policy/
│   ├── recovery/
│   ├── context/
│   ├── memory/
│   ├── evaluation/
│   └── session/
│
├── runtime/
│   ├── scheduler/
│   ├── event_bus/
│   ├── executor/
│   └── lifecycle/
│
├── domain-sdk/
│
├── domains/
│   ├── kubernetes/
│   ├── coding/
│   ├── browser/
│   └── research/
│
├── model/
│   ├── providers/
│   ├── routing/
│   └── structured_output/
│
├── storage/
│   ├── state/
│   ├── world_model/
│   ├── memory/
│   └── evidence/
│
├── api/
│
├── cli/
│
├── tests/
│   ├── kernel/
│   ├── domains/
│   ├── integration/
│   └── evaluation/
│
└── examples/
```

---

# 29. Runtime API

核心接口建议：

```python
class AgentRuntime:

    async def create_session(
        self,
        profile: AgentProfile
    ) -> Session:
        ...

    async def submit_goal(
        self,
        session_id: str,
        goal: Goal
    ) -> GoalExecution:
        ...

    async def step(
        self,
        session_id: str
    ) -> DecisionResult:
        ...

    async def resume(
        self,
        session_id: str
    ):
        ...

    async def cancel(
        self,
        session_id: str
    ):
        ...
```

---

# 30. Decision API

```python
class DecisionEngine:

    async def decide(
        self,
        context: DecisionContext
    ) -> Decision:
        ...
```

Decision 必须结构化：

```python
class Decision:

    type: Literal[
        "execute",
        "wait",
        "ask_user",
        "finish",
        "recover"
    ]

    capability: str | None

    arguments: dict

    reason: str

    expected_observations: list[str]
```

---

# 31. Agent Loop

核心 Loop：

```python
while not goal.completed:

    state = state_store.load()

    world = world_model.snapshot()

    task = task_manager.current()

    context = context_compiler.build(
        goal=goal,
        state=state,
        world=world,
        task=task
    )

    decision = decision_engine.decide(context)

    policy_result = policy_engine.check(decision)

    if policy_result.denied:
        recovery.handle(policy_result)
        continue

    result = action_runtime.execute(decision)

    observation = observer.observe(result)

    evidence = evidence_system.extract(observation)

    world_model.update(evidence)

    state_store.update(observation)

    evaluation = evaluator.evaluate(
        goal,
        task,
        world_model
    )

    if evaluation.completed:
        task_manager.complete(task)

    elif evaluation.failed:
        recovery.handle(evaluation)

    else:
        task_manager.expand(world_model)
```

这段代码应该尽可能保持简单。

复杂度应该被隔离到各个模块。

---

# 32. MVP

第一阶段不要一次实现全部能力。

建议 MVP：

```text
Phase 1
├── Agent Runtime
├── Goal
├── State
├── Task
├── Decision
├── Tool
├── Observation
└── Basic Context
```

实现最小 Loop：

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

---

# 33. Phase 2：Domain Runtime

加入：

```text
Domain Manifest
Ontology
Capability
Policy
Evaluator
Domain Composition interface
```

首先只做一个 Domain：

```text
Kubernetes
```

验证：

```text
Universal Runtime
+
Kubernetes Domain
```

是否真的能够完成：

```text
inspect cluster
diagnose pod
deploy workload
verify workload
```

这一阶段只需要预留 Domain Composition 的接口边界，不需要实现多 Domain 调度。

---

# 34. Phase 3：World Model

加入：

```text
Entity
Relation
Fact
Evidence
Artifact
Event
Cross-domain entity/relation schema
```

并让 Decision Engine 开始依赖 World Model。

World Model 从一开始就应该允许不同 Domain 贡献 Entity、Relation 和 Evidence，但早期实现可以只激活 Kubernetes Domain。

---

# 35. Phase 4：Recovery

加入：

```text
Retry
Diagnosis
Alternative Action
Rollback
Ask User
Escalation
```

测试真正的失败场景。

---

# 36. Phase 5：Context / Memory

加入：

```text
Context Compiler
Semantic Memory
Episodic Memory
Procedural Memory
Compression
```

解决长任务。

---

# 37. Phase 6：Multi-Domain

加入 Domain Composition 的完整实现：

```text
Domain Discovery
Domain Activation
Capability Merge
Policy Merge
Context Contribution
Shared World Model updates
```

测试：

```text
Kubernetes
+
Observability
+
Database
```

以及：

```text
Coding
+
GitHub
+
Browser
```

重点测试 Cross-Domain World Model。

这一阶段仍然是一个 Agent 加载多个 Domain，不引入 Agent Router、Supervisor Agent 或多 Agent 通信协议。

---

# 38. Phase 7：Domain Marketplace / SDK

最终允许：

```text
agent domain install xxx
```

或者：

```text
Agent Package
```

例如：

```text
kubernetes-operator
dify-operator
devops-engineer
software-engineer
research-agent
database-operator
```

Multi-Agent Runtime 不属于 Domain Marketplace / SDK 的必要前置条件。

---

# 38.1 Phase 8：Optional Multi-Agent Runtime

只有当单 Agent 多 Domain 无法满足独立自治、权限隔离、生命周期隔离或并行评估要求时，才实现 Multi-Agent Runtime。

这一阶段设计：

```text
Agent Runtime
    |
    +-- Agent A
    +-- Agent B
    +-- Agent C
    |
    v
Task Contract
```

必须提供：

```text
Agent Task Contract
Result Contract
Evidence references
Policy boundary
Evaluator boundary
Session isolation
Permission isolation
```

禁止：

```text
Supervisor prompt routes everything
Agent handoff by chat transcript
Domain equals Agent
Agent prose equals Evidence
```

---

# 39. 最重要的 Evaluation

不要用：

```text
LLM Judge:
"这个回答看起来不错吗？"
```

作为唯一指标。

应该测：

```text
Task Success Rate
Goal Completion Rate
Action Accuracy
Recovery Rate
Policy Violation Rate
False Action Rate
Tool Efficiency
Token Efficiency
Time To Completion
Human Intervention Rate
```

特别重要：

```text
Goal Completion Rate
```

因为 Agent 最终是干活，不是聊天。

---

# 40. 终极架构

最终目标：

```text
                         User Goal
                            |
                            v
                   Universal Agent Runtime
                            |
                  +---------+---------+
                  |                   |
              Agent Kernel       Agent Profile
                  |
        +---------+---------+
        |         |         |
      State    Context   Decision
        |         |         |
        +---------+---------+
                  |
                  v
           Domain Composition
                  |
    +-------------+-------------+
    |             |             |
Kubernetes      Dify      Observability
  Domain       Domain        Domain
    |             |             |
    +-------------+-------------+
                  |
                  v
           Shared World Model
                  |
           +------+------+
           |             |
       Evidence       Ontology
           |             |
           +------+------+
                  |
                  v
              Capability
                  |
                Policy
                  |
                Action
                  |
             Observation
                  |
               Evaluate
                  |
        Continue / Recover / Finish

        Optional Multi-Agent Layer

              Agent Runtime
                  |
    +-------------+-------------+
    |             |             |
 Agent A       Agent B       Agent C
    |             |             |
    +-------------+-------------+
                  |
            Task Contract
```

---

# 41. 最终设计目标

这个项目最终不应该成为：

> 一个很强的 Agent。

而应该成为：

> **一个可以不断加载新领域能力的 Agent Operating Runtime。**

用户不需要重新开发 Agent。

只需要：

```text
Install Domain
        ↓
Create Profile
        ↓
Attach Tools
        ↓
Attach Knowledge
        ↓
Configure Policy
        ↓
Run Agent
```

最终形成：

```text
Universal Agent Kernel
        +
Kubernetes Domain
        ↓
Kubernetes Expert

Universal Agent Kernel
        +
Coding Domain
        ↓
Software Engineer

Universal Agent Kernel
        +
Research Domain
        ↓
Research Agent

Universal Agent Kernel
        +
Kubernetes
+
Dify
+
Observability
        ↓
AI Infrastructure Operator
```

## 42. 第一版开发优先级

严格按照以下顺序开发：

```text
P0
├── Agent Loop
├── State
├── Goal
├── Task
├── Decision
├── Tool
└── Observation

P1
├── Domain Manifest
├── Capability
├── Policy
├── Evaluator
├── Context Compiler
└── Domain Composition interface

P2
├── World Model
├── Evidence
├── Dynamic Task Expansion
├── Recovery
└── Cross-domain entity/relation schema

P3
├── Memory
├── Multi-Domain
├── Cross-Domain World Model
└── Agent Profile

P4
├── Domain SDK
├── Domain Package
├── Domain Marketplace
└── Evaluation Platform

P5
├── Optional Multi-Agent Runtime
├── Agent Task Contract
├── Agent Result Contract
├── Agent Evidence handoff
└── Agent isolation model
```

最重要的是：

> **不要从 P4 开始。**

先证明 P0 + P1：

```text
Universal Runtime
+
Kubernetes Domain
```

能够稳定完成一个真实的复杂任务。

如果这个 Loop 能跑通，再逐步加入 World Model、Evidence、Recovery 和 Multi-Domain。

实施顺序应该是：

```text
Single Agent
    ↓
Single Domain
    ↓
Domain Composition interface
    ↓
Multi-Domain
    ↓
Cross-Domain Reasoning
    ↓
Optional Multi-Agent Runtime
```

这样可以避免一开始就陷入“Agent Framework 大而全”的陷阱。
