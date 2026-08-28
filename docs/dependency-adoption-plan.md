# 第三方库引入计划（2026-08-26）

- 文档性质：技术规划与决策依据，供后期按批次执行
- 结论前提：项目**没有任何文档禁止引入第三方依赖**。早期
  `dependencies = []` 是历史实现状态而非规则；AGENTS.md 反模式清单仅反对
  "vector database dependency everywhere" 与架构边界违规，不反对常规依赖。
- 执行纪律：每次引入遵循 AGENTS.md §21（先测试后行为、保持公共契约稳定）；
  若引入构成新的架构边界，同步更新架构设计文档（§86 工程规则）。

## 现状基线

- 源码约 39.7k 行，已引入 `httpx`、`openai`、`jsonschema`、`pydantic`、`orjson`、
  `filelock`、`jinja2`、`junit-xml`、`python-dateutil`、`rapidfuzz`、`rich` 等基础运行时依赖，mypy strict + ruff 全量约束
- 早期超 2000 行源码文件已被压回 1000 行以内；后续先优先做库替换和 seam 收口，
  暂不继续以拆文件作为主线
- 主要瓶颈是**代码量增长速度**，非性能（主循环为 I/O 密集：LLM 秒级、kubectl 子进程、HTTP）

---

## Tier 1 — 高收益，优先执行

### 1. httpx → 替换线程包装的 urllib 传输层（已完成）

| 项 | 说明 |
|---|---|
| 现状 | 已引入 `HttpxJsonHttpTransport` / `HttpxKubernetesApiTransport` 作为默认实现；OpenAI SDK base_url 归一化也改用 `httpx.URL`；旧 `StdlibJsonHttpTransport` / `UrllibKubernetesApiTransport` 名称保留为兼容别名 |
| 改动面 | 传输层已是 Protocol（`JsonHttpModelTransport` / `KubernetesApiTransport`），调用方仍可注入测试 adapter |
| 收益 | 真 async + 连接池；原生流式读取（SSE / streaming LLM 前置条件）；重试、超时、代理语义标准化 |
| 预估 | 改动 ~100 行，ROI 最高的单点改动 |
| 风险 | 低 |

### 2. OpenAI SDK → 替换手写 OpenAI HTTP 调用层（已完成）

| 项 | 说明 |
|---|---|
| 现状 | 已引入 `openai>=1.99,<3`；`OpenAIChatCompletionsModelAdapter` 与 `OpenAIResponsesModelAdapter` 默认通过 `OpenAISdkModelTransport` 调用官方 SDK；`JsonHttpModelTransport` 仍保留给 provider-agnostic `json_http` 和旧测试 fake 的兼容包装 |
| 改动面 | Runtime 的 `ModelAdapter` / `Decision` / 本地校验契约不变；替换的是 OpenAI provider transport seam，而不是把 Kernel 绑定到 OpenAI |
| 收益 | 鉴权、base_url、超时、HTTP 错误与 SDK response model 交给官方库维护；OpenAI-compatible Chat Completions 和 Responses 都保留同一 runtime-owned Decision 解码/验证路径 |
| 风险 | 低：新增 SDK fake 单测覆盖 payload/base_url/header/close；旧 `post_json` fake 仍可注入，避免破坏现有 integration tests |

### 3. Pydantic v2 → 替换手写校验/解析层（持续推进）

