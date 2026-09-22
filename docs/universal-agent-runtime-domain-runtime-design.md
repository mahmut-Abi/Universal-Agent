可以。结合你现在的 **Universal Agent Runtime + Domain Runtime + World Model + Multi-Agent + DSH Plugin** 方向，我建议把这件事正式定成一套架构，而不是单独解决“Metric 截图”问题。

核心设计只有一句话：

> **Runtime 提供“观察世界”的通用协议，不提供任何领域观察算法；Domain Runtime 决定如何观察、观察什么、观察粒度以及如何解释观察结果。**

下面这版可以直接作为你后续 `universal-agent-runtime-domain-runtime-design.md` 的设计基线。

---

# 1. 总体架构

```text
                         Universal Agent
                               │
                    ┌──────────┴──────────┐
                    │                     │
                 Intent                 Goal
                    │                     │
                    └──────────┬──────────┘
                               ▼
                         Decision Loop
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
             Observation                 Action
                 │                           │
                 ▼                           ▼
        ┌────────────────┐          ┌────────────────┐
        │ Observation API│          │   Action API   │
        └───────┬────────┘          └───────┬────────┘
                │                           │
                ▼                           ▼
        Domain Runtime              Domain Runtime
                │                           │
       ┌────────┴────────┐          ┌───────┴────────┐
       │                 │          │                │
 Observability        Browser     K8s             Git
       │                 │
       ▼                 ▼
 Prometheus          Playwright
 Grafana             Browser
 Loki                DOM
 Tempo               Screenshot
```

这里最重要的是：

```text
Observation API
        ↑
属于 Universal Runtime

Metric Resolution
        ↑
属于 Observability Domain
```

---

# 2. Runtime 只定义 8 个核心概念

我建议不要继续扩 Runtime 的具体业务能力。

Runtime 层只需要理解：

```text
Agent
Session
Intent
World
Observation
Action
Artifact
Evidence
```

其他：

```text
Metric
Log
Trace
Screenshot
Kubernetes Resource
Git Repository
Database Row
```

全部属于 Domain。

---

# 3. Observation 成为 Runtime 一级原语

这是这次设计的核心。

定义：

```text
Observation
```

含义：

> Agent 从外部世界获取的一次有上下文的状态观测。

例如：

```json
{
  "id": "obs_01",
  "target": "service.api.latency",
  "observed_at": "2026-09-20T10:20:00Z",
  "time_range": {
    "start": "...",
    "end": "..."
  },
  "representation": "visual",
  "resolution": {
    "mode": "adaptive"
  },
  "artifacts": [
    "artifact://chart/123"
  ]
}
```

Runtime 不知道：

```text
service.api.latency
Prometheus
Grafana
```

它只知道这是一个 Observation。

---

# 4. ObservationRequest

Agent 不直接调用：

```text
prometheus.query_range
grafana.render
```

而是提出：

```json
{
  "target": "api.latency",
  "time_range": "24h",
  "purpose": "detect_trend",
  "representation": "visual",
  "resolution": {
    "mode": "adaptive"
  }
}
```

这个东西叫：

```text
ObservationRequest
```

它表达的是：

> **我需要知道什么。**

而不是：

> **我应该调用哪个 API。**

---

# 5. Resolution 只定义“语义”，不定义领域规则

这是非常关键的边界。

Runtime 可以定义：

```text
ResolutionMode

coarse
medium
fine
raw
adaptive
```

甚至：

```text
Resolution {
    mode
    budget
}
```

例如：

```json
{
  "mode": "adaptive",
  "max_points": 2000,
  "max_tokens": 8000
}
```

但 Runtime **不能定义**：

```text
5m
15s
1h
```

因为这些是 Metric Domain 的解释。

---

# 6. Observability Domain 自己解释 Resolution

例如：

```text
adaptive
```

Observability Domain 可以解释成：

```text
30d → 1h
7d  → 15m
24h → 5m
2h  → 1m
10m → raw
```

另一个 Domain：

```text
Browser
```

可能解释成：

```text
coarse → viewport screenshot
medium → element screenshot
fine   → DOM + screenshot
raw    → full DOM
```

Runtime 完全不需要知道。

这就是这个抽象能够长期稳定的关键。

---

# 7. Observation Provider

Runtime 提供接口：

```go
type ObservationProvider interface {
    Observe(
        ctx context.Context,
        request ObservationRequest,
    ) (Observation, error)
}
```

Domain 注册 Provider：

```text
ObservabilityObservationProvider
BrowserObservationProvider
KubernetesObservationProvider
CodeObservationProvider
```

---

