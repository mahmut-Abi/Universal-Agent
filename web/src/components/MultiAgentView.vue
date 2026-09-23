<script setup>
import { view, topo } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'MultiAgentView' })
</script>
<template>
<!-- 视图十二：多智能体 -->
      <section v-show="view === 'multiagent'" class="view" :class="{ active: view === 'multiagent' }" id="view-multiagent" role="tabpanel">        <div class="card" data-od-id="topo-card">
          <div class="card-head"><h3>协作拓扑</h3></div>
          <svg class="topo" viewBox="0 0 680 280" role="img"
            aria-label="多智能体协作拓扑图：coordinator 连接 triage、ops、audit 三个 worker" id="topo-svg">
            <line v-for="(e, i) in topo.edges" :key="'e' + i" :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" class="edge" />
            <text v-for="(e, i) in topo.edges" :key="'et' + i" :x="(e.x1 + e.x2) / 2" :y="(e.y1 + e.y2) / 2 - 6" text-anchor="middle">{{ e.label }}</text>
            <g v-for="(b, i) in topo.boxes" :key="'b' + i">
              <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="8" :class="b.hub ? 'node-box hub' : 'node-box'" />
              <text :x="b.x + b.w / 2" :y="b.y + b.h / 2 - 2" text-anchor="middle" class="node-label">{{ b.label }}</text>
              <text :x="b.x + b.w / 2" :y="b.y + b.h / 2 + 16" text-anchor="middle">{{ b.sub }}</text>
            </g>
          </svg>
          <div class="mono-row" style="border-top:1px solid var(--border);margin-top:8px;padding-top:12px">
            <span style="font-size:12.5px;color:var(--muted)" id="topo-summary">{{ topo.summary }}</span>
          </div>
          <div class="agent-roster">
            <div class="w-sec">Agent 名册</div>
            <div v-for="b in topo.boxes" :key="b.label + ':' + b.sub" class="mono-row">
              <span class="lbl">{{ b.label }}</span>
              <span class="tag" :class="b.hub ? 'tag-hub' : ''">{{ b.hub ? 'coordinator' : 'worker' }}</span>
              <span style="color:var(--muted)">{{ b.sub }}</span>
            </div>
            <div class="roster-note">
              多智能体通过结构化 Task/Result/Evidence 契约通信；域组合由单 Agent 内多个 Domain 共享同一世界模型承担（参见架构文档 §4.9）。
            </div>
          </div>
        </div>
      </section>
</template>

<style scoped>
.agent-roster { margin-top: 12px; }
.w-sec {
  font-weight: 600;
  font-size: 12px;
  margin: 6px 0 4px;
  color: var(--muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tag-hub { color: var(--accent, #0071e3); border-color: var(--accent, #0071e3); }
.roster-note {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted, #888);
  border-top: 1px dashed var(--border, #eee);
  padding-top: 8px;
}
</style>
