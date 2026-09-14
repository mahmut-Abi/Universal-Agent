# P1 Kubernetes SRE Vertical

## Universal-Agent 可验收实施规格

> 目标：基于现有 Universal-Agent Runtime，完成第一个真正可用、可验证、可回放的 Kubernetes SRE Incident Response Vertical。
>
> 本阶段的核心不是增加更多 Agent Framework 抽象，而是证明现有 Runtime 在真实系统操作场景下的价值。

---

# 1. P1 总目标

P1 完成后，必须能够用 Universal-Agent 处理以下完整生命周期：

```text
Incident / Goal
      ↓
Investigate
      ↓
Collect Evidence
      ↓
Diagnose
      ↓
Propose Remediation
      ↓
Policy Check
      ↓
Human Approval（需要时）
      ↓
Execute
      ↓
Observe
      ↓
Verify
      ↓
Recover / Retry（失败时）
      ↓
Complete
```

最终必须能够做到：

```bash
agent run "检查 checkout 服务为什么异常，并告诉我应该怎么处理"
```

以及在允许 mutation 的情况下：

```bash
agent run "检查 checkout 服务异常，如果确认安全，请恢复服务"
```

Runtime 必须负责：

- capability resolution
- policy enforcement
- tool execution
- observation
- evidence extraction
- state transition
- confirmation
- verification
- bounded recovery
- session persistence
- audit/event recording

LLM 不得直接决定具体 Kubernetes tool invocation。

---

# 2. 本阶段明确禁止的工作

P1 不允许借机扩大项目范围。

禁止：

- Multi-Agent 新能力
- Distributed Runtime
- Agent Marketplace
- Domain Marketplace
- Plugin Marketplace
- 新的复杂 Memory 系统
- 新的通用 Agent abstraction
- Rust rewrite
- 重写 Runtime Core
- 大规模重构现有架构
- 新 Web 大功能
- 新 Scheduler/Queue/Worker 系统
- Kubernetes Operator
- 自动化生产集群部署
- 无 Policy 的 destructive action
- 无 Verification 的 mutation
- 通过 prompt 规避 Runtime Policy

如果现有架构存在阻碍，应优先通过最小改动解决。

不要为了 P1 重写已经存在的 Runtime。

---

# 3. P1 产品边界

本阶段只支持：

```text
Kubernetes Incident Response
```

第一版 Domain：

```text
KubernetesDomain
```

Domain 负责 Kubernetes 世界的：

- capabilities
- tools
- manifests
- context
- evidence extractors
- evaluators
- recovery strategies
- policies / policy hooks

Kernel / Runtime 不得出现 Kubernetes-specific branching。

禁止：

```python
if domain == "kubernetes":
    ...
```

Runtime 必须保持 Domain-agnostic。

---

# 4. 支持的 Kubernetes Capability

P1 至少实现以下 capabilities。

## Read-only capabilities

### inspect_workload

输入：

```text
namespace
workload name
workload kind
```

输出必须能够支持：

- desired replicas
- available replicas
- ready replicas
- rollout status
- image
- container status
- conditions
- generation
- observed generation

---

### inspect_pod

必须能够获取：

- pod phase
- readiness
- restart count
- container state
- exit code
- reason
- node
- events
- conditions

---

### inspect_logs

必须支持：

- pod
- container
- tail lines
- timestamp（如果 backend 支持）

输出不得直接被 Runtime 当成事实。

必须经过 Evidence extraction。

---

### inspect_events

获取 Kubernetes events。

至少支持：

- reason
- message
- type
- involved object
- timestamp

---

### inspect_service

至少支持：

- service
- selector
- endpoints / EndpointSlices
- port
- targetPort

---

# 5. Mutation capabilities

P1 至少支持：

```text
restart_workload
scale_workload
rollback_workload
```

如果现有 Kubernetes backend 不适合直接实现其中某项，可以先实现：

```text
restart_workload
scale_workload
```

但至少必须有 2 个 mutation capability。

---

# 6. Capability / Tool / Action 边界

必须保持：

```text
Capability
    ↓
Tool
    ↓
Action
    ↓
Observation
```

Capability 表达：

> “Runtime 可以做什么。”

Tool 表达：

> “如何调用 Kubernetes backend。”

Action 表达：

> “这次具体执行了什么。”

禁止：

```text
LLM → kubectl
```

禁止：

```text
LLM → arbitrary shell command
```

禁止让 LLM 自己构造：

