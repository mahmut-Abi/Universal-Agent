# /goal Prompt — Project Optimization Execution

> Date: 2026-09-11  
> Purpose: copy-paste prompt for running a `/goal` that implements the backlog in `docs/revision/README.md` and `docs/revision/2026-09-11-project-optimization-recommendations.md`.

## Recommended Usage

Start with the conservative Sprint 1 prompt first. After it finishes cleanly, run a second `/goal` for Sprint 2. The full prompt is included for cases where you want one long-running goal to continue through both phases.

---

## Full Prompt — Sprint 1 + Sprint 2

```text
/goal

你现在在 `/Users/mahmut/Documents/Universal-Agent` 仓库中工作。

目标：根据 `docs/revision/README.md` 和 `docs/revision/2026-09-11-project-optimization-recommendations.md`，系统性完成 Universal-Agent 的产品化修改和优化。优先完成 P0.1/P1 用户可见体验，不要扩展新的 Agent 能力，不要继续做 P4/P6/P7 的新功能。

最高优先级原则：

1. 先让 Golden Path 真正顺畅：
   clone -> install -> init -> doctor -> run -> session
2. 新用户不需要理解 Kubernetes、agentd、distributed、multi-agent、ecosystem，也能完成第一次运行。
3. 默认 `agent run "Hello"` 不应表现为 Kubernetes cluster/workload/pod 检查，除非用户显式选择 Kubernetes profile。
4. Runtime 仍然必须拥有状态、控制流、Policy、Evidence、Evaluation；不能把完成判定或安全边界交给 LLM。
5. 每次完成一个 backlog item，更新 `docs/revision/README.md` 中对应 checkbox，从 `[ ]` 改为 `[x]`，并在 Completion Log 添加证据。
6. 不要做大规模无关重构；每次只改与当前 issue 直接相关的文件。
7. 每个阶段完成后必须运行相关测试；若修改核心/CLI，至少运行 ruff + 相关 pytest；最终阶段运行完整质量门。

主要参考文件：

- `AGENTS.md`
- `Universal-Agent-P0-spec.md`
- `docs/product.md`
- `docs/revision/README.md`
- `docs/revision/2026-09-11-p0-golden-path-validation.md`
- `docs/revision/2026-09-11-project-optimization-recommendations.md`
- `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- `docs/revision/2026-08-31-remaining-todo.md`

先执行 Sprint 1，然后 Sprint 2。不要进入 Sprint 3+，除非 Sprint 1 和 Sprint 2 已完成并验证。

Sprint 1 — Product Consistency，必须完成：

- UA-P01-001 — Reconcile config location semantics in all contexts
- UA-P01-002 — Make `default` profile truly domain-neutral or explicitly rename it
- UA-P01-003 — Split beginner and advanced CLI help surfaces
- UA-P01-004 — Enrich `agent config` and `agent profile show`
- UA-TEST-001 — Add clean-room config and Golden Path regression tests
- UA-TEST-002 — Add default-profile domain-neutral regression

Sprint 2 — Better Runtime UX，完成 Sprint 1 后继续：

- UA-P01-005 — Make `agent session show` human-first
- UA-P01-006 — Add `agent session explain <id>` for failures and waiting sessions
- UA-P01-007 — Standardize repairable CLI errors
- UA-P01-008 — Add doctor final next-step guidance
- UA-P1-001 — Add reproducible local Golden Demo script

具体验收标准：

1. 配置路径一致：
   - `agent init` 实际写入位置、`agent init --help`、README、`docs/product.md`、`agent config` 输出必须一致。
   - 明确处理：
     - clean cwd
     - clean HOME
     - `$AGENT_CONFIG_DIR`
     - 显式 `--output`
     - 如果新增 `--global`，必须有测试和文档。

