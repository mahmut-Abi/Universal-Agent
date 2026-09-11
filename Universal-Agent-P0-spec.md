# Universal-Agent P0 产品化任务与验收标准

## 0. 任务目标

当前项目功能较多，但存在一个核心问题：

> 新用户甚至项目作者自己都无法快速回答：
> - 从哪里启动？
> - CLI / Web / agentd 分别是什么？
> - 配置在哪里？
> - Agent 如何运行？
> - Profile / Domain / Policy / Session 分别是什么？
> - 如何检查环境是否配置正确？
> - 一个任务执行后如何查看结果、状态和历史？

本阶段不新增复杂 Agent 能力。

本阶段唯一目标：

> **把 Universal-Agent 收敛成一个有明确产品入口、明确配置入口、明确运行入口、明确 Session 入口的可使用产品。**

最终要求：

```text
clone
  ↓
install
  ↓
init
  ↓
doctor
  ↓
run
  ↓
session
```

必须形成完整 Golden Path。

---

# 1. 本阶段明确禁止事项

除非为了修复现有功能而必须修改，否则本阶段禁止：

- 新增 Multi-Agent 能力
- 深化 Distributed Runtime
- 新增 Scheduler / Worker 能力
- 新增复杂 Memory 能力
- 新增 Domain
- 新增 Web 大功能
- Rust rewrite
- 大规模修改核心 Agent 语义
- 为了“架构更漂亮”而进行无用户价值的重构

允许：

- 删除重复代码
- 合并重复 abstraction
- 调整目录
- 增加 Facade API
- 增加 CLI
- 增加配置系统
- 增加 doctor
- 增加测试
- 修改 README
- 修复现有 CLI / Runtime / Web 的入口混乱问题

---

# 2. 最终产品心智模型

README、CLI help、代码结构必须统一使用以下概念。

## Agent

用户真正运行的 AI Agent。

用户最终应该能够通过类似：

```bash
ua run "分析当前项目的结构"
```

执行 Agent。

---

## Runtime

Agent 的执行引擎。

负责：

- task execution
- tool/capability execution
- policy enforcement
- observation
- evidence
- evaluation
- session
- persistence
- recovery

Runtime 不是用户首先接触的东西。

---

## Profile

Agent 的工作身份 / 行为配置。

例如：

```text
default
sre
developer
researcher
```

Profile 可以决定：

- system prompt
- enabled capabilities
- domain
- policy
- model 等

用户应该可以：

```bash
ua profile list
ua profile show sre
```

---

## Domain

Agent 可以操作的外部世界。

例如：

```text
kubernetes
git
filesystem
postgres
```

Domain 不是 Agent 本身。

---

## Policy

Agent 的执行边界。

例如：

```text
production restart -> confirmation required
staging restart    -> allowed
```

Policy 必须由 Runtime 强制执行。

LLM 不得绕过 Policy。

---

## Session

一次 Agent 执行的持久化上下文。

Session 应支持至少：

```text
list
show
resume
cancel
```

---

## Web

Web 是 Runtime 的观察 / 管理界面。

Web 不得成为另一套 Agent Runtime。

Web 应通过 Runtime/API 访问 Agent。

---

## agentd

agentd 是长期运行的 Runtime Server / deployment mode。

它不是个人用户第一次运行 Agent 的必要步骤。

个人用户：

```text
ua -> embedded/local Runtime
```

企业部署：

```text
CLI / Web / API
        ↓
      agentd
        ↓
      Runtime
```

---

# 3. P0 Golden Path

必须实现以下完整流程。

## Step 1：安装

用户按照 README：

```bash
git clone <repo>
cd Universal-Agent

uv sync
```

即可完成安装。

README 必须明确写出当前支持的 Python 版本和依赖安装方式。

---

# 4. CLI 主入口

必须存在唯一明确的 CLI 主入口。

推荐：

```bash
ua
```

如果当前项目实际使用的是其他命令名称，也可以保留，但必须：

1. 在 README 第一屏明确说明
2. 所有文档统一使用同一个入口
3. 不允许同时存在多个互相竞争的“主入口”

运行：

```bash
ua --help
```

必须能看到清晰的命令结构。

最少包含：

```text
init
doctor
run
session
config
profile
```

如果现有项目已经有其他命令，不要求立即删除，但需要：

- 标记为 advanced/developer/internal
- 不影响上述 Golden Path

---

# 5. `ua init`

实现：

```bash
ua init
```

要求：

### 5.1 首次运行

如果没有配置：

