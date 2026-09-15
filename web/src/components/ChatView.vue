<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, chatMsgs, CHAT_PROFILES, activeChat, chatInput, sending, replyIdx, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'ChatView' })
</script>
<template>
<!-- 视图〇：对话 -->
      <section v-show="view === 'chat'" class="view" :class="{ active: view === 'chat' }" id="view-chat" role="tabpanel">
        <div class="chat-layout" data-od-id="chat-layout">
          <aside class="chat-history" data-od-id="chat-history">
            <div class="ch-head">
              <h3>历史对话</h3>
              <button class="btn btn-primary btn-sm" id="btn-new-chat" data-od-id="btn-new-chat" @click="newChat">＋ 新建对话</button>
              <div class="ch-filter" id="chat-profile-filter" role="group" aria-label="按 Profile 过滤">
                <button v-for="p in CHAT_PROFILES" :key="p" :class="{ active: chatFilter === p }" @click="chatFilter = p">{{ p }}</button>
              </div>
            </div>
            <div class="ch-list" id="chat-history-list" data-od-id="chat-history-list">
              <div v-if="!filteredChats.length" class="empty">暂无该 Profile 的对话</div>
              <button v-for="c in filteredChats" :key="c.id" class="ch-item" :class="{ active: activeChatId === c.id }"
                :data-chat="c.id" @click="activeChatId = c.id">
                <div class="t">{{ c.title }}</div>
                <div class="m"><span class="p">{{ c.profile }}</span><span>{{ c.time }}</span></div>
              </button>
            </div>
          </aside>
          <div class="chat-main" data-od-id="chat-main">
            <div class="chat-msgs" id="chat-msgs" ref="chatMsgsEl" data-od-id="chat-msgs" aria-live="polite">
              <div v-if="!activeChat" class="empty" style="margin:auto">选择左侧历史对话，或新建一个对话开始</div>
              <template v-else>
                <div v-for="(msg, mi) in activeChat.msgs" :key="mi" class="msg" :class="msg.role === 'user' ? 'user' : 'agent'">
                  <span class="who">{{ msg.role === 'user' ? '你' : 'Agent · ' + activeChat.profile }}</span>
                  <div v-for="(t, ti) in msg.tools || []" :key="ti" class="msg-tool">⚙ 调用 <b>{{ t }}</b></div>
                  <!-- prettier-ignore -->
                  <div class="bubble" v-html="msg.text"></div>
                </div>
                <div v-if="sending" class="msg agent">
                  <span class="who">Agent · {{ activeChat.profile }}</span>
                  <div class="bubble"><span class="typing-dots"><i></i><i></i><i></i></span></div>
                </div>
              </template>
            </div>
            <div class="chat-inputbar">
              <div class="chat-inputrow">
                <textarea ref="chatInputEl" class="textarea" id="chat-input" rows="1" v-model="chatInput"
                  @keydown="onChatKeydown" @input="autoGrow"
                  placeholder="输入指令，例如：排查 default 命名空间下异常的 Pod…" aria-label="消息输入"></textarea>
                <button class="btn btn-primary" id="btn-chat-send" :disabled="sending" @click="sendChat">发送</button>
              </div>
              <div class="chat-hint">Enter 发送 · Shift+Enter 换行 · 运行事件同步写入 <span class="num">/v1/sessions/{id}/events</span></div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图一：总览 -->
</template>
