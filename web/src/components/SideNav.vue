<script setup>
// 分组侧边栏：核心 / 运维中心（macOS 设置式导航，来自重构原型）
import { NAV_GROUPS, view, switchView } from "../store.js";

defineOptions({ name: "SideNav" });

const ICONS = {
  overview:
    '<rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1.2"/><rect x="9" y="1.5" width="5.5" height="5.5" rx="1.2"/><rect x="1.5" y="9" width="5.5" height="5.5" rx="1.2"/><rect x="9" y="9" width="5.5" height="5.5" rx="1.2"/>',
  chat: '<path d="M14 8a6 6 0 1 1-2.2-4.6L14 2.5V8z" stroke-linejoin="round"/>',
  session:
    '<path d="M2.5 4.5h11v8h-11z"/><path d="M2.5 6.5h11"/><circle cx="4.5" cy="10.5" r=".8" fill="currentColor" stroke="none"/>',
  config:
    '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6L11 5M5 11l-1.4 1.4"/>',
  eval: '<path d="M2 13.5V9M6 13.5V5M10 13.5V7.5M14 13.5V3"/>',
  cluster:
    '<circle cx="8" cy="8" r="2"/><circle cx="2.8" cy="3" r="1.4"/><circle cx="13.2" cy="3" r="1.4"/><circle cx="2.8" cy="13" r="1.4"/><circle cx="13.2" cy="13" r="1.4"/><path d="M4 4l2.5 2.5M12 4L9.5 6.5M4 12l2.5-2.5M12 12L9.5 9.5"/>',
  memory:
    '<path d="M8 1.8l5.5 3v6.4l-5.5 3-5.5-3V4.8z" stroke-linejoin="round"/>',
  cost:
    '<path d="M8 1.5v13"/><path d="M11 4.5c0-1.1-1.3-1.8-3-1.8s-3 .7-3 1.8c0 2.7 6 1.6 6 4.4 0 1.1-1.3 1.8-3 1.8s-3-.7-3-1.8" stroke-linecap="round"/>',
  logs:
    '<path d="M2.5 3.5h11M2.5 6.5h11M2.5 9.5h7M2.5 12.5h5" stroke-linecap="round"/>',
  k8sops:
    '<path d="M8 1.5l5.6 2.7v4.3c0 3.1-2.3 5.2-5.6 6-3.3-.8-5.6-2.9-5.6-6V4.2z" stroke-linejoin="round"/>',
  ecosystem:
    '<path d="M3 6.5L8 2l5 4.5V14H3z" stroke-linejoin="round"/><path d="M6.2 14V9h3.6v5"/>',
  audit:
    '<circle cx="8" cy="8" r="6.2"/><path d="M8 4.5V8l2.4 1.6" stroke-linecap="round"/>',
  multiagent:
    '<rect x="5.5" y="1.5" width="5" height="4" rx="1"/><rect x="1.5" y="10.5" width="5" height="4" rx="1"/><rect x="9.5" y="10.5" width="5" height="4" rx="1"/><path d="M8 5.5v2.5M4 10.5V8h8v2.5"/>',
  health:
    '<path d="M1.5 8.5h3l1.5-4 2.5 7L10 7l1 1.5h3.5" stroke-linejoin="round" stroke-linecap="round"/>',
};
</script>

<template>
  <aside class="sidenav" data-od-id="sidenav">
    <div class="brand">
      <span class="brand-mark">UA</span>
      <span class="brand-name">Universal-Agent</span>
    </div>
    <nav v-for="g in NAV_GROUPS" :key="g.label" class="nav-group" :aria-label="g.label">
      <div class="nav-group-label">{{ g.label }}</div>
      <button
        v-for="v in g.views"
        :key="v.name"
        type="button"
        class="nav-item"
        :class="{ active: view === v.name }"
        :aria-current="view === v.name ? 'page' : undefined"
        @click="switchView(v.name)"
      >
        <svg
          class="ico"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          stroke-width="1.4"
          aria-hidden="true"
          v-html="ICONS[v.name] || ICONS.overview"
        ></svg>
        <span>{{ v.label }}</span>
      </button>
    </nav>
  </aside>
</template>
