# 平台化设计：Principal 模型（用户 + 租户 + 角色）

> 状态：**设计（Design）** —— 待评审后按分阶段计划落地。
> 关联：`docs/security-production-decisions.md`（本文将其中的 Identity provider / Tenant model / Authorization model 从 `Deferred` 移出为可实施计划）、`docs/RUNTIME_CONTRACT.md`（所有权契约，本文不得破坏）。

---

## 0. 摘要

本项目当前的 `agentd` 是 **单租户、共享 bearer token、无逻辑用户概念** 的本地 runtime server。要成为平台级多用户 / 多租户产品，必须先建立 **Principal（主体）模型**：

```text
IdP/OIDC  →  User (principal)   ← 你是谁
                 │ belongs to
                 ▼
             Tenant (组织/数据边界)  ← 你属于哪家
                 │ assigned
                 ▼
             Role / Scope         ← 你能做什么
                 │  gates
                 ▼
     agentd 请求 → 解析 user → 解析 tenant → RBAC 校验
                 └──────────┬───────────┘
                    跨租户拒绝（store + agentd 双层强制）
```

本文给出的是一份 **可落地的设计**：覆盖 User / Tenant / Role 模型、Postgres schema 迁移、`tenant_id` 从配置到 store 的打通、agentd 认证升级（共享令牌 → 解析身份）、Profile/Session 的归属建模、RBAC 与 Runtime Policy 的分层，以及分阶段实施计划与验收标准。

---

## 1. 现状（代码事实，作为设计基线）

以下是设计必须兼容或绕开的现状，全部可在仓库验证：

### 1.1 持久化层已有租户缝，但无人填充
- `persistence/postgres.py`：`POSTGRES_SCHEMA_VERSION = 1`，`POSTGRES_DEFAULT_TENANT_ID = "default"`。
- 表 `ua_sessions` / `ua_runtime_events` / `ua_runtime_event_outbox` **都已带 `tenant_id` 列**，且 `sessions` 主键、events/outbox 唯一约束都含 `tenant_id`。
- `PostgresRuntimeStore.__init__` 已接受 `tenant_id: str = POSTGRES_DEFAULT_TENANT_ID` 参数。
- **但**：全仓 `grep tenant` 只有 `postgres.py` 一处命中 —— 没有任何上层（config / session 创建 / agentd）填充它。现网构造点在 `host/runtime.py:468` `pg_store = PostgresRuntimeStore(url=url)`，不传 `tenant_id`，一律落到 `default`。
- `ua_sessions` 没有 `user_id` / `owner_id` / `created_by` 列。

### 1.2 agentd 是共享凭据、非 per-user
- `agentd/http.py`：`AgentdAuthPolicy` 只维护 `bearer_token` 与 `read_only_bearer_token` 两个共享令牌；`public_paths` 默认 `("/health", "/ready")`。
- 认证只做两层：token 匹配 → read-only/read-write 作用域，**不解析"是哪个用户"**。
- 凭据在配置中以环境变量**名**引用（`auth_token_env`），secret 值从不落盘 —— 这条安全习惯必须保留。

### 1.3 Profile 是文件归属，不是逻辑所有权
- `profile/config.py`：配置发现顺序是 `$AGENT_CONFIG_DIR/profile.json` → `./universal-agent/profile.json`（项目）→ `~/.universal-agent/profile.json`（**OS home**）。
- Profile 无 `owner_id` / `tenant_id` 字段；serverside 热切换靠 `ProfileStore` + `X-Profile` 头（`agentd/app.py` 的 `_bundle_for`）。
- 服务端 profile 列表已存在（`profile list` 端点），但没有任何所有权边界。

### 1.4 授权模型缺位
- 现状明确区分了"bearer token 只挡 HTTP"，Domain capability Policy 不是用户 RBAC。
- 全仓无 `role` / `rbac` / `account` / `oidc` 概念。

### 1.5 文档契约
- `docs/security-production-decisions.md` 把 Identity / Tenant / Authorization / Secret / Audit / Package trust 全部标为 `Deferred`，并明确 guardrail：**在这些决策移出 Deferred 前，不得实现生产级 AuthN/AuthZ/tenancy 后端**。
- `docs/RUNTIME_CONTRACT.md`：Session 是持久化执行上下文；所有权必须与 Goal/Task/Decision/Action/Observation/Evidence/Policy/Evaluation/Domain/Profile 对齐。

