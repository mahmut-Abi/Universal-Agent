<script setup>
import { m, view, toast, evalByDataset, pct, runEval } from "../store.js";

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'EvalView' })
</script>
<template>
<!-- 视图四：评估工作台 -->
      <section v-show="view === 'eval'" class="view" :class="{ active: view === 'eval' }" id="view-eval" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <div class="row-actions"><button class="btn btn-primary btn-sm" id="btn-eval-run" @click="runEval().then(() => toast('评估已启动')).catch((e) => toast('评估启动失败：' + e.message))">▶ 运行评估</button></div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="eval-reports-card">
            <div class="card-head"><h3>评估报告</h3></div>
            <div id="eval-reports">
              <div v-for="r in m.evals.reports" :key="r.id" class="eval-row">
                <span class="lbl">{{ r.id }}</span>
                <span>{{ r.dataset }} · {{ r.profile }}</span>
                <span class="scorebar" role="img" :aria-label="'通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                <span class="num">{{ pct(r.score) }}%</span>
                <span class="muted">{{ r.pass }}/{{ r.total }} · {{ r.at }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="eval-compare-card">
            <div class="card-head"><h3>多 Profile 对比</h3></div>
            <div id="eval-compare">
              <template v-for="(rows, ds) in evalByDataset" :key="ds">
                <div class="eval-ds-name">{{ ds }}</div>
                <div v-for="r in rows" :key="r.id" class="eval-compare-row">
                  <span class="lbl">{{ r.profile }}</span>
                  <span class="scorebar" role="img" :aria-label="r.profile + ' 通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                  <span class="num" style="font-weight:600">{{ pct(r.score) }}%</span>
                </div>
              </template>
            </div>
          </div>
          <div class="card" data-od-id="eval-datasets-card">
            <div class="card-head"><h3>数据集</h3></div>
            <div id="eval-datasets">
              <div v-for="d in m.evals.datasets" :key="d.name" class="eval-ds-row">
                <span class="lbl">{{ d.name }}</span>
                <span style="color:var(--muted)">{{ d.desc }}</span>
                <span class="num">{{ d.cases }} 用例</span><span class="muted">{{ d.updated }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图五：分布式集群 -->
</template>
