# 真实任务验证报告：Live Kubernetes + 真实模型（2026-09-21）

> **更新（2026-09-21 同日）**：首轮验证发现 F1–F4 后已全部修复并重测，
> 真实模型下 Goal Completion Rate 从 1/5 提升至 **5/5（100%）**，且首次验证了
> 完整的生产确认链路（REQUIRE_CONFIRMATION → 人工批准 → 变异执行 → 验证）。
> 二轮补测：F6（resume 超时）已修复；S2 诊断目标以 `--success root_cause=...`
> 判据正式 completed（诊断证据链 inspect_workload → inspect_logs → inspect_pod
> 全程只读）。详见文末「修复与重测结果」。原首轮结果保留作对照。

验证目标：AGENTS.md §16 的核心指标——"Can the Agent reliably complete real tasks?"
即用真实模型 + 真实 Kubernetes 集群验证核心 Agent Loop。

## 修复与重测结果（同日，模型切换为 bigmodel/z-ai/glm-5.3-flash）

四个 finding 全部修复（F3 新增 set_image 能力为最大变更），全量单测 / ruff / mypy --strict 全绿后重测：

| 修复 | 内容 | 验证 |
|------|------|------|
| F4 | `_workload_target()`：target 缺失时从 `arguments.name` 推导；`deployment/<name>:<container>` 形式归一化；显式不匹配仍 DENY | S4 scale 到达确认环节并完成；新增 3 条单测 |
| F1 | `_inspect_logs` 对 waiting 容器的 BadRequest 返回结构化观察（`logs_available=false` + 容器等待原因）而非 tool_failure | S2 成功产出正确根因报告（ErrImagePull），不再中止；115 事件 17.7K tokens 多步诊断 |
| F2 | suite 场景 task/goal 描述自包含目标参数；scripted 专属 policy 场景加 `scripted` tag 供真模型运行排除 | fake backend + 真模型：`healthy workload` PASS，`--exclude-tag scripted` 后 suite gate 全绿 |
| F3 | 新增 `set_image` 变异能力：kubectl backend + KubernetesSetImagePolicy（环境校验/目标校验/goal 范围/镜像引用校验/生产 REQUIRE_CONFIRMATION）+ 新增 7 条单测 | S3 完整闭环：确认 → 真实改镜像 → 2/2 Ready → 评估通过 |

重测明细（live，production 环境）：

| # | 场景 | 结果 | 迭代 | Tokens |
|---|------|------|------|--------|
| S1 | 只读巡检 | ✅ completed | 2 | 5,955 |
| S2 | 诊断故障（dry-run） | ✅ 二轮补测：`--success root_cause="image_pull_back_off"` 后 **completed**（3 次只读 inspect，根因由 evidence→world fact→evaluator 判定） | 4 | 15,797 |
| S3 | 修复错误镜像 | ✅ **确认后 completed**（set_image 真实生效，nginx:1.27，2/2 Ready，evaluator 验证） | 2+resume | 6,592 |
| S4 | scale 2→3 | ✅ **确认后 completed**（真实生效 3/3，事后已恢复 2 副本） | 3+resume | 11,313 |

**Goal Completion Rate：5/5 = 100%**（二轮补测 S2 判据化后）。

首次完整验证的生产治理链路：

```text
DecisionGenerated(set_image/scale_workload)
  → PolicyChecked(REQUIRE_CONFIRMATION, production)
  → pending_action / session waiting
  → agent session resume <sid> --confirmed true（人工批准）
  → ActionStarted → ActionCompleted（真实集群变更）
  → Observation → Evidence → WorldModelUpdated
  → EvaluationCompleted(healthy=true) → GoalCompleted
```

附带发现（重测期间）：

- **F6 🟡**：`agent session resume --confirmed true` 的 CLI 等待超时仅 30s，而确认后的
  执行+验证需要更久；CLI 报 `agentd_request_failed: timed out` 但 runtime 实际继续执行
  并成功完成（错误信息有误导性）。建议 resume 路径复用 run 的 `--timeout-seconds`。
- **F7 🟢**：模型偶发把 decision target 写成 `deployment/<name>:<container>`
  （已通过归一化兼容）。长期应在 capability payload 里显式声明 target 格式约定。
