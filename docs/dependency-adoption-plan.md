# 第三方库引入计划（2026-08-26）

- 文档性质：技术规划与决策依据，供后期按批次执行
- 结论前提：项目**没有任何文档禁止引入第三方依赖**。`pyproject.toml` 的
  `dependencies = []` 是当前实现状态而非规则；AGENTS.md 反模式清单仅反对
  "vector database dependency everywhere" 与架构边界违规，不反对常规依赖。
- 执行纪律：每次引入遵循 AGENTS.md §21（先测试后行为、保持公共契约稳定）；
  若引入构成新的架构边界，同步更新架构设计文档（§86 工程规则）。

## 现状基线

- 源码约 39.7k 行，零运行时依赖，mypy strict + ruff 全量约束
- 已出现 4 个超 2000 行文件：`agentd/app.py`(2796)、`web.py`(2728)、
  `cli.py`(2781)、`service/runtime.py`(2222) —— 违反 AGENTS.md §17.1 小模块偏好
- 主要瓶颈是**代码量增长速度**，非性能（主循环为 I/O 密集：LLM 秒级、kubectl 子进程、HTTP）

---

## Tier 1 — 高收益，优先执行

### 1. httpx → 替换线程包装的 urllib 传输层（已完成）

| 项 | 说明 |
|---|---|
| 现状 | 已引入 `HttpxJsonHttpTransport` / `HttpxKubernetesApiTransport` 作为默认实现；旧 `StdlibJsonHttpTransport` / `UrllibKubernetesApiTransport` 名称保留为兼容别名 |
| 改动面 | 传输层已是 Protocol（`JsonHttpModelTransport` / `KubernetesApiTransport`），调用方仍可注入测试 adapter |
| 收益 | 真 async + 连接池；原生流式读取（SSE / streaming LLM 前置条件）；重试、超时、代理语义标准化 |
| 预估 | 改动 ~100 行，ROI 最高的单点改动 |
| 风险 | 低 |

### 2. Pydantic v2 → 替换手写校验/解析层

| 项 | 说明 |
|---|---|
| 现状 | `_string/_int/_bool/_optional_*` 手写解析器散布于 `host/config.py`(7 处)、`domain/package.py`(5 处)、`profile/config.py`、`persistence/codec.py`；各 dataclass 手写 `validate()` |
| 收益 | 预计 **-1500~2500 行**；pydantic-core（Rust）解析快一个量级；错误信息标准化 |
| 合规性 | 零风险：AGENTS.md §6 明文列出 Pydantic 为首选方案之一 |
| 风险 | 低；建议从 config 层渐进迁移 |

### 3. FastAPI/Starlette + uvicorn → 替换手写 agentd HTTP 服务

| 项 | 说明 |
|---|---|
| 现状 | `agentd/app.py` 2796 行：stdlib http.server 上手写路由、HMAC 中间件、SSE、JSON 序列化 |
| 收益 | 预计 **-1200~1500 行**；SSE 一等公民；uvicorn 吞吐高于 stdlib 线程模型；自动 OpenAPI 文档（官方 client 可生成）；认证收进 middleware 缩小审计面 |
| 风险 | 中：依赖树变大；需保证 Runtime API 行为不变（现有 test_agentd_routes 兜底） |

---

## Tier 2 — 明确收益，按需排期

| 库 | 目标模块 | 收益 | 备注 |
|---|---|---|---|
| Textual | `tui.py` | 683 行一次性静态打印 → 真 TUI（现状无 curses/事件循环/刷新，见 cli.py:1192 仅"建快照→渲染→退出"） | headless 测试模式保住可测性；现有 `build_tui_snapshot` 直接复用为数据层 |
| jsonschema | `tools/runtime.py:181` | `validate_tool_arguments` 为 JSON Schema 子集手写实现 → 全规范合规（oneOf/pattern 等） | 删维护负担 |
| opentelemetry-sdk | `operations/runtime.py:544+` | OTel 形状投影（span/OTLP JSON）变真 span 推送，对接已有 Tempo/Grafana 监控栈 | 观测闭环；README 列 "OpenTelemetry exporters" 为未来工作 |
| Typer + Rich | `cli.py` | argparse 样板约 -30%；内省命令表格化输出 | 与 Textual 同批做体验统一 |

## Tier 3 — 场景触发再引入

| 库 | 触发条件 | 说明 |
|---|---|---|
| Redis 或 PostgreSQL | P6 分布式超过本地原语 | 文件轮询队列 → `FOR UPDATE SKIP LOCKED` / Redis TTL 锁的生产级语义 |
| Jinja2 | `web.py` 继续膨胀 | 2728 行 f-string 拼 HTML + escape → 模板化，预计省一半 |
| PyYAML | Domain manifest 从 json 迁 yaml | 设计文档 §11 写的是 manifest.yaml，k8s 风格创作更友好 |

---

## 不引入清单（维持决策）

| 库类型 | 理由 |
|---|---|
| LangChain / LlamaIndex 类框架 | 与 Kernel 架构根本冲突（状态放对话历史），违反 AGENTS.md 核心原则 |
| kubernetes 官方 client | kubectl 子进程 / API 直连是深思熟虑的选择（kubectl.py:33），维持 |
| 向量数据库 | AGENTS.md 反模式清单点名项 |
| Celery 类重型任务队列 | 分布式层自有抽象，缺的只是存储后端实现 |

---

## 执行路线图

```text
第一批（立即可做，一周内可见效）
    httpx（传输层 Protocol 实现替换）
    Pydantic（host/profile/domain config 层先行）

第二批（产品化冲刺）
    FastAPI(agentd 重写) + OpenAPI 导出
    → 官方 Python/JS client 由 schema 自动生成

第三批（体验与观测）
    Textual TUI + Typer/Rich CLI
    opentelemetry-sdk exporter（推送 Tempo）

第四批（触发式）
    Redis/PostgreSQL 分布式后端
    Jinja2 模板化 web console
```

## 关联待办（非依赖类，同批考虑）

1. CLI thin-client 模式（`--api-url` 转发到 agentd）—— 对齐设计文档 §34
   "CLI must call the Runtime API"；当前每个 CLI 命令自建 service
2. agentd/app.py 的 `domain_package_body()` 与 `DomainPackageView` 字段对齐
3. 引入第二个真实 Domain（如 observability/prometheus），验证多域共享 World Model
4. 按设计文档 §82 建立成熟度指标基线（Goal Completion Rate 等，用 eval harness）