---

## 2. 目标模型（要新增的三个一等概念）

三者在代码里是**不同的概念**，不可混为一个字段。

| 概念 | 代码对象 | 语义 | 是否已存在 |
|---|---|---|---|
| **User** | `UserPrincipal` | 逻辑主体 / 身份 | ❌ |
| **Tenant** | `Tenant` | 组织 / 数据隔离边界 | 半（只到 store 列，无实体） |
| **Role** | `RoleBinding` | 用户在一个 tenant 内的权限 | ❌ |

### 2.1 关键约束（架构红线）

1. **tenant 边界 ≠ session 边界**。禁止靠"给 session 标 tenant"偷工；租户必须在 **身份解析 + store 分区 + agentd 授权** 三层同时落地。
2. **用户 RBAC 与 Runtime Policy 是两层**：
   - `Policy` 决定"这个动作在上下文中**安不安全**"（现有，保留）。
   - `RBAC` 决定"这个用户**有没有权力请求**这个 capability"（新增）。
   - 二者是 AND 关系：`允许执行 = RBAC.allowed(user, capability) and Policy.decides(action) ∈ ALLOW/CONFIRM`。
3. **所有权贯通**：Goal / Task / Decision / Action / Observation / Evidence / Evaluation / Session / Domain / Profile 的 `owner` 与 `tenant` 归属必须全链路一致，不得"一半带用户、一半裸奔"。

---

## 3. 身份解析与请求流

设计一个统一的请求生命周期，**每个 agentd 请求都必须解析出一个 Principal**：

```text
HTTP 请求
   │  携带凭证：Bearer <JWT>（OIDC） 或  X-API-Key（服务账户）
   ▼
[1] AuthN 凭证校验       → 无效 → 401
   ▼
[2] 建立 Principal       → 未绑定 tenant/role → 403（未授权）
   │   UserPrincipal { user_id, account_status }
   ▼
[3] 解析 Tenant          → 请求携带显式 tenant 或在身份上下文中解析；
   │                     必须与用户有权访问的 tenant 集合校验（防止跨租户越权）
   ▼
[4] RBAC 校验 (role)     → 不允许的 capability → 403（含可审计的拒绝原因）
   ▼
[5] 构造 Store 作用域    → store 绑定 <tenant_id, user_id>
   ▼
[6] Runtime 执行        → 动作仍接受 Runtime Policy 检查（第 2.1 条）
```

### 3.1 Principal 从何而来（过渡平滑）
- **阶段 A（无 IdP，单机/自托管）**：`AgentdAuthPolicy` 扩展为"令牌 → 主体"映射（静态服务账户或本地 user store），保持现有 `auth_token_env` 风格。
- **阶段 B（企业）**：接入 OIDC，`Authorization: Bearer <JWT>`，从 IdP 校验签名抽取 `sub` / `email` / `groups`，映射到本地 `User` 与 `TenantMembership`。

---

## 4. 数据模型 / Schema 变更

### 4.1 迁移策略
- 沿用现有 `ua_schema_migrations` + `POSTGRES_SCHEMA_VERSION`。本设计引入 **schema v2**：
  - `POSTGRES_SCHEMA_VERSION = 2`；
  - 迁移脚本**为旧 `default` 租户创建默认 tenant 行**，保证兼容。
- 新表与现有 `tenant_id` 结构一致：首列 `tenant_id String NOT NULL`，唯一约束含 `tenant_id`，便于按租户分区。

### 4.2 新增表（示意 DDL）

```sql
-- 租户
CREATE TABLE ua_tenants (
    tenant_id   TEXT PRIMARY KEY,        -- 例如 "acme"
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,           -- active | disabled
    created_at  TIMESTAMPTZ NOT NULL
);

-- 用户（跨租户的全局主体，身份唯一）
CREATE TABLE ua_users (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,    -- 或 external subject
    display_name TEXT,
    status      TEXT NOT NULL,           -- active | disabled
    created_at  TIMESTAMPTZ NOT NULL
);

-- 用户↔租户↔角色（membership + RBAC binding）
CREATE TABLE ua_tenant_memberships (
    tenant_id   TEXT NOT NULL REFERENCES ua_tenants(tenant_id),
    user_id     TEXT NOT NULL REFERENCES ua_users(user_id),
    role        TEXT NOT NULL,           -- admin | operator | read_only
    created_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, user_id)
);

-- Scoped token / 服务账户（阶段 A 用，替代共享 bearer）
CREATE TABLE ua_credentials (
    credential_id    TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    token_hash       TEXT NOT NULL,      -- 只存哈希，绝不存明文
    scope            TEXT NOT NULL,      -- read_write | read_only
    created_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ
);
```