| 项 | 说明 |
|---|---|
| 现状 | 已用 Pydantic v2 接管 `host/config.py`、`profile/config.py`、`domain/package_codec.py`、`domain/runtime.py`、`persistence/codec.py` 的 JSON/config payload 类型解析，并开始接管 `agentd/routing.py` 中分布式、doctor 与 session 路由的简单 HTTP payload 校验、分布式请求体非空字段校验与中间 payload 映射以及 agentd bool/int/float query scalar / 非空 query value 解析、`agentd/http.py` 的 goal submission 非空集合约束与字段级非空校验、`agentd/server.py` 的 server config 与 request body JSON object 校验、`cli.py` 的 session event wait polling 数值边界校验、`core/arguments.py` 进入 jsonschema 前的 JSON shape 校验、`service/distributed_runtime.py` 的 distributed goal work payload 解码与非空字符串约束、`coordination/locks.py` 的 resource key/version 校验、`recovery/models.py` 的 recovery attempt 与替代 capability 校验、`evaluation/deterministic.py` 的 deterministic id 标量校验、`security/secrets.py` 的 secret reference 与 file secret key 校验、`multi_agent/contracts.py` 的 Agent Task/Result contract 解码及非完成结果 reason 校验、`multi_agent/conflicts.py` 的 proposal 非空字段与 conflict resolution 状态解码、`multi_agent/merge.py` 的 merge report 状态解码、`multi_agent/orchestrator.py` 的 delegation batch 状态解码、`multi_agent/evaluation.py` 的 expectations/report/check payload 解码、`multi_agent/registry.py` 的 Agent registry snapshot 与 record 非空字段校验、`domains/kubernetes/resources.py` 的 Kubernetes 资源摘要解析、`domains/kubernetes/evidence.py` 的 pod evidence 列表过滤、`domains/kubernetes/policy.py` 的 scale policy 环境/参数 shape 与副本数边界校验、`model/http.py` 的 OpenAI provider response payload 解析与 HTTP model adapter 构造参数非空校验、`distributed/queue_codec.py` 的 work queue 持久化 payload 解析、`distributed/worker_state.py` 的 worker registry 持久化 payload 解析、`distributed/locks.py` 的 distributed lock 持久化 payload 解析、`distributed/queue.py` / `distributed/queue_models.py` 的 runtime queue 参数校验、`evaluation/dataset.py` 的 dataset manifest、`evaluation/scenario_config.py` 的 suite/scenario config、`evaluation/recording.py` 的 report/replay recording codec 解析、`evaluation/replay.py` 的 replay recording scenario key 校验，以及 `ecosystem/validation.py` 的 registry 非空字段与 SHA-256 digest 校验；公共 dataclass API、持久化 schema version 与 legacy 默认值保留；`core.config_validation.parse_payload()`、`parse_json_object_sequence()`、`parse_json_value()`、`parse_string_sequence()`、`parse_non_empty_string()`、`parse_non_empty_string_sequence()`、`parse_unique_non_empty_string_sequence()`、`duplicate_values()`、`parse_optional_lower_sha256_hex_digest()`、`parse_bounded_float()`、`parse_bounded_int()`、numeric range helper、text scalar helper、rate helper 与 `pydantic_error_details()` 开始统一复用 Pydantic 错误路径、JSON object/list/string sequence、SDK JSON values、非空 string / sequence、HTTP/query text scalar、config/backend timeout、SHA-256 digest pattern 校验与类型文案，避免每个 codec 重写一套 formatter |
| 收益 | 预计 **-1500~2500 行**；pydantic-core（Rust）解析快一个量级；错误信息标准化 |
| 合规性 | 零风险：AGENTS.md §6 明文列出 Pydantic 为首选方案之一 |
| 剩余 | 可继续评估 agentd 其他请求体是否值得按模块迁移；当前不建议一次性替换核心 runtime contracts |
| 风险 | 低；建议继续按模块迁移，避免一次性替换所有 dataclass 构造契约 |

### 4. orjson → 替换分散的标准库 JSON 编解码（第一批已完成）

