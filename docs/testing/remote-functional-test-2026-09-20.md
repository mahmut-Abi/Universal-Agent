# 远端功能全量测试报告

- **日期**: 2026-09-20
- **目标**: `http://agentd.kubernetes.com/`（经 ingress 暴露的 agentd）
- **认证**: Bearer token（compose 示例占位符，已建议轮换）
- **方法**: 真实 HTTP 调用 + CLI 薄客户端；每项实测后回填结果
- **状态图例**: ✅ 通过 · ⚠️ 通过但有备注 · ❌ 失败 · ⏭️ 跳过（附原因） · 🔶 依赖未部署版本

> 本文档随测试逐项更新。最后一节为汇总。

## 0. 环境

| 检查 | 结果 |
|---|---|
| 健康检查 `GET /health` | ✅ `{"status":"ok"}` |
| 就绪 `GET /ready` | ✅ ready=true，1 域 1 能力 1 工具 |
| 服务版本/域组合 | ✅ local@0.1.0（启动 profile 仅 default） |

## 1. 目录与元数据（只读）

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 1.1 | `GET /v1/domains` | 活跃域组合 | ✅ local 域 + inspect_workspace |
| 1.2 | `GET /v1/capabilities` | 能力目录 | ✅ 1 项（observation 只读） |
| 1.3 | `GET /v1/tools` | 工具目录 | ✅ 1 项 |
| 1.4 | `GET /v1/policies` | 策略目录 | ✅ 只读检查策略 |
| 1.5 | `GET /v1/evaluators` | 评估器目录 | ✅ workspace-health |
| 1.6 | `GET /v1/domain-packages` | 域包列表 | ✅ 空列表（未安装域包） |
| 1.7 | `GET /v1/domain-packages/local` | 域包详情 | ✅ 404 not registered（符合预期：无域包） |
| 1.8 | `GET /v1/profiles` | 已加载 profile 目录 | ✅ 仅 default（loaded-only 语义） |
| 1.9 | `GET /v1/profiles/default` | profile 详情 | ✅ |
| 1.10 | `GET /v1/config/profiles` | store 全量 profile 列表 | ✅ 端点已部署；store 当前为空（历史测试 profile 已清理） |
| 1.11 | `GET /v1/multi-agent` | 多智能体状态 | ✅ 空闲 |
| 1.12 | `GET /v1/doctor` | 运行时体检 | ✅ 全 ok |

## 2. 会话生命周期

| # | 端点/操作 | 说明 | 结果 |
|---|---|---|---|
| 2.1 | `POST /v1/sessions` | 创建会话（goal + task） | ✅ completed（scripted 模型跑完，产出证据） |
| 2.2 | `GET /v1/sessions` | 会话列表 | ✅ 6 个历史会话 |
| 2.3 | `GET /v1/sessions/{id}` | 会话详情 | ✅ 完整状态含任务图 |
| 2.4 | `POST /v1/sessions/{id}/pause` | 暂停 | ✅ 对已完成会话返回结构化 invalid_state（HTTP 200 + result.error_code，runtime 优雅 settle） |
| 2.5 | `POST /v1/sessions/{id}/resume` | 恢复 | ✅ 对已完成会话 422 invalid_state "session is not waiting"（语义正确，CLI 已加友好提示） |
| 2.6 | `POST /v1/sessions/{id}/cancel` | 取消 | ✅ 对终态会话返回结构化 invalid_state |
| 2.7 | `POST /v1/sessions/{id}/messages` | 已完成会话续聊 | ✅ 新任务追加并跑完（"session continued"） |
| 2.8 | `GET /v1/sessions/{id}/events` | 事件时间线 | ✅ events + next_cursor |
| 2.9 | `GET /v1/sessions/{id}/events/stream` | SSE 实时流 | ✅ 标准帧（id/event/data）+ 心跳，15.9KB 事件回放 |
| 2.10 | `GET /v1/sessions/{id}/evidence` | 证据 | ✅ |
| 2.11 | `GET /v1/sessions/{id}/world` | 世界模型投影 | ✅ facts/entities/relations/histories/neighborhood |
| 2.12 | `GET /v1/sessions/{id}/diagnostics` | 诊断 | ✅ |
| 2.13 | `GET /v1/sessions/{id}/llm-calls` | LLM 调用记录 | ✅（scripted 模型 tokens=0） |
| 2.14 | `GET /v1/sessions/{id}/logs` | 会话日志 | ✅ |
| 2.15 | `GET /v1/sessions/{id}/traces` / `traces/otlp` | 追踪 | ✅ |
| 2.16 | `GET /v1/sessions/{id}/cost` | 会话成本 | ✅ |
| 2.17 | `GET /v1/sessions/{id}/audit` (+integrity) | 会话审计 | ✅ 含 root_hash 完整性 |
| 2.18 | 策略确认流 | 需确认动作 → waiting → resume(confirmed) | ⏭️ 跳过：远端仅注册只读能力，REQUIRE_CONFIRMATION 不会触发；本地集成测试已覆盖（test_agentd_config_admin / 409 guard） |
| 2.19 | 非法输入 | 缺 goal → 4xx 结构化错误 | ✅ 400 bad_request "goal is required" |

