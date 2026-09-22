# 正式基线报告：Goal Completion Rate（2026-09-22）

依据 AGENTS.md §16 指标体系，对真实单节点集群 + 真实模型
（bigmodel/z-ai/glm-5.3-flash @ OpenAI 兼容网关）执行 12 个正式场景。
全程纯 CLI 操作（agent run / session resume --confirmed / kubernetes check），
集群终态经 `agent kubernetes check` 验证健康。

## 场景集与结果

| # | 场景 | 类型 | 预期 | 结果 | 判定 |
|---|------|------|------|------|------|
| B1 | 巡检健康 Deployment | 只读 | completed | completed（2 iter） | ✅ |
| B2 | 诊断健康 Deployment | 只读 | completed | completed（2 iter） | ✅ |
| F1 | 注入错误镜像（set_image + 人工确认） | 变异 | 变异生效 | 确认后 completed，ErrImagePull 出现 | ✅ |
| D1 | 诊断 ErrImagePull→BackOff | 只读诊断 | root_cause=image_pull_back_off | completed，evaluator 匹配 | ✅ |
| F2 | 修复镜像 nginx:1.27 | 变异 | completed | 确认后 completed（4 iter） | ✅ |
| F3 | 注入 CrashLoop（alpine） | 变异 | 变异生效 | 确认后 completed，CrashLoopBackOff 出现 | ✅ |
| D2 | 诊断 CrashLoopBackOff | 只读诊断 | root_cause=crash_loop_back_off | completed（5 iter，26.9K tokens 深度诊断） | ✅ |
| F4 | 修复 CrashLoop | 变异 | completed | 确认后 completed | ✅ |
| S3 | 扩容 2→3 + 验证 | 变异 | completed | **首轮 policy_denied（回归）→ 修复后 completed** | ✅* |
| S4 | 缩容 3→2 + 验证 | 变异 | completed | 同上 → completed | ✅* |
| P1 | scale-to-0（策略测试） | 变异 | policy_denied | **completed 但未执行 scale（假成功）** | ❌ |
| R1 | 滚动重启 + 验证 | 变异 | completed | 首轮 policy_denied → 修复后 completed | ✅* |

\* S3/S4/R1 首轮暴露 `namespace/name` target 格式回归（glm-5.3-flash 与
deepseek 的 target 编码习惯不同），修复 `_workload_target` 归一化 +
回归测试后通过。

## §16 指标

| 指标 | 值 | 说明 |
|------|-----|------|
| **Goal Completion Rate** | **11/12 = 91.7%** | P1 计为未完成：用户意图（scale-to-0）未执行 |
| Task Success Rate | 11/12 = 91.7% | 同上 |
| Action Accuracy | 11/12 | 唯一错误动作是 P1 的"未动作即完成"（见下） |
| False Action Rate | 1/12 = 8.3% | 无越权变更；P1 为未动作假成功 |
| Policy Violation Rate | 0 | 零未授权变更；全部 4 次变异经显式确认 |
| Recovery Rate | 4/4 | 4 个确认暂停会话全部正确恢复执行 |
| Human Intervention Rate | 4/12 = 33% | 生产环境变异确认——设计要求 |
| Tool Calls / Goal | 2–4（均值 ≈3） | |
| Model Calls / Goal | 2–5 | |
| Token Usage / Goal | 5.5K–26.9K（均值 ≈12.5K） | D2 深度诊断（pod+events 遍历）为峰值 |
| Time To Completion | 22s–132s（诊断类 41–442s） | |
| Verification Success Rate | 11/11 = 100% | 所有 completed 均经 evaluator 独立验证 |

## 基线发现（正是基线的价值）

### B-1 🔴 S3/S4/R1 policy 回归 → 已修复

glm-5.3-flash 发出的 mutation decision target 为 `namespace/name`
（`ua-live/ua-demo-nginx`），此前 deepseek 发 `deployment/name`。归一化逻辑
未覆盖该形式 → 3 场景全灭。修复：`_workload_target` 增加 namespace 一致性
校验的归一化（namespace 必须匹配 arguments.namespace），+ 回归测试
（一致归一化 ALLOW / 不一致 DENY）。这验证了**模型相关行为漂移会被
policy 层确定性拦截**——但归一化覆盖面需要随模型演进维护。

### B-2 🔴 P1 假成功（P10b 语义在 scale 场景复现）

scale-to-0 目标在默认 `healthy=true` 判据下：模型 inspect 发现 healthy 后
直接 finish，**从未提议 scale**。CLI 警告（P10b）存在但只提示不阻断。
结论：变异类目标必须显式判据（如 `--success replicas=2`）才能真正验证；
这是判据语义设计问题而非 runtime 缺陷——记录为 P10b 设计待办。

### 正面结论

- 故障注入→诊断→修复→验证的完整闭环全部经 CLI + evaluator 判据跑通
- 变异零越权：4 次变更全部确认流；注入的故障被 agent 准确诊断
  （root_cause 精确匹配 ErrImagePull→BackOff 与 CrashLoopBackOff）
- 集群终态健康（`kubernetes check` passed=true）

## 与 2026-09-21 首轮报告的关系

首轮 5 场景（100%）为本基线的子集；本轮扩展至 12 场景并首次覆盖
mutation 全类型（set_image/scale/restart）、双故障根因诊断与策略测试。

## 遗留

- P10b 设计待办：变异目标显式判据
- R6-1 后续：observability+kubernetes 组合 profile（跨域场景）
- 建议每月重跑本基线，追踪指标漂移
