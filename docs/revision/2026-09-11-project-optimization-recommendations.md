# Universal-Agent 项目优化、补充与下一步建议

> 日期：2026-09-11  
> 目的：基于当前仓库、P0 spec、`docs/product.md`、remaining TODO、critical review、CLI 冒烟结果和代码热点，形成可执行的优化路线。

## 总体判断

这个项目不是“没东西”，而是**东西太多、产品入口不够收敛**。核心架构已经很完整：Runtime、Domain、Policy、Evidence、World Model、Evaluation、Session、Persistence、agentd、CLI、TUI/Web、Multi-Agent、Distributed、Ecosystem 都已有 foundation。真正的问题是：

- **用户第一天体验还不够干净**：`agent run "Hello"` 虽然能跑通，但默认仍进入 Kubernetes fake domain，输出类似 cluster overview，这对普通用户是错位体验。
- **产品文档/帮助/实际行为存在不一致**：`agent init --help` 说默认写 `./universal-agent/profile.json`，`docs/product.md` 也说项目目录，但隔离 HOME 冒烟时实际写到了 `~/.universal-agent/profile.json`。
- **能力广度已经超过 P0/P1/P2 需要**：P4/P6/P7 代码存在大量 foundation，但当前最重要的是把 P0 Golden Path 做成“没人解释也能 10 分钟跑通”。
- **维护面开始偏重**：最大文件已超过 1,000 行，例如 `src/universal_agent_cli/agentd.py` 1050 行、`src/universal_agent/runtime/agent.py` 1011 行、`src/universal_agent/domains/kubernetes/cli_reports.py` 1030 行。
- **公开 API 表面积过大**：`src/universal_agent/__init__.py` 705 行，导入/导出约 337 个名字，对用户和维护者都太重。

---

## P0：最高优先级，先把 Golden Path 做“真顺”

### 1. 先修正配置路径的一致性

**现象：**

- `docs/product.md` D4 写的是：`agent init` 默认写入 `./universal-agent/profile.json` 和 `./universal-agent/config.json`。
- `agent init --help` 也提示 default 是当前目录下 `universal-agent/profile.json`。
- 但隔离 HOME 实测：实际写到了 `~/.universal-agent/profile.json` 和 `~/.universal-agent/config.json`。

**建议：必须二选一，不能混着来。**

推荐规则：

```text
默认：项目级配置
  ./universal-agent/profile.json
  ./universal-agent/config.json

显式全局配置：
  agent init --global
  ~/.universal-agent/profile.json
  ~/.universal-agent/config.json

显式路径：
  agent init --output path/to/profile.json
```

**原因：**

P0 的 Golden Path 是：

```bash
git clone
cd Universal-Agent
uv sync
agent init
agent doctor
agent run "..."
agent session list
```

这个心智天然是“我在这个项目里初始化”。如果默认写 HOME，新用户会困惑：

> 我明明在 repo 里运行，配置怎么跑到用户目录去了？

**验收标准：**

- `agent init` 输出路径、`agent init --help`、README、`docs/product.md` 完全一致。
- `agent doctor` 读取到同一份配置。
- `agent run` 使用同一份配置。
- `agent config` 的 Config 路径与 init 输出一致。
- 增加测试：`test_init_default_config_location_matches_help_and_readme_assumption`。

---

### 2. 默认 Profile 必须从 Kubernetes 解耦

**现象：**

隔离 HOME 后执行：

```bash
agent init
agent run "Hello from isolated HOME"
```

可以成功，但 session 显示：

```text
Domains: kubernetes@0.2.0
DecisionGenerated capability=inspect_cluster
Termination: workload health criteria satisfied
```

这说明默认 `default` Profile 仍然在做 Kubernetes 语义。对 P0 来说，这是最大 UX 问题之一。

**建议：新增真正的通用默认 Domain / Profile。**

建议把 Profile 分成：