```text
Universal Agent setup

Model provider:
> OpenAI
  Anthropic
  Ollama
  ...

Model:
...

API key:
...

Default profile:
...
```

具体交互方式不限。

可以是：

- interactive prompt
- 参数
- 环境变量
- config file

但必须有明确、统一的配置方式。

---

### 5.2 init 完成后

必须生成一个用户可理解的配置。

例如：

```text
~/.universal-agent/config.toml
```

或者项目当前已有合理配置目录。

不要求一定使用上述路径，但必须满足：

- 唯一
- 可发现
- 文档明确
- CLI 可查看

---

### 5.3 `ua init` 幂等

重复运行：

```bash
ua init
```

不能破坏现有配置。

应允许：

```text
reuse
modify
reset
```

中的一种合理行为。

---

# 6. 配置系统

必须明确区分：

## Secret

例如：

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Secret 不应该默认明文写入 Git。

---

## Runtime Config

例如：

```toml
[agent]
profile = "default"

[runtime]
max_steps = 30
workspace = "~/.universal-agent"

[policy]
mode = "safe"
```

---

## Domain Config

例如：

```toml
[domains.kubernetes]
context = "production"
```

---

## Profile Config

例如：

```text
profiles/
  default.toml
  sre.toml
```

具体文件格式可以根据现有项目决定。

但用户必须能够回答：

> “我的模型在哪里配置？”

> “我的 Profile 在哪里配置？”

> “我的 Policy 在哪里配置？”

> “我的 Domain 在哪里配置？”

---

# 7. `ua config`

必须实现：

```bash
ua config
```

至少能够展示当前生效配置。

例如：

```text
Universal Agent Configuration

Model
  Provider: openai
  Model: xxx

Agent
  Profile: default

Runtime
  Max steps: 30

Policy
  Mode: safe

Domains
  kubernetes: disabled
  git: enabled

Config:
  ~/.universal-agent/config.toml
```

注意：

**不要输出 API key / secret。**

可以显示：

```text
OPENAI_API_KEY: configured
```

而不能显示完整 key。

---

# 8. `ua doctor`

这是 P0 的核心功能。

实现：

```bash
ua doctor
```

必须检查至少：

```text
[Environment]
✓ Python
✓ dependencies

[Configuration]
✓ config found
✓ config valid

[Model]
✓ provider configured
✓ model configured
✓ credentials configured

[Runtime]
✓ runtime initialization
✓ persistence

[Profiles]
✓ default profile

[Domains]
✓ enabled domains can initialize

[Policy]
✓ policy loaded
```

如果失败：

```text
✗ OpenAI API key missing
```

必须给出下一步建议：

```text
Set OPENAI_API_KEY or run `ua init`.
```

原则：

> doctor 的目标不是告诉用户“哪里错了”，而是告诉用户“怎么修”。

---

# 9. `ua run`

这是整个项目最重要的 CLI。

必须支持：

```bash
ua run "分析当前项目"
```

---

## 9.1 最简单运行

第一次用户执行：

```bash
ua run "Hello"
```

必须可以看到：

```text
Agent started
Session: <id>

...
Agent completed

Status: success
```

不能要求用户提前启动：

```text
agentd
web server
worker
scheduler
```

---

## 9.2 Profile

必须支持：

```bash
ua run --profile default "..."
```

如果有 profile：

```bash
ua run --profile sre "..."
```

---

## 9.3 Session

运行时必须创建 Session。

结束后显示：

```text
Session: abc123
Status: success
Duration: 12.4s
Steps: 7
Tool calls: 4
Evidence: 3
```

具体字段可以根据现有 Runtime 能力调整。

---

# 10. `ua session`

至少实现：

```bash
ua session list
ua session show <id>
ua session resume <id>
ua session cancel <id>
```

---

## 10.1 list

例如：

```text
SESSION       STATUS      CREATED
abc123        completed   10:21
def456        running     10:25
ghi789        failed      10:30
```

---

## 10.2 show

例如：

```text
Session: abc123
Status: completed

Goal:
Analyze current project

Timeline:

10:21:01 task started
10:21:02 model decision
10:21:03 tool call
10:21:05 observation
10:21:06 evidence
10:21:10 evaluation
10:21:11 completed

Evidence: 3
Actions: 4
```

如果项目已经有 Event / Evidence / Trace 系统，应优先复用，不要重新实现第二套。

---

## 10.3 resume

如果现有 Runtime 支持 pause/resume：

```bash
ua session resume <id>
```

必须恢复原 Session，而不是创建一个完全无关的新任务。

---

# 11. Profile CLI

至少：