| 项 | 说明 |
|---|---|
| 现状 | 已新增 `core/json_codec.py` 作为统一 JSON codec seam，并用 `orjson` 接管 Runtime 源码中的 JSON dumps/loads、JSON-safe value coercion、文件读写、model prompt 编码、CLI JSON 输出、agentd 请求体解析与响应序列化、persistence payload、distributed queue/lock/worker registry payload、evaluation recording/config、ecosystem registry、Kubernetes API/kubectl 响应解析，以及 web/tui value text 的 JSON 渲染；`write_json_file()` 已用标准库 `tempfile` 做同目录临时文件 + atomic replace，persistence、distributed、evaluation、ecosystem、domain scaffold 与 CLI init 的完整 JSON 文件写入都走同一入口 |
| 收益 | JSON 编解码、JSON-safe value coercion 和完整 JSON 文件写入从调用点收口到一个库适配层；compact canonical JSON 用于 hash/fingerprint，pretty JSON 用于 CLI/config/report 文件输出；写入失败时统一清理临时文件 |
| 兼容性 | `JsonCodecError` 继承 `ValueError`；HTTP/CLI 的用户可见错误保持原语义；UI 中嵌入的小 JSON 片段改为 `orjson` compact one-line 格式；文件写入输出格式保持不变 |
| 剩余 | 测试和 examples 仍可继续使用标准库 JSON 构造 fixture；若未来要强制全 repo 收口，再单独迁移测试工具层 |
| 风险 | 低：全量 `ruff`、`mypy` 与 `pytest` 已覆盖 |

### 5. Starlette + uvicorn → 替换 agentd socket/server / routing 适配层（持续推进）

| 项 | 说明 |
|---|---|
| 现状 | `AgentdHttpServer` 已从 stdlib `http.server` 切到 Starlette ASGI + uvicorn；`AgentdRouteMatcher` 已用 Starlette `Route.matches()` 接管 agentd API 与 console route family 的模板匹配，并用 `QueryParams` 接管 HTTP query 解析；agentd auth header lookup 已用 Starlette `Headers` 接管大小写不敏感匹配；旧私有 path helper 已清理，稳定 Runtime API / Console 路由契约保留 |
| 收益 | 生产服务边界获得 ASGI/uvicorn 生命周期、并发、socket 处理、标准 path matching 与 HTTP header 语义；CLI/server 注入测试契约保持兼容 |
| 剩余 | 后续在稳定 schema 后补 OpenAPI；更大范围的 CLI Typer 迁移单独排期，避免扰动现有命令契约 |
| 风险 | 中：依赖树变大；需持续保证 Runtime API 行为不变（现有 test_agentd_routes / test_agentd_server 兜底） |

### 6. prometheus-client → 替换手写 Prometheus text exposition（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `operations/prometheus.py` 已使用 `CollectorRegistry`、`Gauge` 与 `generate_latest` 生成 Prometheus exposition |
| 收益 | 指标格式交给官方库维护，避免手写 HELP/TYPE/sample 行细节；Runtime 仍只负责从事件投影 metrics view |
| 风险 | 低：输出数值使用 prometheus-client 的 float 表达，相关 CLI/agentd 测试已覆盖 |

### 7. PyYAML → 替换 Domain manifest 的 JSON-only loader（已完成）

| 项 | 说明 |
|---|---|
| 现状 | Domain package loader/registry 已使用 `yaml.safe_load` 读取 manifest，并兼容 `manifest.json`、`manifest.yaml`、`manifest.yml` |
| 收益 | 对齐设计文档的 `manifest.yaml` 目标，同时保留 scaffold 默认写 `manifest.json` 的兼容契约 |
| 风险 | 低：同目录存在多个 manifest 时显式报错，避免 registry 静默选错 |

### 8. filelock → 替换手写 fcntl 文件互斥锁（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `FileWorkQueue`、`FileDistributedLockRegistry`、`FileWorkerRegistry` 已使用 `FileLock` 接管 `.lock` 文件互斥；重入保护、JSON 原子替换和公开 registry/queue 契约保持不变 |
| 收益 | 去除三处直接 `fcntl` 调用，获得跨平台文件锁抽象；测试仍覆盖跨进程互斥行为 |
| 风险 | 低：仅替换文件型本地协调边界，不改变 SQLite 后端和调度语义 |

### 9. Jinja2 → 替换手写 Web 页面外壳 / Hero / Row 拼接（持续推进）