```text
default
  - 用于第一天体验
  - scripted/local model
  - local/general domain
  - 不依赖 kubeconfig
  - 不出现 kubernetes、workload、cluster、pod 等词

local-kubernetes
  - 保留现有 fake Kubernetes flow
  - advanced / explicit profile

sre-kubernetes
  - kubectl 或 Kubernetes API 后端
  - 真实集群场景
```

**`default` 应该怎么跑？**

最小可用能力可以是：

```text
capability: answer_goal
target: local
observation: "goal accepted / local response generated"
evidence: "runtime completed a local no-op/read-only task"
evaluation: success
```

或者更有产品价值一点：

```text
capability: inspect_workspace
target: cwd
observation: project files summary
evidence: file count / detected pyproject / README exists
evaluation: success
```

但如果做 `inspect_workspace`，需要清晰的 filesystem/read-only domain，而不是偷偷复用 Kubernetes。

**验收标准：**

```bash
agent init
agent run "Hello"
agent session show <id>
```

输出中不应该出现：

```text
kubernetes
cluster
workload
pod
healthy=True
workload health criteria satisfied
```

除非用户显式选择了 Kubernetes profile。

---

### 3. `agent init --help` 太重，需要初学者/高级选项分层

现在 `agent init --help` 一屏内暴露了大量高级参数：

```text
--distributed-queue-backend
--distributed-locks-backend
--distributed-workers-backend
--domain-backend
--kubectl-namespace
--kubernetes-api-token-secret
--model-response-format
--model-header
...
```

这和 P0 “让新用户知道怎么开始”冲突。

**建议：把 init 的帮助分成两层。**

第一层只显示：

```bash
agent init
  --profile
  --model-provider
  --model-name
  --model-api-key-env
  --output
  --force
  --global
  --output-format
```

高级项移动到：

```bash
agent init --advanced-help
```

或拆成：

```bash
agent init
agent init kubernetes
agent init server
agent init distributed
```

如果不想改命令结构，可以至少在 argparse help 中把高级参数 group 标成：

```text
Advanced: Kubernetes backend
Advanced: Distributed runtime
Advanced: Model transport details
```

**验收标准：**

`agent init --help` 的前 40 行可以让新用户直接理解：

```text
1. 会创建什么文件
2. 默认模型是什么
3. 如何配置 API key
4. 如何不破坏已有配置
5. 下一步运行什么命令
```

---

### 4. 顶层 `agent --help` 应减少“命令墙”

当前顶层 help 虽然说：

```text
Golden path: init -> doctor -> run -> session.
All other commands are advanced/developer commands.
```

但 usage 仍然列出一长串：

```text
version, health, ready, metrics, cost, logs, traces, audit,
multi-agent, repair, distributed, serve, kubernetes, tui,
ecosystem, eval, domain, domain-packages, capabilities, tools,
policies, evaluators, chat, memory
```

对新用户来说，视觉上仍然是“命令墙”。

**建议：**

顶层只突出：

```text
Core:
  init
  doctor
  run
  session
  config
  profile

Advanced:
  agent advanced --help
```

如果必须保留现有命令，也建议：

- argparse description 中不要把所有高级命令塞到 usage 第一行；
- 帮助正文按 group 展示；
- advanced 命令每个都写 `(advanced)`，而不是只给 `chat` 写。

**验收标准：**

第一次看 `agent --help` 的人应该能在 10 秒内回答：

```text
我应该先 agent init
然后 agent doctor
然后 agent run
最后 agent session list/show
```

---

### 5. 补齐 `agent config` 和 `agent profile show` 的产品信息

现在 `agent config` 输出已有：

```text
Model
Runtime
Domains
Secrets
Config
```

但缺：

```text
Agent / Active Profile
Policy
Config discovery order
Store resolved absolute path
```

`agent profile show default` 目前只显示：

```text
Profile
Version
Description
Domains
```

但 P0 需要用户能回答：