```bash
kubectl delete ...
```

Runtime 必须通过 capability resolver 获得合法 capability。

---

# 7. Evidence 模型

Kubernetes tool 的返回值不是事实。

必须经过：

```text
Observation
    ↓
EvidenceExtractor
    ↓
Evidence
```

Evidence 至少包含：

```text
id
type
source
timestamp
subject
summary
provenance
confidence（如果模型支持）
```

例如：

```text
Evidence:
  type: pod_restart_count
  subject: checkout-abc
  value: 17
  source: kubernetes_api
  provenance: pod.status.containerStatuses
```

Logs 也必须具备 provenance。

例如：

```text
Evidence:
  type: container_log_signal
  source: kubernetes_api
  provenance: pod checkout-abc / container checkout
```

禁止：

```text
LLM says pod restarted 17 times
```

直接成为 Evidence。

Evidence 必须能够追溯到 Observation。

---

# 8. Diagnosis

P1 必须支持结构化 diagnosis。

至少包含：

```text
diagnosis
confidence
evidence_refs[]
affected_resources[]
```

例如：

```json
{
  "diagnosis": "checkout pods are failing during container startup",
  "confidence": 0.92,
  "evidence_refs": [
    "ev-17",
    "ev-21",
    "ev-24"
  ],
  "affected_resources": [
    "deployment/checkout"
  ]
}
```

Diagnosis 必须引用 Evidence。

禁止无 Evidence 的高置信度 diagnosis。

---

# 9. Remediation Proposal

Mutation 前必须先产生结构化 proposal。

至少包含：

```text
action
target
reason
evidence_refs[]
expected_effect
risk
requires_confirmation
```

例如：

```text
Action:
restart_workload

Target:
deployment/checkout

Reason:
pods are repeatedly failing startup

Evidence:
ev-17
ev-21
ev-24

Expected effect:
recreate unhealthy pods

Risk:
medium

Requires confirmation:
true
```

---

# 10. Policy

Mutation 默认：

```text
DENY
```

除非存在明确允许策略。

至少支持三种状态：

```text
ALLOW
DENY
REQUIRE_CONFIRMATION
```

示例：

```toml
[kubernetes]
read = "allow"

[kubernetes.restart_workload]
policy = "require_confirmation"

[kubernetes.scale_workload]
policy = "require_confirmation"
```

生产环境不得默认：

```text
mutation = allow
```

---

# 11. Policy Denial

必须有一个明确的 Policy denial 测试。

例如：

```bash
agent run "重启 checkout"
```

Policy：

```text
restart_workload = deny
```

最终必须：

```text
Action denied by policy
```

并且：

- 不执行 Kubernetes action
- 不调用 mutation backend
- session/event 中记录 denial
- 用户能够看到 denial reason

禁止通过 prompt、LLM decision 或 retry 绕过 Policy。

---

# 12. Human Confirmation

对于：

```text
REQUIRE_CONFIRMATION
```

Runtime 必须暂停。

状态：

```text
WAITING_FOR_CONFIRMATION
```

用户确认后才能继续。

确认前：

```text
mutation backend call count = 0
```

确认后：

```text
mutation backend call count >= 1
```

确认必须绑定：

- session
- proposed action
- target
- policy decision

禁止：

```text
用户确认 restart checkout
```

然后 Runtime 实际执行：

```text
scale checkout to 0
```

必须保证 approval/action binding。

---

# 13. Dry Run

P1 必须支持 Dry Run。

例如：

```bash
agent run --dry-run "检查 checkout，如果确认需要，请提出恢复方案"
```

Dry Run：

- 可以 investigation
- 可以产生 Evidence
- 可以产生 Diagnosis
- 可以产生 Proposal
- 不得执行 mutation

最终必须明确：

```text
DRY RUN
No mutation was executed.
```

---

# 14. Verification

所有 mutation 都必须有 verification。

禁止：

```text
kubectl command succeeded
    ↓
task = success
```

必须：

```text
Action
 ↓
Observation
 ↓
Verification
 ↓
Evaluator
 ↓
Task completion
```

例如 restart：

```text
restart_workload
 ↓
observe workload
 ↓
wait for readiness
 ↓
available replicas == desired replicas
 ↓
Evaluator = SUCCESS
```

如果 Kubernetes API 返回 200，但 workload 仍然 unhealthy：

```text
Task != SUCCESS
```

---

# 15. Task Success 定义

P1 必须明确区分：

