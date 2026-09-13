# Universal-Agent 产品心智与 P0 决策

本文档是 P0 产品化阶段的产品心智与入口决策记录。README、CLI help、`init` 默认值、`run` 无参路径必须与本文保持同一套概念；后续阶段（P1+）引入新概念前必须先更新本文。

状态来源：`Universal-Agent P0 产品化与可验收标准.md`（下称 P0 标准）§2 / §4 / §12 / §13。

---

## 1. 概念表（唯一词汇表）

| 概念 | 是什么 | 用户何时接触 |
| --- | --- | --- |
| **Agent** | 用户真正运行的 AI Agent。用户通过 `agent run "goal"` 使用它。 | 第一天 |
| **Runtime** | Agent 的执行引擎：task/tool 执行、policy 强制、observation/evidence/evaluation、session、persistence、recovery。不是用户首先接触的东西。 | 通过 Session/Evidence 间接接触 |
| **Profile** | Agent 的工作身份/行为配置：system prompt、模型、启用 Domain、Policy 等。 | `agent init`、`agent profile list/show`、`agent run --profile` |
| **Domain** | Agent 可操作的外部世界（kubernetes、git、filesystem…）。Domain 不是 Agent 本身。 | Profile 里配置；`agent doctor` 检查 |
| **Policy** | Agent 的执行边界，由 Runtime 强制执行，LLM 不得绕过。 | `agent run` 遇到危险动作时感知（confirm/deny） |
| **Session** | 一次 Agent 执行的持久化上下文，支持 list/show/resume/cancel。 | `agent session list/show/resume/cancel` |
| **Web** | Runtime 的观察/管理界面，通过 Runtime API 访问，不是另一套 Runtime。 | 需要可视化查看 Session/Event/Evidence 时 |
| **agentd** | 长期运行的 Runtime Server（部署模式）。个人用户不需要它。 | 企业部署 / 多用户 / API 服务时 |

## 2. 入口决策

### D1：唯一 CLI 主入口是 `agent`，不改名为 `ua`

- 依据 P0 标准 §4：允许保留项目实际使用的命令名，条件是 README 第一屏明确说明、所有文档统一、不允许竞争入口。
- 现状：`pyproject.toml [project.scripts] agent = "universal_agent_cli:main"`，全仓测试与文档以 `agent` 为准。
- 约束：
  - README 第一屏必须写明"CLI 命令是 `agent`"；
  - 所有文档、help 示例统一使用 `agent`；
  - 删除/修正指向已失效入口（如 `python -m universal_agent.cli`）的文档。

### D2：通用默认 Profile 是 `default`，与 K8s 解耦

- 依据：P0 标准要求第一次用户 `agent run "Hello"` 即可完成，不要求 kubeconfig/集群/真 LLM。
- 现状：默认 Profile 是 `local-kubernetes`（`LOCAL_PROFILE_NAME`）+ scripted `inspect_workload`，隐含 K8s 语义。
- 决策：
  1. 新增通用默认 Profile `default`：scripted 模型 + 本地/fake Domain + 基础 Policy，不隐含 K8s 健康检查；
  2. `agent init` 默认产出 `default` Profile；
  3. `agent run`（P2 改造后）不带 `--profile` 时使用 `default`；
  4. `local-kubernetes` 保留，作为 K8s 场景的显式选择（`agent init --domain-backend kubectl|kubernetes_api` 或 advanced 命令），不删除。

### D3：高级面保留但标 advanced，不删除

- `kubernetes` / `distributed` / `eval` / `ecosystem` / `multi-agent` / `memory` / `tui` / `serve`（agentd wrapper）/ `repair` 等命令保留现有能力。
- 约束：
  - `agent --help` 中 Golden Path 命令（init / doctor / run / session / config / profile）在顶层优先展示，其余分组标 `advanced`；
  - 这些命令不参与 Golden Path 验收，也不得阻塞它（例如：没有 kubeconfig 时 `agent run` 必须可用）。