### 4.3 现有表变更

```sql
ALTER TABLE ua_sessions
    ADD COLUMN user_id TEXT NOT NULL DEFAULT 'system';  -- 归属主体
-- tenant_id 已存在；保留默认 'default' 兼容
```

> 说明：`RUNTIME_CONTRACT` 的 Session 所有权即由 `(tenant_id, user_id)` 表达。`ua_runtime_events` / outbox 保持按 `tenant_id` 分区，不再额外加 `user_id`（事件归属可由 session 关联，降低膨胀）。

---

## 5. `tenant_id` 与 `user_id` 的打通（核心实现项）

当前 `tenant_id` 只在 `PostgresRuntimeStore` 有参数，却无人传。要将它变成真正的边界：

### 5.1 Configuration 层
- `StoreConfig`（`configuration.py`）新增可选 `tenant_id`（默认 `"default"`）。
- agentd 启动时可显式声明 `server.tenant_id` + 期望的 store `url_env`。

### 5.2 构造 / 工厂层
- 所有 `PostgresRuntimeStore(...)` 构造点（含 `host/runtime.py:468`）改为从 `RuntimeConfig.StoreConfig.tenant_id` 取值。
- 引入统一的 **store 工厂**：`tenant_id` 与 `user_id` 在 RuntimeService 组装时绑定，作为不可变作用域注入，避免散落。

### 5.3 跨租户拒绝（双层强制）
- **store 层**：所有查询/SQL `where tenant_id == self._tenant_id`（已实现，保持）—— 即使上层漏传，存储层也天然隔离。
- **agentd 层**：请求解析出的 `user → tenant` 若与 store 绑定不一致，直接拒绝；禁止 `X-Profile` / `X-Tenant` 头被用作越权跳板。

---

## 6. agentd 认证升级

### 6.1 过渡令牌模型（阶段 A）
将 `AgentdAuthPolicy` 从"两个共享令牌"升级为"按凭据解析主体"：

```text
Bearer <credential>  →  查 ua_credentials.token_hash（哈希比对）
                     →  解析出 { user_id, tenant_id, scope }
                     →  若 revoked / tenant disabled → 401 / 403
```

- 保留 `/health` `/ready` 为 public path。
- `read_only` / `read_write` 语义映射到 scope，并向 RBAC 下游传递。

### 6.2 OIDC（阶段 B）
- 新增 `agentd` 的 OIDC 依赖注入点：校验 `id_token` / `access_token`，`sub` → `ua_users.user_id`，`userinfo` 的 tenant/group claim → membership 校验。
- 不引入对具体 IdP 的耦合；通过抽象 `TokenValidator` 协议接入 Keycloak / Auth0 / Okta / 自建。

### 6.3 安全约束（延续现状）
- 命令行传 token 仍只允许**环境变量名**参考；secret 值永不落盘、不进日志/事件/doctor 输出。
- 新增 `ua_credentials.token_hash` 只存哈希；轮换通过 `revoked_at` + 新凭证实现。

---

## 7. RBAC：作用域模型

### 7.1 角色（初始三档，可扩展）
| 角色 | 覆盖 | 典型能力 |
|---|---|---|
| `admin` | 租户全量 | 用户管理、profile 编辑、读写、删除 |
| `operator` | 租户读写 | 运行 goal、读写 session、开关 profile 能力 |
| `read_only` | 租户只读 | 查看 session / event / evidence / world，不可执行 |

### 7.2 与 Runtime Policy 的交互（关键实现约定）
- 新增一个 `AuthorizationEvaluator`（Kernel 服务层，见 §8 模块落点），只做"用户是否有权请求 capability"，**不做**动作安全性判定。
- 决策管线的串行检查顺序：

```text
Decision(想执行 capability C)
   → [RBAC] 用户/租户是否允许请求 C ?    否 → deny(403, reason)
   → [Policy] C 的动作在上下文是否安全 ?  deny/confirm → 交给 Runtime 处理
   → 执行
```

