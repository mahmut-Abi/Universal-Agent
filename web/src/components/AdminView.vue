<script setup>
// 用户与租户管理视图（Phase 2/3 admin plane）。
// 只消费 agentd 的 /v1/admin/* HTTP API —— 与 CLI / Web 共用同一套
// RBAC 门禁，无旁路。admin plane 未启用时给出可操作的提示。
import { onMounted, ref } from 'vue'
import { apiGet, apiPost, apiPut, apiDelete } from '../api.js'
import { toast } from '../store.js'

const ROLES = ['admin', 'operator', 'read_only']

const auditEvents = ref([])
const auditLoaded = ref(false)
const planeError = ref('')

// tenant form
const tenantId = ref('')
const tenantName = ref('')
// user form
const userId = ref('')
const userEmail = ref('')
const userDisplay = ref('')
// role form
const roleTenant = ref('')
const roleUser = ref('')
const roleValue = ref('operator')
// credential form
const credUser = ref('')
const credTenant = ref('')
const credRole = ref('operator')
const issuedToken = ref('')
const issuedId = ref('')
// members
const members = ref([])
const membersTenant = ref('')

function recordError(e) {
  planeError.value = e.message || String(e)
  toast(planeError.value)
}

async function loadAudit() {
  try {
    const d = await apiGet('/v1/admin/audit')
    auditEvents.value = Array.isArray(d.events) ? d.events : []
    auditLoaded.value = true
    planeError.value = ''
  } catch (e) {
    recordError(e)
  }
}

async function createTenant() {
  try {
    await apiPost('/v1/admin/tenants', { tenant_id: tenantId.value, name: tenantName.value })
    toast(`租户 ${tenantId.value} 已创建`)
    tenantId.value = ''
    tenantName.value = ''
    loadAudit()
  } catch (e) {
    recordError(e)
  }
}

async function createUser() {
  try {
    const body = { user_id: userId.value, email: userEmail.value }
    if (userDisplay.value) body.display_name = userDisplay.value
    await apiPost('/v1/admin/users', body)
    toast(`用户 ${userId.value} 已创建`)
    userId.value = ''
    userEmail.value = ''
    userDisplay.value = ''
    loadAudit()
  } catch (e) {
    recordError(e)
  }
}

async function setRole() {
  try {
    const path = `/v1/admin/tenants/${encodeURIComponent(roleTenant.value)}/members/${encodeURIComponent(roleUser.value)}`
    await apiPut(path, { role: roleValue.value })
    toast(`${roleUser.value} 在 ${roleTenant.value} 的角色已设为 ${roleValue.value}`)
    loadAudit()
    if (membersTenant.value === roleTenant.value) await listMembers()
  } catch (e) {
    recordError(e)
  }
}

async function listMembers() {
  try {
    const d = await apiGet(`/v1/admin/tenants/${encodeURIComponent(membersTenant.value)}/members`)
    members.value = Array.isArray(d.members) ? d.members : []
  } catch (e) {
    members.value = []
    recordError(e)
  }
}

async function issueCredential() {
  issuedToken.value = ''
  issuedId.value = ''
  try {
    const d = await apiPost('/v1/admin/credentials', {
      user_id: credUser.value,
      tenant_id: credTenant.value,
      role: credRole.value,
    })
    issuedToken.value = d.token || ''
    issuedId.value = d.credential_id || ''
    toast('凭证已签发：token 仅显示一次，请立即保存')
    loadAudit()
  } catch (e) {
    recordError(e)
  }
}

async function revokeCredential(id) {
  try {
    await apiDelete(`/v1/admin/credentials/${encodeURIComponent(id)}`)
    toast(`凭证 ${id} 已吊销`)
    loadAudit()
  } catch (e) {
    recordError(e)
  }
}

onMounted(loadAudit)
</script>

