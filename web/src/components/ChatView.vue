<script setup>
import { m, view, statusCls, statusLabel, chatFilter, activeChatId, CHAT_PROFILES, activeChat, chatInput, chatQueue, pendingUserMsg, sending, chatMsgsEl, chatInputEl, filteredChats, newChat, selectChat, refreshChats, autoGrow, sendChat, onChatKeydown, onMsgsClick, liveTailOn, liveTailState, liveEvents, toggleLiveTail } from "../store.js";

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
              <div style="display:flex;gap:6px">
                <button class="btn btn-secondary btn-sm" id="btn-refresh-chats" aria-label="刷新会话列表" title="刷新会话列表" @click="refreshChats">↻</button>
                <button class="btn btn-primary btn-sm" id="btn-new-chat" data-od-id="btn-new-chat" @click="newChat">＋ 新建对话</button>
                <button class="btn btn-secondary btn-sm" id="btn-live-tail" :class="{ active: liveTailOn }"
                  :aria-pressed="String(liveTailOn)" aria-label="实时事件流"
                  title="实时事件流：跟踪当前会话的运行事件（SSE）" @click="toggleLiveTail">⚡ 实时</button>
              </div>
              <div class="ch-filter" id="chat-profile-filter" role="group" aria-label="按 Profile 过滤">
                <button v-for="p in CHAT_PROFILES" :key="p" :class="{ active: chatFilter === p }" @click="chatFilter = p">{{ p }}</button>
              </div>
            </div>
            <div class="ch-list" id="chat-history-list" data-od-id="chat-history-list">
              <div v-if="!filteredChats.length" class="empty">暂无该 Profile 的对话</div>
              <button v-for="c in filteredChats" :key="c.id" class="ch-item" :class="{ active: activeChatId === c.id }"
                :data-chat="c.id" @click="selectChat(c.id)">
                <div class="t">{{ c.title }}</div>
                <div class="m"><span class="p">{{ c.profile }}</span><span class="status" :class="statusCls(c.status)">{{ statusLabel(c.status) }}</span><span>{{ c.time }}</span></div>
              </button>
            </div>
          </aside>
          <div class="chat-main" data-od-id="chat-main">
            <div class="chat-msgs" id="chat-msgs" ref="chatMsgsEl" data-od-id="chat-msgs" aria-live="polite" @click="onMsgsClick">
              <!-- 实时事件流（SSE live tail） -->
              <div v-if="liveTailOn" class="live-tail" data-od-id="live-tail">
                <div class="lt-head">
                  <span class="lt-dot" :class="liveTailState"></span>
                  <span>实时事件 · {{ liveTailState === 'live' ? '已连接' : liveTailState === 'reconnecting' ? '重连中…' : '空闲' }}</span>
                  <button class="btn btn-secondary btn-sm" @click="toggleLiveTail">关闭</button>
                </div>
                <div class="lt-list">
                  <div v-for="(ev, li) in liveEvents" :key="li" class="lt-ev">
                    <span class="meta">{{ ev.at }}</span>
                    <span class="tl-type">{{ ev.t }}</span>
                    <!-- ev.text 来自 eventText()（内部 escapeHtml 后再拼装），用 v-html 安全渲染 -->
                    <span class="lt-text" v-html="ev.text"></span>
                  </div>
                  <div v-if="!liveEvents.length" class="empty">等待事件…</div>
                </div>
              </div>
              <div v-if="!activeChat && !pendingUserMsg && !chatQueue.length" class="chat-empty" data-od-id="chat-empty">
                <div class="ring" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
                </div>
                <h3>开始新对话</h3>
                <p>输入任务或问题，Agent 将执行工具调用并汇报结果；也可以在左侧选择历史会话继续。</p>
              </div>
              <div class="msgs-col" data-od-id="chat-msgs-col">
              <template v-if="activeChat">
                <div v-for="(msg, mi) in activeChat.msgs" :key="mi" class="msg" :class="msg.role === 'user' ? 'user' : 'agent'">
                  <span class="who">{{ msg.role === 'user' ? '你' : 'Agent · ' + activeChat.profile }}</span>
                  <div class="bubble" v-html="msg.text"></div>
                </div>
              </template>
              <!-- 乐观显示：发送中先上屏，事件回流后由转录接管 -->
              <div v-if="pendingUserMsg" class="msg user">
                <span class="who">你</span>
                <div class="bubble">{{ pendingUserMsg }}</div>
              </div>
              <!-- 排队中：agent 运行期间输入的消息，结束后自动发送 -->
              <div v-for="(q, qi) in chatQueue" :key="'q' + qi" class="msg user queued">
                <span class="who">你 · 排队中</span>
                <div class="bubble">{{ q }}</div>
              </div>
              <div v-if="sending" class="msg agent">
                <span class="who">Agent{{ activeChat ? ' · ' + activeChat.profile : '' }}</span>
                <div class="bubble"><span class="typing-dots"><i></i><i></i><i></i></span></div>
              </div>
              </div>
            </div>
            <div class="chat-inputbar">
              <div class="composer" data-od-id="chat-composer">
                <textarea ref="chatInputEl" id="chat-input" rows="1" v-model="chatInput"
                  @keydown="onChatKeydown" @input="autoGrow"
                  placeholder="输入任务或问题，Agent 将执行并汇报结果…" aria-label="消息输入"></textarea>
                <div class="composer-actions">
                  <span class="chat-hint">Enter 发送 · Shift+Enter 换行 · 运行中的消息会排队</span>
                  <button class="btn btn-primary" id="btn-chat-send" :disabled="sending" @click="sendChat">发送</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图一：总览 -->
</template>

<style scoped>
.live-tail {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 10px;
  background: var(--bg);
  max-height: 220px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.live-tail .lt-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.live-tail .lt-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted, #999);
}
.live-tail .lt-dot.live {
  background: #22c55e;
  box-shadow: 0 0 4px #22c55e;
}
.live-tail .lt-dot.reconnecting {
  background: #f59e0b;
}
.live-tail .lt-head .btn {
  margin-left: auto;
}
.live-tail .lt-list {
  overflow-y: auto;
  padding: 4px 10px;
  font-size: 12px;
}
.live-tail .lt-ev {
  display: flex;
  gap: 8px;
  padding: 2px 0;
  align-items: baseline;
}
.live-tail .lt-ev .tl-type {
  font-weight: 600;
  min-width: 140px;
}
.live-tail .lt-ev .lt-text :deep(code) {
  font-size: 11px;
}
</style>