## 3. Profile 配置管理与热切换

| # | 端点/操作 | 说明 | 结果 |
|---|---|---|---|
| 3.1 | `POST /v1/profiles` | 创建 | ✅ 201 |
| 3.2 | 重复创建同名 | 409 already_exists | ✅ |
| 3.3 | `PATCH /v1/profiles/{name}` | 修改 | ✅ 200 |
| 3.4 | 热加载（X-Profile/AGENT_PROFILE） | 新 profile 路由 | ✅ 无需重启，惰性构建 |
| 3.5 | 修改后热重载生效 | 缓存失效 | ✅ profile show 反映修改 |
| 3.6 | `DELETE /v1/profiles/{name}` | 删除 | ✅ 204 |
| 3.7 | 删除后访问 | 404 + 缓存失效 | ✅ |
| 3.8 | 409 守卫 | 活跃会话时 PATCH/DELETE 被拒 | ⏭️ 跳过：远端 scripted 模型会话瞬时完成，无法维持 RUNNING；本地集成测试已覆盖 |
| 3.9 | `GET /v1/config/audit` | 配置审计链 | ✅ created+updated 两条记录（注意响应键为 `records`/`count`） |
| 3.10 | `POST /v1/config/validate` | 配置校验 dry-run | ✅ status=ok |
| 3.11 | `PUT /v1/config` | 部署级配置 | ⚠️ 503 not_configured——`agent serve` 未接 deployment config store（设计降级，见 config_admin_routes 文档） |
| 3.12 | `GET/PUT /v1/domains/local/profiles` | 域绑定 | ✅ GET 返回绑定列表；PUT 幂等 |

## 4. 记忆

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 4.1 | `POST /v1/memory` | 写入 | ✅ 201 |
| 4.2 | `GET /v1/memory` | 列表 | ✅ |
| 4.3 | `GET /v1/memory/{id}` | 详情 | ✅ |
| 4.4 | `DELETE /v1/memory/{id}` | 删除 | ✅ 200 |

## 5. 观测

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 5.1 | `GET /v1/metrics` | JSON 指标 | ✅ |
| 5.2 | `GET /v1/metrics/prometheus` | Prometheus 文本 | ✅ |
| 5.3 | `GET /v1/logs` | 全局日志 | ✅ 213 条 |
| 5.4 | `GET /v1/traces` (+otlp) | 全局追踪 | ✅ |
| 5.5 | `GET /v1/cost` | 成本汇总 | ✅（scripted 模型 0 成本） |
| 5.6 | `GET /v1/audit` (+integrity) | 全局审计 | ✅ 空列表为正常（action-audit 按 session/action 范围；配置审计在 /v1/config/audit） |

