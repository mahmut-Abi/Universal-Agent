# Kubernetes 真实环境运行 — 建议与修复清单(2026-08-31)

状态:基于 2026-08-31 对真实 Kubernetes 集群(v1.36.4 单控制面)与 360智脑
GLM 5.3-flash(OpenAI 兼容 Responses API)的完整生产 operator 流程实测。
所有"已验证根因"均定位到具体文件/行/错误消息,可直接作为修复依据。

## 演示结论(背景)

真实集群闭环已跑通:

```text
目标(修复不健康负载)
  → 模型探针 ok → 集群预检 ok(kubectl 后端真实读取)
  → 诊断:deployment/ua-demo-api 3 副本仅 2 就绪,
         第 3 Pod 因 Insufficient cpu 永久 Pending,根因 under_replicated
  → Agent 提案:scale_workload 3→2(正确的缩容判断)
  → 生产策略 REQUIRE_CONFIRMATION(确定性代码)→ 人工确认
  → kubectl scale 真实执行(resource_version 乐观锁守卫)
  → 新鲜验证:2/2 Ready 全健康,healthy=true 证据入库
```

演示过程中真实触发并验证的机制:结构化决策契约、策略在模型外、非法恢复拒绝
("session is not waiting")、资源版本乐观锁 + 幂等键、评估器不轻信模型
(两次违规 finish 被拒)、证据驱动的世界模型。

现场遗留:`ua-live` 命名空间(演示负载,健康)、
`.universal-agent/`(profile/会话存储/事件流,已加入 .gitignore)。

---

## 一、准确性(修复建议)

### A1【P0】证据 claim 多粒度冲突,阻断健康路径收敛(真 Bug)

- 现象:健康负载 run(session-1e43b248,iteration 2)模型 `finish` 被拒:
  `finish rejected because evaluator has not completed the task and goal`,
  会话以 `invalid_state` 失败;contract `completion_verification=failed`。
- 已验证根因链:
  1. `src/universal_agent/runtime/processing.py` L83-88 从 World Model facts
     按 claim 名构建评估输入 dict:
     `criteria = {fact.claim: fact.value for fact in world.facts if ...}`
  2. `resource` claim 在两个粒度产生 fact:
     workload 级(`deployment/ua-demo-api`)与 pod 级(`pod/ua-demo-api-xxx`)
  3. dict 推导中 pod 级 fact 后写入,覆盖 workload 级值
  4. 评估器 `WorkloadHealthEvaluator`
     (`src/universal_agent/domains/kubernetes/domain.py` L100)要求 goal
     criterion `resource == deployment/ua-demo-api` → 值失配 →
     `task_complete=False`
  5. Runtime 确定性拒绝 finish(拦截行为本身正确)→ 会话失败
- 修复(建议 a+b 组合,改动局部):
  - a. claim 按粒度限定命名:pod 级证据的 `resource` 改名 `pod.resource`
    (改 `src/universal_agent/domains/kubernetes/evidence.py` 的 extractor)
  - b. 评估器作用域感知匹配:goal 级 `resource` 期望 `deployment/` 前缀值时,
    匹配同前缀 fact(改 `WorkloadHealthEvaluator`)
- 回归测试:健康负载完整 run 必须到达 `completed`(当前会 fail)。
  这是"工具成功≠任务成功"防线的直接测试价值。

### A2【P1】模型 finish 决策违规的廉价恢复

- 现象:GLM 的 finish 决策携带 action 字段 →
  `GoalFailed: model failed: finish decision cannot include an action`;
  此时缩容已执行、healthy=true 已满足,却整会话失败(session-72973912)。
- Runtime 拒绝非法决策是正确的,但恢复路径代价过高(整个 goal 失败)。
- 修复:
  - 确定性规范化(首选):`finish` 类型决策若除 action 外全部合法,
    Runtime 剥离 action 后重新校验 —— 运行时拥有的规范化,不信任模型输出,
    符合"LLM 不拥有控制流"。
  - 纠错重试(备选):校验失败时把违规原因作为一次性纠错消息回传模型,
    限 1 次(符合"恢复有预算"原则)。
  - 位置:`src/universal_agent/model/decision_codec.py` /
    `src/universal_agent/runtime/decision.py`。

### A3【P1】model-probe 未覆盖 finish 契约

- 现象:探针只验证 `execute` 决策;首个会话死在 `finish` 上,
  探针"通过"给出虚假信心。
- 修复:探针增加第二个场景(healthy fixture → 期望合规 `finish`),
  或在 contract report 显式标注 `finish_contract: unverified`。
- 位置:`src/universal_agent/domains/kubernetes/production_contract.py`。

### A4【P2】response_format 的"执行假象"

- 实测:360 端点接受 `response_format: json_schema` 但不执行
  (schema 无约束力);prompt_json / json_object / json_schema 三种模式
  在真实上下文下全部失败的直接原因。
- 已由"schema 进 prompt"修复缓解(见本文件末尾"本次已实施")。
- 建议:runbook/文档明确 —— 非 OpenAI 端点不保证 schema enforcement,
  prompt-embedded schema 应为默认防线。

---

## 二、操作复杂度(优化建议)

### B1【P0】客户端崩溃导致会话永久搁浅(本次真实发生)

- 现象:CLI 被外部 600s 超时强杀 → 当前 task 标 `failed` → 会话非 waiting
  → `resume` 永久拒绝(`session is not waiting`)→ 只能整个重跑
  (重复探针/诊断开销)。