> 我的模型在哪里配置？Profile 是什么？Policy 是什么？Domain 是什么？

**建议：**

`agent config` 应显示：

```text
Agent
  Active profile: default

Model
  Provider: scripted
  Model: scripted
  Credentials: not required / env OPENAI_API_KEY configured / missing

Runtime
  Max steps: 20
  Store: file
  Store path: /absolute/path

Policy
  Mode: safe
  Loaded policies:
    - local-read-only
    - kubernetes-scale-safety

Domains
  enabled:
    - local@0.1.0
  disabled/available:
    - kubernetes@0.2.0

Config
  Active profile config: ...
  Settings: ...
  Discovery order:
    1. $AGENT_CONFIG_DIR/profile.json
    2. ./universal-agent/profile.json
    3. ~/.universal-agent/profile.json
```

`agent profile show default` 应显示：

```text
Profile: default
Model: scripted/scripted
Domains: local@0.1.0
Policy: local-safe
Runtime: max_steps=20, store=file
```

**验收标准：**

用户看完 `agent config` 和 `agent profile show default` 后，不需要读源码就知道模型、Profile、Policy、Domain 和持久化在哪里。

---

## P1：真实能力证明，不要再只证明框架

### 6. 做一个“真 Golden Demo”，比继续写架构文档更重要

当前项目最强的是架构完整性，最弱的是“我能看见它真的干活”。

建议做两个 demo：

#### Demo A：普通用户本地 demo

```bash
agent init
agent doctor
agent run "summarize this repository"
agent session show <id>
```

这个 demo 不需要 Kubernetes、不需要真 LLM、不需要 agentd。

它应该展示：

```text
Goal accepted
Task created
Capability selected
Observation produced
Evidence recorded
Evaluation completed
Session persisted
```

#### Demo B：Kubernetes SRE demo

```bash
agent init --profile sre-kubernetes --domain-backend kubectl
agent doctor
agent kubernetes check ...
agent kubernetes run --submit-run ...
agent session show <id>
```

必须能证明：

```text
unhealthy workload
  -> diagnose
  -> policy check
  -> safe remediation or confirmation
  -> fresh verification
  -> evidence
  -> completed
```

**关键建议：**

把这两个 demo 写成：

```text
docs/golden-path-local.md
docs/golden-path-kubernetes.md
scripts/demo-local.sh
scripts/demo-kubernetes-contract.sh
```

并且每次 README 更新后跑一次。

**验收标准：**

- `scripts/demo-local.sh` 在干净 HOME 下可重复运行。
- `scripts/demo-kubernetes-contract.sh` 在有凭据时输出脱敏 artifact。
- README 的 Quick Start 完全对应真实命令，不是“理论命令”。

---

### 7. 给 live Kubernetes proof 一个明确“结束条件”

remaining TODO 里 live Kubernetes 还有外部阻塞：真实集群、model provider、目标 workload。

建议不要让它无限挂在 TODO，而是定义三档：

```text
Level 0: fixture contract, CI 必跑
Level 1: kind/minikube local live-like contract, CI 可选跑
Level 2: real cluster + real model provider, gated live CI
```

**为什么？**

如果只等真实生产环境，永远会被 credentials / cluster policy 阻塞。Level 1 的 kind/minikube 可以证明大量真实边界：

- kubectl/API backend
- watch/wait
- CrashLoopBackOff 或 zero replicas
- scale mutation
- fresh verification
- artifact redaction

**建议落地：**

新增：

```text
tests/live_like/test_kubernetes_kind_contract.py
docs/kubernetes-live-like-contract.md
.github/workflows/kubernetes-live-like.yml
```

用 kind/minikube 创建临时 deployment：

```text
deployment starts unhealthy
agent diagnoses
safe remediation happens
fresh health check passes
artifact written
```

**验收标准：**