```text
Tool success
```

和：

```text
Task success
```

例如：

```text
restart API call = success
pods remain CrashLoopBackOff
```

结果必须是：

```text
Action succeeded
Task failed / recovery required
```

不能显示：

```text
Task completed successfully
```

---

# 16. Recovery

至少实现一个 bounded recovery flow。

例如：

```text
restart
 ↓
verify
 ↓
still unhealthy
 ↓
re-investigate
 ↓
new evidence
 ↓
new diagnosis
```

Recovery 必须重新进入：

```text
Capability Resolver
    ↓
Policy
    ↓
Action
```

禁止 Recovery 直接调用 mutation tool。

必须有最大 retry / recovery 次数。

例如：

```text
max_recovery_attempts = 2
```

达到上限后：

```text
FAILED_REQUIRES_HUMAN
```

禁止无限 retry。

---

# 17. Golden Incident Scenarios

必须建立至少 10 个 deterministic scenario。

第一版建议：

```text
S01 CrashLoopBackOff
S02 ImagePullBackOff
S03 Readiness failure
S04 OOMKilled
S05 Deployment unavailable
S06 Rollout stuck
S07 Service has no healthy endpoints
S08 Bad configuration signal
S09 Failed remediation
S10 Policy-denied remediation
```

每个 scenario 必须包含：

```text
initial state
expected observations
expected evidence
expected diagnosis
allowed capabilities
expected proposal
policy
expected execution
expected verification
expected final state
```

---

# 18. Scenario 必须可重复

不能依赖真实生产环境。

P1 测试必须支持：

```text
Fake Kubernetes backend
```

或者：

```text
Fixture Kubernetes backend
```

同一个 scenario 重复运行：

```text
100 次
```

应该得到等价结果。

测试不得依赖：

- 真实 Kubernetes cluster
- 真实 OpenAI API
- 真实 Gemini API
- 网络
- 随机 LLM 输出

---

# 19. Golden Scenario #1：CrashLoopBackOff

必须实现一个完整 golden path。

初始：

```text
deployment/checkout
desired replicas = 3
available replicas = 0
```

Pod：

```text
phase = Running
container state = Waiting
reason = CrashLoopBackOff
restartCount > 5
```

Logs：

```text
startup/configuration failure
```

用户：

```bash
agent run "检查 checkout 为什么异常，并告诉我怎么处理"
```

预期：

```text
Evidence collected
    ↓
Diagnosis generated
    ↓
No mutation
    ↓
Proposal generated
```

如果用户允许 mutation：

```text
restart_workload
```

然后：

```text
verification
```

最终：

```text
3/3 replicas ready
```

Task：

```text
SUCCESS
```

---

# 20. Golden Scenario #2：Policy Denial

初始：

```text
checkout unhealthy
```

用户：

```bash
agent run "恢复 checkout 服务"
```

Policy：

```text
restart_workload = deny
```

预期：

```text
Diagnosis = available
Proposal = available

Policy = DENY

Action = NOT EXECUTED
Task = BLOCKED / POLICY_DENIED
```

必须验证 backend 没有收到 mutation call。

---

# 21. Golden Scenario #3：Confirmation

Policy：

```text
restart_workload = require_confirmation
```

第一次执行：

```text
WAITING_FOR_CONFIRMATION
```

没有 mutation。

用户确认：

```text
resume
```

然后：

```text
restart
 ↓
verify
 ↓
success
```

---

# 22. Golden Scenario #4：Tool Success / Task Failure

模拟：

```text
restart_workload = API success
```

但之后：

```text
pods still unhealthy
```

预期：

```text
action = success
verification = failed
task = failed
```

然后进入 bounded recovery。

---

# 23. Golden Scenario #5：Recovery

模拟：

```text
first remediation
    ↓
verification failed
    ↓
new observation
    ↓
new evidence
    ↓
second allowed remediation
    ↓
verification success
```

必须验证：

- recovery attempt 有记录
- 每次 recovery 都经过 Policy
- 每次 action 有对应 observation
- 最终状态正确

---

# 24. Session

Kubernetes Incident 必须完整持久化。

Session 至少包含：

```text
goal
state
tasks
decisions
actions
observations
evidence
policy decisions
evaluations
recovery attempts
timestamps
events
```

执行中退出进程：

```text
process killed
```

重新启动：

```bash
agent session resume <id>
```

必须能够继续执行。

---

# 25. Session Resume

重点测试：

