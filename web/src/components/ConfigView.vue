<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, selectedProfile, switchProfile, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'ConfigView' })
</script>
<template>
<!-- 视图三：配置与运行时 -->
      <section v-show="view === 'config'" class="view" :class="{ active: view === 'config' }" id="view-config" role="tabpanel">
        <h2 class="viewtitle">配置与运行时</h2>
        <div class="grid g-config">
          <div class="card" data-od-id="model-card">
            <div class="card-head"><h3>模型配置 · 运行时</h3><span class="tag">GET /v1/config → model</span></div>
            <template v-if="m.runtimeConfig.available && m.runtimeConfig.model">
              <div class="switch-row"><div class="s-label">Provider</div><span class="num">{{ m.runtimeConfig.model.provider }}</span></div>
              <div class="switch-row"><div class="s-label">模型名称</div><span class="num">{{ m.runtimeConfig.model.name }}</span></div>
              <div v-if="m.runtimeConfig.model.endpoint" class="switch-row"><div class="s-label">Endpoint</div><span class="num" style="max-width:70%;word-break:break-all">{{ m.runtimeConfig.model.endpoint }}</span></div>
              <div v-if="m.runtimeConfig.model.api_key_secret" class="switch-row"><div class="s-label">API Key Secret</div><span class="num">{{ m.runtimeConfig.model.api_key_secret }}</span></div>
              <div class="switch-row"><div class="s-label">超时</div><span class="num">{{ m.runtimeConfig.model.timeout_seconds }}s</span></div>
            </template>
            <div v-else class="empty">
              <div class="empty-title">运行时配置不可读</div>
              GET /v1/config 返回 503（部署未启用 deployment config store）
            </div>
            <div style="font-size:12.5px;color:var(--muted);margin-top:12px">
              修改途径（需重启 agentd 生效）：<code class="num">agent init --model-provider openai_chat_completions --model-name gpt-4o-mini --model-api-key-env OPENAI_API_KEY</code>
              或在 profile-config 的 <code class="num">model</code> 段配置；API 不提供 model 写入。
            </div>
          </div>
          <div class="card" data-od-id="model-observed-card">
            <div class="card-head"><h3>实际生效的模型调用</h3>
              <div class="row-actions"><span class="tag">GET /v1/sessions/{'{id}'}/llm-calls</span>
              <button class="btn btn-secondary btn-sm" aria-label="刷新模型调用" @click="reload('config')">↻</button></div>
            </div>
            <div v-if="!m.modelInfo.calls.length" class="empty">暂无 LLM 调用记录（当前为 scripted 离线模型时不产生真实调用）</div>
            <div v-for="(c, i) in m.modelInfo.calls" :key="i" class="mono-row">
              <span class="lbl">{{ c.provider }} / {{ c.model }}</span>
              <span style="color:var(--muted)">{{ c.sid.slice(0, 18) }}…</span>
              <span class="num">{{ c.tokens }} tok · ${{ c.cost.toFixed(4) }}</span>
              <span class="meta">{{ c.at }}</span>
            </div>
          </div>

          <div class="card" data-od-id="doctor-card">
            <div class="card-head"><h3>运行时体检 · doctor</h3><span class="tag">GET /v1/doctor</span></div>
            <div id="doctor-list">
              <div v-if="!m.doctor.length" class="empty">暂无体检数据</div>
              <div v-for="c in m.doctor" :key="c.name" class="check-row">
                <span class="check-ico" :class="'ck-' + c.level">{{ c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '×' }}</span>
                <div><div class="c-name">{{ c.name }}</div><div class="c-detail">{{ c.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="profiles-card">
            <div class="card-head">
              <h3>Agent Profile</h3>
              <div class="row-actions">
                <span class="tag">GET /v1/profiles</span>
                <button class="btn btn-primary btn-sm" id="btn-new-profile" data-od-id="btn-new-profile" @click="openProfileModal(null)">＋ 新建</button>
              </div>
            </div>
            <div id="profile-list">
              <div v-for="p in m.profiles" :key="p.name" class="conf-row">
                <div class="conf-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5"/></svg></div>
                <div class="grow">
                  <div class="name">{{ p.name }} <span v-if="p.model" class="tag">{{ p.model }}</span> <span v-if="p.builtin" class="tag">内置</span> <span v-if="p.name === selectedProfile" class="tag">当前</span></div>
                  <div class="desc">{{ p.desc }}</div>
                </div>
                <div class="row-actions">
                  <button class="btn btn-secondary btn-sm" @click="openProfileModal(p.name)">编辑</button>
                  <button v-if="!p.builtin" class="btn btn-danger btn-sm" @click="askDelete(p.name)">删除</button>
                  <button v-if="p.name !== selectedProfile" class="btn btn-secondary btn-sm" @click="switchProfile(p.name)">切换</button>
                  <span v-else class="tag">当前使用</span>
                </div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="domains-card">
            <div class="card-head"><h3>Domains 与 Tools</h3><span class="tag">GET /v1/domains · /v1/tools</span></div>
            <div id="domain-list">
              <div v-for="(d, i) in m.domains" :key="d.name" class="conf-row clickable" role="button" tabindex="0"
                :data-od-id="'domain-row-' + d.name" aria-label="查看 Domain 详情"
                @click="openDomainDetail(i)" @keydown.enter.prevent="openDomainDetail(i)" @keydown.space.prevent="openDomainDetail(i)">
                <div class="conf-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><circle cx="7.5" cy="7.5" r="1" fill="currentColor" stroke="none"/><circle cx="7.5" cy="16.5" r="1" fill="currentColor" stroke="none"/></svg></div>
                <div class="grow">
                  <div class="name">{{ d.name }} · {{ d.tools ? d.tools.length : 0 }} tools</div>
                  <div class="desc">{{ d.desc }}</div>
                </div>
                <span class="tag">已启用</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="policies-card">
            <div class="card-head"><h3>Policy 偏好</h3><span class="tag">控制台本地偏好</span></div>
            <div id="policy-toggles">
              <div v-for="p in policies" :key="p.key" class="switch-row">
                <div><div class="s-label">{{ p.label }}</div><div class="s-desc">{{ p.desc }}</div></div>
                <label class="switch">
                  <input type="checkbox" v-model="p.on" @change="togglePolicy">
                  <span class="track"><span class="knob"></span></span>
                </label>
              </div>
            </div>
            <div style="font-size:12.5px;color:var(--muted);margin-top:12px">部署级 Policy 由 deployment.json / <code class="num">PUT /v1/config</code> 管理；此处仅为控制台显示偏好（localStorage）。</div>
          </div>
        </div>
      </section>

      <!-- 视图四：评估工作台 -->
</template>
