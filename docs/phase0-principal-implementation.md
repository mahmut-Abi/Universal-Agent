# Phase 0 实现清单：打通 tenant/user 作用域

> 配套设计：`docs/multitenancy-user-management-design.md` §5、§13（Phase 0）。
> 本文件是**可执行的实现单**：每项给出文件、函数、精确改动、要加的测试与"做完了"判定。遵循 `AGENTS.md`——**先加/随加测试、保接口稳定、边界守卫测试随层变更更新**。
>
> **状态：Phase 0 已实现（2026-09-20）**，实现与测试落在：`core.DEFAULT_TENANT_ID`、`StoreConfig.tenant_id/effective_tenant_id`、`security/principal.py`、`persistence/postgres.py` schema v2（`ua_sessions.user_id` + 真实 ALTER 迁移）、`host/runtime.py:_postgres_store` 透传 tenant。新增测试：`test_store_tenant_config.py`、`test_security_principal.py`、`test_postgres_migration.py`、`test_tenant_isolation.py`（后两者是 gated integration，无 PG DSN 时跳过）。全套单测 1762 passed / 13 skipped，ruff + mypy(strict) 通过。
>
> 实现时发现的边界事实：`configuration` 与 `persistence` 都是只依赖 `core` 的兄弟模块（零互相引用），故默认租户常量归 `core`；`apply_postgres_migrations` 只用 `create_all`（不会 ALTER 已存在表），故 v2 的 `user_id` 列走显式 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 迁移步骤。
>
> Phase 0 目标：让 `tenant_id` 与 `user_id` 真正贯通 `config → store → service`，跨租户拒绝在 store 层生效；同时引入最小 `security/principal.py` 类型。**不做** IdP、不做 RBAC、不做用户管理面。

---

## 0. 边界与范围纪律

- **只动**：`configuration.py`、`host/runtime.py`、`persistence/postgres.py`、新增 `security/principal.py`，以及对应测试与 `tests/unit/test_package_boundaries.py`。
- **不动**：`agentd/app.py` 路由、`profile/*`、kernel core 循环。
- **后端语义**：POSTGRES 是唯一支持 tenant 作用域的后端；FILE/SQLITE/MEMORY 保持**单隐式租户**（不传 tenant，行为不变）。此差异要在 `StoreConfig.validate()` 与文档中写明，避免误导。
- **兼容**：旧 `default` 部署迁移后原 session 可读、行为不变。

---

## 1. Configuration 层：`StoreConfig.tenant_id`

### 1.1 `src/universal_agent/configuration.py`
- `_StoreConfigPayload`（约 L66）新增字段：`tenant_id: str | None = None`（可选，注释：仅在 `backend==POSTGRES` 时使用；默认解析为 `"default"`）。
- `StoreConfig`（约 L146）新增字段 `tenant_id: str | None = None`。
  - `from_mapping`：把 `payload.tenant_id` 传入（注意保持位置/关键字参数顺序，`classmethod` 构造处同步）。
  - `validate()`：解释规则 ——
    - `backend==POSTGRES` 且 `tenant_id is None` → 允许，归一化为 `POSTGRES_DEFAULT_TENANT_ID`；非 None 则 `parse_non_empty_string(tenant_id, "store tenant_id")`。
    - FILE/SQLITE/MEMORY：`tenant_id` 若被显式设置 → 报错或忽略？**建议报错**（在后端不支持时给清晰反馈），文档写明语义。
- 归一化处新增一个只读属性或其他方式暴露 `effective_tenant_id`，避免多处重复默认值。
- **默认常量归属（先做 §1.3 再改）**：不要从 `persistence` import `POSTGRES_DEFAULT_TENANT_ID`（会制造 configuration→persistence 兄弟边）。改为在 `core` 层定义共享常量（见 §1.3），两边共同依赖 `core`。

