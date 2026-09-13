# P1 Phase 1 — Audit（2026-09-13）

Spec: `P1 Kubernetes SRE Vertical — Codex 可验收实施规格.md`（§41 Phase 1 要求：
只审计、不改代码）。逐条对照 P1 验收标准与当前代码库。

## What Already Exists（可复用，P1 无需新写）

| P1 要求 | 现状 | 证据 |
| --- | --- | --- |
| Read-only capabilities | `inspect_cluster/workload/pod/logs/events` 已实现（kubectl + API 双后端） | `domains/kubernetes/domain.py` L139 |
| Mutation: restart_workload | ✅ 已实现（本批，rolling + 双后端 + policy） | `KubernetesRestartTool` |
| Mutation: scale_workload | ✅ 已实现（CAS 守卫 + bounded replicas） | `KubernetesScaleTool` |
| Policy 三态 | ✅ ALLOW / DENY / REQUIRE_CONFIRMATION（scale/restart 各有 policy） | `domains/kubernetes/policy.py` |
| Observation→Evidence | ✅ `KubernetesEvidenceExtractor`（provenance 从 observation.source 追溯） | `evidence.py` |
| Evaluator 验证 | ✅ `WorkloadHealthEvaluator`（criteria 匹配，tool success ≠ task success） | `domain.py` L104 |
| Bounded recovery | ✅ RecoveryManager + 规则（timeout retry ×2、conflict reconcile ×1），recovery 重入 policy | `runtime/agent.py` `_plan_recovery_for_pending` |
| Session 持久化 + resume | ✅ file/sqlite/postgres 三后端；WAITING confirmation 重启后 resume 测试齐 | `test_persistence.py:596/657` |
| Replay / Evaluation harness | ✅ `evaluation/`（Recording/Replay/ScenarioConfig/quality gate/JUnit） | 5415 行 |
| Scenario expectations | ✅ 已支持 `policy_denial_count/required_evidence_claims/forbidden_events/max_*` 等（§32 metrics 大部分现成） | `harness.py:55-89` |
| Secret 红线 | ✅ `redact_sensitive_mapping/text`、live contract artifact 扫描器、config show 不泄值 | 多处（有测试） |
| Fake backend 计数 | ✅ 测试后端带 `inspect_calls/mutation_calls` 计数 | `test_kubernetes_remediation.py:24-38` |
| 现有 scenarios | 5 个（crashloop / memory-pressure / policy-denial / tool-failure / under-replicated） | `tests/scenarios/*.json` |
| 结构化 Decision | ✅ DecisionContext/Decision/normalize + validator | `core/models.py` |

## What Is Missing（P1 DoD 缺口）

| # | 缺口 | 规格 § | 影响 |
| --- | --- | --- | --- |
| M1 | `inspect_service` capability（service/endpoints/ports） | §4 | read-only 覆盖不全 |
| M2 | `rollback_workload` mutation（§5 允许至少 2 个 mutation，现刚好达标——**可不实现**，标记为可选） | §5 | 可接受 |
| M3 | **结构化 Diagnosis/Proposal 模型**（diagnosis/confidence/evidence_refs/affected_resources；proposal 带 risk/requires_confirmation）——完全不存在 | §8/§9 | 核心 UX 缺口：目前 diagnosis 隐含在 events/evidence 里，CLI 无法展示"为什么" |
| M4 | **Dry Run 模式**（investigate + proposal，零 mutation） | §13 | 缺口 |
| M5 | **Scenario 数量**：5/10（缺 S02 ImagePull、S04 OOMKilled、S06 Rollout stuck、S07 Service no endpoints、S08 config signal——部分与 M1 关联） | §17 | 可接受基线待补 |
| M6 | **Safety invariant 自动化测试**：I1-I10 分散在各测试中但无集中 invariant suite | §33 | 测试组织问题 |
| M7 | **CLI Incident 输出**：run 输出缺 Evidence/Diagnosis/Proposal/Policy 摘要区（事件计数有，语义聚合无） | §27 | 依赖 M3 |
| M8 | `agent eval run kubernetes` 场景批量入口（harness 有、dispatch 接线未确认） | §31 | 小 |
| M9 | Approval/Action binding 测试（确认 checkout restart 不能执行 scale checkout——§12） | §12 | 现有 resume 测试隐式覆盖，无显式 negative 测试 |
| M10 | `--dry-run` CLI flag | §13 | 依赖 M4 |

## What Must Change（最小改动方案）

1. **M3 优先**：新增 `domains/kubernetes/diagnosis.py`——纯 projection 层：
   - `build_diagnosis(evidence, world) -> Diagnosis`（dataclass：summary、confidence（由 evidence confidence 聚合）、evidence_refs、affected_resources）
   - `build_proposal(diagnosis, capabilities) -> Proposal`（action/target/reason/evidence_refs/expected_effect/risk/requires_confirmation——后两者直接读 capability 定义与 policy 结果）
   - 挂到 `RuntimeService`/CLI 投影视图（遵循 `world_views.py` 的 projection 惯例）
2. **M1**：`inspect_service`（kubectl get svc + endpointslices / API GET services）——模式照抄 inspect_events
3. **M4/M10**：dry-run = `read_only=True` 的 session + run 输出标注 `DRY RUN: no mutation executed`（Runtime 已有 read_only 约束 mutation 的机制 `constrain_capability_context`——只差 CLI 接线）
4. **M6**：`tests/integration/test_p1_safety_invariants.py` 集中把 I1-I10 各写成显式断言测试（多数复用现有 harness/backend）
5. **M5**：补 5 个 scenario JSON（复用现有 FakeBackend 形状）
6. **M7**：CLI 输出加 Diagnosis/Proposal/Policy 区块（读 M3 的 projection）

## What Must NOT Change

- Kernel：Decision/Policy/Evaluation/Recovery 语义（P1 全部通过 Domain 扩展点实现）
- `agent.py` 主循环（iteration/recovery/confirmation 流程已满足 §12/§16）
- 已有 policy 文件（KubernetesScalePolicy/RestartPolicy 只需随 capability 注册）
- 事件模型、幂等/resource lock 语义

## 不确定项（实施时决策）

- Diagnosis 的 `confidence` 聚合策略：evidence confidence 均值 vs 最低值（建议均值，标准已在 Evidence 里）
- Proposal 由谁产生：LLM Decision 已含 capability/target/arguments——Proposal 建议作为 Decision 的**投影**（从 events 重建），而不是要求模型输出新 JSON 契约（避免动 Decision validator）
- rollback_workload 不实现（§5 授权 2 个 mutation 即可），记录为后续

## 建议执行顺序（§41 Phase 2-10 映射）

```text
Phase 2: inspect_service（契约补全）           → 半天
Phase 3: Diagnosis/Proposal projection + 测试   → 核心
Phase 4: dry-run 接线 + invariant 测试起步      → 
Phase 5-7: 验证/recovery/resume 测试已有大部分，补 M9 显式测试
Phase 8: 补 5 个 scenario JSON + `agent eval run kubernetes` 接线
Phase 9: CLI incident 输出区
Phase 10: README k8s quick start
```

## Risks

- Diagnosis projection 若想让 LLM 参与总结（而非纯 evidence 聚合），会引入不可确定性——**建议第一版纯确定性**（从 evidence 聚合），与 §18 "scenario 可重复" 一致
- inspect_service 若扩 endpointslices 解析面较大，先做 service + endpoints 基础字段