```bash
ua profile list
ua profile show <name>
```

例如：

```text
$ ua profile list

default
sre
```

```text
$ ua profile show sre

Profile: sre

Model:
  ...

Domains:
  kubernetes
  prometheus
  git

Policy:
  production-safe
```

---

# 12. Web 的处理方式

本阶段不要求重做 Web。

但必须明确：

> Web 是管理 / 观察 Runtime 的 UI。

README 必须说明：

```text
CLI:
用于运行 Agent。

Web:
用于查看 Session、Event、Evidence、Action。

agentd:
用于长期运行 Runtime 服务。
```

必须消除：

```text
到底应该启动 Web 还是 CLI？
```

这个问题。

---

# 13. agentd 的处理方式

保留现有 agentd 能力。

但 README 必须明确：

## Personal

```text
ua run ...
```

不需要 agentd。

## Server

```text
agentd
```

用于：

- 长期运行
- API
- Web
- 多用户
- worker 等

如果当前 agentd 启动方式复杂，可以增加：

```bash
ua server start
```

作为用户友好的 wrapper。

是否增加 wrapper 由实现者根据现有架构决定。

---

# 14. Runtime Facade

如果当前代码要求用户直接构造大量 Runtime 内部对象，则需要增加一个简单 Facade。

目标：

用户应该可以在 Python 中：

```python
from universal_agent import Agent

agent = Agent.from_profile("default")

result = await agent.run(
    "分析当前项目"
)
```

具体 API 名称可以根据当前代码调整。

但是原则必须满足：

> 普通用户不需要了解 Runtime 内部的 Host / Service / Coordinator / DomainManager 等内部组件才能运行一个 Agent。

---

# 15. Core API 边界

不要重写现有 Runtime。

需要识别并明确以下核心边界：

```text
Agent
Runtime
Capability
Tool
Policy
Action
Observation
Evidence
Evaluator
Recovery
Session
Profile
Domain
```

输出一份：

```text
docs/RUNTIME_CONTRACT.md
```

说明每个概念：

```text
是什么
谁创建
谁调用
生命周期
是否持久化
是否允许绕过 Policy
```

---

# 16. README 重写要求

README 第一屏必须让一个完全不了解项目的人在 30 秒内知道：

1. Universal-Agent 是什么
2. 为什么使用它
3. CLI 是什么
4. Web 是什么
5. agentd 是什么
6. 最简单的运行命令

推荐结构：

```markdown
# Universal-Agent

一句话定位。

## Quick Start

安装

配置

doctor

第一次运行

## Configuration

## Profiles

## Sessions

## Domains

## Policy

## Web

## Server / agentd

## Architecture

## Development
```

Architecture 不得放在 Quick Start 前面。

---

# 17. README Golden Path 必须真实验证

不能只写文档。

Codex 必须实际执行：

```bash
uv sync
ua init
ua doctor
ua run "..."
ua session list
```

并记录实际结果。

如果命令名称不同，以项目最终实际 CLI 为准。

---

# 18. Automated Tests

必须增加 CLI smoke tests。

至少覆盖：

```text
test_cli_help
test_init
test_config
test_doctor
test_run
test_session_list
test_profile_list
```

如果 LLM 调用无法在 CI 中进行：

必须提供 fake/mock model。

CI 不应该依赖真实 API key。

---

# 19. Fake Model

为了让 Runtime 可以稳定测试，必须有一个 Fake Model / Mock Model。

例如：

```text
FakeModel
  ↓
ToolCall
  ↓
FakeTool
  ↓
Observation
  ↓
Evaluation
  ↓
Success
```

这样可以测试：

```text
Agent loop
Session
Persistence
Policy
Evidence
Evaluation
Recovery
```

而不依赖真实 LLM。

---

# 20. Golden Path Integration Test

必须有一个完整 integration test：

```text
create config
    ↓
create agent
    ↓
run task
    ↓
tool call
    ↓
observation
    ↓
evidence
    ↓
evaluation
    ↓
success
    ↓
persist session
    ↓
session list
    ↓
session show
```

这个测试是 P0 最重要的自动化测试。

---

# 21. Policy 验收

必须有一个明确测试：

```text
Agent requests dangerous action
        ↓
Policy denies
        ↓
Tool MUST NOT execute
```

以及：

```text
Agent requests production mutation
        ↓
Policy = confirmation_required
        ↓
Runtime pauses/waits
        ↓
Human approves
        ↓
Tool executes
```

必须证明：

> LLM 无法绕过 Policy 直接调用 mutation capability。

---

# 22. Session 验收

必须验证：