```text
WAITING_FOR_CONFIRMATION
```

进程退出。

重新启动。

执行：

```bash
agent session resume <id>
```

必须仍然知道：

```text
which action was proposed
why it was proposed
which policy decision applies
whether confirmation is still required
```

不能重新从头执行。

不能因为 resume 而绕过 confirmation。

---

# 26. CLI

P1 不需要继续扩 CLI。

保证以下路径稳定：

```bash
agent doctor

agent run "<goal>"

agent session list

agent session show <id>

agent session resume <id>

agent profile list

agent config
```

Kubernetes 场景不得要求用户理解内部 Runtime 类。

用户不应该需要：

```python
RuntimeHost(...)
RuntimeService(...)
Coordinator(...)
DomainManager(...)
```

---

# 27. CLI 输出

一个 Incident Run 至少显示：

```text
Goal
Status
Duration
Current phase

Evidence
Diagnosis
Proposed action
Policy decision
Confirmation state
Execution result
Verification result
Recovery attempts
Final result
Session ID
```

不要输出大量内部 debug 信息作为默认输出。

需要 debug 时提供：

```bash
agent run --verbose ...
```

---

# 28. Web / agentd

P1 不要求开发新的 Web 功能。

但现有 Web / agentd 如果已经支持 Session、Events、Trace，应确保 Kubernetes incident 能够被观察。

最少能够看到：

```text
Session
 ↓
Task
 ↓
Action
 ↓
Observation
 ↓
Evidence
 ↓
Policy
 ↓
Evaluation
```

Web 不能成为 P1 阻塞项。

CLI + Runtime 是 P1 主路径。

---

# 29. Evaluation Harness

必须给 Kubernetes scenarios 建立 Evaluation。

每个 scenario 至少评估：

```text
diagnosis correctness
evidence completeness
policy correctness
action correctness
verification correctness
final task state
```

至少支持：

```text
PASS
FAIL
```

最好增加：

```text
PARTIAL
```

---

# 30. Evaluation 不允许只检查文本

禁止只比较：

```text
LLM answer == expected answer
```

应该检查 runtime state。

例如：

```python
assert final_state == "completed"
assert policy_decision == "allow"
assert mutation_count == 1
assert verification.success is True
```

以及：

```python
assert diagnosis.evidence_refs
```

---

# 31. Replay

每一个 Golden Scenario 都必须可以 replay。

例如：

```bash
agent eval run kubernetes
```

和：

```bash
agent eval replay <recording-id>
```

Replay 必须能够检查：

```text
decision sequence
capability resolution
policy decisions
actions
observations
evidence
evaluation
final state
```

---

# 32. Metrics

P1 不要求复杂 telemetry。

但必须能够统计：

```text
scenario
success
failure
duration
tool_calls
mutation_calls
recovery_attempts
policy_denials
confirmation_required
verification_success
```

Evaluation 输出至少能够回答：

```text
10 scenarios
8 passed
2 failed

Task Success Rate = 80%
Verification Success Rate = ...
Policy Violations = 0
Unexpected Mutation = 0
```

---

# 33. Safety Invariants

P1 必须建立不可违反的 invariants。

至少：

### I1

LLM cannot directly invoke Kubernetes tools.

### I2

Every mutation requires Policy evaluation.

### I3

DENY means zero mutation execution.

### I4

REQUIRE_CONFIRMATION means zero mutation before approval.

### I5

Every mutation must have verification.

### I6

Tool success does not imply task success.

### I7

Recovery must pass through Policy.

### I8

Recovery must be bounded.

### I9

Evidence must have provenance.

### I10

Session resume cannot bypass Policy or confirmation.

这些必须有自动化测试。

---

# 34. Security

至少检查：

- Kubernetes credentials 不进入 logs
- secret 不进入 Evidence
- secret 不进入 Session event
- secret 不进入 Evaluation recording
- `agent config` 不打印 secret value
- Web / agentd 不暴露 Kubernetes credentials
- command execution 不允许 arbitrary shell escape
- mutation capability 必须经过 resolver + policy

---

# 35. Fake Kubernetes Backend

建议提供：

```text
FakeKubernetesBackend
```

支持：

```text
get_workload
get_pod
get_logs
get_events
get_service
restart_workload
scale_workload
rollback_workload
```

Backend 必须记录：

```text
read_calls
mutation_calls
```

测试可以直接断言：

```python
assert backend.mutation_calls == []
```

或者：