- Level 1 不需要真实云集群。
- Level 2 需要 secrets 才跑。
- remaining TODO 从“阻塞”变成“分层可推进”。

---

## P2：降低维护成本，先拆核心热点

### 8. 按 ROI 拆大文件，不做大重构

当前最大风险文件：

```text
src/universal_agent_cli/agentd.py                       1050
src/universal_agent/domains/kubernetes/cli_reports.py   1030
src/universal_agent/runtime/agent.py                    1011
src/universal_agent/distributed/queue.py                 935
src/universal_agent/evaluation/harness.py                923
src/universal_agent/ecosystem/catalog.py                 914
src/universal_agent/service/distributed_runtime.py       897
src/universal_agent/runtime/actions.py                   879
src/universal_agent_cli/parser.py                        684
src/universal_agent/__init__.py                          705
```

**不要一口气全拆。**建议按“离 P0 用户最近 + 离 Kernel 最近”的顺序。

#### 第一批：P0 直接相关

##### `src/universal_agent_cli/parser.py`

现状：`build_parser()` 约 639 行，一个函数承担所有命令树。

建议拆成：

```text
src/universal_agent_cli/parser.py              # build_parser orchestration
src/universal_agent_cli/parser_core.py         # init/run/session/config/profile/doctor
src/universal_agent_cli/parser_advanced.py     # advanced group
src/universal_agent_cli/parser_kubernetes.py
src/universal_agent_cli/parser_distributed.py
src/universal_agent_cli/parser_eval.py
```

验收：

- 每个 parser 文件 < 250 行。
- `agent --help` 输出不回退。
- parser tests 不变或增强。

##### `src/universal_agent_cli/agentd.py`

现状：remote dispatch、distributed、session、profile、run、kubernetes、eval、ecosystem 全塞一个文件。

建议拆成：

```text
src/universal_agent_cli/remote/__init__.py
src/universal_agent_cli/remote/base.py
src/universal_agent_cli/remote/run.py
src/universal_agent_cli/remote/session.py
src/universal_agent_cli/remote/config.py
src/universal_agent_cli/remote/distributed.py
src/universal_agent_cli/remote/kubernetes.py
src/universal_agent_cli/remote/eval.py
src/universal_agent_cli/remote/ecosystem.py
```

验收：

- `agentd.py` 只保留 client setup + command router，< 250 行。
- 每个 remote command 单测/集成测试保留。
- 不改变 HTTP/agentd API 合约。

#### 第二批：Kernel 核心

##### `src/universal_agent/runtime/agent.py`

现状：`AgentRuntime` 1011 行，包含 start/resume/pause/cancel、loop、decision、drive、observe、recovery、settle、save/load、emit。

建议拆为“内部 helpers”，不改变 public API：

```text
runtime/agent.py              # AgentRuntime facade/public API
runtime/session_control.py    # pause/resume/cancel/control transitions
runtime/loop.py               # _loop, _apply_decision, _drive
runtime/observation_flow.py   # _observe, evidence/world/task update
runtime/recovery_flow.py      # recovery planning
runtime/settlement.py         # evaluate/settle/finish
runtime/persistence_flow.py   # save/load/emit helpers
```

验收：

- `AgentRuntime` public methods 不变。
- behavior tests 全通过。
- `runtime/agent.py` < 350 行。
- 不引入 Domain-specific branches。

##### `src/universal_agent/service/runtime.py`

现状：RuntimeService 同时暴露 profiles、memory、distributed、run、session、events、ops、audit、doctor。

建议按 service area 拆：

```text
service/runtime.py              # RuntimeService main facade
service/runtime_sessions.py
service/runtime_distributed.py
service/runtime_observability.py
service/runtime_memory.py
service/runtime_audit.py
```

或用 composition：

```python
service.sessions.list(...)
service.distributed.schedule(...)
service.audit.integrity(...)
```

但为了兼容，旧方法继续代理。

验收：