```text
run
 ↓
persist
 ↓
process restart
 ↓
session still exists
```

如果支持：

```text
pause
 ↓
restart process
 ↓
resume
```

必须有 integration test。

---

# 23. 错误处理

以下错误不能出现 Python stack trace 直接甩给普通用户：

```text
missing API key
invalid config
model unavailable
domain unavailable
policy denied
tool failed
session not found
```

CLI 应输出：

```text
Error:
Kubernetes domain could not initialize.

Reason:
No kubeconfig/context found.

Try:
kubectl config get-contexts
```

原则：

> CLI 错误信息必须告诉用户下一步怎么办。

---

# 24. `--help` 验收

以下命令都必须有可读 help：

```bash
ua --help
ua init --help
ua run --help
ua doctor --help
ua config --help
ua session --help
ua profile --help
```

Help 不得暴露大量内部实现概念。

---

# 25. 用户验收场景

完成后，找一个没有参与开发的人。

给他一台干净环境。

只告诉他：

> “这是 Universal-Agent，README 在这里。”

不要口头解释。

他必须能够完成：

```text
1. 安装
2. 配置模型
3. doctor
4. 执行一个 Agent task
5. 找到 Session
6. 查看执行过程
```

目标：

> **10 分钟内完成。**

如果他问：

> “我到底应该运行 CLI、Web 还是 agentd？”

则视为 P0 未通过。

---

# 26. Definition of Done

本阶段只有同时满足以下条件才算完成：

### Product

- [ ] 有唯一明确 CLI 主入口
- [ ] README 第一屏明确入口
- [ ] 有 Quick Start
- [ ] 有明确配置位置
- [ ] 有 `init`
- [ ] 有 `doctor`
- [ ] 有 `run`
- [ ] 有 `session`
- [ ] 有 `profile`
- [ ] 明确 Web 定位
- [ ] 明确 agentd 定位

### Runtime

- [ ] Agent 可以通过一个简单 Facade 启动
- [ ] Session 自动创建
- [ ] Session 可以查询
- [ ] Runtime persistence 正常
- [ ] Policy 仍然是强制边界
- [ ] Evidence / Evaluation 复用现有实现
- [ ] Recovery 不绕过 Policy

### Testing

- [ ] CLI smoke tests
- [ ] Fake Model
- [ ] Golden Path integration test
- [ ] Policy denial test
- [ ] Session persistence test
- [ ] CI 不依赖真实 LLM API

### Documentation

- [ ] README 重写
- [ ] Configuration 文档
- [ ] Profile 文档
- [ ] Session 文档
- [ ] CLI 文档
- [ ] Runtime Contract
- [ ] Web / agentd 定位说明

---

# 27. 最终人工验收命令

在干净环境执行：

```bash
git clone <repo>
cd Universal-Agent

uv sync

uv run ua --help

uv run ua init

uv run ua doctor

uv run ua run "分析当前项目的结构"

uv run ua session list
```

然后：

```bash
uv run ua session show <session-id>
```

最终必须能够回答：

```text
我从哪里启动？
    → ua run

我在哪里配置？
    → config

我的配置是否正确？
    → ua doctor

我的 Agent 是什么？
    → Profile + Runtime

Agent 做了什么？
    → Session / Events / Evidence

Web 是干嘛的？
    → Runtime observation / management

agentd 是干嘛的？
    → Long-running server deployment
```

---

# 28. 给 Codex 的执行要求

不要一次性大规模重写。

按照以下顺序执行：

```text
Phase 1
Repository audit
        ↓
Phase 2
CLI / configuration normalization
        ↓
Phase 3
init / doctor / config
        ↓
Phase 4
run / session / profile
        ↓
Phase 5
Facade API
        ↓
Phase 6
tests
        ↓
Phase 7
README
        ↓
Phase 8
clean-room validation
```

每个 Phase 完成后：

1. 运行测试
2. 检查 git diff
3. 不破坏已有 Runtime capability
4. 不引入无关重构
5. 更新 TODO / progress
6. 再进入下一阶段

---

# 29. 最终原则

本阶段不是：

> “让代码变得更先进。”

而是：

> **让一个第一次接触 Universal-Agent 的人知道自己该做什么。**

判断成功的唯一标准：

```text
我 clone 下来
    ↓
我知道运行什么
    ↓
我知道配置什么
    ↓
我知道怎么执行
    ↓
我知道结果在哪里
    ↓
我知道失败怎么排查
```

如果以上流程成立，P0 完成。

否则继续修。

**不要在 P0 完成之前增加新的 Agent 能力。**