```python
assert len(backend.mutation_calls) == 1
```

---

# 36. 不要让测试依赖 LLM

测试结构应该尽可能：

```text
Scenario
 ↓
Deterministic Decision Provider
 ↓
Runtime
 ↓
Fake Kubernetes
 ↓
Evaluator
```

如果需要测试真实 LLM integration，可以另外建立：

```text
optional live tests
```

但 CI 不得依赖真实 API。

---

# 37. Definition of Done

P1 只有在以下全部完成后才能宣布完成。

## Product

```text
[ ] Kubernetes 是第一个明确 Vertical
[ ] CLI 可以启动 Incident
[ ] 用户可以看到 Evidence
[ ] 用户可以看到 Diagnosis
[ ] 用户可以看到 Proposal
[ ] 用户可以看到 Policy decision
[ ] 用户可以确认 mutation
[ ] 用户可以看到 Verification
[ ] 用户可以看到最终结果
```

## Runtime

```text
[ ] Capability Resolver 工作
[ ] Policy 工作
[ ] Evidence provenance 工作
[ ] Confirmation 工作
[ ] Verification 工作
[ ] Recovery 工作
[ ] Session persistence 工作
[ ] Session resume 工作
```

## Safety

```text
[ ] DENY 永远不会执行 mutation
[ ] Confirmation 前永远不会执行 mutation
[ ] Recovery 不绕过 Policy
[ ] Mutation 后一定执行 Verification
[ ] 无 arbitrary shell execution
[ ] Secret 不进入 session/event/evidence
```

## Testing

```text
[ ] >= 10 Kubernetes scenarios
[ ] CrashLoopBackOff golden path
[ ] Policy denial test
[ ] Confirmation test
[ ] Tool-success/task-failure test
[ ] Recovery test
[ ] Session resume test
[ ] Replay test
[ ] Safety invariant tests
[ ] CI 无需真实 LLM
[ ] CI 无需真实 Kubernetes
```

## Evaluation

```text
[ ] scenario pass/fail
[ ] task success rate
[ ] verification success
[ ] policy violations
[ ] unexpected mutations
[ ] recovery attempts
[ ] replay recording
```

## Documentation

```text
[ ] README 有 Kubernetes Quick Start
[ ] README 有 Incident 示例
[ ] README 解释 Evidence
[ ] README 解释 Policy
[ ] README 解释 Confirmation
[ ] README 解释 Verification
[ ] README 有 Golden Scenario
[ ] README 不要求用户理解内部 Runtime classes
```

---

# 38. 最终人工验收

必须能够从 clean environment 开始。

第一阶段：

```bash
git clone <repo>
cd Universal-Agent

uv sync

uv run agent --help

uv run agent init

uv run agent doctor
```

然后：

```bash
uv run agent run "检查 checkout 服务为什么异常，并告诉我应该怎么处理"
```

必须能够看到：

```text
Evidence
Diagnosis
Proposal
```

而不执行 mutation。

---

然后使用：

```bash
uv run agent run "恢复 checkout 服务"
```

如果 Policy 是：

```text
REQUIRE_CONFIRMATION
```

必须进入：

```text
WAITING_FOR_CONFIRMATION
```

确认前：

```text
mutation = 0
```

---

确认后：

```bash
uv run agent session resume <session-id>
```

必须：

```text
execute
 ↓
observe
 ↓
verify
 ↓
complete
```

最终：

```text
SUCCESS
```

并且：

```text
desired replicas == available replicas
```

---

# 39. Clean-room 验收

找一个没有参与开发的人。

只给：

```text
Repository
README
```

不给口头解释。

要求对方完成：

```text
install
 ↓
init
 ↓
doctor
 ↓
run incident
 ↓
inspect evidence
 ↓
approve remediation
 ↓
resume
 ↓
verify result
```

目标：

```text
≤ 15 minutes
```

如果用户需要开发者解释：

```text
RuntimeHost 是什么
DomainManager 是什么
EvidenceExtractor 怎么工作
agentd 为什么需要
```

则 P1 Product UX 仍然失败。

---

# 40. P1 成功标准

P1 不是：

> “代码写完了。”

P1 的成功定义是：

> **Universal-Agent 能够在一个 deterministic Kubernetes incident 上，安全地完成 investigate → diagnose → propose → approve → execute → verify，并且整个过程可以持久化、回放和评估。**

最终至少达到：