- **F8 🟢**：`--success` 判据值必须是合法 JSON（`root_cause=image_pull_backoff`
  裸字符串报 bad_request，需 `root_cause="image_pull_back_off"`）；且 kubernetes
  root_cause 取值是 snake_case 的 k8s waiting reason（`ImagePullBackOff` →
  `image_pull_back_off`）。建议 CLI 对裸字符串做宽松解析或在帮助文本示例中注明。
- **F6 ✅ 已修复**：`session resume`（含确认执行）客户端超时从 30s 提升到与 run
  相同的 900s 默认值（`remote/client.py`），新增单测锁定。

~~遗留：诊断类 goal 在 dry-run 下以 ask_user 上报结束~~（已解决：判据机制本就支持——
inspect_workload 对不健康工作负载输出 `root_cause`，经 evidence→world fact 匹配
`--success root_cause=...` 判据即正式 completed；二轮补测验证通过）。

---

# 以下为首轮（修复前）结果存档

## 验证环境

- **模型**：OpenAI 兼容网关 `http://127.0.0.1:33333/v1`，
  model `nvidia:deepseek-ai/deepseek-v4-flash-0731`（json_object 响应格式，timeout 120s）。
  端点共 19 个模型，实测仅 `hermes-agent` 与 `deepseek-v4-flash-0731` 可用
  （`deepseek-v4-pro-0813` 已 EOL，`z-ai/glm-5.3-flash` 网关不可用）。
- **集群**：单节点 k8s v1.37.0 真实生产集群（kubectl v1.34 client，版本偏差超出 skew 支持但仍可用）。
- **沙箱**：独立 namespace `ua-live`（新建），未触碰集群内既有工作负载。
- **Profile**：`live-eval`（`.universal-agent/live-eval-profile.json`），
  kubectl backend、environment=production、store=file、max_steps=20。

## 场景与结果

| # | 场景 | 结果 | 迭代 | 模型调用 | Tokens |
|---|------|------|------|---------|--------|
| S0 | 模型回路连通（greeting） | ✅ 通过（S1 同款回路） | 1 | 1 | 2,223 |
| S1 | 只读巡检健康 Deployment | ✅ **通过** | 2 | 2 | 5,712 |
| S2 | 诊断不健康 Deployment（dry-run） | ❌ tool_failure 中止 | 2 | 2 | 5,534 |
| S3 | 修复错误镜像 | ⚠️ 诚实上报缺能力（ask_user） | 2 | 2 | 6,430 |
| S4 | scale 变异（2→3 副本） | ❌ policy_denied（契约缺口） | 1 | 1 | ~2,0xx |
| E1 | 内置 eval suite（kubernetes，真模型） | ❌ 2/2 waiting | 1 | 2 | 4,765 |

Goal Completion Rate（本次实测）：**1/5 = 20%**（S0 口径则 2/6）。

## S1 详析：核心循环完整走通（最重要结论）

真实集群 + 真实模型下，事件链与 AGENTS.md §17.4 完全一致，无缺失环节：

```text
GoalCreated → TaskCreated → DomainActivated → IterationStarted
  → DecisionGenerated (inspect_workload, 结构化参数 + expected_observations)
  → DecisionValidated → ModelUsageRecorded → CapabilityResolved
  → PolicyChecked (kubernetes-read-only, allow, low risk, side_effect=none)
  → ActionStarted (idempotency_key + parameters_hash) → ActionCompleted (真实 kubectl 输出)
  → ObservationReceived → EvidenceRecorded ×36 → WorldModelUpdated
  → (第2次迭代) DecisionGenerated (finish, 引用 replica 数与 criteria 满足)
  → EvaluationCompleted (evaluator=workload-health, healthy=true 匹配)
  → GoalCompleted
```

模型决策质量好：正确选择能力、参数准确、`finish` 前引用了具体证据（ready 2/2）。
Evaluator 独立验证（非模型自证）。Token 效率：单目标 ~5.7K tokens，2 次调用，合理。

## 发现（按严重度排序）

### F1 🔴 工具失败即停：诊断类任务的致命恢复缺陷