# 8. Provider 内部可以非常复杂

例如：

```text
ObservabilityObservationProvider
             │
             ▼
       Observation Planner
             │
             ▼
       Resolution Manager
             │
       ┌─────┼──────┐
       ▼     ▼      ▼
   Prometheus Grafana Loki
       │
       ▼
    Renderer
       │
       ▼
   Observation
```

Runtime 完全看不到这些东西。

---

# 9. Metric Resolution Manager

这就是你刚才发现的核心能力。

但是它的位置应该是：

```text
Domain Runtime
└── Observability
    └── Observation
        └── ResolutionManager
```

而不是：

```text
Universal Runtime
└── MetricResolution
```

---

# 10. Resolution Manager 的职责

它负责：

### 输入

```text
target
time range
purpose
current evidence
current world state
budget
```

### 输出

```text
observation plan
```

例如：

```json
{
  "time_range": "30d",
  "step": "1h",
  "representation": "visual",
  "max_points": 720
}
```

---

# 11. Adaptive Resolution

这是整个设计最有价值的部分。

Agent 初始：

```text
30 days
```

Resolution Manager：

```text
30d / 1h
```

得到：

```text
Observation #1
```

分析发现：

```text
Day 18 ~ Day 20
出现明显变化
```

于是：

```text
Observation #2

window:
Day 17 ~ Day 21

resolution:
5m
```

继续发现：

```text
14:20 ~ 14:40
```

继续：

```text
Observation #3

window:
14:00 ~ 15:00

resolution:
15s
```

最终：

```text
Observation #4

raw samples
```

这就是：

> **Progressive Observation Refinement**

我建议把这个概念正式写进你的架构。

---

# 12. Observation 不等于 Evidence

必须分开。

例如：

```text
Observation
```

是：

> 我看到 CPU 在过去 24h 的图。

而：

```text
Evidence
```

是：

> CPU 从 14:20 开始持续升高。

所以：

```text
External World
      │
      ▼
 Observation
      │
      ▼
 Interpretation
      │
      ▼
 Evidence
      │
      ▼
 World Model
```

这会让你的 Agent 推理链非常清晰。

---

# 13. Observation → Artifact

截图、JSON、CSV、Prometheus result 都应该是 Artifact。

例如：

```text
Observation
├── metadata
├── target
├── resolution
├── time_range
└── artifacts
     ├── chart.png
     ├── metric.json
     └── raw.csv
```

所以不要把：

```text
Grafana Screenshot
```

直接塞进 Observation。

它应该是：

```text
Artifact
    ↑
Observation references it
```

---

# 14. Visual Observation

你的 Grafana 思路就可以非常自然地进入这个模型。

例如：

```json
{
  "representation": "visual",
  "resolution": {
    "mode": "coarse"
  }
}
```

Domain：

```text
Prometheus
    ↓
Downsample
    ↓
Grafana / renderer
    ↓
PNG
    ↓
Artifact
```

然后：

```text
Observation
    ↓
Multimodal LLM
```

---

# 15. Structured Observation

同一个 metric，也可以：

```json
{
  "representation": "structured"
}
```

返回：

```json
{
  "min": 12.1,
  "max": 93.2,
  "mean": 42.3,
  "p95": 81.4,
  "trend": "increasing"
}
```

所以：

```text
Observation
├── structured
├── visual
└── text
```

这些是通用 representation。

---

# 16. 更进一步：同一个 Observation 可以多表示

例如：

```text
api.latency / 24h
```

同时生成：

```text
Observation
│
├── structured
│     └── statistics.json
│
├── visual
│     └── latency.png
│
└── raw
      └── metric.json
```

Agent 可以根据任务选择：

```text
先 visual
↓
发现异常
↓
structured
↓
定位
↓
raw
```

这比简单的：

```text
Tool → Result
```

强很多。

---

# 17. Decision Loop 应该改成这样

你之前的 Agent：

```text
Intent
  ↓
Task
  ↓
Tool
  ↓
Result
  ↓
LLM
```

升级后：

```text
Intent
   ↓
World Model
   ↓
Decision
   │
   ├──── Observation Need
   │           ↓
   │      Observation
   │           ↓
   │      World Model
   │
   └──── Action Need
               ↓
             Action
               ↓
          World Model
```

这实际上是：

> **Observe → Update World → Decide → Act**

而不是：

> **Tool → Result → Tool → Result**

---

# 18. Tool 和 Observation 的最终关系

我建议你在文档里明确：

```text
Tool
= 能力执行接口

Observation
= 世界状态获取接口

Action
= Agent 对世界产生变化的意图
```

例如：

