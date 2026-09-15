# 严格审计报告（2026-09-15）

审计范围：`AGENTS.md`、`universal-agent-runtime-domain-runtime-design.md`（架构目标）、
`docs/specs/Universal-Agent-P0-spec.md`、`docs/specs/P1 Kubernetes SRE Vertical — Codex 可验收实施规格.md`。

验证手段：全量测试套件（xdist 并行，exit 0，约 1650+ tests，仅 6 个环境门控 skip）、
ruff check / mypy --strict 全绿（522 文件）、Golden Path 实际执行（`ua init/doctor/config/run`）、
SDK Facade 实际复现、逐条 spec 对照。

---

## 总体结论

项目整体质量高于绝大多数同类仓库：ruff + mypy strict 零告警、测试纪律严格、
事件模型 / Policy / Evidence / Recovery 语义与 AGENTS.md 核心原则高度一致、
P0/P1 spec 的 DoD 大部分已闭环。
但存在 1 个功能性 bug、2 处架构边界违规、若干 spec 偏差。

---

## 🔴 必须处理的问题

### A1. SDK Facade Golden Path 对默认 profile 是坏的（P0 spec §14 违规）

实测复现：

```text
uv run python -c "Agent.from_profile('default').run('Hello')"
→ ValueError: configured domain local does not match kubernetes
```

根因：`src/universal_agent/facade.py::_build_service`（约 L170-181）当 profile 无
`domain_package_paths` 时无条件回退到 `domains.kubernetes.cli_runtime.build_configured_service`，
而该函数无论 profile domain 是什么都构建 `KubernetesRemediationDomain`。
CLI 有正确的 local/kubernetes dispatch（`universal_agent_cli/__init__.py:87`），Facade 没有。

- `facade.py` 自己的文档示例写的正是 `Agent.from_profile("default")` —— 文档承诺的行为不可用。
- `tests/integration/test_facade_golden_path.py` 只测 kubernetes profile，local 默认路径零覆盖。
- P0 spec §14 明确要求 Facade 能跑 `Agent.from_profile("default")`，
  §26 DoD "Agent 可以通过一个简单 Facade 启动" 实际未达成。

修复：把 CLI 的 dispatch 逻辑（local → `build_local_profile_service`）提取为共享函数，
facade 复用；补一条 local profile Facade 集成测试。

### A2. Domain 反向泄漏进 kernel 层（AGENTS.md §4.7 违规）— ✅ 已修复（2026-09-15）

- `src/universal_agent/facade.py` import `domains.kubernetes.*`（kernel/compat 层依赖具体 domain）；
- `src/universal_agent/evaluation/dispatch.py` L386-404 在 kernel evaluation dispatch
  里硬编码 kubernetes 场景 tags；
- 全仓库无 `if domain == "kubernetes"` 分支进入 core runtime（这点合格），
  但 facade/eval dispatch 属于 `universal_agent` 主包，按 §4.7
  "Kernel should depend on interfaces" 的标准属于违规。
  domain 注册/发现机制（`domain_package_paths`、`DomainLoader`）已存在，
  正确做法是让默认 domain 通过 profile/domain-package 解析，而不是 facade 硬编码回退。

**修复结果（UA-AUDIT-002）**：

- 新增 `src/universal_agent/domains/profile_service.py` 作为唯一的 profile→service
  composition 点（`build_configured_service` / `build_default_service` / `build_probe_service`），
  位于 domains 包内，kernel 不再 import 任何具体 domain；
- `facade.py` 与 `agentd/__main__.py` 均委托该 composition 点，
  同时消除了 agentd 中第三份重复的 dispatch 拷贝；
- `evaluation/dispatch.py` 仅使用字符串 tags（无具体 domain import），
  且 suite 名 `kubernetes` 是 P1 spec §31 的用户契约，保留并在此记录为可接受；
- `agentd/_routes_kubernetes.py` 是 server 组合层的 domain 特性路由接线，
  移入 domains 包会造成 domain→server 反向依赖，判定为组合层合法归属，在此记录。

### A3. 实际 roadmap 与 AGENTS.md §13/§19 的范围冲突未闭环