S2 中模型正确决策 `inspect_logs`（对 ErrImagePull pod），
`kubectl logs` 对未启动容器返回 exit 1（BadRequest，stderr 含根因
"failing to pull image"），runtime 将其归类 `tool_failure` →
`RecoveryPlanned (rule=default-stop, strategy=stop)` → `GoalFailed`。

问题三层：
1. stderr 明确携带根因，却未被转为 Observation/Evidence，模型失去了刚到手的诊断线索；
2. `tool_failure` 粗分类吞掉了可细分情况（BadRequest ≠ 瞬时故障 ≠ 权限问题）；
3. recovery 未允许模型改用替代能力（`inspect_pod` / `inspect_events` 可拿到同等信息），
   而是直接停止。诊断场景是 §14 首要场景，此缺陷使其在真实故障上不可用。

建议：`kubectl logs` 对 waiting 容器的 BadRequest 应映射为正常观察（或专用错误类别），
并让 recovery 把错误观察回灌给模型重决策，而不是 default-stop。

### F4 🔴 scale 策略与决策契约脱节：变异能力实际不可用

S4 中模型决策完美（`scale_workload, {name, namespace, replicas:3}`），
但 `kubernetes-scale-safety` 策略要求 `context.target` 精确等于
`"deployment/<name>"`（policy.py L53-58）。问题：
- 决策 JSON schema 中 `target` 是可选字段，capability payload
  （name/description/risk/required_arguments）与决策提示均未告知模型
  scale_workload 需要发射 `target: "deployment/<name>"`；
- domain 里已有 `_decision_workload_subject()`（把 name 规范化为
  `deployment/<name>`），但只用于参数 provider，未用于 policy target 兜底；
- 内置 eval 场景能过只因 scripted model 的 canned decision 手工带了 target。

结果：**真实模型下 scale_workload 100% 被 deny**，生产审批/确认流程（CONFIRM 路径）
在本次验证中根本无法到达。建议：policy 在 target 缺失时从 arguments.name 推导
（与 argument provider 同源逻辑），或把 target 要求写进 capability payload。

### F2 🟡 eval harness 场景参数未进入真模型上下文

内置 suite（`agent eval run --suite kubernetes`）在真实模型下 2/2 场景
`GoalWaiting`：场景依赖 initial_state 注入的目标工作负载，但编译给模型的
DecisionContext 中没有 workload name/namespace，模型正确选择 ask_user。
scripted model 掩盖了该问题（canned decision 不看上下文）。
结果：human_intervention_count=2、goal_completion=0，suite 门限（若启用 fail-on）
会全红。建议：scenario_builder 把场景目标资源写入 task description 或 domain_context。

### F3 🟡 变异能力面过窄，§14 目标场景只支持一半

域能力 = 6 × inspect_* + scale_workload + restart_workload，无 image patch/apply。
S3 中模型诚实上报"I lack the capability to update the container image"并请人工处理
——行为正确（无幻觉执行、policy 兜底有效），但"发现不健康并修复"（立项 §14 首要场景）
只有 scale/restart 两条腿。镜像错误（最常见故障）只能人工修。
建议：若维持窄面，需在文档明确该边界；否则按 §11 契约新增受限 patch 能力
（限定 namespace + 目标校验 + 生产确认）。

### F5 🟢 小问题

- `agent run` 无 `--profile-config` 时会静默走 thin-client/embedded agentd，
  以另一份 profile 报 404，错误信息有误导性（hint 正确但根因链路不透明）；
- 首次 `agent run` 用 profile 默认 model timeout（30s？）遇到模型 reasoning 慢即
  `model_failure`，需手工调大 profile timeout；建议 init 对非 scripted 模型默认 ≥120s；
- 模型网关 19 个模型中 4 个不可用（EOL/网关路由），选择模型前应先 probe
  （`/v1/models` 列表 ≠ 可用）。

## 正面结论（架构兑现度）

1. **事件模型完整可审计**：每个 session 62-73 条事件，决策/策略/动作/证据/评估全程
   可回放，`agent session` 可查询——§18 要求的" reconstruct why"达成。
2. **Policy 在模型外**：所有变异被确定性策略拦截（F4 的 deny 本身证明管控有效），
   模型无法绕过；read-only 场景 side_effect=none 判定准确。
