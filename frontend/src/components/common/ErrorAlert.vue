<script setup lang="ts">
import { XCircle, X } from "lucide-vue-next";
import { ref } from "vue";

defineProps<{
  message: string;
  code?: string;
  // When retryable is passed, show a "Retry" button that emits the retry event on click
  retryable?: boolean;
  retrying?: boolean;
}>();

defineEmits<{
  retry: [];
}>();

const visible = ref(true);

function close() {
  visible.value = false;
}
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="error-alert">
      <XCircle :size="16" class="alert-icon" />
      <div class="alert-content">
        <span v-if="code" class="alert-code">{{ code }}</span>
        <span class="alert-message">{{ message }}</span>
      </div>
      <button
        v-if="retryable"
        class="alert-retry"
        :disabled="retrying"
        @click="$emit('retry')"
      >
        {{ retrying ? "重试中…" : "重试" }}
      </button>
      <button class="alert-close" @click="close">
        <X :size="14" />
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.error-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border: 1px solid var(--danger);
  border-radius: var(--radius-md);
  background-color: rgba(239, 68, 68, 0.08);
  color: var(--danger);
}

.alert-icon {
  flex-shrink: 0;
}

.alert-content {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
}

.alert-code {
  font-size: 12px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background-color: var(--danger);
  color: white;
}

.alert-message {
  font-size: 14px;
}

.alert-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--danger);
  transition: background-color var(--transition-fast);
}

.alert-close:hover {
  background-color: rgba(239, 68, 68, 0.15);
}

.alert-retry {
  flex-shrink: 0;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--danger);
  border-radius: var(--radius-sm);
  color: var(--danger);
  transition: background-color var(--transition-fast);
}

.alert-retry:hover {
  background-color: rgba(239, 68, 68, 0.15);
}

.alert-retry:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