| 项 | 说明 |
|---|---|
| 现状 | `web_ui._page()`、`_fragment()`、`_section()`、`_section_blocks()`、`_empty_paragraph()`、`_metric_card()`、`_metric_grid()`、`_table()`、`_table_from_cells()`、`_table_section()`、`_hero_block()`、`_table_row()` 与 `_detail_list()` 已用 Jinja2 接管 Web Console 与 Evaluation Console 的页面骨架、公共 section/card/grid/table/empty-state 片段、hero/nav/status pill 片段；Session、World、Catalog 与 Operational 页面族的主要 table section 已从逐行手写 HTML 组装迁到公共 Jinja2 table helper；`domain/package_runtime_stub.py` 也已改用 Jinja2 接管 scaffold runtime stub 源码模板；Evaluation Console 已复用公共 helper，去除第二套手写 hero HTML 与 summary grid HTML |
| 收益 | HTML 外壳与高频片段渲染统一到模板 seam，减少重复拼接和 escaping 漏洞面；后续复杂页面只需要准备 cell 数据，不必重复编写空表、row 渲染与 raw cell handling |
| 风险 | 低：渲染内容顺序和现有 URL/section helper 不变，Web/Evaluation/agentd route 测试覆盖 escaping、导航链接、table section 输出与关键页面文本 |

### 10. python-dateutil → 替换手写 ISO datetime 兼容解析（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `core.time.parse_iso_datetime()` 已使用 `dateutil.parser.isoparse` 接管 ISO 8601 解析；persistence codec、agentd HTTP payload、CLI `--before` 与 distributed runtime payload 共享同一时间解析入口 |
| 收益 | 去除 `value.replace("Z", "+00:00")` / `datetime.fromisoformat()` 分散写法，统一 `Z` 后缀、时区强制和错误文案 |
| 风险 | 低：保留 public dataclass/API 类型，测试覆盖持久化恢复和 CLI/HTTP 输入解析 |

### 11. Rich → 替换手写终端渲染执行层（第一批已完成）

| 项 | 说明 |
|---|---|
| 现状 | Runtime TUI 与 Evaluation Console 的 deterministic text output 已通过共享 `terminal.render_terminal_lines()` 使用 Rich `Console` / `Text` 渲染；保持现有纯文本输出契约，先不引入交互式 Textual |
| 收益 | 终端渲染 seam 从手写 `"\n".join(...)` 与重复 Console/StringIO 循环收口到库适配层，为后续颜色、表格、实时刷新和 Textual headless 测试做准备 |
| 风险 | 低：输出不含 ANSI，现有 TUI / Evaluation Console / CLI 测试继续使用关键文本断言 |

### 12. junit-xml → 替换手写 JUnit XML 生成（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `evaluation/junit.py` 已使用 `junit_xml.TestSuite` / `TestCase` 接管 testcase、failure 与 XML escaping；Runtime 只保留 evaluation-specific failure message/text 组装和 suite duration 覆盖 |
| 收益 | JUnit XML 结构交给专用库维护，减少手写 Element/SubElement 拼装和 escaping 细节；CLI `eval --format junit` 外部契约保持单个 `<testsuite>` 根节点 |
| 风险 | 低：单测覆盖 scenario/gate failure 结构，CLI integration 覆盖 JUnit 输出可解析性 |

### 13. graphlib / hashlib.file_digest → 替换手写图排序与文件 digest（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `ecosystem/catalog.py` 的 Domain package install plan 与 `multi_agent/orchestrator.py` 的 batch delegation 调度已用 `graphlib.TopologicalSorter` 接管；`ecosystem/validation.py`、`domain/package_verification.py` 与 `tasks/manager.py` 的 dependency cycle 检测也使用同一标准库图算法；registry file SHA-256 改用 `hashlib.file_digest` |
| 收益 | 去掉五处手写 DFS/cycle stack/pending-ready 扫描逻辑和手写 chunk digest loop；依赖排序、cycle detection、batch scheduling 与文件摘要行为交给标准库维护 |
| 风险 | 低：新增 `verify=False` install-plan cycle 测试，既有 ecosystem/domain registry verification 测试继续覆盖 cycle 与 sha256 mismatch |

### 14. RapidFuzz → 替换手写 memory relevance matching（已完成）