- 分析:Runtime 拒绝非法 resume 是正确的;缺的是"执行中崩溃"的合法恢复路径。
- 修复(三选一,建议 c 为治本):
  - a. `session recover <id>`:从最后提交快照重建,重新进入循环
    (新 action id,重走 capability 解析 + policy,有界)——
    语义 = "执行中断续跑",与"确认续跑"(resume)并列。
  - b. `kubernetes run --session <id>` reattach。
  - c. 长任务走 P6 队列执行(`run --async` 经 DistributedRuntimeCoordinator
    入队,客户端只订阅事件)—— 客户端生命周期与会话解耦。
    分布式原语已全部存在,只差 CLI 接线,最符合架构。

### B2【P1】CLI 无墙钟预算

- `kubernetes run` 无 `--timeout-seconds`。GLM 每轮 1-2 分钟 × 5-7 轮
  = 10 分钟+,依赖客户端被外部强杀。
- 修复:到达预算时干净停止在边界(waiting/paused)而非强杀;
  文档给推理模型推荐值(model timeout 120s 实测多次不够)。

### B3【P1】Profile 构建缺引导

- `agent init` 有 20+ 旗标;实测烧了 3 次探针才发现 GLM 需要
  `json_object` + 组织 header + 长 timeout。
- 修复:`--model-provider-preset 360zhinao|deepseek|moonshot`
  (预设 endpoint/response_format/headers/timeout);
  probe 失败信息应直接建议替代格式(现在只报 `type is required`,
  不给修复方向)。

### B4【P2】演示负载手工构建

- 本次欠副本负载为手工 YAML。
- 建议 `agent kubernetes lab provision --scenario under-replicated --ttl 2h`
  (自带 managed-by 标签与 TTL 清理),与 `tests/scenarios/` 场景文件对齐,
  让 live contract 一键可复现。

---

## 三、操作便捷性(优化建议)

### C1【P1】确认边界的人话横幅

- 现在 `pending_action` 输出原始 JSON。建议:

  ```text
  ⚠ PROPOSED CHANGE: scale deployment/ua-demo-api (ua-live) 3 → 2 replicas
    Reason: 第 3 副本因 CPU 不足永久 Pending,正确修复为缩容
    Confirm: agent ... session resume <id> --confirmed true
  ```

- `next_step.command` 已给出可粘贴命令(好的设计),补人话横幅降低误确认风险。

### C2【P1】失败诊断摘要(`session explain`)

- 本次根因靠人工从快照 + 事件流反推(专家级工作量)。
- 建议 `session explain <id>` 把
  `finish rejected because evaluator has not completed the task and goal`
  翻译为:`task requires resource=deployment/ua-demo-api; latest world fact
  resource=pod/...(pod 级证据覆盖了 workload 级)`。
- 纯 service 层投影(读快照 + 事件),不动内核。

### C3【P2】凭据引导

- `init` 时即校验 env 存在性(实测第一次 `config show` 才发现缺 key);
  支持 `file:` secret ref;错误信息给修复方向。

---

## 四、工具链问题(本会话环境实测)

| 问题 | 证据 | 建议 |
| --- | --- | --- |
| lens 捆绑 pyright typeshed 过旧,33 条误报反复阻塞 | `datetime.UTC`/`StrEnum` 成员/PEP695 泛型全部误判;venv Python 3.14 实测正常;mypy --strict 全仓 466 文件零错误 | 升级 lens 的 pyright/typeshed,或按项目 `python_version=3.12` 配置其基线 |
| lens `suppress` 写注释破坏源文件(工具 bug) | 注入残片致 `distributed/queue.py` 语法错误(L928 出现 `(item)),`) | 已回滚;建议向上游报 bug;false-positive 处置不被检查消费也需修 |
| Homebrew 死 pytest shim | `/opt/homebrew/bin/pytest` shebang 指向已卸载的 python@3.14,lens 测试运行器 ENOENT | 已修复(venv 优先 shim);同类机器会复现 |

---

## 优先级矩阵(影响 × 成本)

| 优先级 | 项 | 理由 |
| --- | --- | --- |
| P0 | A1 证据粒度冲突 | 唯一阻断"健康路径 completed"的真 Bug;改动局部 |
| P0 | B1 会话恢复路径 | 任何客户端崩溃即搁浅;P6 原语已在,只差接线 |
| P1 | A2 finish 规范化/重试 | 小改动,避免"修复成功却整会话失败" |
| P1 | B2 超时预算 + C1 确认横幅 | 小改动,直接提升实测痛点 |
| P1 | A3 probe 扩 finish 契约 | 探针完整性 |
| P2 | B3 预设 / C2 explain / C3 凭据 / B4 lab | 体验优化 |

---

## 本次会话已实施(供对照)

1. `src/universal_agent/model/openai_adapters.py`:
   - Responses 路径复用 `_loads_json_text` 剥 markdown 围栏
     (OpenAI 兼容 provider 在 strict schema 下仍可能输出围栏);
   - Chat Completions 与 Responses 的 prompt 嵌入
     `decision_schema`(`_openai_decision_json_schema()`)及字段说明,
     解决无 schema enforcement 端点的 Decision 契约失败;
   - 附单测 `test_openai_responses_model_adapter_strips_markdown_code_fence`。
2. `src/universal_agent/distributed/queue.py`:
   事故恢复 —— suppress 注释破坏源文件后 `git checkout` 误回滚未提交改动,
   已从 Codex 会话日志逆向重建 FencingToken 逻辑
   (导出、租约递增、持久化恢复、prune 清理,净增 18 行),
   fencing 测试与全量单测通过。
3. `.gitignore` 增加 `.universal-agent/`(运行时状态不提交)。
4. `/opt/homebrew/bin/pytest` 替换为 venv 优先 shim(替换已损坏的
   Homebrew python@3.14 遗留 shim)。