- `RuntimeService` 外部调用不破。
- 旧方法保留至少一个 minor version。
- 新内部文件各 < 300 行。

---

### 9. 缩小 `universal_agent.__init__` 的公开 API

现在根包导出约 337 个名字，这是 SDK 体验和维护风险。

**建议：分三层导出。**

#### 根包只导出 5–10 个 Facade

```python
from universal_agent import Agent, AgentResult, RuntimeService, RuntimeHost
```

建议根包保留：

```text
Agent
AgentResult
UniversalAgentRuntime
RuntimeService
RuntimeHost
AgentProfile
RuntimeConfig
__version__
```

#### 专业用户从子模块导入

```python
from universal_agent.runtime import AgentRuntime
from universal_agent.domain import DomainRuntime
from universal_agent.policy import PolicyEngine
```

#### 兼容旧导出

不要立刻删除。可以：

```text
0.1.x: 保留旧导出，但标 Deprecated
0.2.x: README/Docs 不再使用根包大导出
0.3.x: 移入 universal_agent.compat 或 universal_agent.all
```

**验收标准：**

- README 中 Python API 只展示 facade。
- `__init__.py` < 150 行。
- public API 有 `docs/api-surface.md` 或 `docs/runtime-contract.md` 说明。
- typing/mypy 不受影响。

---

## P3：补齐产品文档，不是继续扩架构文档

### 10. README 应该以“使用路径”而非“架构广度”开头

当前 README 已经明显往 P0 收敛，这是对的。建议继续压缩第一屏，严格顺序：

```markdown
# Universal-Agent

一句话：A policy-enforced agent runtime with persistent sessions and pluggable domains.

## Quick Start

1. Install
2. Init
3. Doctor
4. Run
5. Session

## What You Just Ran

Agent / Runtime / Profile / Domain / Policy / Session 一张表。

## Configuration

## Profiles

## Sessions

## Local Demo

## Kubernetes Demo

## Server / agentd

## Web

## Architecture

## Development
```

**不要在第一屏出现：**

```text
P3.5
P4
P6
P7
distributed queue
ecosystem package registry
multi-agent conflict resolution
```

这些属于 architecture / advanced docs，不属于新用户入口。

---

### 11. 新增 `docs/RUNTIME_CONTRACT.md`

P0 spec 已明确要求，但建议它成为长期架构稳定器。

内容建议：

| 概念 | 谁创建 | 谁拥有状态 | 是否持久化 | 能否绕过 Policy | 主要事件 |
| --- | --- | --- | --- | --- | --- |
| Goal | CLI/API/SDK | Runtime | 是 | 否 | GoalCreated/GoalCompleted |
| Task | TaskManager | Runtime | 是 | 否 | TaskCreated/TaskCompleted |
| Decision | Model/DecisionEngine propose | Runtime validates | 是 | 否 | DecisionGenerated/DecisionValidated |
| Action | ActionRuntime | Runtime | 是 | 否 | ActionStarted/ActionCompleted |
| Observation | ToolRuntime | Runtime stores | 是 | 否 | ObservationReceived |
| Evidence | EvidenceSystem | Runtime | 是 | 否 | EvidenceRecorded |
| Policy | PolicyEngine | Runtime | 是 | N/A | PolicyChecked |
| Evaluation | Evaluator | Runtime | 是 | 否 | EvaluationCompleted |
| Session | Runtime | SessionStore | 是 | 否 | SessionStarted/Terminal |

**价值：**

- 防止后续新功能把状态/control flow 又塞回 LLM。
- 防止 Web/agentd/CLI 各自发明第二套语义。
- 给 SDK/API 文档一个稳定基础。

---

### 12. 文档要有“读者分层”

建议文档目录调整成：

