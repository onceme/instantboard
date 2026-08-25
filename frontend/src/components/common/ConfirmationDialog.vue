<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { AlertTriangle } from "lucide-vue-next";

const props = withDefaults(
  defineProps<{
    visible: boolean;
    title: string;
    message?: string;
    confirmText?: string;
    cancelText?: string;
    danger?: boolean;
  }>(),
  {
    message: "",
    confirmText: "确认",
    cancelText: "取消",
    danger: false,
  },
);

const emit = defineEmits<{
  confirm: [];
  cancel: [];
  "update:visible": [value: boolean];
}>();

const confirmBtn = ref<HTMLButtonElement | null>(null);
let previouslyFocused: HTMLElement | null = null;

function handleConfirm() {
  emit("confirm");
  emit("update:visible", false);
}

function handleCancel() {
  emit("cancel");
  emit("update:visible", false);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && props.visible) {
    handleCancel();
  }
}

watch(
  () => props.visible,
  visible => {
    if (visible) {
      previouslyFocused =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      nextTick(() => confirmBtn.value?.focus());
    } else if (previouslyFocused) {
      previouslyFocused.focus();
      previouslyFocused = null;
    }
  },
  { immediate: true },
);

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div
        v-if="visible"
        class="dialog-overlay"
        @click.self="handleCancel"
      >
        <div
          class="dialog"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <div class="dialog-header">
            <AlertTriangle
              v-if="danger"
              :size="20"
              class="dialog-icon dialog-icon-danger"
            />
            <h3 class="dialog-title">
              {{ title }}
            </h3>
          </div>
          <p v-if="message" class="dialog-message">
            {{ message }}
          </p>
          <div class="dialog-actions">
            <button type="button" class="btn btn-cancel" @click="handleCancel">
              {{ cancelText }}
            </button>
            <button
              ref="confirmBtn"
              type="button"
              class="btn btn-confirm"
              :class="{ 'btn-danger': danger }"
              @click="handleConfirm"
            >
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background-color: rgba(15, 23, 42, 0.45);
}

.dialog {
  width: 100%;
  max-width: 400px;
  padding: 20px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.dialog-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dialog-icon-danger {
  flex-shrink: 0;
  color: var(--danger);
}

.dialog-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.dialog-message {
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--text-secondary);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

.btn {
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-md);
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.btn-cancel {
  color: var(--text-secondary);
  background-color: transparent;
  border: 1px solid var(--border-color);
}

.btn-cancel:hover {
  color: var(--text-primary);
  background-color: var(--bg-hover);
}

.btn-confirm {
  color: #fff;
  background-color: var(--accent);
  border: none;
}

.btn-confirm:hover {
  background-color: var(--accent-hover);
}

.btn-confirm.btn-danger {
  background-color: var(--danger);
}

.btn-confirm.btn-danger:hover {
  opacity: 0.9;
}

.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity var(--transition-fast);
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}
</style>