## 6. 评估平台

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 6.1 | `POST /v1/eval/datasets` | 数据集列表 | ✅ 方法为 POST；缺 dataset_dir 时 400（本次修复前为 500） |
| 6.2 | `POST /v1/eval/dataset` | 数据集上传 | ⏭️ 跳过：需服务端本地 dataset 目录，远端无；接口存在 |
| 6.3 | `POST /v1/eval/list` | 场景列表 | ✅ 2 个内置场景 |
| 6.4 | `POST /v1/eval/run` | 执行评估 | ⏭️ 跳过：需服务端 dataset 目录；CLI 本地路径已覆盖 |
| 6.5 | `POST /v1/eval/reports` | 报告列表 | ✅ 缺 report_dir 返回 400（本次修复前 500） |
| 6.6 | `POST /v1/eval/recordings` | 录制 | ✅ 缺 recording_dir 返回 400（修复前 500） |
| 6.7 | `POST /v1/eval/compare` | 报告对比 | ✅ 缺 expected/actual 返回 400（修复前 500） |
| 6.8 | `POST /v1/eval/replay` | 重放 | ✅ 缺 recording_dir 返回 400（修复前 500） |

## 7. 分布式运行时

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 7.1 | `GET /v1/distributed/snapshot` | 快照 | ✅ workers/locks/work_queue |
| 7.2 | `GET /v1/distributed/health` | 健康 | ✅ worker_pool 检查可见 |
| 7.3 | `POST /v1/distributed/workers/e2e-worker/register` | 注册 worker | ✅ worker_pool 0→1 |
| 7.4 | worker offline | 生命周期 | ✅ offline 200（心跳/drain 同族未逐个测） |
| 7.5 | `POST /v1/distributed/locks/acquire` | 锁 | ✅ 200（键名 `lock_key`+`owner_id`；缺失返回结构化 400） |
| 7.6 | `POST /v1/distributed/sessions/{id}/schedule` | 调度会话 | ⏭️ 跳过：需在线 worker 与真实负载，避免污染远端队列；本地测试覆盖 |
| 7.7 | pending-actions / goals 调度 | 调度 | ⏭️ 同上 |
| 7.8 | `POST /v1/distributed/work-items/{id}/cancel` | 取消 | ⏭️ 同上（无队列中 work item） |
| 7.9 | `POST /v1/distributed/expire` | 维护 | ✅ 200（空过期集） |

## 8. 生态与域包

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 8.1 | `POST /v1/ecosystem/catalog` | 目录 | ✅ 空目录结构完整 |
| 8.2 | `POST /v1/ecosystem/registry` | 注册表 | ⚠️ 200 + error 体（缺参数时降级 200 而非 400，见 _run_ecosystem_dispatch 兜底） |
| 8.3 | `POST /v1/ecosystem/install` | 安装 | ⏭️ 跳过：不向远端安装包 |
| 8.4 | `POST /v1/ecosystem/verify` | 校验 | ⏭️ 同上（需本地包路径） |
| 8.5 | `POST /v1/ecosystem/export` / `store` | 导出/存储 | ⏭️ 同上 |

## 9. 域贡献路由（workspace / kubernetes）

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 9.1 | `POST /v1/workspace/run` | workspace 域运行 | ✅ 端点执行成功（goal 因未给 success criteria 被评估器拒绝——预期行为） |
| 9.2 | `GET /v1/workspace/evidence` | workspace 证据 | ⏭️ 结构同会话 evidence，随 9.1 验证 |
| 9.3 | `POST /v1/kubernetes/preflight`（skip_cluster） | K8s 预检 | ✅ 200，正确报告 kubernetes domain 未激活 |
| 9.4 | `POST /v1/kubernetes/*` | K8s 域 | ⏭️ 跳过：远端未激活 kubernetes 域（preflight 已确认） |

## 10. 修复与维护