3. **Evaluator 独立**：finish 建议被评估判据校验（S0 首跑被拒："finish rejected
   because evaluator has not completed the task"），Tool Success ≠ Task Success 落实。
4. **ask_user 不猜测**：缺参数、缺能力时模型请求澄清/人工，runtime 正确 pause，
   无一例幻觉参数或幻觉工具调用（deepseek-v4-flash + json_object 结构化输出稳定）。
5. **Token 成本可控**：单目标 2-5.7K tokens，单调用 1.1-5.0K in / 0.2-1.4K out。

## 修复后状态

- `ua-live` namespace：`ua-demo-nginx` 2/2（已恢复 2 副本）、`ua-demo-broken` 2/2
  （本轮由 Agent 通过 set_image 能力自主修复，经人工确认）。
- Profile：`.universal-agent/live-eval-profile.json`（live）、
  `.universal-agent/fake-eval-profile.json`（fake+真模型，用于 eval suite）。
- 清理：`kubectl delete ns ua-live && rm .universal-agent/{live,fake}-eval-profile.json`。

## 三轮验证：纯 CLI 面测试与确认流语义（同日）

约束：不使用 python/kubectl 直接操作，全部通过项目 CLI（`agent`）执行。

### 新发现

| # | 严重度 | 问题 | 状态 |
|---|--------|------|------|
| P1 | 🔴→✅ | `agent config` / `profile show` 等只读 golden-path 命令在凭据缺失时整块不可用（embedded agentd 启动即要求解析全部 secrets） | **✅ 已修复** |
| P3 | 🟡→✅ | `session explain` 对 `policy_denied` session 给出 "Domain/tool failure"（分类过时、无针对性 Try 建议） | **✅ 已修复** |
| P4 | 🟡→✅ | `agent config` 的 Policy 段显示 Python 类名（`KubernetesScalePolicy`），与 read-only 策略的人类可读描述不一致，泄露实现细节 | **✅ 已修复** |
| P6 | 🔴→✅ | `kubernetes preflight` 子命令无参调用被 agentd 400 拒绝（"workload is required"），且不接受 `check`/`evidence`/`model-probe` 都支持的 profile 位置参数——同组兄弟命令签名互不一致，CLI 与 agentd 路由契约脱节 | **✅ 已修复** |
| P8 | ❌误报 | ~~`kubernetes check` 失败时 exit=0~~——复测证实为测试方法错误（`cmd | head; echo $?` 捕获的是 head 的退出码）。实际 contract 失败时 exit=1，行为正确 | 撤销 |
| P10a | 🔴→✅ | 模型 ask_user 造成的 waiting session 上 `resume --confirmed true` 被静默接受；随后 runtime 用**变异前状态**的默认 `healthy=true` 判据将"修改镜像"goal 判为完成——变异从未执行却报成功（假成功，违反 §4.5 精神）。此前 S3/S4 确认流能成功，纯属模型恰好重新提出了同一动作（依赖 approved_fingerprint 记忆），确认执行本身是非确定性的 | **✅ 已修复** |
| P10b | 🟡→✅ | 变异类 goal 的默认判据是 `healthy=true`——变异前的健康状态即可满足，天然产生假成功空间 | **✅ 已缓解**（CLI 层） |
| P2 | ✅ | `agent config` 确认不泄密（secret 只显示引用名 `model_api_key`） | 合格 |
| F6 | ✅ | `session resume` 900s 超时修复经真实确认执行验证通过 | 已修复 |

### 三轮修复明细

- **P1**：`host/runtime.py` 模型适配器改为凭据延迟校验——secrets 缺失时返回
  `UnresolvedCredentialsModelAdapter`（decide 时抛非 transient `JsonHttpModelError`），
  服务启动不再需要凭据；直接调用模型的 operator probe 路径在命令边界用
  `model_credentials_missing_reason()` 保留原 fail-fast `ValueError` 契约（既有测试
  `test_cli.py` model-probe 断言不变）。live 验证：无凭据 `agent config` / `profile show` 正常输出。
