<script setup>
// 顶栏：仅页面标题 + 环境（导航已移至分组侧边栏）
import { computed } from "vue";
import { view, VIEW_TITLES, apiHost, doRefresh } from "../store.js";

defineOptions({ name: "TopBar" });
const title = computed(() => VIEW_TITLES[view.value] || "");
</script>

<template>
  <header class="topbar" data-od-id="topbar">
    <h1>{{ title }}</h1>
    <span class="spacer"></span>
    <button type="button" class="btn btn-secondary btn-sm" aria-label="刷新数据" @click="doRefresh">刷新</button>
    <span class="env-pill"><span class="dot"></span>agentd · <span class="num">{{ apiHost }}</span></span>
  </header>
</template>