```text
Prometheus Query
    ↓
Tool

Metric Observation
    ↓
Observation

kubectl delete pod
    ↓
Action
```

---

# 19. Tool 仍然是实现机制

例如：

```text
ObservationRequest
       ↓
Observability Provider
       ↓
Tool
       ↓
Prometheus API
```

所以：

```text
Observation
    └── may use Tools
```

而不是：

```text
Observation = Tool
```

这个区别非常重要。

---

# 20. Domain Runtime 的标准结构

我建议以后所有 Domain 尽量遵循：

```text
domain/
├── manifest
├── runtime/
│   ├── observation/
│   ├── action/
│   ├── reasoning/
│   └── state/
│
├── capabilities/
│
├── providers/
│
├── adapters/
│
├── policies/
│
└── artifacts/
```

例如 Observability：

```text
observability/
├── runtime/
│   ├── observation/
│   │   ├── metric.go
│   │   ├── resolution.go
│   │   ├── planner.go
│   │   └── provider.go
│   │
│   ├── reasoning/
│   │   ├── anomaly.go
│   │   ├── correlation.go
│   │   └── rca.go
│   │
│   └── state/
│
├── capabilities/
│   ├── query-metric
│   ├── query-log
│   ├── query-trace
│   └── render-chart
│
├── adapters/
│   ├── prometheus/
│   ├── grafana/
│   ├── loki/
│   └── tempo/
│
└── policies/
```

---

# 21. World Model 怎么接

World Model 不保存所有原始数据。

它保存：

```text
Facts
Relations
Variables
Observations
Evidence
Artifacts
Hypotheses
```

例如：

```text
World
│
├── Entity
│    └── api-server
│
├── Variable
│    └── latency.p99
│
├── Observation
│    ├── 30d coarse
│    ├── 5d medium
│    └── 1h fine
│
├── Evidence
│    └── latency increased after deployment
│
├── Artifact
│    ├── chart.png
│    └── metric.json
│
└── Hypothesis
     └── deployment-related degradation
```

---

# 22. Multi-Agent 怎么处理

这里也不要为 Observation 单独搞一套 Multi-Agent。

统一：

```text
Root Agent
      │
      ├── Observation Request
      │
      ▼
Observability Agent
      │
      ├── Resolution Manager
      ├── Metric Provider
      └── Evidence
      │
      ▼
Shared World Model
```

Root Agent 不需要知道：

```text
PromQL
Grafana
step
downsampling
```

它只需要：

```text
"Investigate latency degradation."
```

Observability Agent 自己负责：

```text
观察
→ 提高分辨率
→ 获取证据
→ 更新 World Model
```

---

# 23. Permission 模型也自然成立

例如：

```text
Observation
    │
    ▼
Capability
    │
    ▼
Policy
```

权限可以区分：

```text
observe:metrics
observe:logs
observe:traces
observe:visualization
```

进一步：

```text
observe:metrics:production
observe:metrics:namespace/dify
```

而 Action：

```text
act:kubernetes:delete
```

可以拥有完全不同的权限。

这比把所有东西都当 Tool 更容易做安全模型。

---

# 24. DSH Plugin 怎么落

你现在做的：

```text
dsh-universal-agent
```

不应该暴露：

```text
metric-resolution
grafana
prometheus
```

作为 Universal Agent Runtime 的核心接口。

DSH Adapter 只需要映射：

```text
DSH
 │
 ├── Session
 ├── Event
 ├── Tool
 ├── Capability
 ├── Artifact
 └── Agent Lifecycle
        │
        ▼
Universal Agent Runtime
        │
        ▼
Domain Runtime
```

以后：

```text
dsh-universal-agent
```

加载：

```text
observability-domain
```

就获得 Observability 能力。

---

# 25. 最终目录建议

结合你当前项目，我建议最终逐步收敛到：

```text
universal-agent/
│
├── runtime/
│   ├── agent/
│   ├── session/
│   ├── intent/
│   ├── task/
│   ├── world/
│   ├── observation/
│   │   ├── request.go
│   │   ├── observation.go
│   │   ├── resolution.go
│   │   ├── representation.go
│   │   └── provider.go
│   │
│   ├── action/
│   ├── capability/
│   ├── artifact/
│   ├── evidence/
│   ├── memory/
│   ├── event/
│   ├── policy/
│   └── multiagent/
│
├── domain/
│   ├── observability/
│   ├── browser/
│   ├── coding/
│   └── ...
│
├── adapters/
│   └── dsh/
│
├── plugins/
│
└── cmd/
```

---

# 26. 但有一个东西我建议你现在先不要做

不要马上实现：

