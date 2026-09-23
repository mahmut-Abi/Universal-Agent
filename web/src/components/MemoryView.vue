<script setup>
import { m, view, memSearch, memList, addMemory, delMemory, memKind, memKinds, memStats } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'MemoryView' })
</script>
<template>
<!-- 视图六：记忆管理 -->
      <section v-show="view === 'memory'" class="view" :class="{ active: view === 'memory' }" id="view-memory" role="tabpanel">
        <div class="card" data-od-id="memory-card">
          <div class="card-head"><h3>记忆管理</h3>
            <span class="tag">{{ m.memories.length }} 条</span>
          </div>
          <div class="mem-summary" data-od-id="memory-stats">
            <span v-if="!Object.keys(memStats).length" class="mem-kind-count muted">暂无记忆</span>
            <span v-for="(n, k) in memStats" :key="k" class="mem-kind-count">{{ k }} <b>{{ n }}</b></span>
            <span v-if="memSearch || memKind" class="mem-filtered">筛选后 {{ memList.length }} 条</span>
          </div>
          <div class="mem-toolbar">
            <input type="text" id="mem-search" v-model="memSearch" placeholder="搜索记忆内容…" aria-label="搜索记忆"
              class="mem-search">
            <button class="btn btn-primary btn-sm" id="btn-mem-add" @click="addMemory">＋ 新增记忆</button>
          </div>
          <div class="filter-pills" role="group" aria-label="按类型筛选记忆" style="margin-bottom:8px">
            <button class="fpill" :aria-pressed="memKind === ''" @click="memKind = ''">全部</button>
            <button v-for="k in memKinds" :key="k" class="fpill" :aria-pressed="memKind === k" @click="memKind = k">{{ k }}</button>
          </div>
          <div id="memory-list">
            <div v-if="!memList.length" class="empty">
              <div class="empty-title">{{ m.memories.length ? '无匹配记忆' : '暂无记忆' }}</div>
              {{ m.memories.length ? '调整搜索词或类型筛选' : '新增一条记忆后，Agent 可在后续会话中调用' }}
            </div>
            <div v-for="mem in memList" :key="mem.id" class="mem-row" :data-od-id="'mem-' + mem.id">
              <div class="mt">{{ mem.text }}
                <div class="mm"><span>{{ mem.kind }}</span><span>{{ mem.at }}</span><span>{{ mem.id }}</span></div>
              </div>
              <button class="btn btn-danger btn-sm" :aria-label="'删除记忆 ' + mem.id" @click="delMemory(mem.id)">删除</button>
            </div>
          </div>
        </div>
      </section>
</template>

<style scoped>
.mem-toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 8px;
}
.mem-search {
  flex: 1;
  padding: 8px 10px;
  font: inherit;
  font-size: 13px;
  color: var(--fg);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  outline: none;
}
.mem-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.mem-kind-count {
  background: var(--bg, #f6f6f7);
  border: 1px solid var(--border, #eee);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12.5px;
  color: var(--text, #333);
}
.mem-kind-count b {
  font-variant-numeric: tabular-nums;
}
.mem-kind-count.muted {
  color: var(--muted, #888);
}
.mem-filtered {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--muted, #888);
}
</style>