```text
docs/
  index.md
  quickstart.md
  concepts.md
  configuration.md
  profiles.md
  sessions.md
  doctor.md
  runtime-contract.md
  local-demo.md
  kubernetes-demo.md

  operators/
    agentd.md
    web.md
    persistence.md
    security.md
    observability.md

  developers/
    architecture-map.md
    domain-sdk.md
    evaluation.md
    testing.md

  advanced/
    multi-agent.md
    distributed.md
    ecosystem.md
```

**原则：**

- 用户文档只回答“怎么用”。
- Operator 文档回答“怎么部署/运维”。
- Developer 文档回答“怎么扩展”。
- Advanced 文档承认 P4/P6/P7 是 advanced/experimental，不抢 P0 心智。

---

## P4：测试体系优化，不是盲目追求数量

### 13. 将测试目标从“很多测试”转为“关键行为证明”

已有测试数量很大：约 1421 tests collected，质量投入高。但现在应该新增的是**少量高价值行为测试**。

建议测试金字塔改成：

```text
Golden Path smoke tests
  - agent init
  - agent doctor
  - agent run
  - agent session list/show
  - clean HOME / clean cwd

Policy boundary behavior tests
  - deny means tool not executed
  - confirm means pause, approve, execute, verify

Session durability tests
  - run -> persist -> new process -> show
  - pause -> restart -> resume

Domain live-like contract tests
  - Kubernetes kind/minikube
  - optional real cluster

Contract/unit tests
  - 保持，但不要继续无限扩张
```

**具体新增/调整：**

```text
tests/integration/test_golden_path_clean_home.py
tests/integration/test_default_profile_is_domain_neutral.py
tests/integration/test_config_location_consistency.py
tests/integration/test_cli_help_beginner_surface.py
tests/integration/test_session_persistence_cross_process.py
tests/behavior/test_policy_cannot_be_bypassed.py
tests/live_like/test_kubernetes_kind_contract.py
```

**最重要的断言：**

```python
assert "kubernetes" not in session_show_output.lower()
assert "workload health criteria" not in output.lower()
```

用于默认 `agent run "Hello"`。

---

### 14. 给 behavior/contract/unit 比例设门槛

critical review 里已经做过分类：behavior 约 25.7%、contract 约 28.8%、unit 约 45.5%。这比原先想象好，但行为测试仍偏少。

建议设置目标：

```text
短期：behavior >= 30%
中期：behavior >= 35%
contract <= 30%
unit 不强行压缩，但新增测试优先 behavior
```

CI 中可以输出分类报告，不必一开始 hard fail：

```bash
python tools/classify_tests.py --report
```

后续加门槛：

```bash
python tools/classify_tests.py --min-behavior-ratio 0.30
```

---

## P5：安全与生产化建议

### 15. Secret/Config 需要更明确的红线

当前已有 secret redaction 和安全测试，但产品层应更直接：

`agent config` 应显示：

```text
OPENAI_API_KEY: configured
```

不能显示：

```text
sk-...
```

建议补充：

```text
agent doctor --output json
```

也必须脱敏。

新增测试：

```text
test_config_never_prints_secret_values
test_doctor_never_prints_secret_values
test_session_show_never_prints_secret_values
test_live_artifact_secret_scanner_blocks_secret_payload
```

---

### 16. Enterprise AuthN/AuthZ 暂停实现，但先写决策框架

remaining TODO 里 AuthN/AuthZ、tenant、KMS/Vault、audit storage 都需要外部决策。现在不建议直接实现，否则会继续扩大表面积。

建议先做一份：

```text
docs/security-production-decisions.md
```

只记录待决策项：

```text
Identity provider:
  - OIDC?
  - SAML?
  - static token only?

Tenant model:
  - single tenant?
  - org/project/workspace?

Secret provider:
  - env?
  - file?
  - Vault?
  - cloud KMS?

Audit storage:
  - Postgres append-only?
  - object storage WORM?
  - external SIEM?

Authorization model:
  - role-based?
  - policy-as-code?
  - domain-specific capability grants?
```

等这些决策确定后再实现。

---