```text
Universal Observation Engine
Universal Resolution Engine
Universal Multimodal Engine
Universal Perception Engine
```

这些名字听起来很高级，但很容易重新把 Runtime 做大。

第一版只实现：

```text
ObservationRequest
Observation
ObservationProvider
Resolution
Representation
```

五个概念就够。

然后让 Observability Domain 验证它。

---

# 27. 第一阶段真正应该实现的链路

你可以用这个作为 P3/P4 的一个验证 Vertical Slice：

```text
User:
"分析 api 过去 30 天 latency 的变化"
              │
              ▼
          Universal Agent
              │
              ▼
        Observation Need
              │
              ▼
     Observability Domain
              │
              ▼
      Resolution Manager
              │
              ▼
       30d / 1h / Visual
              │
              ▼
          Grafana
              │
              ▼
        Observation
              │
              ▼
        Multimodal LLM
              │
              ▼
       "Day 18 开始上升"
              │
              ▼
        Evidence
              │
              ▼
       World Model Update
              │
              ▼
      Resolution Escalation
              │
              ▼
       5d / 5m / Visual
              │
              ▼
       ...
```

这个 Vertical Slice 跑通以后，你基本就验证了整个设计。

---

# 28. 最终的架构原则

我建议你把下面这些直接写进设计文档：

### Principle 1 — Runtime Stability

> Universal Runtime MUST remain domain-agnostic and should evolve slowly.

### Principle 2 — Domain Evolution

> Domain Runtime MAY evolve rapidly to incorporate domain-specific intelligence, strategies, representations, and optimizations.

### Principle 3 — Observation First

> Agents should reason from observations of the world rather than directly coupling reasoning to domain-specific tools.

### Principle 4 — Tool Is Implementation

> Tools are execution mechanisms. Observation and Action are semantic concepts above tools.

### Principle 5 — Progressive Resolution

> Agents MAY progressively refine observations from coarse to fine resolution instead of retrieving maximum-resolution data upfront.

### Principle 6 — Evidence Is Derived

> Observations are raw or structured perceptions; Evidence represents interpreted information that supports reasoning.

### Principle 7 — Artifact Is Externalized Data

> Large observations and multimodal outputs SHOULD be stored as Artifacts and referenced by the World Model rather than embedded directly into reasoning state.

### Principle 8 — Runtime Extension Threshold

> A capability should enter Universal Runtime only when it is domain-agnostic, fundamental to agent operation, and cannot be reasonably implemented by Domain Runtime.

---

# 29. 这套设计解决了你刚才最大的担忧

以后你遇到：

```text
Metric resolution
```

不用改 Runtime。

遇到：

```text
Browser screenshot resolution
```

不用改 Runtime。

遇到：

```text
代码 AST 分层观察
```

不用改 Runtime。

遇到：

```text
数据库 coarse → row → cell
```

也不用改 Runtime。

它们都可以变成：

```text
ObservationRequest
        ↓
Domain-specific Observation Provider
        ↓
Observation
        ↓
World Model
```

而真正属于 Universal Agent 的，是：

```text
我需要观察什么？
我观察到了什么？
这个观察产生了什么 Evidence？
World 发生了什么变化？
下一步应该观察还是行动？
```

**这才是 Universal Agent 的边界。**

---

## 最后给你一个架构决策

我建议你现在正式确定：

```text
                    ┌──────────────────────────┐
                    │   Universal Agent        │
                    │                          │
                    │ Agent / Session / World  │
                    │ Intent / Task             │
                    │ Observation / Action      │
                    │ Artifact / Evidence       │
                    │ Capability / Policy      │
                    │ Event / Memory            │
                    └────────────┬─────────────┘
                                 │
                         Stable Contracts
                                 │
             ┌───────────────────┼───────────────────┐
             │                   │                   │
             ▼                   ▼                   ▼
      Observability          Browser             Coding
        Domain                Domain              Domain
             │
       ┌─────┴──────┐
       │            │
   Observation   Resolution
     Planner       Manager
       │            │
 Prometheus      Adaptive
 Grafana         Resolution
 Loki
 Tempo
```

**Runtime：稳定、少、抽象。**

**Domain：复杂、聪明、快速迭代。**

**Observation：Runtime 与 Domain 之间的关键桥梁。**

**Resolution：不要进入 Runtime，只作为 Observation Contract 的一个通用语义字段，由 Domain 解释。**

而你现在发现的“Grafana 图比海量 metric 点更容易让 LLM 理解”，正好可以成为这个架构的第一个真实验证场景，而不是再给 Runtime 增加一个 Metrics 子系统。