- **P3**：`text_views.py` explain 增加 `error_code` 结构化分发表
  （policy_denied/model_failure/validation_error/invalid_state/user_required/
  permission_denied/dependency_missing），优先于事件文本启发式。live 验证：
  policy_denied session 现在给出 "Policy denied" 与针对性 Try 建议。
- **P4**：三个 kubernetes guard 策略类增加 `description` 属性；config 文本视图
  优先显示 description，不再回落到 Python 类名。live 验证：Policy 段全部人类可读。
- **P6**：agentd 路由 guard 放宽（preflight 的 workload 为可选，其报告本就支持
  无目标运行）；CLI parser 给 preflight 补可选 profile 位置参数对齐兄弟命令。
  live 验证：`kubernetes preflight`（无 workload、带/不带 profile）均正常。
- **F8**：`--success` 值解析增加宽松回落（裸词/数字/布尔 → JSON 字符串/数字/布尔），
  显式 JSON 仍优先。live 验证：`--success root_cause=image_pull_back_off` 免引号可用。
- **P10b**：`agent run`（注入服务与 remote/embedded 两条路径）对"变异形状 goal
  且未提供 --success"输出 stderr 警告，提示默认 healthy 判据可能被变异前状态满足。
  live 验证：scale 目标无判据时输出警告。
- **P8 撤销**：复测证明 `kubernetes check` 失败时 exit=1 正确；首轮观察是测量方法
  错误（管道后 `$?` 取到 head 的退出码）。
- 测试：重写 `test_kubernetes_preflight_route_requires_workload` →
  `test_kubernetes_preflight_route_runs_without_workload`（新契约）；CLI 判据测试
  更新为 lenient 语义 + 非法 KEY= 仍拒绝；全量单测+集成测试通过。

### P10a 修复与验证

- 修复：`runtime/agent.py::resume` 在 `confirmed` 非空但无 pending action 时显式返回
  `INVALID_STATE: no pending action to confirm; session is waiting for user input`
- 测试：新增集成测试 `test_agentd_resume_route_rejects_confirmation_without_pending_action`
  （422 + invalid_state + 不产生任何 action）；既有确认流测试全部保持通过
- Live 验证：ask_user waiting session 上 `resume --confirmed true` 返回结构化 422
  `invalid_state`，不再静默丢弃用户确认意图
- 附带：set_image 新增能力使 readiness `capability_count` 8→9，两处集成断言已同步

### 闭环观察

"用 Agent 弄坏工作负载"的测试目标未达成——但原因正是 P10a/P10b：模型的 ask_user
确认流 + 陈旧判据使变异从未发生。这也反证了安全边界有效（用户确认从未授予时
集群不会被改），但确认流的语义正确性需要 P10b 的判据设计配合。

## 四轮验证：CLI 高级面与 server 生命周期（纯 CLI）

覆盖：observability（health/ready/version/cost/metrics/traces/logs）、memory、
audit、repair、ecosystem、multi-agent、eval 生命周期、session pause/cancel、
serve + thin-client + 认证。发现如下：

| # | 严重度 | 问题 |
|---|--------|------|
| Q5 | 🔴 | `agent memory add/get/delete` 在生产路径（embedded/remote agentd）全部不可用："unknown memory command: add"——本地注入 dispatch 实现了四个子命令，remote dispatch（`_dispatch_remote_list_command`）只实现 list，而 server 端 POST/GET/DELETE /v1/memory 路由齐全。纯客户端缺口，已定位 |
| Q6 | 🔴 | memory 无持久化：`MemoryStore` 仅有 `InMemoryMemoryStore`。`memory add` 返回 201，embedded agentd 进程退出后记录即丢失（list 里的记录只是 domain 启动种子）——用户视角是静默数据丢失 |
| Q8 | 🟡 | 未配置 `--admin-store` 时，`/v1/admin/*` 静默 fall-through 为 404 "unknown route"，误导排障（应明确"admin plane 未配置"） |
| Q9 | 🔴 | `agent serve` 不暴露 `--admin-store`/`--admin-store-url-env`/`--audit-log` 等 agentd 参数——admin/多租户面无法经 CLI 启用，只能 `python -m universal_agent.agentd` 直启。P3.5 admin 特性缺少 CLI 产品化入口 |
| Q7 | 🟡 | `.universal-agent/config.json` 的 `"profile": "live-eval"` 键被 profile 发现顺序忽略（无 `--profile-config` 时回落 `./universal-agent/profile.json` 的 default）——init 写入的"活动 profile"设置不生效 |
| Q11 | 🟢 | `session list` 文本视图状态用词 "success"，其余位置用 "completed"，术语不一致 |
| Q10 | ❌误报 | ~~auth-token 未生效~~——探针误用公开路径 /health（设计上免认证）；受保护路由无 token 正确返回 401 |