| 项 | 说明 |
|---|---|
| 现状 | `memory/retrieval.py` 已使用 `rapidfuzz.process.extract()` / `fuzz.WRatio` / `utils.default_process` 接管 memory relevance 的文本标准化与模糊匹配 |
| 收益 | 去除自维护 token overlap/substring scoring 的空间，提升拼写变体、词序变化和近似查询的稳定性；仍保留 Runtime 自有 confidence threshold 与 limit 逻辑 |
| 风险 | 低：`test_memory.py` 覆盖阈值、截断、模糊词形变体和 confidence 加权 |

---

## Tier 2 — 明确收益，按需排期

| 库 | 目标模块 | 收益 | 备注 |
|---|---|---|---|
| Textual | `tui.py` | 静态 Rich text output → 真 TUI（事件循环、刷新、筛选、详情面板） | Rich deterministic renderer 已作为第一批终端 seam；Textual 仍需等 Runtime API/生产流程更稳定后再做 |
| jsonschema | `core/arguments.py` / `tools/runtime.py:181` | 已由 `Draft202012Validator` 接管 capability/tool argument schema 校验，并在进入 jsonschema 前复用 Pydantic JSON adapter 校验 schema/arguments 形状；`tools/runtime.py` 继续通过统一 contract 入口调用 | 后续可补充更多 JSON Schema 关键字覆盖用例 |
| packaging | `domain/package_models.py` | 已用 `SpecifierSet` 接管 `compatibility.runtime_api` 校验与 runtime API version 支持判断 | 当前刻意不收紧所有 Domain identity version 字符串，避免破坏既有包标识兼容性 |
| opentelemetry-proto | `operations/otlp.py` | 已用官方 OTLP protobuf schema 类型接管 trace export payload 生成，保留现有 JSON/hex ID Runtime API 契约 | 后续若需要直接推送 Tempo/Collector，再引入 `opentelemetry-sdk` / OTLP exporter |
| Typer | `cli_parser.py` / `cli.py` | argparse 样板约 -30%；命令声明与 help 文案更可维护 | Rich 已先用于终端渲染；Typer 迁移单独排期，避免一次性改动全部 CLI 契约 |

## Tier 3 — 场景触发再引入

| 库 | 触发条件 | 说明 |
|---|---|---|
| Redis 或 PostgreSQL | P6 分布式超过本地原语 | 文件轮询队列 → `FOR UPDATE SKIP LOCKED` / Redis TTL 锁的生产级语义 |
| Jinja2 | Web 复杂 section helper 继续膨胀 | 页面外壳、hero/nav、公共 table section 与主要 row/detail 已完成；后续只在 section 复杂度继续上升时迁移更细模板 |

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
    Starlette/uvicorn(agentd server 适配层，已完成)
    FastAPI/Starlette(agentd route primitives 渐进迁移) + OpenAPI 导出
    → 官方 Python/JS client 由 schema 自动生成

第三批（体验与观测）
    Rich deterministic terminal renderer（第一批已完成）
    Textual TUI + Typer CLI（后续渐进）
    opentelemetry-proto OTLP payload（已完成）；opentelemetry-sdk exporter（按部署需要再接入）
    PyYAML(Domain Package manifest YAML 兼容，已完成)
    packaging runtime_api compatibility specifier（已完成）
    filelock 文件协调锁（已完成）
    Jinja2 Web/Evaluation 页面外壳、hero/nav、主要 row/detail（持续推进）
    python-dateutil ISO datetime 解析（已完成）

第四批（触发式）
    Redis/PostgreSQL 分布式后端
    Jinja2 模板化 web console 复杂 section 片段
```

## 关联待办（非依赖类，同批考虑）

1. CLI thin-client 模式（`--api-url` 转发到 agentd）—— 对齐设计文档 §34
   "CLI must call the Runtime API"；当前每个 CLI 命令自建 service
2. agentd/app.py 的 `domain_package_body()` 与 `DomainPackageView` 字段对齐
3. 引入第二个真实 Domain（如 observability/prometheus），验证多域共享 World Model
4. 按设计文档 §82 建立成熟度指标基线（Goal Completion Rate 等，用 eval harness）