AGENTS.md §13 规定 P0→P7 逐级演进、§19 列出 "What NOT To Build Prematurely"
（multi-agent、distributed scheduler、TUI/Web、ecosystem 等）。
当前仓库 P0–P7 全部有实现（`multi_agent/`、`distributed/`、`ecosystem/`、TUI、Web、Domain SDK）。
`docs/index.md` 如实记录了这一现状，但 AGENTS.md 本身没有更新，两份规范现在矛盾。
需要二选一：修订 AGENTS.md 承认当前范围，或在 docs 中标记这些层为 experimental 并冻结。
P0 spec §1 也明文禁止在 P0 期间新增这些能力——至少要有一份决策记录（docs/revision）
说明为何扩大范围。

---

## 🟡 应处理的问题

1. **ErrorCode 分类缺口（AGENTS.md §9）** —— ✅ 已修复（2026-09-15，UA-AUDIT-004）：
   `ErrorCode` 新增 `TRANSIENT`/`PERMISSION_DENIED`/`DEPENDENCY_MISSING`/`USER_REQUIRED`，
   `classify_failure` 完成映射，新增确定性 `user_required_rule`，含测试。
2. **Facade 与 CLI 构建逻辑重复**（A1 的直接温床）—— ✅ 已修复（2026-09-15，UA-AUDIT-001/002）：
   dispatch 收敛到 `domains/profile_service.py` 单一 composition 点，facade/CLI/agentd 三处均委托。
3. **P1 spec M9（Approval/Action binding 显式 negative 测试）未见独立测试** —— ✅ 已修复（2026-09-15，UA-AUDIT-005）：
   `test_confirmation_binding_approval_does_not_authorize_other_mutations`
   加入 I1–I10 invariant suite，验证批准绑定到其提案、不可转移。
4. **Coverage 无门槛** —— ✅ 已修复（2026-09-15，UA-AUDIT-006）：
   `[tool.coverage.report] fail_under = 86` ratchet gate（基线实测 86.7%，2026-09-15），
   CI coverage 步骤成为硬门，只升不降；低覆盖模块（facade.py 66%、tui_app.py 64%、sdk.py 0%）
   可从 coverage.json 定位后续补测。

---

## ✅ 已验证合格项

| 审计项 | 结果 |
| --- | --- |
| 全量测试套件（xdist 并行） | PASS（exit 0，仅 6 个 env-gated skip） |
| ruff check / ruff format / mypy --strict（522 文件） | 全绿 |
| P0 Golden Path：`init → doctor → run → session` | 实际跑通；doctor 按 spec §8 给出"怎么修" |
| `ua config` 不泄 secret | 合格（spec §7） |
| 唯一 CLI 主入口 + advanced 标记 | 合格（`agent`/`ua` 同一入口，spec §4） |
| `docs/RUNTIME_CONTRACT.md`（spec §15） | 优秀：概念表 + 7 条不可协商 invariants |
| P1 能力面：inspect_service、diagnosis/projection、dry-run、10 golden scenarios | 已落地（2026-09-13 审计的 M1/M3/M4/M5/M7 已闭环） |
| Safety Invariants I1–I10 集中自动化测试（spec §33） | `test_p1_safety_invariants.py` 显式逐条断言 |
| Fake/Scripted Model、Fake K8s backend 计数器、无真实依赖 CI | 合格（spec §18/§19/§36） |
| Live 测试环境变量门控 | 合格（CI 不依赖真实 LLM/K8s） |
| README 第一屏 30 秒可懂、架构后置（spec §16） | 合格 |
| Secret 红线、`.universal-agent/`、`universal-agent/` 均 gitignore | 合格 |
| Context Compiler budget-aware（AGENTS.md §10） | `_budget_fragments` 存在且有测试 |
| 事件模型可观测性（AGENTS.md §17.4） | EventStream / PolicyChecked / EvidenceCreated 等齐备 |
| 错误处理不甩 stack trace（P0 §23） | CLI 层有结构化错误输出 |

---

## 建议处理顺序

1. 修复 Facade local-profile bug（A1）+ 补集成测试 —— 直接违反 P0 DoD；
2. 收敛 CLI/Facade 构建逻辑，顺手消除 kernel→domain 反向依赖（A2 的 facade 部分）；
3. 补 Approval/Action binding negative 测试与 ErrorCode 缺失分类；
4. 做一次 AGENTS.md 范围决策（修订 roadmap 或冻结 experimental 层）并记录到 docs/revision；
5. 给 coverage 设 per-module baseline。