- 2026-09-11 冻结补充：
  - `distributed` 标注 advanced/experimental，且仅覆盖本地 queue/lock/worker 原语，不是生产 HA；
  - `ecosystem`（package registry）标注 advanced/experimental，非 Golden Path 必需；新 ecosystem 功能冻结，除非它直接解锁 Domain SDK 验证；现有 catalog/verify 行为保持测试覆盖；
  - Multi-Agent 明确标注 optional：Domain 是能力/知识边界，Agent 是自治执行边界；多 Domain 通过 Domain Composition 在一个 Agent 内完成，Multi-Agent 生产化推迟到单 Agent 多 Domain 行为验证完成之后，未来实现必须使用结构化 task/result/evidence 契约而非聊天转写。

## 3. 概念 → 用户界面落点（验收对照表）

P0 标准要求用户能回答"我从哪里启动/配置/排查"。各概念必须落到：

| 概念 | init | doctor | config | run | session | profile |
| --- | --- | --- | --- | --- | --- | --- |
| Agent | — | ✓ 环境就绪 | — | ✓ `agent run "goal"` | — | — |
| Runtime | ✓ 生成 runtime 配置 | ✓ 初始化/持久化检查 | ✓ 展示 runtime 段 | ✓ 创建 Session | ✓ list/show/resume/cancel | — |
| Profile | ✓ 默认 `default` | ✓ 默认 profile 存在 | — | ✓ `--profile` 可选 | — | ✓ list/show |
| Domain | ✓ 默认不含 K8s | ✓ 已启用 Domain 可初始化 | — | — | — | ✓ show 列出 Domains |
| Policy | — | ✓ policy loaded | — | ✓ deny/confirm 行为 | ✓ 事件可见 | ✓ show 列出 Policy |
| Model | ✓ provider/name/key | ✓ credentials 检查 | ✓ 脱敏展示 | — | — | ✓ show 列出 Model |
| Session 持久化 | ✓ store 配置 | ✓ persistence 检查 | — | ✓ 结束摘要含 Session id | ✓ 跨进程可查 | — |

## 4. 变更纪律

- 修改产品心智或入口语义：先改本文，再动代码，README/--help 随对应卡片同步。
- 新增命令：默认进入 advanced 分组，除非明确属于 Golden Path。
- 本文档与实际行为不一致时，以 bug 对待并修复其一。

## 5. P0 实施决策补充（2026-09-11）

### D4：配置目录布局（Golden Path）

- `agent init` 默认写入 `./universal-agent/`：
  - `universal-agent/profile.json`：AgentProfile 配置（Runtime 消费）；
  - `universal-agent/config.json`：用户可读的 agent 级设置（environment/profile/model/policy/runtime/domains）。
- 发现阶段（`default_profile_config_path`）：`$AGENT_CONFIG_DIR/profile.json`（容器约定）→ `./universal-agent/profile.json`（项目）→ `~/.universal-agent/profile.json`（用户级）。
- 语义：`ua init` 幂等 —— 已存在且无 `--force` 时 reuse（不破坏现有配置，返回 `status=reused`）；`--force` 重写并保留 `.bak` 备份。
- store 默认 `file`，位于 `./.universal-agent/store`（未显式指定时），保证同一工作区内 Session 跨进程可查；`agent config` 以绝对路径展示解析后位置。

### D5：人读输出默认、机器 JSON 可选

- Golden Path 命令默认输出人读文本：`run`（`--output text|json`，默认 text）、`session list/show`（`--output`，默认 text）、`profile list/show`（默认 text）、`doctor`（默认 text）、`init`（`--output-format`，默认 text）。
- 裸 `agent config` 输出人读配置视图；`agent config show` 保持 JSON（advanced）。
- `agent doctor` 默认 `--fail-on error`：存在 ✗ 时退出码 1（测试中可用 `--fail-on never` 覆盖）。
- 生产 `run`/`doctor`/`session`/`config`/`profile` 在无注入 service 时经 embedded agentd；embedded launcher 无显式 `--profile-config` 时应用标准配置发现，保证 Golden Path 命令命中同一份配置。
