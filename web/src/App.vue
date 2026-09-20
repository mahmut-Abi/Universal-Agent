<script setup>
import { onMounted } from 'vue'
import { initDashboard, view, toastMsg, fatal } from './store.js'
import TopBar from './components/TopBar.vue'
import SideNav from './components/SideNav.vue'
import ChatView from './components/ChatView.vue'
import OverviewView from './components/OverviewView.vue'
import SessionView from './components/SessionView.vue'
import ConfigView from './components/ConfigView.vue'
import EvalView from './components/EvalView.vue'
import ClusterView from './components/ClusterView.vue'
import MemoryView from './components/MemoryView.vue'
import CostView from './components/CostView.vue'
import LogsView from './components/LogsView.vue'
import K8sOpsView from './components/K8sOpsView.vue'
import EcosystemView from './components/EcosystemView.vue'
import AuditView from './components/AuditView.vue'
import MultiAgentView from './components/MultiAgentView.vue'
import HealthView from './components/HealthView.vue'
import AdminView from './components/AdminView.vue'
import Modals from './components/Modals.vue'

onMounted(() => {
  initDashboard()
})
</script>

<template>
  <div class="app-shell">
    <!-- 全局错误兜底 -->
    <div v-if="fatal.length" class="fatal-error" role="alert" aria-live="assertive">
      <div class="fe-head">
        <span class="fe-title">页面发生错误</span>
        <button type="button" class="fe-close" aria-label="关闭错误提示" @click="fatal = []">✕</button>
      </div>
      <div class="fe-detail">{{ fatal.join('\n') }}</div>
      <div class="fe-hint">功能可能部分不可用；接入真实 agentd 时请把该信息提供给后端排查。</div>
    </div>

    <SideNav />

    <div class="main-col">
      <TopBar />

      <main class="shell">
      <ChatView v-show="view === 'chat'" />
      <OverviewView v-show="view === 'overview'" />
      <SessionView v-show="view === 'session'" />
      <ConfigView v-show="view === 'config'" />
      <EvalView v-show="view === 'eval'" />
      <ClusterView v-show="view === 'cluster'" />
      <MemoryView v-show="view === 'memory'" />
      <CostView v-show="view === 'cost'" />
      <LogsView v-show="view === 'logs'" />
      <K8sOpsView v-show="view === 'k8sops'" />
      <EcosystemView v-show="view === 'ecosystem'" />
      <AuditView v-show="view === 'audit'" />
      <MultiAgentView v-show="view === 'multiagent'" />
      <HealthView v-show="view === 'health'" />
      <AdminView v-show="view === 'admin'" />
      </main>
    </div>
  </div>

    <div class="toast" id="toast" :class="{ show: !!toastMsg }" role="status">{{ toastMsg }}</div>

    <Modals />
</template>
