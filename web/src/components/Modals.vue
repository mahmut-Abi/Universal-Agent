<script setup>
import { m, statusCls, statusLabel, profileModal, pmForm, MODEL_PROVIDERS, pmError, saveProfile, delModal, delDetail, confirmDelete, domainModal, domainDetail, domainUsedBy, closeModal } from "../store.js";
import AppModal from './AppModal.vue'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'Modals' })
</script>
<template>
<!-- Profile 新建/编辑弹窗 -->
    <AppModal :open="profileModal.open" :title="profileModal.editing ? '编辑 Profile · ' + profileModal.editing : '新建 Profile'" id="pm-title" @close="closeModal">
      <div class="modal-body">
        <div class="field" :class="{ invalid: !!pmError }"><label for="pf-name">名称</label>
          <input type="text" id="pf-name" v-model="pmForm.name" :disabled="!!profileModal.editing" placeholder="如 prod-readonly" autocomplete="off" @keydown.enter="saveProfile">
          <div class="f-err">{{ pmError }}</div></div>
        <div class="field"><label>模型（per-profile，可选）</label>
          <select v-model="pmForm.model.provider" class="input" id="pm-model-provider" style="margin-bottom:6px">
            <option v-for="p in MODEL_PROVIDERS" :key="p" :value="p">{{ p }}</option>
          </select>
          <template v-if="pmForm.model.provider !== 'scripted'">
            <input v-model="pmForm.model.name" class="input" placeholder="模型名，如 gpt-4o-mini" style="margin-bottom:6px" id="pm-model-name">
            <input v-model="pmForm.model.endpoint" class="input" placeholder="Endpoint（可选，如 https://api.openai.com/v1）" style="margin-bottom:6px" id="pm-model-endpoint">
            <input v-model="pmForm.model.api_key_env" class="input" placeholder="API Key 环境变量名，如 OPENAI_API_KEY" style="margin-bottom:6px" id="pm-model-key">
            <input v-model.number="pmForm.model.timeout_seconds" class="input" type="number" min="5" max="600" placeholder="超时秒数（默认 30）" id="pm-model-timeout">
            <input v-model="pmForm.model.response_format" class="input" placeholder="response_format（可选，如 json_object）" id="pm-model-rf">
            <textarea v-model="pmForm.model.headers" class="input" rows="2" placeholder='自定义 Header（每行一条，格式 Key: Value）' id="pm-model-headers"></textarea>
            <label class="check-row" style="cursor:pointer">
              <input type="checkbox" v-model="pmForm.model.secret_required"> <span class="c-name">API Key 必需（缺失时构建即失败，推荐）</span>
            </label>
          </template>
          <div class="hint">写入 profile 的 runtime.model + runtime.secrets；scripted 表示使用部署默认模型。需重启/重载 agentd 生效。</div>
        </div>
        <div class="field"><label>启用 Domains</label>
          <div id="pf-domains" style="display:grid;gap:6px">
            <label v-for="d in m.domains" :key="d.name" class="check-row" style="cursor:pointer">
              <input type="checkbox" :value="d.name" v-model="pmForm.domains"> <span class="c-name">{{ d.name }}</span>
            </label>
          </div></div>
        <div class="field">
          <label class="check-row" style="cursor:pointer">
            <input type="checkbox" v-model="pmForm.advanced"> <span class="c-name">高级设置（存储 / 分布式 / 预算 / 域设置）</span>
          </label>
          <div v-if="pmForm.advanced" class="adv-grid" id="pm-advanced" style="border:1px solid var(--border);border-radius:8px;padding:10px;display:grid;gap:10px">
            <div>
              <div class="adv-title">存储</div>
              <select v-model="pmForm.store_backend" class="input" id="pm-store-backend" style="margin-bottom:4px">
                <option value="memory">memory（会话不持久）</option>
                <option value="file">file</option>
                <option value="sqlite">sqlite（推荐）</option>
              </select>
              <input v-if="pmForm.store_backend !== 'memory'" v-model="pmForm.store_path" class="input" placeholder="路径（默认 /data/state.db）" id="pm-store-path">
            </div>
            <div>
              <div class="adv-title">分布式运行时</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
                <select v-model="pmForm.dist_queue_backend" class="input" id="pm-dq-backend"><option value="memory">queue: memory</option><option value="file">queue: file</option><option value="sqlite">queue: sqlite</option></select>
                <input v-if="pmForm.dist_queue_backend !== 'memory'" v-model="pmForm.dist_queue_path" class="input" placeholder="queue 路径" id="pm-dq-path">
                <select v-model="pmForm.dist_locks_backend" class="input" id="pm-dl-backend"><option value="memory">locks: memory</option><option value="file">locks: file</option><option value="sqlite">locks: sqlite</option></select>
                <input v-if="pmForm.dist_locks_backend !== 'memory'" v-model="pmForm.dist_locks_path" class="input" placeholder="locks 路径" id="pm-dl-path">
                <select v-model="pmForm.dist_workers_backend" class="input" id="pm-dw-backend"><option value="memory">workers: memory</option><option value="file">workers: file</option><option value="sqlite">workers: sqlite</option></select>
                <input v-if="pmForm.dist_workers_backend !== 'memory'" v-model="pmForm.dist_workers_path" class="input" placeholder="workers 路径" id="pm-dw-path">
              </div>
            </div>
            <div>
              <div class="adv-title">预算限制（留空 = 默认）</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
                <input v-model="pmForm.max_iterations" class="input" type="number" min="1" placeholder="最大迭代（默认 20）" id="pm-max-iter">
                <input v-model="pmForm.max_recovery_steps" class="input" type="number" min="1" placeholder="最大恢复步数（默认 8）" id="pm-max-rec">
                <input v-model="pmForm.max_total_cost_micros" class="input" type="number" min="0" placeholder="成本上限（micros）" id="pm-max-cost">
                <input v-model="pmForm.max_total_tokens" class="input" type="number" min="0" placeholder="Token 上限" id="pm-max-tokens">
              </div>
            </div>
            <div>
              <div class="adv-title">域设置（JSON，如 {"workspace_path": "/data/workspace"}）</div>
              <textarea v-model="pmForm.domainSettingsJson" class="input" rows="3" placeholder="{}" id="pm-domain-settings"></textarea>
            </div>
          </div>
        </div>
        <div class="field"><label for="pf-desc">描述</label>
          <textarea id="pf-desc" v-model="pmForm.desc" rows="2" placeholder="Profile 用途说明"></textarea></div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
        <button class="btn btn-primary btn-sm" id="pm-save" data-od-id="pm-save" @click="saveProfile">保存</button>
      </div>
    </AppModal>

    <!-- 删除确认弹窗 -->
    <AppModal :open="delModal.open" title="确认删除" id="dm-text" width="380px" @close="closeModal">
      <div class="modal-body"><p style="margin:0;font-size:13.5px;line-height:1.6;color:var(--muted)" id="del-detail">{{ delDetail }}</p></div>
      <div class="modal-foot">
        <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
        <button class="btn btn-danger btn-sm" id="dm-confirm" data-od-id="dm-confirm" @click="confirmDelete">删除</button>
      </div>
    </AppModal>

    <!-- Domain 详情弹窗 -->
    <AppModal :open="domainModal.open" :title="domainDetail ? 'Domain · ' + domainDetail.name : 'Domain'" id="dom-title" @close="closeModal">
      <div class="modal-body">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap" id="dom-meta">
          <template v-if="domainDetail">
            <span class="status" :class="statusCls(domainDetail.active ? 'success' : 'paused')">{{ statusLabel(domainDetail.active ? 'success' : 'paused') }}</span>
          </template>
        </div>
        <div class="field"><label>Tools（<span id="dom-tool-count">{{ domainDetail ? domainDetail.tools.length : 0 }}</span>）</label>
          <div id="dom-tools" style="display:grid;gap:0">
            <div v-for="t in domainDetail ? domainDetail.tools : []" :key="t.name" class="tool-row">
              <span class="t-name">{{ t.name }}</span>
              <span class="t-desc">{{ t.desc }}</span>
              <span v-if="t.mutation" class="pill pill-mutation">mutation</span>
              <span v-else class="pill pill-readonly">只读</span>
            </div>
          </div>
          <div v-if="domainDetail && !domainDetail.tools.length" class="f-err" style="display:block" id="dom-empty">该 Domain 暂无注册工具</div></div>
        <div class="field"><label>引用此 Domain 的 Profiles</label>
          <div id="dom-profiles" style="display:flex;gap:6px;flex-wrap:wrap">
            <template v-if="domainDetail">
              <span v-if="domainUsedBy(domainDetail)" v-for="n in domainUsedBy(domainDetail)" :key="n" class="tag">{{ n }}</span>
              <span v-else style="font-size:12.5px;color:var(--muted)">暂无 Profile 引用</span>
            </template>
          </div></div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-secondary btn-sm" id="dm2-cancel" data-od-id="dom-close-btn" @click="closeModal">关闭</button>
      </div>
    </AppModal>
</template>
<style scoped>
.adv-title {
  font-weight: 600;
  font-size: 11.5px;
  color: var(--muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
</style>
