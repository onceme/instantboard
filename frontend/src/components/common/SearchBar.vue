<script setup lang="ts">
import { onUnmounted } from "vue";
import { Search, X, Loader2 } from "lucide-vue-next";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    placeholder?: string;
    loading?: boolean;
    debounceMs?: number;
  }>(),
  {
    placeholder: "搜索…",
    loading: false,
    debounceMs: 300,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  search: [query: string];
}>();

let timer: ReturnType<typeof setTimeout> | null = null;

function clearPending() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function scheduleSearch(query: string) {
  clearPending();
  timer = setTimeout(() => {
    timer = null;
    emit("search", query);
  }, props.debounceMs);
}

function onInput(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  emit("update:modelValue", value);
  scheduleSearch(value);
}

function onEnter(event: KeyboardEvent) {
  clearPending();
  emit("search", (event.target as HTMLInputElement).value);
}

function onClear() {
  clearPending();
  emit("update:modelValue", "");
  emit("search", "");
}

onUnmounted(clearPending);
</script>

<template>
  <div class="search-bar" role="search">
    <Search :size="16" class="search-icon" />
    <input
      type="text"
      class="search-input"
      :value="modelValue"
      :placeholder="placeholder"
      @input="onInput"
      @keydown.enter="onEnter"
    />
    <button
      v-if="modelValue !== ''"
      type="button"
      class="search-clear"
      :class="{ shifted: loading }"
      aria-label="清除搜索"
      @click="onClear"
    >
      <X :size="14" />
    </button>
    <Loader2 v-if="loading" :size="14" class="search-loading" />
  </div>
</template>

<style scoped>
.search-bar {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}

.search-icon {
  position: absolute;
  left: 10px;
  color: var(--text-muted);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 8px 34px 8px 34px;
  font-size: 14px;
  color: var(--text-primary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  outline: none;
  transition:
    border-color var(--transition-fast),
    box-shadow var(--transition-fast);
}

.search-input::placeholder {
  color: var(--text-muted);
}

.search-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
}

.search-clear {
  position: absolute;
  right: 6px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.search-clear:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.search-clear.shifted {
  right: 30px;
}

.search-loading {
  position: absolute;
  right: 10px;
  color: var(--text-muted);
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