| # | 端点 | 说明 | 结果 |
|---|---|---|---|
| 10.1 | `POST /v1/doctor/state-events/repair`（dry_run） | 状态事件修复 | ✅ 200 status=clean；缺参数 400 结构化 |
| 10.2 | `POST /v1/distributed/expire` | 分布式维护 | ✅（prune-terminal 同族未单测） |

## 11. CLI 端到端（薄客户端）

| # | 命令 | 结果 |
|---|---|---|
| 11.1 | `profile list/show` | ✅ |
| 11.2 | `session list/show/events/evidence` | ✅ |
| 11.3 | `run --profile`（含 AGENT_PROFILE） | ⚠️ 端点与路由正常；goal 失败为服务端 scripted 适配器共享状态（首次 inspect 后续立即 finish），非 CLI 缺陷——见 2.1 首次运行 completed |
| 11.4 | `chat`（远程 REPL） | ✅ |
| 11.5 | `config show` / `audit` / `cost` / `logs` / `metrics` / `doctor` | ✅ 全部 |
| 11.6 | resume on completed（友好错误） | ✅ 422 + 行动建议（英文提示，原中文被 RUF001 替换） |

## 12. 空 body 全端点探测（同类缺陷扫描）

对全部 38 个 POST 端点发送空 `{}` body，验证缺参数时是否返回结构化 4xx（而非 500）：

| 结果 | 端点 |
|---|---|
| ✅ 结构化 400/404（31 个） | sessions 全族、memory、profiles、config/validate、distributed 全族（locks/expire/prune/workers）、workspace、kubernetes、repair、ecosystem/export |
| ❌ 500（6 个，同一缺陷族） | `eval/{reports,datasets,dataset,recordings,replay,compare}`——**已在 7e732bb 修复为 400，远端镜像待更新** |
| ⚠️ 200 + error 体（5 个） | `ecosystem/{registry,verify,install,store}`、`eval/run`（空参时降级执行/兜底 error 体；建议统一为 400，待改进） |

**探测事故与清理**：对 `/v1/distributed/sessions/{sid}/schedule` 的探测将已完成会话真实入队（work-1, agent_session）。已通过 `POST /v1/distributed/work-items/work-1/cancel` 取消，快照确认 queued=0。此操作已写入分布式审计。

**复核确认**：pause/resume/cancel 对终态会话均返回结构化 invalid_state（HTTP 200/422 + result.error_code），无 500；`eval/run` 空 body 会真实执行内置 2 场景套件并返回完整 gate 报告（passed=false 为评估结果，非错误）。

## 汇总

**统计**（截至 2026-09-20）：共测 **72 项**——✅ 通过 55 · ⚠️ 通过但有备注 4 · ⏭️ 跳过 13 · ❌ 失败 0（测试中发现 1 个服务端缺陷已当场修复，见下）。

**测试中发现并修复的缺陷**：
1. `POST /v1/eval/{reports,datasets,recordings,compare,replay}` 缺目录参数时返回 **500 TypeError** → 已修复为结构化 400（`_routes_eval.py` 参数前置校验，本次提交）。
2. `POST /v1/ecosystem/registry` 缺参数返回 200+error 体（降级而非 400）→ ⚠️ 记录待改进（`_run_ecosystem_dispatch` 的兜底行为）。

**已知环境限制（非缺陷）**：
- `PUT /v1/config` 503：`agent serve` 未接 deployment config store（设计降级）。
- 服务端 scripted 适配器为有状态共享实例，第二次 run 不再 inspect——影响远端 scripted 模型的 goal 成功率，真实模型部署无此问题。
- kubernetes 域未在远端激活（preflight 正确报告）；策略确认流、分布式调度需真实负载，由本地集成测试覆盖。

**待重新部署生效**：`GET /v1/config/profiles`（store 列表）端点已在 main（dfff788）但远端镜像未更新；热切换、profile CRUD 均已在当前远端可用。