验证通过项：session pause/cancel、eval reports/recordings/datasets（参数完备）、
cost/metrics/traces/logs、audit 哈希链（root_hash 输出）、repair state-events
（dry-run 报 clean）、multi-agent 状态、ecosystem catalog/verify、
thin-client 全套命令、401 认证、agentd 优雅启动。

## 四轮修复明细（同日，全部完成）

| # | 修复 | 验证 |
|---|------|------|
| Q5 | `_dispatch_remote_memory`：remote dispatch 补齐 add/get/delete（POST /v1/memory、GET/DELETE /v1/memory/{id}） | live roundtrip：add→get→delete→404 |
| Q6 | 新增 `memory/file_store.py` `FileMemoryStore`（JSONL + FileLock），file backend 下经 `RuntimeBuilder(memory_store_factory=...)` 接线；builder 既有 seed 去重保证 domain 种子不重复落盘 | 跨进程 add/get/delete 实测；多次重启后 list 无重复种子 |
| Q7 | `default_profile_config_path()` 增加 settings 链：`agent init` 在 config.json 写入绝对 `profile_config` 路径，发现顺序在固定名回落之前解析 | init 自定义 --output 后，裸 `agent config` 正确解析 Active profile |
| Q8 | `AgentdApp.handle`：`/v1/admin/*` 在无 admin_store 时返回明确 404 "admin plane is not configured ... --admin-store"，不再伪装成 unknown route | 集成测试 + live |
| Q9 | 新增 `agentd/bootstrap.py`（`build_admin_store`/`build_audit_recorder`，`__main__` 委托）；`agent serve` 补 `--admin-store/--admin-store-url-env/--audit-log/--tenant-id` 并接线 AgentdApp | serve --admin-store memory 启动 admin 面，tenant/user/role/credential 全流程可用 |
| Q12 | bootstrap 窗口条件改为「无 admin membership 或无任何 credential」——role set 先行不再锁死 credential 签发；window 在第一个真实 credential 存在后关闭 | live：role set → credential create → alice token 可用 → bootstrap token 正确 401 |
| Q11 | 撤销：`session list` 的 "success" 是有测试锁定的紧凑显示映射，非缺陷 | — |
| — | 全仓 `ruff format` 漂移清理（多租户批次 commit 引入），格式门禁恢复绿 | format --check 0 |

## 五轮验证：多 profile / 多 domain / 多用户 / 多租户（纯 CLI）

### ✅ 验证通过

- **多 profile 生命周期**：init 多实例、`--profile-config` 切换、ProfileStore 热切换
  （X-Profile via AGENT_PROFILE env 或 run --profile）、**per-profile session/store
  隔离正确**（各 profile 的 sessions/events/memory 落各自 store，互不可见）
- **RBAC 角色门禁（admin 面）**：read_only 执行 run → 403（明确 rbac 消息）；
  operator 访问 admin plane → 403；admin 全通
- **用户生命周期**：disable → 401；enable → 恢复；credential revoke → 401 立即生效
- **workspace domain 沙箱**：拒绝读取 workspace 外文件（correct ask_user 上报
  "restricted to workspace paths"）；workspace 内写文件 completed
- thin-client、multi-agent/ecosystem/domain-packages 状态面、audit/repair/cost/metrics

### 🔴 发现