### 1.3 `core` 层共享默认租户常量（必须先做，避免边界违规）
- **已核实**：`configuration.py` 当前只 import `core`（与 `config_validation`）；`persistence/postgres.py` 只 import `core`/`eventstream`/`state`。两者是共同依赖 `core` 的兄弟模块，**当前无 configuration↔persistence 引用**，`test_package_boundaries.py` 也未登记此边。
- **方案**：在 `core` 新增常量定义 `DEFAULT_TENANT_ID = "default"`（位置建议 `core/__init__.py` 或新 `core/identity.py`）。
  - `configuration.StoreConfig` 用它做归一化；
  - `persistence/postgres.py` 的 `POSTGRES_DEFAULT_TENANT_ID` 改为别名到它（保留常量名以减少波及）。
- **红线**：禁止 `configuration → persistence` 或 `persistence → configuration` 的新依赖边。实现后跑 `test_package_boundaries.py` 确认无新违规。

### 1.2 测试
- `tests/unit/test_configuration.py`（或新 `test_store_tenant_config.py`）：
  1. POSTGRES + 显式 `tenant_id` → 解析并校验通过。
  2. POSTGRES + 省略 → `effective_tenant_id == "default"`。
  3. POSTGRES + 空串/非法 → 抛 `ValueError`。
  4. FILE/SQLITE/MEMORY + 显式 `tenant_id` → 抛 `ValueError`（若按建议报错）。
  5. 反向兼容：旧配置 JSON（无 `tenant_id` 字段）解析不报错。

---

## 2. Host 层：store 工厂透传 `tenant_id`

### 2.1 `src/universal_agent/host/runtime.py`
- `_postgres_store(store_config)`（约 L460）：构造改为
  ```python
  pg_store = PostgresRuntimeStore(url=url, tenant_id=store_config.effective_tenant_id)
  ```
  （保持 `os.environ` DSN 读取方式不变，secret 仍只从环境来。）
- `_build_stores(config)`（约 L474）：POSTGRES 分支透传 store_config（已有）；FILE/SQLITE 分支不传 tenant（单隐式租户语义，见 §0），无需改动但要补注释说明原因。
- **注意**：`_postgres_store` 只返回 `(pg_store, pg_store)`，未附加 user_id——Phase 0 的 `user_id` 由 store 构造默认 `"system"` 承载（见 §3），此处先不动 user 传参，避免把 per-request 概念提前塞进单次组装。

### 2.2 测试
- `tests/unit/test_postgres_store_wiring.py`（或 `host/runtime` 相关测试）：
  1. 构造 RuntimeConfig（POSTGRES store + tenant_id）→ 断言生成的 store `.tenant_id == 配置值`。
  2. 通过**假 env DSN**（`url_env` 指向 monkeypatch 的 env）走通 `_build_stores` → store 持有预期 tenant。
  3. FILE 后端 → store 不抛错、无 tenant（单隐式）。

---

## 3. Persistence 层：Postgres schema v2 + `user_id`

### 3.0 关键陷阱（先读再改）
现状 `apply_postgres_migrations`（postgres.py L537）：
```python
_METADATA.create_all(connection)              # 只建不存在的表，绝不 ALTER 已存在表
if POSTGRES_SCHEMA_VERSION in existing: return
connection.execute(INSERT ua_schema_migrations ...)
```
**因此给已存在的 `ua_sessions` 加列不能依赖 create_all**，必须新增"真实 ALTER 迁移分支"。

### 3.1 `src/universal_agent/persistence/postgres.py`
- 常量：`POSTGRES_SCHEMA_VERSION = 2`（L58）。保留 `POSTGRES_DEFAULT_TENANT_ID`。
- 表定义 `_SESSIONS`（L72）：新增列
  ```python
  Column("user_id", String, nullable=False, server_default="system"),
  ```
  `server_default` 保证存量行在 ALTER 后有效。
