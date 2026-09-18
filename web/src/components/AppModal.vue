<script setup>
// 通用弹窗外壳：全站唯一弹窗实现。
// - Teleport 到 body：不受任何祖先层叠上下文（sticky 侧栏、backdrop-filter、transform）影响
// - v-if 渲染：开/关即挂载/卸载，无残留状态
// - Esc 关闭 + 打开后焦点移入弹窗 + 关闭后焦点归还触发元素
import { watch, ref, nextTick, onBeforeUnmount } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  id: { type: String, required: true }, // aria-labelledby 的标题 id，每处唯一
  width: { type: String, default: '' }, // 可选 max-width，如 "380px"
})
const emit = defineEmits(['close'])

const el = ref(null)
let lastFocus = null

function onKey(e) {
  if (e.key === 'Escape' && props.open) {
    e.stopPropagation();
    emit('close');
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      lastFocus = document.activeElement;
      document.addEventListener('keydown', onKey, true);
      nextTick(() => el.value && el.value.focus());
    } else {
      document.removeEventListener('keydown', onKey, true);
      if (lastFocus && lastFocus.focus) lastFocus.focus();
      lastFocus = null;
    }
  },
);
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true));
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="modal-overlay open"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="id"
      @click.self="emit('close')"
    >
      <div class="modal" ref="el" tabindex="-1" :style="width ? { maxWidth: width } : null">
        <div class="modal-head">
          <h3 :id="id">{{ title }}</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="emit('close')">✕</button>
        </div>
        <slot></slot>
      </div>
    </div>
  </Teleport>
</template>