| # | 问题 | 说明 |
|---|------|------|
| R5-7 | **业务数据零租户隔离** | admin 面有完整 RBAC，但业务数据面完全共享：bob（globex operator）可 list 并读取 acme 用户 alice 的全部 sessions（含 59 条 LLMCallRecorded——prompt/completion 内容跨租户泄露）、全部 memory、服务器模型端点配置。tenant_id 只作用于 admin gate 与 credential 校验。多租户宣称（P3.5）与数据面实现严重不符 |
| R5-1 | profile 管理缺口 | 无 `profile delete`；client `profile list` 不枚举自定义 profile（只报启动 profile） |
| R5-4 | server `profile list` 不枚举 profiles-dir 中的 profile（热切换可用但不可发现） |
| R5-8 | `domain-packages list` 显示 0——entry-point 内置 domain（kubernetes）不计入 discovered packages，与 ecosystem verify 的口径矛盾 |
| R5-9 | `eval datasets --dataset-dir examples/evaluation` 识别 0 数据集——仓库自带场景文件不被识别 |
| R5-10 | argparse 前缀匹配坑：`--profile X` 在未定义该 flag 的命令上被静默缩写匹配为 `--profile-config X` |
| ⚠️ | F5 复现：profile 默认 model timeout 30s 导致偶发 model_failure（ws1 列目录超时；ws2/ws3 同 profile 正常） |

### 多租户正确的部分

- credential 哈希存储（token 仅显示一次）✅
- 跨租户 deny（--tenant-id 进程级 scoping 路径存在）✅（admin 面）
- provisioning 顺序灵活（Q12 修复后 role set / credential 任意顺序）✅

## R5-7 修复：租户数据面隔离（同日）

设计：`AgentState.tenant_id`（可选、向后兼容字段）+ agentd API 层强制执行，
kernel 不引入租户逻辑；legacy 无凭据模式（principal=None）行为不变。

- 会话创建：POST /v1/sessions 按请求 principal 的 tenant 打标（run_goal /
  run_compiled_goal → AgentRuntime.run → AgentState.tenant_id）