## P6：生产基础设施别急着做，先做适配边界

### 17. Postgres / Broker / Distributed 不要继续堆抽象，先选择真实后端

remaining TODO 里有：

```text
Postgres persistence
broker-backed event stream
cross-node queue/lock backend
leader election / HA
```

这些不应继续写 fake foundation，而应进入“选型决策”。

建议顺序：

1. **Postgres first**：先把 session/event/world store 和 transactional outbox 在真实 Postgres 上跑通。
2. **Broker second**：在 Postgres outbox 稳定后，再接 NATS/Redis Streams/Kafka 之一。
3. **Distributed queue last**：只有在 agentd + worker 真实部署需求明确后才做 HA queue。

**推荐默认：**

```text
开发/小团队:
  Postgres + polling outbox

中等实时性:
  Postgres + NATS signal

企业/高吞吐:
  Postgres + Kafka/NATS JetStream
```

不要一开始上 Kafka，除非明确有吞吐/审计/多区域要求。

---

### 18. P6 Distributed 当前应标 `experimental`

`src/universal_agent/distributed/` 已经有 queue/lock/worker/coordinator，但 P0 产品阶段不应让用户误以为这是稳定生产能力。

建议：

- CLI help 中标 `(advanced, experimental)`。
- README 不放 Quick Start。
- docs 中写清楚：当前是 local/file/sqlite foundation，不是 cross-node HA guarantee。
- 若用户启用 production distributed backend，doctor 应提示：

```text
Warning:
Distributed runtime is experimental unless a production queue/lock backend is configured.
```

---

## P7：Ecosystem/Package 先冻结，不再扩张

### 19. Ecosystem/Domain Package 是长期方向，但不是当前 ROI

`src/universal_agent/ecosystem/catalog.py` 914 行，`universal_agent.__init__` 中 ecosystem/multi_agent/distributed 导出占很大比例。现在继续做 registry、package install、signature verification 会拖慢 P0。

建议：

```text
P0-P1：冻结新增功能
P2：只保留 catalog read/verify
P3：等 Domain SDK 稳定后再恢复 package install
```

文档标识：

```text
Ecosystem package registry is experimental and not required for Golden Path.
```

---

## P8：产品交互补充建议

### 20. Error message 必须全部变成“可修复”

P0 spec 已写得很对：

```text
Error:
Reason:
Try:
```

建议统一所有 CLI 错误为：

```text
Error:
  OpenAI credentials are missing.

Reason:
  Profile default uses provider openai_chat_completions and expects OPENAI_API_KEY.

Try:
  export OPENAI_API_KEY=...
  or run: agent init --force --model-provider scripted
```

具体覆盖：

```text
missing API key
invalid config
model unavailable
domain unavailable
policy denied
tool failed
session not found
profile not found
unsupported backend
agentd unavailable
```

新增统一 helper：

```python
render_cli_error(error: CliError) -> str
```

不要每个 command 自己 print stack trace / raw exception。

---

### 21. `agent doctor` 应输出“下一步命令”

当前 doctor 已经有 Try 行，这是好方向。建议末尾加：

```text
Next:
  agent run "Hello"
```

如果有 warning：

```text
Next:
  Fix warnings above, or continue with:
  agent run "Hello"
```

如果有 error：

```text
Next:
  Run:
    agent init --force
```

这会显著改善用户体验。

---

### 22. Session show 可以更像“执行故事”

现在 timeline 很完整，但偏 Runtime 内部事件：

```text
DecisionGenerated
DecisionValidated
CapabilityResolved
PolicyChecked
ActionStarted
...
```

建议默认 `agent session show` 输出两层：

```text
Summary
  Goal
  Status
  Duration
  Steps
  Actions
  Evidence

What happened
  1. Runtime created the goal
  2. Agent inspected local workspace
  3. Runtime recorded 3 evidence items
  4. Evaluator marked the task complete

Technical timeline
  Use: agent session events <id>
```

