<script setup>
import { m, view, fatal, reload, statusCls, statusLabel } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'HealthView' })
import { computed } from 'vue'
// 总体健康判定：按检查项级别计数给出状态与结论
const healthSummary = computed(() => {
  const c = m.health.checks || []
  const ok = c.filter((x) => x.level === 'ok').length
  const warn = c.filter((x) => x.level === 'warn').length
  const err = c.filter((x) => x.level === 'error' || x.level === 'fatal').length
  const total = c.length
  let verdict
  let cls
  if (!total) {
    verdict = '暂无体检数据'
    cls = 'paused'
  } else if (err) {
    verdict = '存在异常项，建议立即处理'
    cls = 'failed'
  } else if (warn) {
    verdict = '总体健康，存在需关注项'
    cls = 'waiting'
  } else {
    verdict = '运行时健康'
    cls = 'success'
  }
  return { ok, warn, err, total, verdict, cls }
})
</script>
<template>
<!-- 视图十三：健康中心 -->
      <section v-show="view === 'health'" class="view" :class="{ active: view === 'health' }" id="view-health" role="tabpanel">
        <div class="health-summary card" data-od-id="health-summary">
          <div class="hs-main">
            <span class="status" :class="statusCls(healthSummary.cls)">{{ statusLabel(healthSummary.cls) }}</span>
            <span class="hs-verdict">{{ healthSummary.verdict }}</span>
            <span class="hs-meta">共 {{ healthSummary.total }} 项 · <i class="h-ok">✓ {{ healthSummary.ok }}</i> · <i class="h-warn">! {{ healthSummary.warn }}</i> · <i class="h-err">× {{ healthSummary.err }}</i></span>
          </div>
          <div class="row-actions"><button class="btn btn-secondary btn-sm" id="btn-health-refresh" @click="reload('health')">刷新体检</button></div>
        </div>
        <div class="grid g-config">
          <div class="card" data-od-id="health-checks-card">
            <div class="card-head"><h3>逐项检查</h3></div>
            <div id="health-list">
              <div v-for="c in m.health.checks" :key="c.name" class="check-row">
                <span class="check-ico" :class="'ck-' + c.level">{{ c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '×' }}</span>
                <div><div class="c-name">{{ c.name }}</div><div class="c-detail">{{ c.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="health-state-card">
            <div class="card-head"><h3>状态事件存储</h3><span class="tag">state-events</span></div>
            <div id="health-state">
              <div v-for="s in m.health.state" :key="s.name" class="check-row">
                <span class="check-ico" :class="s.warn ? 'ck-warn' : 'ck-ok'">{{ s.warn ? '!' : '✓' }}</span>
                <div><div class="c-name">{{ s.name }}</div><div class="c-detail">{{ s.detail }}</div></div>
              </div>
              <div class="empty" v-if="!m.health.state.length">暂无状态事件存储信息</div>
            </div>
          </div>
        </div>
      </section>
</template>

<style scoped>
.health-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  padding: 10px 14px;
}
.hs-main { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hs-verdict { font-weight: 600; font-size: 14px; color: var(--text, #222); }
.hs-meta { font-size: 12.5px; color: var(--muted, #888); }
.h-ok { color: var(--ok, #2a7); font-style: normal; }
.h-warn { color: var(--warn, #b45309); font-style: normal; }
.h-err { color: var(--danger, #c53); font-style: normal; }
</style>