- 持久化：codec 编解码 tenant_id（旧快照缺字段 → None，向后兼容）
- 读取守卫：/v1/sessions/{id}/* 对外租户会话一律 404（不存在即不可见，
  无存在性泄露）
- 列表过滤：GET /v1/sessions 过滤外租户会话
- memory：create 按 principal 打标 metadata.tenant_id；list 过滤；
  get/delete 对外租户记录 404
- 附带：SessionSummaryView / SessionView / MemoryView 增加 tenant_id / metadata
- 附带：config-admin 变更路由（POST/PATCH/DELETE profiles、PUT config）
  加 admin 门禁（GET 与 /v1/config/validate 不受限）

Live 验证（双租户 acme/globex）：bob 列表不含 alice 会话；按 id 读取 → 404；
acme-secret memory 对 bob 不可见；bob 的 globex memory 对 alice 404；
alice 自己一切正常；legacy 旧会话（tenant_id=None）单租户模式保持可见。

其余修复：F5（init 默认 model timeout 30s→120s）、R5-1/R5-4（`profile
create/delete` 子命令 + `profile list` 合并 stored_profiles）、R5-8/R5-9
撤销（ecosystem 包与 dataset manifest 是独立口径，非缺陷，属文档澄清）。

## 六轮验证：compile-goal / eval suite-file / 修复闭环 / 错误质量

### ✅ 验证通过

- **--compile-goal**：复合目标（健康检查+诊断）正确编译为 2 个任务并全部完成——
  动态任务扩展首次 live 验证
- **eval --suite-file**：仓库自带 cross_domain_scenarios.json 可跑通全套
  （2 场景 × 任务扩展 × 真模型）
- **config validate** 正常；workspace 沙箱拒绝越界（复测）

### 发现与修复

| # | 严重度 | 问题 | 状态 |
|---|--------|------|------|
| R6-2 | 🔴→✅ | **repair 工具在其核心场景失效**：events.jsonl 出现坏行（崩溃残留/tampering）后，事件读取器对整条日志抛异常——repair 400、doctor 500，恢复闭环断裂。修复：追加型 JSONL 事件日志按行跳过损坏行（torn line 不应使整个历史不可读），repair/doctor/全部恢复可用；新增回归测试（坏行后 list_events 仍返回全部健康事件） | **✅ 已修复** |
| R6-3 | 🔴→✅ | **OpenAI SDK transport 丢失 transient 分类**：HTTP 500 未标记 transient → 不重试、第一次即 model_failure（json_http transport 对 >=500 正确标记并重试，两个 transport 行为不一致）。修复：`_openai_status_error` 按 408/429/5xx 恢复 transient 分类；新增单测 | **✅ 已修复** |
| R6-4 | 🟡→✅ | 坏 profile JSON 的报错不含文件名（多 profile 场景无法定位）→ `from_json_file` 在 JsonCodecError 上附加路径 | **✅ 已修复** |
| R6-1 | 🟡 | `agent init --domain-backend` 无 observability 选项——observability 域只能编程组合，CLI Golden Path 不可达；cross_domain 场景（k8s+observability）无法经 CLI 端到端运行 | 记录待修 |
| ⚠️ | 瞬时 | cross-domain suite 2 场景遇模型网关瞬时 500——R6-3 修复后此类失败将自动重试 | R6-3 覆盖 |

## R6-1 修复：observability 域接入 CLI Golden Path（同日）

环境事实：集群观测栈为 **VictoriaMetrics**（monitoring ns vmagent），其
`/api/v1/query*` 与 Prometheus 完全兼容——observability 域的 PrometheusBackend
无需改动即可对接。

实现（全部经 entry-point 贡献制，零 kernel 改动）：

- `domains/observability/prometheus.py`：HttpxPrometheusTransport/PrometheusBackend
  支持自定义 headers（Bearer token 认证）
- 新增 `domains/observability/cli_runtime.py`：`profile_domain_config` +
  `build_observability_profile_service`（settings: base_url/timeout_seconds/
  bearer_token_secret）
- 新增 `domains/observability/registration.py`：`init_backends=("prometheus",)`
  的 CLI 贡献（--observability-endpoint/--observability-token-env/
  --observability-timeout-seconds）
- `profile_service.build_configured_service` 增加 observability 分派分支；
  pyproject 注册 cli_contributions entry point

**Live 验证（真实 VictoriaMetrics @ victoria-metrics.kubernetes.com）**：

- `agent init --domain-backend prometheus` 创建 profile，doctor 全绿（1 域 3 能力，
  observability-read-only policy）
- 真模型目标"Query the metric up and report..."：5 次迭代、4 次 ActionCompleted
  —— 真实查询 `up`（14 序列）、内存用量表达式（node_memory_*）、告警规则检查，
  16 条 Evidence、完整事件链，策略 read-only 放行
- 会话以 ask_user 上报结束（默认 healthy 判据与观测上报语义不匹配——已知语义，
  见 Round 2 的判据化结论）

新增单测 5 个（init resolve/非目标 backend 忽略/缺 endpoint 报错/服务构建/缺
base_url 报错）。mypy --strict（481 文件）、ruff、全量测试绿。

## 组合 profile：kubernetes + observability 跨域（R6-1 完结）

实现：`profile_service.build_configured_service` 增加 kubernetes+observability
组合分派——`RuntimeHost.from_profile_composed` 组合两域（组合式 profile 要求
顶层 `domains` 与 `runtime.domains` 同时列出两域）。observability 域构建抽为
`build_observability_domain(profile_config, domain_config=...)`。

**Live 验证（跨域单会话）**：目标"Check deployment health, then query the
metric up and report"→ 3 迭代完成；同一会话内跨域执行
`inspect_workload`（kubernetes）+ `query_metrics`（observability），
39 条 Evidence 共享世界模型——AGENTS.md §4.9 "One Agent + Multiple Active
Domains → Shared World Model" 首次 live 证明。

已知边界：`agent init` 仍为单域（组合 profile 需手写 JSON，两处 domains
列表必须一致）；跨域目标完成于默认 healthy 判据（P10b 语义同前）。

## 建议后续（更新）

1. 修 P1（只读命令离线可用）、P6（preflight 契约统一）、P8（check exit code）；
2. P10b：为变异类 goal 设计显式判据要求（CLI 警告或 evaluator 层增强）；
3. F8（--success 宽松解析/帮助示例）；
2. 把本报告场景固化为一套 live suite 文件（`--suite-file`），纳入定期验证；
3. 用 10+ 个多样化故障场景（OOMKilled、CrashLoopBackOff、探针失败、资源不足等）
   扩大样本量，产出正式的 Goal Completion Rate 基线。
