<script setup>
import { m, view, toast, reload, statusCls, statusLabel } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'K8sOpsView' })
</script>
<template>
<!-- 视图九：K8s 运维 -->
      <section v-show="view === 'k8sops'" class="view" :class="{ active: view === 'k8sops' }" id="view-k8sops" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <div class="row-actions"><button class="btn btn-primary btn-sm" id="btn-k8s-preflight" @click="reload('k8sops'); toast('Preflight 已运行')">运行 Preflight</button></div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="k8s-preflight-card">
            <div class="card-head"><h3>Preflight 检查</h3><span class="tag">context · prod-cluster</span></div>
            <div id="k8s-preflight">
              <div v-for="p in m.k8sops.preflight" :key="p.name" class="check-row">
                <span class="check-ico" :class="p.ok ? 'ck-ok' : 'ck-fail'">{{ p.ok ? '✓' : '!' }}</span>
                <div><div class="c-name">{{ p.name }}</div><div class="c-detail">{{ p.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="k8s-runs-card">
            <div class="card-head"><h3>运行记录</h3></div>
            <div id="k8s-runs">
              <div v-for="(r, i) in m.k8sops.runs" :key="i" class="mono-row">
                <span>{{ r.name }}</span><span class="tag">{{ r.kind }}</span>
                <span style="color:var(--muted)">{{ r.at }}</span>
                <span class="status" :class="statusCls(r.state)">{{ statusLabel(r.state) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图十：生态与包 -->
</template>