2. 默认 profile 语义正确：
   - 默认 `agent init` 生成的 `default` profile 不应隐式绑定 Kubernetes 用户心智。
   - `agent run "Hello"` 的 text output 和 `agent session show <id>` 默认不应出现：
     - `kubernetes`
     - `cluster`
     - `workload`
     - `pod`
     - `healthy=True`
     - `workload health criteria satisfied`
   - Kubernetes fake/local/real profile 可以保留，但必须显式选择，例如 `local-kubernetes` 或 `sre-kubernetes`。

3. CLI help 清晰：
   - `agent --help` 必须优先展示 Golden Path 命令：
     - init
     - doctor
     - run
     - session
     - config
     - profile
   - advanced/developer/internal 命令必须明显标记为 advanced 或 experimental。
   - `agent init --help` 不应让新用户第一屏看到大量 distributed/kubernetes/backend/internal flags；如果保留这些 flags，必须分组并清楚标记 Advanced。

4. `agent config` 输出增强：
   默认 text 输出必须显示：
   - Active profile
   - Model provider/model name
   - Credential status，但不能输出 secret value
   - Runtime max steps
   - Store backend 和 resolved absolute store path
   - Policy mode / loaded policies
   - Enabled domains
   - Config file paths
   - Discovery order

5. `agent profile show` 输出增强：
   默认 text 输出必须显示：
   - Profile name/version/description
   - Model
   - Domains
   - Policy
   - Runtime/store summary

6. `agent session show` 人读优化：
   - 默认 text 输出先给 Summary / What happened。
   - 原始 Runtime event timeline 仍可通过 `agent session events <id>` 或 JSON 查看。
   - Evidence count、Action count、terminal reason 不得丢失。

7. `agent session explain <id>`：
   - 新增或实现失败/等待会话解释命令。
   - 输出使用：
     - Error
     - Reason
     - Try
   - 至少覆盖：
     - policy waiting / confirmation required
     - session not found
     - session is not waiting
     - invalid finish decision
     - evaluator did not complete
     - missing model credentials
     - domain/tool failure
   - 只做 read-model/service projection，不改变 Runtime state。

8. CLI 错误统一：
   - 普通用户不应看到裸 Python stack trace。
   - common failures 输出必须包含 actionable `Try:`。
   - 保持 JSON 输出机器可读。

9. Doctor next step：
   - `agent doctor` 成功时末尾提示下一步，例如 `agent run "Hello"`。
   - warning/error 时给具体修复命令或环境变量建议。
   - `--fail-on` 行为保持不变。

10. Demo script：
    - 新增 `scripts/demo-local.sh`。
    - 脚本应在 clean HOME / 临时工作区可重复运行：
      - init
      - doctor
      - run
      - session list
      - session show
    - README Quick Start 命令必须与 demo script 对齐。

测试要求：

- 新增或更新 integration/unit tests 覆盖以上行为。
- 必须覆盖 clean HOME / clean cwd。
- 必须覆盖默认 profile 不泄露 Kubernetes 心智。
- 必须覆盖 config/profile/session text output。
- 必须覆盖 secret masking。
- 必须覆盖 CLI help 的 Golden Path 优先级。
- CI 不得依赖真实 LLM API key。
- CI 不得依赖真实 Kubernetes cluster。
- 使用 scripted/fake model 和 fake/local domain 完成测试。

推荐测试命令：

```bash
uv run ruff check src tests
uv run pytest tests/integration/test_cli.py tests/integration/test_cli_agentd_client.py tests/unit/test_security_secrets.py -q
uv run pytest tests/integration/test_p0_golden_path.py tests/integration/test_facade_golden_path.py -q
```

如果修改核心 Runtime 或 Profile/Host 装配，还需要运行：

```bash
uv run mypy
uv run pytest tests/ -q
```

最终完成 Sprint 1 + Sprint 2 后，运行完整质量门：

```bash
uv run ruff format --check src tests examples
uv run ruff check .
uv run mypy
uv run pytest tests/ -q
```

工作方式：

