<script setup>
// 顶栏：页面标题 + profile 热切换选择器 + 环境（导航已移至分组侧边栏）
import { computed, onMounted, ref } from "vue";
import {
  m,
  view,
  VIEW_TITLES,
  apiHost,
  doRefresh,
  activeProfile,
  hotSwapAvailable,
  setActiveProfile,
  toast,
  busy,
  autoRefreshOn,
  toggleAutoRefresh,
} from "../store.js";
import { apiGet, pick } from "../api.js";

defineOptions({ name: "TopBar" });
const title = computed(() => VIEW_TITLES[view.value] || "");

const options = ref([]); // [{ name, desc }]
const loading = ref(false);

async function loadOptions() {
  loading.value = true;
  try {
    const d = await apiGet("/v1/profiles");
    options.value = pick(d, "profiles").map((p) => ({
      name: p.name,
      desc: p.description || (p.domains || []).map((x) => x.name).join("/"),
    }));
  } catch (e) {
    // 目录不可达不阻塞顶栏；切换器仅显示已选项
    toast("profile 列表加载失败：" + e.message);
  } finally {
    loading.value = false;
  }
}
onMounted(loadOptions);

function onChange(event) {
  setActiveProfile(event.target.value);
}
</script>

<template>
  <header class="topbar" data-od-id="topbar">
    <h1>{{ title }}</h1>
    <span class="spacer"></span>
    <select
      v-if="hotSwapAvailable && (options.length || activeProfile)"
      class="btn btn-secondary btn-sm profile-select"
      data-od-id="profile-select"
      :value="activeProfile"
      :disabled="loading"
      aria-label="切换 profile（热切换，无需重启 agentd）"
      title="Profile 热切换：目录与会话数据将反映所选 profile 的域组合"
      @change="onChange"
    >
      <option value="">默认 profile（启动时加载）</option>
      <option v-for="opt in options" :key="opt.name" :value="opt.name">
        {{ opt.name }}{{ opt.desc ? ` · ${opt.desc}` : "" }}
      </option>
    </select>
    <button type="button" class="btn btn-secondary btn-sm" aria-label="刷新数据" @click="doRefresh">刷新</button>
    <button
      type="button"
      class="btn btn-secondary btn-sm"
      :class="{ active: autoRefreshOn }"
      :aria-pressed="String(autoRefreshOn)"
      aria-label="自动刷新"
      title="自动刷新总览（30s，标签页隐藏时暂停）"
      @click="toggleAutoRefresh"
    >
      {{ autoRefreshOn ? '⏱ 自动开' : '⏱ 自动关' }}
    </button>
    <span v-if="busy" class="spinner" role="status" aria-label="加载中"></span>
    <span class="env-pill"><span class="dot"></span>agentd · <span class="num">{{ apiHost }}</span></span>
  </header>
</template>

<style scoped>
.profile-select {
  max-width: 260px;
  text-overflow: ellipsis;
}
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border, #ccc);
  border-top-color: var(--accent, #0071e3);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex: none;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.btn.active {
  border-color: var(--accent, #0071e3);
  color: var(--accent, #0071e3);
}
</style>