- 审计里要能区分 `deny reason = rbac` 与 `deny reason = policy`，二者不可混淆。

---

## 8. 模块落点（新增/变更清单）

按 `AGENTS.md` 的分层纪律，不破坏 kernel/domain 边界：

| 关注点 | 落点（新增或变更） | 归属层 |
|---|---|---|
| 身份/租户/角色类型 | `universal_agent/security/principal.py`（新增：`UserPrincipal`/`Tenant`/`RoleBinding`/membership） | Kernel 安全 |
| RBAC 判定 | `universal_agent/security/authorization.py`（新增 `AuthorizationEvaluator`） | Kernel 服务 |
| token → principal 解析 | `agentd/http.py` 扩展 + `security/credentials.py` | agentd/security |
| OIDC 抽象 | `security/oidc.py`（新增 `TokenValidator` Protocol） | Kernel 安全 |
| Postgres schema v2 + 迁移 | `persistence/postgres.py`（`POSTGRES_SCHEMA_VERSION=2`、新表、session `user_id`） | Persistence |
| `tenant_id`/`user_id` 注入 | `configuration.py`（`StoreConfig.tenant_id`）、`host/runtime.py` store 工厂、RuntimeService 组装 | Configuration/Host |
| Profile 归属 | `profile/config.py`（`owner_id`/`tenant_id`）、`profile/store.py`（按租户/用户隔离 profile 列表） | Profile |
| agentd 授权中间件 | `agentd/app.py`（请求 → Principal → 作用域 bundle 绑定；`X-Profile` 热切换须 tenant-scoped） | agentd |
| 用户管理 API | `agentd` 新增 `ua_users`/`ua_tenant_memberships` 路由 + CLI admin 命令 | Host |
| 跨租户拒绝守卫 | 沿用 store `tenant_id` where + agentd principal 校验 | 双层 |

> 边界纪律：security 是 Kernel 服务层；`agentd` 是 host。Kernel 不得 import 具体 IdP；agentd 不得把用户逻辑塞进 kernel。`tests/unit/test_package_boundaries.py` 需随新增模块更新。

---

## 9. 用户管理产品面（Host 层）

### 9.1 管理操作
- 用户：创建 / 禁用 / 启用 / 查询（email、status）。
- 成员关系：把用户加入租户并赋角色；移出租户；改角色。
- 租户：创建（`tenant_id` 唯一）、禁用、查询。
- 凭证：为服务账户签发 / 吊销 credential（只存哈希）。

### 9.2 CLI 入口（advanced 分组）
- `agent admin user create|disable|enable|list`
- `agent admin role set|remove <email> --tenant <id> --role <admin|operator|read_only>`
- `agent admin tenant create|disable|list`
- `agent admin credential create|revoke`

### 9.3 Web（`web/` tier）
- 依赖 agentd HTTP API；新增用户/租户/角色管理视图，须严格走同一套 Principal → RBAC 校验，**不得**为 UI 开旁路。

---

## 10. 审计

- 现有本地 hash-chain 投影保留；新增 `ua_audit` 事件类型：`user_created` / `membership_changed` / `credential_issued` / `credential_revoked` / `denied(rbac)` / `denied(cross_tenant)`。
- 每次 `deny` 必须带可读且可审计的 reason（见 §7.2）。
- 审计存储的持久化/防篡改后端属于**进一步推迟**（见 §12），本设计只保证事件源覆盖。

---

## 11. 兼容性与迁移

- 旧部署（无任何租户配置）跑在 `tenant_id="default"` 上，行为不变：迁移 v2 时会自动插入 `default` tenant 行、背填 `ua_sessions.user_id='system'`。
- `AgentdAuthPolicy` 无 token 时（loopback/本地）仍可用，处于"单用户回退"模式 —— 该模式仅在未配置 identity 时允许，文档需明确此非多人安全形态。
- `RUNTIME_CONTRACT.md` 的 ownership 语义升级为 `(tenant_id, user_id)`，不改动其"Session 由 store 重建、resume 重查 Policy"等既有承诺。

---

## 12. 非目标 / 进一步推迟

本设计**不**覆盖（与 `security-production-decisions.md` 对齐，保持 Deferred）：
- **Secret provider**：KMS/Vault 后端、轮换、访问审计（`UA-PROD-004`）。
- **Audit storage**：防篡改/可导出/保留策略（`UA-PROD-005`）。
- **Package trust**：签名获取、信任根、沙箱（`UA-PROD-007`）。
- **分布式 runtime 高可用**、多机房一致性。
- 多企业 SSO 联邦（可扩展但非本阶段）。