1. 先读取 `docs/revision/README.md`，确认 Sprint 1/Sprint 2 item。
2. 检查当前 git status，注意已有未提交改动，不要覆盖用户工作。
3. 注意 pi-lens 提示：如果 `src/universal_agent/domains/local/domain.py` 或 `src/universal_agent/domains/local/cli_runtime.py` 已被自动修改，编辑前必须重新读取。
4. 每次只处理一个 backlog item 或紧密相关的一小组 item。
5. 每完成一个 item：
   - 跑相关测试。
   - 更新 `docs/revision/README.md` checkbox。
   - 在 Completion Log 写明完成日期、测试命令、主要文件。
6. 如果发现某个 item 已经完成，不要重复实现；直接验证并标记完成。
7. 如果发现文档与实际行为冲突，优先修到一致；无法判断时在 `docs/revision/README.md` 对应 item 下添加 Blocked/Decision needed。
8. 不要新增 Multi-Agent、Distributed、Ecosystem、Scheduler、Worker、Marketplace、复杂 Memory 或新的生产基础设施能力。
9. 不要大规模重写核心 Runtime；只做支撑 P0.1/P1 产品体验所需的最小修改。
10. 不要提交 git commit，除非用户明确要求。

Definition of Done：

- Sprint 1 所有对应 checkbox 已完成或有明确 Blocked 说明。
- Sprint 2 所有对应 checkbox 已完成或有明确 Blocked 说明。
- `docs/revision/README.md` Completion Log 已更新。
- README / docs/product.md / CLI help / 实际行为一致。
- 默认 `agent run "Hello"` 是新用户友好的本地/通用体验。
- `agent init -> agent doctor -> agent run -> agent session list/show` 在 clean HOME 下可复现。
- 相关测试通过，最终质量门尽可能通过；若无法跑完整质量门，必须说明原因和已跑过的替代验证。

```

---

## Conservative Prompt — Sprint 1 Only

Recommended first run:

```text
/goal

只完成 `docs/revision/README.md` 的 Sprint 1，不进入 Sprint 2 或后续阶段。

目标：
完成以下 P0.1 Product Consistency items：

- UA-P01-001 — Reconcile config location semantics in all contexts
- UA-P01-002 — Make `default` profile truly domain-neutral or explicitly rename it
- UA-P01-003 — Split beginner and advanced CLI help surfaces
- UA-P01-004 — Enrich `agent config` and `agent profile show`
- UA-TEST-001 — Add clean-room config and Golden Path regression tests
- UA-TEST-002 — Add default-profile domain-neutral regression

严格约束：

- 不新增 Multi-Agent / Distributed / Ecosystem / Scheduler / Worker 能力。
- 不做无关重构。
- 不修改核心 Runtime 语义，除非默认 profile/domain-neutral 需要最小适配。
- 每完成一个 item 就更新 `docs/revision/README.md` checkbox 和 Completion Log。
- 最终运行相关 CLI/integration tests，并报告结果。

主要参考文件：

- `AGENTS.md`
- `Universal-Agent-P0-spec.md`
- `docs/product.md`
- `docs/revision/README.md`
- `docs/revision/2026-09-11-p0-golden-path-validation.md`
- `docs/revision/2026-09-11-project-optimization-recommendations.md`

具体验收标准：

1. `agent init` 实际写入位置、`agent init --help`、README、`docs/product.md`、`agent config` 输出必须一致。
2. 默认 `agent run "Hello"` 不应表现为 Kubernetes cluster/workload/pod 检查。
3. `agent --help` 必须优先展示 Golden Path 命令：init / doctor / run / session / config / profile。
4. advanced/developer/internal 命令必须明显标记为 advanced 或 experimental。
5. `agent init --help` 不应让新用户第一屏看到大量 distributed/kubernetes/backend/internal flags；如果保留这些 flags，必须分组并清楚标记 Advanced。
6. `agent config` 默认 text 输出必须显示 Active profile、Model、credential status、Runtime store absolute path、Policy、Domains、Config paths、Discovery order。
7. `agent profile show default` 默认 text 输出必须显示 Profile、Model、Domains、Policy、Runtime/store summary。
8. 新增/更新测试覆盖 clean HOME、clean cwd、config discovery、default profile domain-neutral、CLI help Golden Path priority、secret masking。
9. CI 不依赖真实 LLM API key，也不依赖真实 Kubernetes cluster。

