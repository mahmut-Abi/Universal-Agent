<script setup>
import { m, view, openTraceSession } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'LogsView' })
import { ref, computed } from 'vue'
const logLevel = ref('all')
const LOG_LEVELS = ['all', 'info', 'warn', 'error']
const filteredLogs = computed(() =>
  logLevel.value === 'all' ? m.logs : m.logs.filter((l) => l.level === logLevel.value)
)
</script>
<template>
<!-- 视图八：日志与追踪 -->
      <section v-show="view === 'logs'" class="view" :class="{ active: view === 'logs' }" id="view-logs" role="tabpanel">        <div class="grid g-main">
          <div class="card" data-od-id="logs-card">
            <div class="card-head"><h3>日志流</h3>
              <div class="filter-pills" role="group" aria-label="按级别过滤日志">
                <button v-for="lv in LOG_LEVELS" :key="lv" class="fpill" :aria-pressed="logLevel === lv" @click="logLevel = lv">{{ lv === 'all' ? '全部' : lv }}</button>
              </div>
            </div>
            <div id="logs-list">
              <div v-if="!filteredLogs.length" class="empty">当前级别暂无日志</div>
              <template v-for="(l, i) in filteredLogs" :key="i">
              <div class="log-line">
                <span class="lt">{{ l.at }}</span>
                <span class="lvl" :class="'lvl-' + l.level">{{ l.level }}</span>
                <span class="lx"><code>{{ l.src }}</code> · {{ l.text }}</span>
              </div>
              </template>
            </div>
          </div>
          <div class="card" data-od-id="traces-card">
            <div class="card-head"><h3>Trace 查询</h3><span class="tag">OTLP 导出</span></div>
            <div id="traces-list">
              <div v-for="t in m.traces" :key="t.tid" class="mono-row col2">
                <div class="mono-main">
                  <span class="lbl">{{ t.tid }}</span>
                  <span class="meta">{{ t.root }} · {{ t.spans }} spans · {{ t.dur }}</span>
                  <span class="meta">{{ t.session }}</span>
                </div>
                <button class="btn btn-secondary btn-sm" @click="openTraceSession(t.session)">查看会话</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图九：K8s 运维 -->
</template>