```text
10 scenarios

≥ 8 PASS

0 unexpected mutations

0 policy bypass

0 confirmation bypass

100% mutation verification

100% evidence provenance

100% session resume safety
```

---

# 41. Codex 执行方式

不要一次性修改整个仓库。

必须分阶段执行。

## Phase 1 — Audit

先检查：

```text
现有 Kubernetes Domain
现有 capabilities
现有 tools
现有 policies
现有 evidence
现有 evaluator
现有 recovery
现有 session
现有 fake/fixture backend
现有 tests
```

输出：

```text
What already exists
What is missing
What must change
What must NOT change
```

此阶段不要大改代码。

---

## Phase 2 — Kubernetes Capability Contract

先确定：

```text
Capability
Tool
Action
Observation
Evidence
Evaluator
```

以及 Kubernetes capability manifest。

完成后测试。

---

## Phase 3 — Evidence + Diagnosis

实现：

```text
Observation → Evidence
Evidence → Diagnosis
Diagnosis → Proposal
```

完成后测试。

---

## Phase 4 — Policy + Confirmation

实现：

```text
Proposal
 ↓
Policy
 ↓
ALLOW / DENY / REQUIRE_CONFIRMATION
```

完成：

```text
DENY test
Confirmation test
Dry-run test
```

---

## Phase 5 — Execute + Verify

实现：

```text
Action
 ↓
Observation
 ↓
Evaluator
 ↓
Task result
```

完成：

```text
Tool success / Task failure test
Verification test
```

---

## Phase 6 — Recovery

只实现一个 bounded recovery loop。

完成：

```text
Recovery test
Policy re-entry test
Max retry test
```

---

## Phase 7 — Session / Replay

验证：

```text
persistence
resume
event recording
replay
```

尤其测试：

```text
WAITING_FOR_CONFIRMATION
```

进程退出后恢复。

---

## Phase 8 — 10 Golden Scenarios

建立：

```text
S01 ... S10
```

全部 deterministic。

---

## Phase 9 — CLI

只把上述能力接到：

```bash
agent run
agent session
agent doctor
```

不要继续增加 CLI abstraction。

---

## Phase 10 — Documentation

最后更新 README。

README 第一优先级：

```text
Kubernetes
Incident
Quick Start
Example
Evidence
Policy
Approval
Verification
```

架构说明放后面。

---

# 42. 每个 Phase 的交付要求

每完成一个 Phase：

```text
1. 修改代码
2. 添加/更新测试
3. 运行相关测试
4. 运行全量测试
5. 检查 git diff
6. 报告实际修改
7. 报告剩余风险
```

不得：

```text
“顺手重构”
```

不得修改无关模块。

不得为了通过测试删除已有 Runtime invariant。

---

# 43. Codex 最终报告格式

完成后必须输出：

```text
## P1 Implementation Summary

### Implemented
- ...

### Existing Components Reused
- ...

### New Components
- ...

### Kubernetes Capabilities
- ...

### Safety Invariants
- ...

### Golden Scenarios
- S01 ...
- S02 ...

### Evaluation Results
- Passed:
- Failed:
- Success Rate:
- Unexpected Mutations:
- Policy Bypasses:
- Verification Failures:

### Session Resume
- PASS/FAIL

### Replay
- PASS/FAIL

### Full Test Suite
- PASS/FAIL

### Known Limitations
- ...

### Files Changed
- ...

### Architecture Changes
- ...

### Recommended Next Step
- ...
```

如果出现：

```text
Architecture Changes
```

必须明确说明为什么现有架构无法满足 P1。

---

# 44. 最重要的工程原则

本阶段始终遵守：

```text
Don't build more Agent framework.
Prove the Runtime.
```

以及：

```text
LLM proposes.
Runtime decides.
Policy constrains.
Tools execute.
Observations report.
Evidence explains.
Evaluator verifies.
Recovery is bounded.
Session remembers.
```

P1 的最终目标不是让 Universal-Agent 看起来更复杂。

而是让一个工程师实际遇到：

```text
“checkout 挂了。”
```

时，可以运行：

```bash
agent run "检查 checkout 为什么异常"
```

然后得到：

```text
Evidence
    ↓
Diagnosis
    ↓
Safe Proposal
    ↓
Policy
    ↓
Approval
    ↓
Execution
    ↓
Verification
    ↓
Recovery
    ↓
Verified Result
```

**如果这条链能够稳定跑通，Universal-Agent 才真正开始拥有产品价值。**