工作方式：

1. 先读取 `docs/revision/README.md`，确认 Sprint 1 item。
2. 检查当前 git status，不要覆盖用户工作。
3. 如果编辑 `src/universal_agent/domains/local/domain.py` 或 `src/universal_agent/domains/local/cli_runtime.py`，必须先重新读取，因为 pi-lens 曾自动修改它们。
4. 每次只处理一个 backlog item 或紧密相关的一小组 item。
5. 每完成一个 item，更新 `docs/revision/README.md` checkbox 和 Completion Log。
6. 如果发现 item 已经完成，验证后标记完成，不要重复实现。
7. 不要提交 git commit，除非用户明确要求。

推荐验证命令：

```bash
uv run ruff check src tests
uv run pytest tests/integration/test_cli.py tests/integration/test_cli_agentd_client.py tests/unit/test_security_secrets.py -q
uv run pytest tests/integration/test_p0_golden_path.py tests/integration/test_facade_golden_path.py -q
```

如果修改核心 Runtime 或 Profile/Host 装配，还需要运行：

```bash
uv run mypy
uv run pytest tests/ -q
```

Definition of Done：

- Sprint 1 对应 checkbox 全部完成或有明确 Blocked/Decision needed 说明。
- `docs/revision/README.md` Completion Log 已更新。
- README / docs/product.md / CLI help / 实际行为一致。
- 默认 `agent run "Hello"` 是新用户友好的本地/通用体验。
- 相关测试通过；无法跑完整测试时，必须说明原因和已跑过的替代验证。

```

---

## Follow-Up Prompt — Sprint 2 Only

Use this after Sprint 1 is complete:

```text
/goal

只完成 `docs/revision/README.md` 的 Sprint 2，不进入 Sprint 3 或后续阶段。

前置条件：Sprint 1 已完成或已明确 Blocked。

目标：
完成以下 Better Runtime UX items：

- UA-P01-005 — Make `agent session show` human-first
- UA-P01-006 — Add `agent session explain <id>` for failures and waiting sessions
- UA-P01-007 — Standardize repairable CLI errors
- UA-P01-008 — Add doctor final next-step guidance
- UA-P1-001 — Add reproducible local Golden Demo script

严格约束：

- 不新增 Multi-Agent / Distributed / Ecosystem / Scheduler / Worker 能力。
- 不做无关重构。
- 不改变核心 Runtime 状态机语义。
- `session explain` 必须是 read-model/service projection，不改变 Runtime state。
- 每完成一个 item 就更新 `docs/revision/README.md` checkbox 和 Completion Log。
- 不要提交 git commit，除非用户明确要求。

具体验收标准：

1. `agent session show` 默认 text 输出先给 Summary / What happened，再保留技术细节入口。
2. 原始 Runtime event timeline 仍可通过 `agent session events <id>` 或 JSON 查看。
3. `agent session explain <id>` 至少覆盖 policy waiting、session not found、session is not waiting、invalid finish decision、evaluator did not complete、missing model credentials、domain/tool failure。
4. 普通 CLI 错误统一输出 Error / Reason / Try，避免裸 Python stack trace。
5. `agent doctor` 成功时给下一步命令；warning/error 时给修复命令或环境变量建议。
6. 新增 `scripts/demo-local.sh`，在 clean HOME / 临时工作区可重复运行 init、doctor、run、session list、session show。
7. README Quick Start 与 demo script 对齐。
8. 相关测试通过。
```