这些在各自决策移出 Deferred 前不实现。

---

## 13. 分阶段实施计划（含验收标准）

> 纪律：每阶段独立可回滚、可验收；不一次性大改。`AGENTS.md` 要求"改动前加测试、保接口稳定、边界守卫测试随层变更更新"。

### Phase 0 —— 打通 tenant/user 作用域（最小可用边界）
> **✅ 已实现（2026-09-20）**，落到 `docs/phase0-principal-implementation.md`。（正文改动/验收不变，仅状态更新）
- 目标：让 `tenant_id` 与 `user_id` 真正贯通 config → store → service，跨租户拒绝生效。
- 改动：`configuration.StoreConfig.tenant_id`；`host/runtime.py` store 工厂传 `tenant_id`；`ua_sessions` 加 `user_id`；`persistence/postgres.py` schema v2 + `default` 兼容迁移。
- 落点：`security/principal.py`（最小 `Tenant`/`UserPrincipal` 类型）。
- 验收：
  - 两个不同 `tenant_id` 的 store 对同一 session/event 互不可见（单元 + 集成的访问矩阵）。
  - 旧 `default` 部署迁移后原 session 可正常读取。
  - `tests/unit/test_package_boundaries.py` 更新且通过。
- **出口条件**：不做 IdP/RBAC，仅凭 tenant 作用域已能隔离多套部署。

### Phase 1 —— agentd 认证升级 + 基础 RBAC
> **✅ 已实现（2026-09-20）**：RBAC 核心（`Role`/`Scope`/`RequestPrincipal`/`RoleBinding` + `security/authorization.AuthorizationEvaluator`）、凭据模块（`security/credentials.py`：SHA-256 哈希 + `CredentialStore` Protocol + `InMemoryCredentialStore`）、schema v3（`ua_users`/`ua_tenants`/`ua_tenant_memberships`/`ua_credentials`）、agentd 认证升级（`AgentdAuthPolicy(credential_store=, tenant_id=)`；令牌→主体 + 跨租户拒绝 + read_only 写操作 403 reason=rbac，向后兼容共享令牌）。尚未做：CLI 用户管理面（Phase 2）、deny 事件写入事件流（当前为 HTTP 403 拒绝；事件化审计在 Phase 2/3）。
- 目标：从共享 token 变为"令牌 → 主体"，引入 `admin/operator/read_only`。
- 改动：`security/credentials.py`（哈希）、`agentd/http.py`（主体解析）、`security/authorization.py`（`AuthorizationEvaluator`）、`ua_credentials`/`ua_tenant_memberships` 迁移。
- 落点：agentd 中间件把解析出的 principal 绑定到 service bundle。
- 验收：
  - read_only 角色对写操作返回 403 且 reason=`rbac`。
  - `operator` 越过 read_only 权限或 `admin` 越权跨租户均被拒。
  - `deny` 事件进入事件流且区分 rbac/policy。

### Phase 2 —— 用户管理面 + OIDC
> **✅ 已实现（2026-09-20，HTTP 面 + CLI）**：`persistence/credentials.py`（`PostgresCredentialStore`：解析 + create_tenant/create_user/set_role/issue_credential/revoke_credential/list_members，角色权威在 membership）、`security/credentials.py` 扩展（`CredentialAdminStore` Protocol + InMemory 管理面 + `PrincipalAlreadyExistsError`/`PrincipalNotFoundError`）、`security/oidc.py`（`TokenValidator` Protocol + `StaticClaimsTokenValidator` mock IdP + `OidcClaimsPrincipalMapper`）、`agentd/admin_routes.py`（`/v1/admin/*` 六条路由，admin 门禁 + legacy 共享令牌 bootstrap，凭证 token 仅回显一次）、`agentd/__main__.py`（`--admin-store memory|postgres` + `--admin-store-url-env` + `--tenant-id`）、CLI `agent admin` 命令组（tenant/user/role/member/credential，走 remote thin-client，`AgentdClient` 新增 put/delete）——含端到端集成测试（provision→resolve→revoke 闭环、operator 403 rbac、跨租户 403）。
> **待完成**：Web 用户管理视图；embedded 子进程的 durable admin store 需 `--admin-store postgres`（内存店不跨进程存活，仅 dev/test）。
- 目标：Admin 管理 API/CLI 落地；可插入 OIDC。
- 改动：`agentd` 用户/租户/角色路由；CLI admin 命令；`security/oidc.py`（`TokenValidator` Protocol）。
- 验收：
  - `agent admin user create` → 登录/调用闭环可用。
  - OIDC mock 通过 Keycloak/Auth0 风格 token 解析校验（用协议抽象，测试注入假 IdP）。
  - Web UI 的用户管理视图走同一套 RBAC，无旁路。