- **迁移机制改造**：把 `apply_postgres_migrations` 改为识别"已应用版本集合"，并按版本执行 DDL：
  ```python
  _METADATA.create_all(connection)                    # 仍先建表（幂等）
  existing = {...已应用版本...}
  if 2 not in existing:
      connection.execute(text(
          "ALTER TABLE ua_sessions "
          "ADD COLUMN IF NOT EXISTS user_id VARCHAR NOT NULL DEFAULT 'system'"
      ))
      connection.execute(INSERT ua_schema_migrations version=2 ...)
  if 1 not in existing:  # 老库可能只有 1？实际上 v1 建表本身由 create_all 兜底
      ...
  ```
  - 保留对 v1 行为兼容（老库 `ua_schema_migrations` 里只有 version=1 时，本迁移要能正确补到 2，且不重复建表）。
  - `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 使已 v2 的库幂等。
- `PostgresRuntimeStore.__init__`（L170）新增参数 `user_id: str = "system"`；`self._user_id = parse_non_empty_string(...)`，暴露只读属性。校验非空。
- `create_session` 的 `sql_insert(_SESSIONS).values(...)`（L180）：补 `user_id=self._user_id`。
- 其余 session 读写只按 `(tenant_id, session_id)` 操作，不因加列受影响；`_decode_session_row` 不需读 user_id（payload/version 足够）。

### 3.2 测试
- `tests/unit/test_postgres_persistence.py` 或新增 `test_postgres_schema_v2.py`（用可创建真实 PG 的 fixture；若 CI 无 PG 则用 sqlite 不可行——postgres 专用，按仓库现有 postgres 测试的假 engine/fixture 模式）：
  1. 全新库：`migrate()` 应用 v2 → `uasessions` 含 `user_id` 列。
  2. **模拟老库**：先以 v1 定义建表（或直接建无 user_id 列的 `ua_sessions` 并写入 `ua_schema_migrations` version=1）→ `migrate()` 到 v2 → 现有行 `user_id=='system'`、旧 session 可读。
  3. 重复 `migrate()` 幂等：二次调用返回 `(2, ())`，不再重复 ALTER。
  4. `create_session` 写入的 `user_id` 等于 store 构造的 `user_id`。
  5. `postgres_schema_table_names()` / `postgres_schema_ddl()` 含新列（若有断言）。
  6. `DEFAULT_TENANT_ID` 共享常量在 `core` 可见，`persistence` 与 `configuration` 引用同一字面量（可选断言验证一致）。
- **无 PG 环境降级**：若仓库 postgres 测试依赖 docker/engine，确认现有 `test_postgres_persistence` 的 fixture 方式并复用。

---

## 4. 新增 `security/principal.py`（最小类型层）

### 4.1 `src/universal_agent/security/principal.py`（新文件）
- 仅定义轻量、无 store 依赖的类型，**不接 IdP/RBAC**：
  - `class Tenant`：`tenant_id: str`、`name: str`、`status`（建议 str 或常量）。
  - `class UserPrincipal`：`user_id: str`、`display_name: str | None`、`status`。
- 纯数据（frozen dataclass 风格，与仓库一致），不引入 IO。
- 该模块成为 `security/` 的入口之一；`security/__init__.py` 是否导出按仓库惯例（参考 `security/secrets.py` 的导出方式）。

### 4.2 边界纪律
- 这是一个 **新 kernel 层模块**，会改变包边界拓扑。必须同步更新 `tests/unit/test_package_boundaries.py`（kernel layering ledger / sanctioned seam），把 `security` 归类到 kernel 服务层并登记 rationale（遵循 `AGENTS.md`：任何新边界异常都要进台账）。

### 4.3 测试
- 简单单测：构造 `Tenant`/`UserPrincipal`、校验非空 `tenant_id`/`user_id`、字段不可变。
- 若仓库对非法值有统一校验工具（`parse_non_empty_string`），沿用并测错误路径。

---

## 5. 跨租户访问矩阵测试（Phase 0 的验收核心）

本项目先加的测试要在功能之上证明**隔离成立**。建议新增 `tests/unit/test_tenant_isolation.py`：

- fixture：两个 `PostgresRuntimeStore`，`tenant_id="t1"` 与 `tenant_id="t2"`，同一 engine/库。
- 用例（访问矩阵）：
  1. `t1.create_session(s)` 后，`t2.load_session(s.session_id)` 抛 `StateNotFoundError`；`t1` 可读。
  2. `t1` 的 session/event 写入后，`t2.list_sessions()` / `t2.all()` 不含 t1 数据。
  3. `t1.save_session` 不影响 `t2` 的同名 session（互不可见）。
  4. events：`t1.append(event)` 后 `t2.events_for/ all/list_events` 读不到。
  5. outbox：`t1` 待发布事件不进入 `t2.pending_outbox_events()`。
- 目标：把设计文档 §3.3"store 层天然隔离"固化成回归。

---

## 6. 边界守卫测试更新

- `tests/unit/test_package_boundaries.py`：
  - 登记 `universal_agent.security.principal` 归属（kernel 服务/安全层）与 rationale。
  - 确认 `configuration`→`security` 或 `persistence`→`configuration` 的依赖方向是否符合既有台账；若 `configuration.py` 要 import `POSTGRES_DEFAULT_TENANT_ID`（§1），需确认这是**合法依赖方向**（configuration → persistence 是否允许？若不允许，将该默认常量的所有权放回 configuration 或独立常量模块，避免制造边界违规）。**这是改前必须先查的红线**。

---

## 7. 文档同步

- 更新 `docs/multitenancy-user-management-design.md`：Phase 0 里程碑标记为已实现（或保持设计不改，另行记录实现；二选一并说明）。
- 若改 `configuration.py` 对用户可见配置的影响，`docs/product.md` 的 store 配置段可加一句"POSTGRES store 支持可选 `tenant_id`"。
- 在 `security-production-decisions.md` 决策表中给 **Tenant model 行**加注"Phase 0 已打通 store 作用域，IdP/RBAC 仍 Deferred"。

---

## 8. 完成判定（Definition of Done，对齐 AGENTS.md §22）

- [ ] 实现：`StoreConfig.tenant_id`、`_postgres_store` 透传、`PostgresRuntimeStore.user_id`、schema v2 迁移含真实 ALTER、`security/principal.py`。
- [ ] 测试：§1.2/§2.2/§3.2/§4.3/§5 全部新增并通过；全套单元测试回归通过。
- [ ] 边界守卫：`test_package_boundaries.py` 已登记新模块/确认依赖方向合法（尤其是 configuration→persistence 常数依赖）。
- [ ] 错误处理：非法 `tenant_id`/`user_id` 抛清晰 `ValueError`；迁移幂等。
- [ ] 可观测/审计：无 secret 进入日志（沿用现状断言）；本次改动不引入新日志面。
- [ ] 类型：`pyright` 通过（仓库启用 strict，`new function return type` 类注释注意）。
- [ ] 兼容：老 `default` 库迁移后旧 session 可读（§3.2 用例 2 覆盖）。

---

## 9. 建议提交切分（便于评审/回滚）

1. `commit A`：`StoreConfig.tenant_id` + 校验 + 测试（configuration 层）。
2. `commit B`：`security/principal.py` + 边界守卫登记 + 测试。
3. `commit C`：postgres schema v2（`user_id` 列 + 迁移 ALTER）+ 测试。
4. `commit D`：`PostgresRuntimeStore(user_id=)` + `_postgres_store` 透传 tenant_id + 跨租户矩阵测试。
5. `commit E`：文档同步（product/multitenancy/security 决策表）。
每个 commit 独立可回滚、单测绿。

---

## 10. 开放/风险点（实现前确认）

1. **configuration → persistence 常依赖方向**（§6）——会影响默认租户常量放哪里。**先查后改**。
2. **假 engine 测试方案**：确认仓库 `test_postgres_persistence` 是否真的连 PG（docker）还是用 SQL 捕获；决定 `test_tenant_isolation` 复用哪种。
3. **`ua_sessions.user_id` 用 `server_default='system'` 而非 `nullable`**：为存量行补默认，且符合"未来归属不可空"的设计（§4.3 设计文档）。确认 postgres 方言支持 `VARCHAR DEFAULT`（支持）。
4. FILE/SQLITE 单隐式租户：是否需要在校验里显式拒绝 `tenant_id`？——推荐报错，但若破坏现有文档/配置样例则退回"忽略+警告"。实现前按 §1.1 定。