<template>
  <section v-show="view === 'admin'" class="view" id="view-admin" role="tabpanel">
    <div v-if="planeError" class="card" style="margin-bottom: 12px">
      <div class="mt" style="color: var(--danger, #e5484d)">
        管理面请求失败：{{ planeError }}
        <div class="mm">
          请确认 agentd 以 --admin-store 启动，且当前凭据具备 admin 角色
          （403 rbac = 权限不足，401 = 凭证无效，404 = 管理面未启用）。
        </div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px">
      <!-- 租户创建 -->
      <div class="card">
        <div class="card-head"><span class="mt">创建租户</span></div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <input v-model="tenantId" placeholder="tenant_id，例如 acme" aria-label="租户 ID"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <input v-model="tenantName" placeholder="租户名称" aria-label="租户名称"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <button class="btn btn-primary btn-sm" @click="createTenant">＋ 创建租户</button>
        </div>
      </div>

      <!-- 用户创建 -->
      <div class="card">
        <div class="card-head"><span class="mt">创建用户</span></div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <input v-model="userId" placeholder="user_id" aria-label="用户 ID"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <input v-model="userEmail" placeholder="email" aria-label="邮箱"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <input v-model="userDisplay" placeholder="显示名（可选）" aria-label="显示名"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <button class="btn btn-primary btn-sm" @click="createUser">＋ 创建用户</button>
        </div>
      </div>

      <!-- 角色授权 -->
      <div class="card">
        <div class="card-head"><span class="mt">成员授权</span></div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <input v-model="roleTenant" placeholder="租户 ID" aria-label="授权租户"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <input v-model="roleUser" placeholder="用户 ID" aria-label="授权用户"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <select v-model="roleValue" aria-label="角色"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
            <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
          </select>
          <button class="btn btn-primary btn-sm" @click="setRole">设置角色</button>
        </div>
      </div>

      <!-- 凭证签发 -->
      <div class="card">
        <div class="card-head"><span class="mt">签发凭证</span></div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <input v-model="credUser" placeholder="用户 ID" aria-label="凭证用户"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <input v-model="credTenant" placeholder="租户 ID" aria-label="凭证租户"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <select v-model="credRole" aria-label="凭证角色"
            style="padding: 8px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
            <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
          </select>
          <button class="btn btn-primary btn-sm" @click="issueCredential">签发凭证</button>
          <div v-if="issuedToken" class="mt" style="word-break: break-all">
            <div class="mm">credential_id：{{ issuedId }}</div>
            <code style="display: block; padding: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); font-size: 12px">{{ issuedToken }}</code>
            <div class="mm" style="color: var(--danger, #e5484d)">仅显示一次，关闭后无法再查看；服务端只保留哈希。</div>
            <button class="btn btn-sm" @click="issuedToken = ''">我已保存，隐藏</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 成员列表 -->
    <div class="card" style="margin-top: 12px">
      <div class="card-head">
        <span class="mt">租户成员</span>
        <div style="display: flex; gap: 8px">
          <input v-model="membersTenant" placeholder="租户 ID" aria-label="成员租户"
            style="padding: 6px 10px; font: inherit; font-size: 13px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius)">
          <button class="btn btn-sm" @click="listMembers">查询</button>
        </div>
      </div>
      <div v-if="!members.length" class="empty"><div class="empty-title">暂无成员</div>输入租户 ID 后查询</div>
      <div v-for="mem in members" :key="mem.user_id" class="mem-row">
        <div class="mt">{{ mem.user_id }}
          <div class="mm"><span>{{ mem.role }}</span></div>
        </div>
      </div>
    </div>

    <!-- 审计事件 -->
    <div class="card" style="margin-top: 12px">
      <div class="card-head">
        <span class="mt">安全审计</span>
        <button class="btn btn-sm" @click="loadAudit">刷新</button>
      </div>
      <div v-if="auditLoaded && !auditEvents.length" class="empty">
        <div class="empty-title">暂无审计事件</div>管理面变更与认证拒绝会记录在这里
      </div>
      <div v-for="(e, i) in auditEvents" :key="i" class="mem-row">
        <div class="mt">
          <span style="font-weight: 600">{{ e.event }}</span>
          <span v-if="e.reason" style="color: var(--danger, #e5484d)">（{{ e.reason }}）</span>
          <div class="mm">
            <span>{{ e.actor }}</span>
            <span v-if="e.tenant_id">{{ e.tenant_id }}</span>
            <span v-if="e.resource">{{ e.resource }}</span>
            <span>{{ e.occurred_at }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