### Phase 3.5 —— 管理面基础功能补全（2026-09-20）
> **✅ 已实现**：生命周期管理落地——用户/租户禁用与启用（`set_user_status`/`set_tenant_status`，禁用后其凭据立即无法解析）、列表查询（`list_users` 含 email、`list_tenants`、`list_credentials` 按 user/tenant 过滤且永不回吐哈希）、成员移出（`remove_member`，membership 删除后凭据即失效）。新路由：`GET /v1/admin/users|tenants|credentials`、`PUT /v1/admin/users/{u}/status|tenants/{t}/status`、`DELETE /v1/admin/tenants/{t}/members/{u}`；审计事件 `user_status_changed`/`tenant_status_changed`/`member_removed`；CLI 对应 `agent admin user list|disable|enable`、`tenant list|disable|enable`、`credential list`、`member remove`。**引导语义确立**：共享令牌的 bootstrap 窗口以"尚无 admin 成员"为界（`has_any_admin`），首个 admin 建成后窗口关闭。

### Phase 3 —— 归属贯通 + 审计加固（可选）
> **✅ 已实现（2026-09-20）**：审计事件化（`security/audit.py`：`AuditEvent` + `AuditRecorder` Protocol + `InMemoryAuditRecorder`/`FileAuditRecorder` JSONL sink；agentd 记录 `denied(rbac|cross_tenant|unauthorized)` 与全部 admin 变更 `tenant_created`/`user_created`/`membership_changed`/`credential_issued`/`credential_revoked`，暴露 `GET /v1/admin/audit`；`agentd serve --audit-log <path>`）；Profile 归属（`ProfileConfig`/`AgentProfile` 可选 `tenant_id`/`owner_id`，`ProfileStore.names(tenant_id=)` 按租户过滤，无归属元数据的 profile 归隐式默认租户，单租户部署不受影响）。防篡改/可导出的审计存储后端仍 Deferred。
- 目标：Profile 归属 `owner_id`/`tenant_id`、每个 tenant 独立 profile 列表；审计事件完整。
- 改动：`profile/config.py`、`profile/store.py`。
- 验收：每个用户在租户内只见自己的 profile；`ua_audit` 覆盖 §10 全事件。

---

## 14. 开放问题（需评审拍板）

1. **User 与 Tenant 的粒度的产品定义**：User 是否必须全局唯一邮箱？Tenant 是否支持层级（父/子租户）？—— 建议首版扁平，层级留接口。
2. **Role 集合是否为硬编码三档**：还是由 profile 声明？—— 建议首版 config 可配，默认三档。
3. **`operator` 是否允许确认危险动作**：CONFIRM 类 policy 是否需要更高级角色？—— 建议 role 只控制"请求权"，CONFIRM 动作仍经 Runtime/人工确认，不因角色放宽。
4. **单机无 IdP 的"默认 admin"**：首装如何引导出第一个 admin？—— 建议 bootstrap 凭证/种子用户流程。
5. **token 是否要支持长期数据面令牌（API 集成）** 而非仅用户会话。

---

## 15. 验收总纲（Defined of Done 对齐 `AGENTS.md`）

- [ ] 实现 + 单元测试（principal / authorization / credentials / migration）。
- [ ] 跨租户访问矩阵测试（同租户可见、跨租户拒绝、无租户拒绝）。
- [ ] 授权拒绝理由可审计、区分 rbac/policy。
- [ ] 边界守卫测试更新（`test_package_boundaries.py`）。
- [ ] 文档同步（`product.md` 概念表加 User/Tenant/Role；`security-production-decisions.md` 三段移出 Deferred）。
- [ ] secret 值不进日志/事件/doctor 输出（延续现状断言）。
- [ ] 旧 `default` 部署迁移兼容回归通过。