也就是说：默认 `show` 给人读故事，`events` 给机器/开发者看细节。

---

## 建议执行顺序

### Phase 1：锁定 P0 行为一致性

1. 修配置路径不一致：help / README / docs / actual behavior 统一。
2. 默认 Profile 从 Kubernetes 解耦。
3. `agent run "Hello"` 不再输出 cluster/workload/healthy。
4. `agent config` / `profile show` 补齐 Profile/Policy/Model/Domain。
5. 顶层 help 和 init help 降噪。

**完成标准：**

```bash
HOME=$(mktemp -d) agent init
HOME=$(mktemp -d) agent doctor
HOME=$(mktemp -d) agent run "Hello"
HOME=$(mktemp -d) agent session list
```

或用脚本稳定复现。

---

### Phase 2：补 Golden Path 行为测试

新增：

```text
test_golden_path_clean_home
test_config_location_consistency
test_default_profile_is_domain_neutral
test_cli_help_beginner_surface
test_session_persistence_cross_process
```

**完成标准：**

- CI 不需要真实 LLM。
- CI 不需要 Kubernetes。
- fake/scripted model 完整覆盖 run/session/evidence/evaluation。

---

### Phase 3：文档收敛

1. README 第一屏只讲 Quick Start。
2. 新增/完善 `docs/RUNTIME_CONTRACT.md`。
3. `docs/product.md` 与实际 CLI 行为同步。
4. `docs/index.md` 分用户/operator/developer/advanced。
5. 把 P4/P6/P7 从 README 主路径移走。

---

### Phase 4：真实能力证明

1. 本地 demo：`agent run "summarize this repository"`。
2. live-like K8s demo：kind/minikube。
3. gated real K8s demo：真实集群 + 真实 model provider。
4. 产出红线：每个 demo 有 artifact、session id、evidence count、fresh verification。

---

### Phase 5：拆大文件

优先拆：

1. `src/universal_agent_cli/parser.py`
2. `src/universal_agent_cli/agentd.py`
3. `src/universal_agent/runtime/agent.py`
4. `src/universal_agent/service/runtime.py`
5. `src/universal_agent/__init__.py`

**原则：**

- 只拆，不改语义。
- 每次拆一个文件。
- 每次拆完跑相关测试。
- 不碰 P4/P6/P7 行为。

---

### Phase 6：冻结高级面

短期冻结：

```text
multi_agent
distributed
ecosystem
external package install
production HA
```

只允许：

- bug fix
- doc 标 experimental
- doctor warning
- 不阻塞 Golden Path

---

## 最重要的 10 个具体任务

如果下一步直接动手，建议按这个顺序：

1. **修 `agent init` 默认配置路径与 help/docs 不一致。**
2. **新增真正 domain-neutral 的 `default` Profile。**
3. **修 `agent run "Hello"` 不应走 Kubernetes `inspect_cluster`。**
4. **增强 `agent config` 输出：Active profile、Policy、绝对 store path、config discovery order。**
5. **增强 `agent profile show` 输出：Model、Policy、Runtime、Domains。**
6. **重做 `agent --help` / `agent init --help` 的 beginner/advanced 分层。**
7. **新增 clean HOME Golden Path integration test。**
8. **新增 `docs/RUNTIME_CONTRACT.md`。**
9. **写 `scripts/demo-local.sh` 并让 README 命令与脚本一致。**
10. **拆 `src/universal_agent_cli/parser.py`，把 P0 commands 和 advanced commands 分开。**

---

## 一句话路线

**不要再新增 Agent 能力。先把默认用户路径从“架构上能跑”打磨成“新用户不用解释也能跑、能看懂、能排错、能复现”。**

P0 完成后，再用 live-like Kubernetes 证明真实能力；证明之后，再拆大文件和收缩 API 表面积；最后才恢复 P4/P6/P7 的生产化。
