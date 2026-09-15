<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, chatMsgs, CHAT_PROFILES, activeChat, chatInput, sending, replyIdx, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'Modals' })
</script>
<template>
<!-- Profile 新建/编辑弹窗 -->
    <div class="modal-overlay" :class="{ open: profileModal.open }" role="dialog" aria-modal="true" aria-labelledby="pm-title" @click="onOverlayClick">
      <div class="modal">
        <div class="modal-head"><h3 id="pm-title">{{ profileModal.editing ? '编辑 Profile · ' + profileModal.editing : '新建 Profile' }}</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body">
          <div class="field" :class="{ invalid: !!pmError }"><label for="pf-name">名称</label>
            <input type="text" id="pf-name" v-model="pmForm.name" :disabled="!!profileModal.editing" placeholder="如 prod-readonly" autocomplete="off" @keydown.enter="saveProfile">
            <div class="f-err">{{ pmError }}</div></div>
          <div class="field"><label>模型（per-profile，可选）</label>
            <select v-model="pmForm.model.provider" class="input" id="pm-model-provider" style="margin-bottom:6px">
              <option v-for="p in MODEL_PROVIDERS" :key="p" :value="p">{{ p }}</option>
            </select>
            <template v-if="pmForm.model.provider !== 'scripted'">
              <input v-model="pmForm.model.name" class="input" placeholder="模型名，如 gpt-4o-mini" style="margin-bottom:6px" id="pm-model-name">
              <input v-model="pmForm.model.endpoint" class="input" placeholder="Endpoint（可选，如 https://api.openai.com/v1）" style="margin-bottom:6px" id="pm-model-endpoint">
              <input v-model="pmForm.model.api_key_env" class="input" placeholder="API Key 环境变量名，如 OPENAI_API_KEY" id="pm-model-key">
            </template>
            <div class="hint">写入 profile 的 runtime.model + runtime.secrets；scrited 表示使用部署默认模型。需重启/重载 agentd 生效。</div>
          </div>
          <div class="field"><label>启用 Domains</label>
            <div id="pf-domains" style="display:grid;gap:6px">
              <label v-for="d in m.domains" :key="d.name" class="check-row" style="cursor:pointer">
                <input type="checkbox" :value="d.name" v-model="pmForm.domains"> <span class="c-name">{{ d.name }}</span>
              </label>
            </div></div>
          <div class="field"><label for="pf-desc">描述</label>
            <textarea id="pf-desc" v-model="pmForm.desc" rows="2" placeholder="Profile 用途说明"></textarea></div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
          <button class="btn btn-primary btn-sm" id="pm-save" data-od-id="pm-save" @click="saveProfile">保存</button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <div class="modal-overlay" :class="{ open: delModal.open }" role="dialog" aria-modal="true" aria-labelledby="dm-text" @click="onOverlayClick">
      <div class="modal" style="max-width:380px">
        <div class="modal-head"><h3 id="dm-text">确认删除</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body"><p style="margin:0;font-size:13.5px;line-height:1.6;color:var(--muted)" id="del-detail">{{ delDetail }}</p></div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
          <button class="btn btn-danger btn-sm" id="dm-confirm" data-od-id="dm-confirm" @click="confirmDelete">删除</button>
        </div>
      </div>
    </div>

    <!-- Domain 详情弹窗 -->
    <div class="modal-overlay" :class="{ open: domainModal.open }" role="dialog" aria-modal="true" aria-labelledby="dom-title" @click="onOverlayClick">
      <div class="modal">
        <div class="modal-head"><h3 id="dom-title">Domain{{ domainDetail ? ' · ' + domainDetail.name : '' }}</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap" id="dom-meta">
            <template v-if="domainDetail">
              <span class="status" :class="statusCls(domainDetail.active ? 'success' : 'paused')">{{ statusLabel(domainDetail.active ? 'success' : 'paused') }}</span>
              <span class="tag">GET /v1/tools?domain={{ domainDetail.name }}</span>
            </template>
          </div>
          <div class="field"><label>Tools（<span id="dom-tool-count">{{ domainDetail ? domainDetail.tools.length : 0 }}</span>）</label>
            <div id="dom-tools" style="display:grid;gap:0">
              <div v-for="t in domainDetail ? domainDetail.tools : []" :key="t.name" class="tool-row">
                <span class="t-name">{{ t.name }}</span>
                <span class="t-desc">{{ t.desc }}</span>
                <span v-if="t.mutation" class="pill pill-mutation">mutation</span>
                <span v-else class="pill pill-readonly">只读</span>
              </div>
            </div>
            <div v-if="domainDetail && !domainDetail.tools.length" class="f-err" style="display:block" id="dom-empty">该 Domain 暂无注册工具（GET /v1/tools?domain=…）</div></div>
          <div class="field"><label>引用此 Domain 的 Profiles</label>
            <div id="dom-profiles" style="display:flex;gap:6px;flex-wrap:wrap">
              <template v-if="domainDetail">
                <span v-if="domainUsedBy(domainDetail)" v-for="n in domainUsedBy(domainDetail)" :key="n" class="tag">{{ n }}</span>
                <span v-else style="font-size:12.5px;color:var(--muted)">暂无 Profile 引用</span>
              </template>
            </div></div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" id="dm2-cancel" data-od-id="dom-close-btn" @click="closeModal">关闭</button>
        </div>
      </div>
    </div>
</template>
